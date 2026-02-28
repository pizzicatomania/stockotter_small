#!/usr/bin/env bash
set -euo pipefail

OWNER="pizzicatomania"
REPO="stockotter_small"
BASE_BRANCH="main"

LIMIT="${LIMIT:-50}"
LABEL="${LABEL:-}"
ONLY_ME="${ONLY_ME:-0}"
DRY_RUN="${DRY_RUN:-0}"
MERGE_METHOD="${MERGE_METHOD:-squash}"     # merge | squash | rebase
DELETE_BRANCH="${DELETE_BRANCH:-1}"        # 원격 브랜치 삭제(gh pr merge --delete-branch)
CLEAN_LOCAL_BRANCH="${CLEAN_LOCAL_BRANCH:-1}"  # ✅ 로컬 브랜치도 삭제
PRUNE_WORKTREES="${PRUNE_WORKTREES:-1}"        # ✅ worktree 메타 정리
LOG_DIR="${LOG_DIR:-.codex_logs}"
BRANCH_PREFIX="${BRANCH_PREFIX:-codex/issue-}"
CODEX_MODE="${CODEX_MODE:---full-auto}"    # 쓰기 허용 포함

mkdir -p "$LOG_DIR"

need_cmd() { command -v "$1" >/dev/null 2>&1 || { echo "ERROR: '$1' not found"; exit 1; }; }
need_cmd gh; need_cmd jq; need_cmd git; need_cmd codex; need_cmd tee; need_cmd grep

if [ ! -d ".git" ]; then
  echo "ERROR: repo root에서 실행하세요 (.git 없음)."
  exit 1
fi

hr() { printf "%s\n" "============================================================"; }
hdr() { hr; printf "%s\n" "$1"; hr; }

extract_pr_url() {
  grep -Eo 'https://github\.com/[^ ]+/pull/[0-9]+' | tail -n 1
}

QUERY="state:open sort:created-asc"
if [ -n "$LABEL" ]; then QUERY="$QUERY label:\"$LABEL\""; fi
if [ "$ONLY_ME" = "1" ]; then QUERY="$QUERY assignee:@me"; fi

ISSUES=$(gh issue list -R "$OWNER/$REPO" \
  --state open \
  --search "$QUERY" \
  --limit "$LIMIT" \
  --json number \
  --jq '.[].number')

if [ -z "${ISSUES:-}" ]; then
  echo "No issues found."
  exit 0
fi

git fetch --all

for N in $ISSUES; do
  TITLE=$(gh issue view -R "$OWNER/$REPO" "$N" --json title --jq '.title' | tr '\n' ' ')
  BRANCH="${BRANCH_PREFIX}${N}"

  TASK_FILE="$LOG_DIR/task_issue_${N}.md"
  RUN_LOG="$LOG_DIR/run_issue_${N}.log"

  hdr "ISSUE #$N — $TITLE"

  if [ "$DRY_RUN" = "1" ]; then
    echo "[DRY_RUN] Would process #$N"
    continue
  fi

  # 시작 코멘트
  gh issue comment -R "$OWNER/$REPO" "$N" --body "🤖 Codex started (branch: \`$BRANCH\`)."

  # 브랜치 준비
  git checkout "$BASE_BRANCH" >/dev/null 2>&1
  git pull --ff-only >/dev/null 2>&1
  git checkout -B "$BRANCH" >/dev/null 2>&1

  ISSUE_JSON=$(gh issue view -R "$OWNER/$REPO" "$N" \
    --json number,title,body,labels,assignees,author,url,createdAt,updatedAt)

  cat > "$TASK_FILE" <<EOF
You are working in repo $OWNER/$REPO on branch $BRANCH.

GOAL
- Fix GitHub Issue #$N: $TITLE
- Keep changes minimal and focused.

ISSUE DATA (verbatim JSON)
$ISSUE_JSON

HARD REQUIREMENT
- You MUST produce actual code changes (non-empty \`git diff\`).
- If no code change is needed, create: docs/issue_notes/issue-$N.md explaining why.

GLOBAL RULES
- Follow AGENTS.md and docs/CODEX_RULES.md (docs wins on conflict).
- No secrets. No arbitrary external network calls.
- No new dependencies without stopping and explaining.

SPEED-FIRST VALIDATION (best effort)
- Quick: python -m compileall src
- If fast: pytest -q
- If fast: ruff check .

DELIVERABLES
- Implement fix (or issue note)
- Short summary + how to test
EOF

  echo "Codex output -> streaming (also saved to $RUN_LOG)"
  echo

  set +e
  codex exec $CODEX_MODE < "$TASK_FILE" 2>&1 | tee "$RUN_LOG"
  CODEX_RC=${PIPESTATUS[0]}
  set -e

  echo
  if [ $CODEX_RC -ne 0 ]; then
    echo "❌ Codex failed (exit=$CODEX_RC)."
    tail -n 40 "$RUN_LOG" || true
    gh issue comment -R "$OWNER/$REPO" "$N" --body "❌ Codex failed (exit=$CODEX_RC). See local log: \`$RUN_LOG\`."
    continue
  fi

  if git diff --quiet; then
    LAST=$(tail -n 40 "$RUN_LOG" | sed 's/```/`​`​`/g')
    gh issue comment -R "$OWNER/$REPO" "$N" \
      --body "⚠️ Codex produced no git diff. Last log lines:\n\n\`\`\`\n$LAST\n\`\`\`"
    echo "⚠️ No changes produced; skipping."
    continue
  fi

  # 커밋/푸시
  git add -A
  git commit -m "Fix #$N: $TITLE" >/dev/null 2>&1 || true
  git push -u origin "$BRANCH" >/dev/null 2>&1

  # PR 생성
  PR_OUT=$(gh pr create -R "$OWNER/$REPO" \
    --base "$BASE_BRANCH" --head "$BRANCH" \
    --title "Fix #$N: $TITLE" \
    --body "Closes #$N" 2>&1)

  PR_URL=$(echo "$PR_OUT" | extract_pr_url)
  if [ -z "${PR_URL:-}" ]; then
    echo "❌ PR URL parse failed."
    gh issue comment -R "$OWNER/$REPO" "$N" --body "❌ PR created but URL parsing failed."
    continue
  fi

  gh issue comment -R "$OWNER/$REPO" "$N" --body "🔗 PR created: $PR_URL"

  # merge (+ 원격 브랜치 삭제 옵션)
  MERGE_FLAG="--$MERGE_METHOD"
  DELETE_FLAG=""
  if [ "$DELETE_BRANCH" = "1" ]; then DELETE_FLAG="--delete-branch"; fi

  gh pr merge -R "$OWNER/$REPO" "$PR_URL" $MERGE_FLAG $DELETE_FLAG >/dev/null 2>&1

  gh issue comment -R "$OWNER/$REPO" "$N" --body "✅ Merged: $PR_URL"
  gh issue close -R "$OWNER/$REPO" "$N" >/dev/null 2>&1

  echo "✅ merged & closed (#$N)"

  # ====== ✅ 추가: 작업 끝난 로컬 브랜치/워크트리 정리 ======
  # main으로 복귀(브랜치 삭제를 위해)
  git checkout "$BASE_BRANCH" >/dev/null 2>&1
  git pull --ff-only >/dev/null 2>&1 || true

  if [ "$CLEAN_LOCAL_BRANCH" = "1" ]; then
    # 브랜치가 이미 main이면 삭제 불가하므로, main 체크아웃 후 삭제
    git branch -D "$BRANCH" >/dev/null 2>&1 || true
  fi

  if [ "$PRUNE_WORKTREES" = "1" ]; then
    git worktree prune -v >/dev/null 2>&1 || true
  fi

  # 원격 추적 브랜치 정리
  git fetch -p >/dev/null 2>&1 || true
done

echo "Done."