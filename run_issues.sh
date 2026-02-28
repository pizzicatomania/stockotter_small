#!/usr/bin/env bash
set -euo pipefail

OWNER="pizzicatomania"
REPO="stockotter_small"
BASE_BRANCH="main"

# ========== 옵션 ==========
LIMIT="${LIMIT:-50}"
LABEL="${LABEL:-}"
ONLY_ME="${ONLY_ME:-0}"
DRY_RUN="${DRY_RUN:-0}"
MERGE_METHOD="${MERGE_METHOD:-squash}"   # merge | squash | rebase
DELETE_BRANCH="${DELETE_BRANCH:-1}"
LOG_DIR="${LOG_DIR:-.codex_logs}"
BRANCH_PREFIX="${BRANCH_PREFIX:-codex/issue-}"
CODEX_MODE="${CODEX_MODE:---full-auto}" # 권장: --full-auto 또는 --sandbox workspace-write

mkdir -p "$LOG_DIR"

need_cmd() { command -v "$1" >/dev/null 2>&1 || { echo "ERROR: '$1' not found"; exit 1; }; }
need_cmd gh
need_cmd jq
need_cmd git
need_cmd codex
need_cmd tee
need_cmd awk
need_cmd sed
need_cmd grep

if [ ! -d ".git" ]; then
  echo "ERROR: repo root에서 실행하세요 (.git 없음)."
  exit 1
fi

# ====== 콘솔 스타일 ======
# 색상(ANSI)
RED=$'\033[31m'
GRN=$'\033[32m'
YEL=$'\033[33m'
BLU=$'\033[34m'
MAG=$'\033[35m'
CYN=$'\033[36m'
BOLD=$'\033[1m'
DIM=$'\033[2m'
RST=$'\033[0m'

hr() { printf "%s\n" "============================================================"; }
hdr() { hr; printf "%s%s%s\n" "$BOLD" "$1" "$RST"; hr; }
note() { printf "%s%s%s\n" "$DIM" "$1" "$RST"; }
ok() { printf "%s%s%s\n" "$GRN" "$1" "$RST"; }
warn() { printf "%s%s%s\n" "$YEL" "$1" "$RST"; }
err() { printf "%s%s%s\n" "$RED" "$1" "$RST"; }
info() { printf "%s%s%s\n" "$CYN" "$1" "$RST"; }

extract_pr_url() {
  grep -Eo 'https://github\.com/[^ ]+/pull/[0-9]+' | tail -n 1
}

# Codex 출력 prettify:
# - command처럼 보이는 라인(> ... , $ ..., `Running:` 등)을 색칠
pretty_codex_stream() {
  awk -v CYN="$CYN" -v MAG="$MAG" -v YEL="$YEL" -v RST="$RST" '
    {
      line=$0
      # 흔한 command 패턴 하이라이트
      if (line ~ /^(\s*[$>]|Running:|Executing:)/) {
        print MAG line RST
      } else if (line ~ /(git |pytest|ruff|python -m|pip |make )/) {
        print CYN line RST
      } else if (line ~ /(ERROR|Error|FAILED|Failure|Traceback)/) {
        print YEL line RST
      } else {
        print line
      }
      fflush()
    }
  '
}

# Git diff 요약 출력
show_diff_summary() {
  if git diff --quiet; then
    warn "No git diff."
    return 0
  fi

  info "Changed files:"
  git diff --name-status | sed 's/^/  /'

  info "Diff stat:"
  git diff --stat | sed 's/^/  /'
}

# ====== 오래된(open) 이슈부터 ======
QUERY="state:open sort:created-asc"
if [ -n "$LABEL" ]; then QUERY="$QUERY label:\"$LABEL\""; fi
if [ "$ONLY_ME" = "1" ]; then QUERY="$QUERY assignee:@me"; fi

hdr "StockOtter Small — Issue Auto Loop"
note "Repo:  $OWNER/$REPO"
note "Query: $QUERY"
note "LIMIT=$LIMIT DRY_RUN=$DRY_RUN MERGE_METHOD=$MERGE_METHOD DELETE_BRANCH=$DELETE_BRANCH CODEX_MODE=$CODEX_MODE"
echo

ISSUES=$(gh issue list -R "$OWNER/$REPO" \
  --state open \
  --search "$QUERY" \
  --limit "$LIMIT" \
  --json number \
  --jq '.[].number')

if [ -z "${ISSUES:-}" ]; then
  warn "No issues found."
  exit 0
fi

git fetch --all

for N in $ISSUES; do
  hdr "ISSUE #$N (oldest first)"

  ISSUE_JSON=$(gh issue view -R "$OWNER/$REPO" "$N" \
    --json number,title,body,labels,assignees,author,url,createdAt,updatedAt)
  TITLE=$(echo "$ISSUE_JSON" | jq -r '.title' | tr '\n' ' ')
  BRANCH="${BRANCH_PREFIX}${N}"

  info "Title : $TITLE"
  info "Branch: $BRANCH"

  TASK_FILE="$LOG_DIR/task_issue_${N}.md"
  RUN_LOG="$LOG_DIR/run_issue_${N}.log"

  if [ "$DRY_RUN" = "1" ]; then
    warn "[DRY_RUN] Would: comment(start) -> branch -> codex -> commit -> push -> PR -> merge -> comment(done) -> close"
    continue
  fi

  # 1) 시작 코멘트
  gh issue comment -R "$OWNER/$REPO" "$N" \
    --body "🤖 Codex started (branch: \`$BRANCH\`)."

  # 2) main 최신화 + 브랜치 생성
  git checkout "$BASE_BRANCH" >/dev/null 2>&1
  git pull --ff-only
  git checkout -B "$BRANCH" >/dev/null 2>&1

  # 3) Codex 지시
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

  # 4) Codex 실행 (실시간 출력 + 파일 저장)
  echo
  info "Codex output (streaming) -> $RUN_LOG"
  echo

  set +e
  # stdout/stderr 모두: 터미널로 + 로그 파일
  codex exec $CODEX_MODE < "$TASK_FILE" 2>&1 \
    | pretty_codex_stream \
    | tee "$RUN_LOG"
  CODEX_RC=${PIPESTATUS[0]}
  set -e

  echo
  if [ $CODEX_RC -ne 0 ]; then
    err "Codex failed for issue #$N (exit=$CODEX_RC)."
    warn "Last 50 log lines:"
    tail -n 50 "$RUN_LOG" | sed 's/^/  /'
    gh issue comment -R "$OWNER/$REPO" "$N" \
      --body "❌ Codex failed (exit=$CODEX_RC). See local log: \`$RUN_LOG\`."
    continue
  fi

  # 5) 변경 요약
  hdr "Post-Codex Summary for #$N"
  show_diff_summary

  if git diff --quiet; then
    warn "No changes were produced. Leaving a note on the issue and skipping."
    LAST=$(tail -n 40 "$RUN_LOG" | sed 's/```/`​`​`/g')
    gh issue comment -R "$OWNER/$REPO" "$N" \
      --body "⚠️ Codex produced no git diff. Last log lines:\n\n\`\`\`\n$LAST\n\`\`\`"
    continue
  fi

  # 6) 커밋/푸시
  info "Committing & pushing..."
  git add -A
  git commit -m "Fix #$N: $TITLE" || true
  git push -u origin "$BRANCH"

  # 7) PR 생성
  info "Creating PR..."
  PR_OUT=$(gh pr create -R "$OWNER/$REPO" \
    --base "$BASE_BRANCH" --head "$BRANCH" \
    --title "Fix #$N: $TITLE" \
    --body "Closes #$N

Summary:
- (auto)
" 2>&1)

  PR_URL=$(echo "$PR_OUT" | extract_pr_url)
  if [ -z "${PR_URL:-}" ]; then
    err "Could not parse PR URL from gh pr create output:"
    echo "$PR_OUT"
    gh issue comment -R "$OWNER/$REPO" "$N" \
      --body "❌ PR created but URL parsing failed. See local output."
    continue
  fi
  ok "PR: $PR_URL"

  gh issue comment -R "$OWNER/$REPO" "$N" \
    --body "🔗 PR created: $PR_URL"

  # 8) 즉시 merge
  info "Merging immediately (no CI / no required reviews)..."
  MERGE_FLAG="--$MERGE_METHOD"
  DELETE_FLAG=""
  if [ "$DELETE_BRANCH" = "1" ]; then DELETE_FLAG="--delete-branch"; fi

  gh pr merge -R "$OWNER/$REPO" "$PR_URL" $MERGE_FLAG $DELETE_FLAG
  ok "Merged: $PR_URL"

  # 9) 완료 코멘트 + close
  gh issue comment -R "$OWNER/$REPO" "$N" \
    --body "✅ Merged: $PR_URL"
  gh issue close -R "$OWNER/$REPO" "$N"

  ok "Closed issue #$N"
done

hdr "All done."