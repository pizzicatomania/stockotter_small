#!/usr/bin/env bash
set -euo pipefail

OWNER="pizzicatomania"
REPO="stockotter_small"
BASE_BRANCH="main"

# ===== 실행 옵션 =====
LIMIT="${LIMIT:-20}"                 # 최대 처리 이슈 수
LABEL="${LABEL:-}"                   # 예: bug, good-first-issue (빈값이면 라벨 필터 없음)
ONLY_ME="${ONLY_ME:-0}"              # 1이면 assignee:@me 만
DRY_RUN="${DRY_RUN:-1}"              # 1이면 codex 실행/푸시/PR 생성 안 함
CREATE_PR="${CREATE_PR:-0}"          # 1이면 PR 생성
LOG_DIR="${LOG_DIR:-.codex_logs}"    # 로그 저장
BRANCH_PREFIX="${BRANCH_PREFIX:-codex/issue-}"

mkdir -p "$LOG_DIR"

# repo 루트인지 최소 체크
if [ ! -d ".git" ]; then
  echo "ERROR: repo root에서 실행하세요 (.git 없음)."
  exit 1
fi

# 이슈 list 쿼리 구성
QUERY="state:open"
if [ -n "$LABEL" ]; then
  QUERY="$QUERY label:\"$LABEL\""
fi
if [ "$ONLY_ME" = "1" ]; then
  QUERY="$QUERY assignee:@me"
fi

echo "Issue query: $QUERY"
echo "LIMIT=$LIMIT DRY_RUN=$DRY_RUN CREATE_PR=$CREATE_PR"

# 이슈 번호들 가져오기
ISSUES=$(gh issue list -R "$OWNER/$REPO" --search "$QUERY" --limit "$LIMIT" --json number --jq '.[].number')

if [ -z "$ISSUES" ]; then
  echo "No issues found."
  exit 0
fi

git fetch --all

for N in $ISSUES; do
  echo "=== ISSUE #$N ==="

  ISSUE_JSON=$(gh issue view -R "$OWNER/$REPO" "$N" --json number,title,body,labels,assignees,author,url)
  TITLE=$(echo "$ISSUE_JSON" | jq -r '.title' | tr '\n' ' ')

  BRANCH="${BRANCH_PREFIX}${N}"

  git checkout "$BASE_BRANCH"
  git pull --ff-only
  git checkout -B "$BRANCH"

  TASK_FILE="$LOG_DIR/task_issue_${N}.md"
  RUN_LOG="$LOG_DIR/run_issue_${N}.log"

  cat > "$TASK_FILE" <<EOF
You are working in repo $OWNER/$REPO on branch $BRANCH.

GOAL
- Fix GitHub Issue #$N: $TITLE
- Definition of Done:
  - The described bug/feature is resolved as stated in the issue.
  - Changes are minimal and focused.
  - Relevant checks/tests pass (see COMMANDS).

ISSUE DATA (verbatim JSON)
$ISSUE_JSON

CONSTRAINTS (must follow)
- Scope:
  - Do NOT refactor unrelated code.
  - Do NOT mix multiple issues in one branch/PR.
  - Do NOT add new dependencies unless absolutely necessary. If needed, STOP and explain why.
- Quality:
  - If reproducible: document repro steps and verify the fix.
  - Add or update tests when feasible.
  - Keep code style consistent with existing repo.
- Security:
  - Do NOT read or print secrets (.env, keys, tokens, credentials).
  - Do NOT make external network calls (curl/wget/http) unless explicitly necessary; if so, STOP and explain.
- Workflow:
  - Produce a clear summary of changes and why.
  - Provide how to test and actual test output.

COMMANDS (run as appropriate)
- Create/activate venv if needed.
- Run the project's standard checks if present.
- At minimum: python -m compileall .

DELIVERABLES
- Modify code to fix the issue.
- Provide:
  - Summary
  - How to test
  - Commands run + results
EOF

  if [ "$DRY_RUN" = "1" ]; then
    echo "[DRY_RUN] Would run Codex on issue #$N using $TASK_FILE"
    continue
  fi

  set +e
  codex exec --file "$TASK_FILE" >"$RUN_LOG" 2>&1
  CODEX_RC=$?
  set -e

  if [ $CODEX_RC -ne 0 ]; then
    echo "Codex failed for issue #$N (exit=$CODEX_RC). Log: $RUN_LOG"
    # 선택: 실패 라벨
    # gh issue edit -R "$OWNER/$REPO" "$N" --add-label "codex-failed" || true
    continue
  fi

  if ! git diff --quiet; then
    git add -A
    git commit -m "Fix #$N: $TITLE" || true
  else
    echo "No changes made for issue #$N. Skipping push/PR."
    continue
  fi

  git push -u origin "$BRANCH"

  if [ "$CREATE_PR" = "1" ]; then
    gh pr create -R "$OWNER/$REPO" --base "$BASE_BRANCH" --head "$BRANCH" \
      --title "Fix #$N: $TITLE" \
      --body "Closes #$N

Summary:
- (Codex to fill) 

Tests:
- (Codex to fill)
"
  fi
done