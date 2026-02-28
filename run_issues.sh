#!/usr/bin/env bash
set -euo pipefail

OWNER="pizzicatomania"
REPO="stockotter_small"
BASE_BRANCH="main"

# ========== 옵션 ==========
LIMIT="${LIMIT:-50}"                  # 처리할 이슈 수
LABEL="${LABEL:-}"                    # 특정 라벨만 (예: bug) / 없으면 전체
ONLY_ME="${ONLY_ME:-0}"               # 1이면 assignee:@me만
DRY_RUN="${DRY_RUN:-0}"               # 1이면 codex/푸시/PR/merge 안 함(계획만)
MERGE_METHOD="${MERGE_METHOD:-squash}"# merge | squash | rebase
DELETE_BRANCH="${DELETE_BRANCH:-1}"   # 1이면 merge 후 브랜치 삭제
LOG_DIR="${LOG_DIR:-.codex_logs}"
BRANCH_PREFIX="${BRANCH_PREFIX:-codex/issue-}"

mkdir -p "$LOG_DIR"

need_cmd() { command -v "$1" >/dev/null 2>&1 || { echo "ERROR: '$1' not found"; exit 1; }; }
need_cmd gh
need_cmd jq
need_cmd git
need_cmd codex

if [ ! -d ".git" ]; then
  echo "ERROR: repo root에서 실행하세요 (.git 없음)."
  exit 1
fi

# 오래된(open) 이슈부터
QUERY="state:open sort:created-asc"
if [ -n "$LABEL" ]; then QUERY="$QUERY label:\"$LABEL\""; fi
if [ "$ONLY_ME" = "1" ]; then QUERY="$QUERY assignee:@me"; fi

echo "Repo: $OWNER/$REPO"
echo "Query: $QUERY"
echo "LIMIT=$LIMIT DRY_RUN=$DRY_RUN MERGE_METHOD=$MERGE_METHOD DELETE_BRANCH=$DELETE_BRANCH"

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

extract_pr_url() {
  grep -Eo 'https://github\.com/[^ ]+/pull/[0-9]+' | tail -n 1
}

for N in $ISSUES; do
  echo "=================================================="
  echo "=== ISSUE #$N (oldest first) ==="
  echo "=================================================="

  ISSUE_JSON=$(gh issue view -R "$OWNER/$REPO" "$N" \
    --json number,title,body,labels,assignees,author,url,createdAt,updatedAt)

  TITLE=$(echo "$ISSUE_JSON" | jq -r '.title' | tr '\n' ' ')
  BRANCH="${BRANCH_PREFIX}${N}"

  TASK_FILE="$LOG_DIR/task_issue_${N}.md"
  RUN_LOG="$LOG_DIR/run_issue_${N}.log"

  echo "Title : $TITLE"
  echo "Branch: $BRANCH"

  if [ "$DRY_RUN" = "1" ]; then
    echo "[DRY_RUN] Would: branch -> codex -> commit -> push -> PR -> merge"
    continue
  fi

  # main 최신화 + 브랜치 생성
  git checkout "$BASE_BRANCH"
  git pull --ff-only
  git checkout -B "$BRANCH"

  # Codex 지시(속도 우선: 테스트/린트는 '가능하면'으로 두되, 최소 compile은 권장)
  cat > "$TASK_FILE" <<EOF
You are working in repo $OWNER/$REPO on branch $BRANCH.

GOAL
- Fix GitHub Issue #$N: $TITLE
- Keep changes minimal and focused.

ISSUE DATA (verbatim JSON)
$ISSUE_JSON

GLOBAL RULES
- Follow AGENTS.md and docs/CODEX_RULES.md (if conflict, docs/CODEX_RULES.md wins).
- Do NOT read/print secrets (.env, keys, tokens, credentials).
- Do NOT make arbitrary external network calls (curl/wget/custom HTTP) for investigation.
- Do NOT add new dependencies. If absolutely necessary, STOP and explain.

SPEED-FIRST VALIDATION (best effort)
- If quick: python -m compileall src
- If already set up and fast: pytest -q
- If already set up and fast: ruff check .

DELIVERABLES
- Implement fix
- Provide short summary + how to test (even if not executed)
EOF

  # Codex 실행: stdin 방식
  set +e
  codex exec < "$TASK_FILE" >"$RUN_LOG" 2>&1
  CODEX_RC=$?
  set -e

  if [ $CODEX_RC -ne 0 ]; then
    echo "❌ Codex failed for issue #$N (exit=$CODEX_RC). Log: $RUN_LOG"
    continue
  fi

  if git diff --quiet; then
    echo "⚠️ No changes made for issue #$N. Skipping."
    continue
  fi

  # 커밋/푸시
  git add -A
  git commit -m "Fix #$N: $TITLE" || true
  git push -u origin "$BRANCH"

  # PR 생성 (pr create는 --json 사용하지 않음)
  PR_OUT=$(gh pr create -R "$OWNER/$REPO" \
    --base "$BASE_BRANCH" --head "$BRANCH" \
    --title "Fix #$N: $TITLE" \
    --body "Closes #$N

Summary:
- (auto)
" 2>&1)

  PR_URL=$(echo "$PR_OUT" | extract_pr_url)
  if [ -z "${PR_URL:-}" ]; then
    echo "❌ Could not parse PR URL from gh pr create output:"
    echo "$PR_OUT"
    exit 1
  fi
  echo "PR: $PR_URL"

  # 즉시 merge (CI/리뷰 gate 없음)
  MERGE_FLAG="--$MERGE_METHOD"
  DELETE_FLAG=""
  if [ "$DELETE_BRANCH" = "1" ]; then DELETE_FLAG="--delete-branch"; fi

  gh pr merge -R "$OWNER/$REPO" "$PR_URL" $MERGE_FLAG $DELETE_FLAG

  echo "✅ Merged PR for issue #$N. Next..."
done

echo "Done."