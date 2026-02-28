#!/usr/bin/env bash
set -euo pipefail

OWNER="pizzicatomania"
REPO="stockotter_small"
BASE_BRANCH="main"

<<<<<<< Updated upstream
# ===== 실행 옵션 =====
LIMIT="${LIMIT:-20}"                 # 최대 처리 이슈 수
LABEL="${LABEL:-}"                   # 예: bug, good-first-issue (빈값이면 라벨 필터 없음)
ONLY_ME="${ONLY_ME:-0}"              # 1이면 assignee:@me 만
DRY_RUN="${DRY_RUN:-1}"              # 1이면 codex 실행/푸시/PR 생성 안 함
CREATE_PR="${CREATE_PR:-0}"          # 1이면 PR 생성
LOG_DIR="${LOG_DIR:-.codex_logs}"    # 로그 저장
=======
# ========== 실행 옵션 ==========
LIMIT="${LIMIT:-50}"                  # 처리할 이슈 수
LABEL="${LABEL:-}"                    # 특정 라벨만 (예: bug) / 없으면 전체
ONLY_ME="${ONLY_ME:-0}"               # 1이면 assignee:@me 만
DRY_RUN="${DRY_RUN:-0}"               # 1이면 codex/푸시/PR/merge 안 함(계획만)
MERGE_METHOD="${MERGE_METHOD:-squash}"# merge | squash | rebase
DELETE_BRANCH="${DELETE_BRANCH:-1}"   # 1이면 merge 후 브랜치 삭제
LOG_DIR="${LOG_DIR:-.codex_logs}"
>>>>>>> Stashed changes
BRANCH_PREFIX="${BRANCH_PREFIX:-codex/issue-}"

# 체크 대기 최대 시간(초): 30분 기본
CHECK_TIMEOUT_SEC="${CHECK_TIMEOUT_SEC:-1800}"
CHECK_POLL_SEC="${CHECK_POLL_SEC:-10}"

mkdir -p "$LOG_DIR"

# repo 루트인지 최소 체크
if [ ! -d ".git" ]; then
  echo "ERROR: repo root에서 실행하세요 (.git 없음)."
  exit 1
fi

<<<<<<< Updated upstream
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
=======
need_cmd() {
  command -v "$1" >/dev/null 2>&1 || { echo "ERROR: '$1' not found in PATH"; exit 1; }
}

need_cmd gh
need_cmd jq
need_cmd git
need_cmd codex

# ========== 오래된(open) 이슈부터 가져오기 ==========
QUERY="state:open sort:created-asc"
if [ -n "$LABEL" ]; then
  QUERY="$QUERY label:\"$LABEL\""
fi
if [ "$ONLY_ME" = "1" ]; then
  QUERY="$QUERY assignee:@me"
fi

echo "Repo: $OWNER/$REPO"
echo "Query: $QUERY"
echo "LIMIT=$LIMIT DRY_RUN=$DRY_RUN MERGE_METHOD=$MERGE_METHOD DELETE_BRANCH=$DELETE_BRANCH"

ISSUES=$(gh issue list -R "$OWNER/$REPO" \
  --state open \
  --search "$QUERY" \
  --limit "$LIMIT" \
  --json number \
  --jq '.[].number')
>>>>>>> Stashed changes

if [ -z "$ISSUES" ]; then
  echo "No issues found."
  exit 0
fi

git fetch --all

<<<<<<< Updated upstream
for N in $ISSUES; do
  echo "=== ISSUE #$N ==="

  ISSUE_JSON=$(gh issue view -R "$OWNER/$REPO" "$N" --json number,title,body,labels,assignees,author,url)
=======
# ========== PR URL 파싱(gh pr create 출력에서 URL만 추출) ==========
extract_pr_url() {
  grep -Eo 'https://github\.com/[^ ]+/pull/[0-9]+' | tail -n 1
}

# ========== 체크 대기 ==========
wait_for_checks() {
  local pr_url="$1"

  # 체크 자체가 없는 레포일 수도 있음.
  # gh pr checks가 "no checks reported"를 내기도 해서,
  # 실패 처리 대신 일정 시간 재시도 후 그래도 없으면 "체크 없음"으로 통과 처리 옵션을 둔다.
  local start_ts now_ts elapsed
  start_ts=$(date +%s)

  while true; do
    set +e
    OUT=$(gh pr checks -R "$OWNER/$REPO" "$pr_url" 2>&1)
    RC=$?
    set -e

    # 성공(체크 통과) 또는 체크 실패를 gh가 exit code로 구분해주는 경우가 많음.
    if [ $RC -eq 0 ]; then
      echo "✅ Checks passed for $pr_url"
      return 0
    fi

    # 체크가 아직 안 뜨는 케이스: 일정 시간은 기다렸다가,
    # 타임아웃까지 계속 "no checks reported"면 이 레포는 체크가 없는 것으로 보고 통과 처리.
    if echo "$OUT" | grep -qiE "no checks|no check runs|could not find any checks|no status checks"; then
      now_ts=$(date +%s)
      elapsed=$((now_ts - start_ts))
      if [ $elapsed -ge $CHECK_TIMEOUT_SEC ]; then
        echo "⚠️ No checks reported for $pr_url until timeout; continuing without CI checks."
        return 0
      fi
      echo "⏳ Checks not reported yet; waiting... (${elapsed}s)"
      sleep "$CHECK_POLL_SEC"
      continue
    fi

    # 그 외는 실패로 보고 중단
    echo "❌ Checks command failed for $pr_url"
    echo "$OUT"
    return 1
  done
}

for N in $ISSUES; do
  echo "=================================================="
  echo "=== ISSUE #$N (oldest first) ==="
  echo "=================================================="

  ISSUE_JSON=$(gh issue view -R "$OWNER/$REPO" "$N" \
    --json number,title,body,labels,assignees,author,url,createdAt,updatedAt)

>>>>>>> Stashed changes
  TITLE=$(echo "$ISSUE_JSON" | jq -r '.title' | tr '\n' ' ')
  BRANCH="${BRANCH_PREFIX}${N}"
<<<<<<< Updated upstream

=======

  TASK_FILE="$LOG_DIR/task_issue_${N}.md"
  RUN_LOG="$LOG_DIR/run_issue_${N}.log"

  echo "Title : $TITLE"
  echo "Branch: $BRANCH"

  if [ "$DRY_RUN" = "1" ]; then
    echo "[DRY_RUN] Would: create branch -> codex -> commit -> push -> PR -> checks -> merge"
    continue
  fi

  # main 최신화 + 브랜치 생성
>>>>>>> Stashed changes
  git checkout "$BASE_BRANCH"
  git pull --ff-only
  git checkout -B "$BRANCH"

<<<<<<< Updated upstream
  TASK_FILE="$LOG_DIR/task_issue_${N}.md"
  RUN_LOG="$LOG_DIR/run_issue_${N}.log"

=======
  # Codex 작업 지시(AGENTS.md / docs/CODEX_RULES.md 기반)
>>>>>>> Stashed changes
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

<<<<<<< Updated upstream
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
=======
GLOBAL RULES
- Follow AGENTS.md and docs/CODEX_RULES.md (if conflict, docs/CODEX_RULES.md wins).
- Do NOT read/print secrets (.env, keys, tokens, credentials).
- Do NOT make arbitrary external network calls (curl/wget/custom HTTP) for investigation.
- Do NOT add new dependencies. If absolutely necessary, STOP and explain.
>>>>>>> Stashed changes

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

<<<<<<< Updated upstream
  if [ "$DRY_RUN" = "1" ]; then
    echo "[DRY_RUN] Would run Codex on issue #$N using $TASK_FILE"
    continue
  fi

=======
  # Codex 실행: 이 CLI는 --file이 없으니 stdin으로 넣는다.
>>>>>>> Stashed changes
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

<<<<<<< Updated upstream
  if ! git diff --quiet; then
    git add -A
    git commit -m "Fix #$N: $TITLE" || true
  else
    echo "No changes made for issue #$N. Skipping push/PR."
=======
  # 변경 없으면 skip
  if git diff --quiet; then
    echo "⚠️ No changes made for issue #$N. Skipping."
>>>>>>> Stashed changes
    continue
  fi

  git push -u origin "$BRANCH"

<<<<<<< Updated upstream
  if [ "$CREATE_PR" = "1" ]; then
    gh pr create -R "$OWNER/$REPO" --base "$BASE_BRANCH" --head "$BRANCH" \
      --title "Fix #$N: $TITLE" \
      --body "Closes #$N
=======
  # PR 생성: pr create는 --json이 없을 수 있으니 출력에서 URL 파싱
  PR_OUT=$(gh pr create -R "$OWNER/$REPO" \
    --base "$BASE_BRANCH" --head "$BRANCH" \
    --title "Fix #$N: $TITLE" \
    --body "Closes #$N
>>>>>>> Stashed changes

Summary:
- (Codex to fill) 

Tests:
<<<<<<< Updated upstream
- (Codex to fill)
"
  fi
done
=======
- pytest -q
- ruff check .
- python -m compileall src
" 2>&1)

  PR_URL=$(echo "$PR_OUT" | extract_pr_url)

  if [ -z "${PR_URL:-}" ]; then
    echo "❌ Could not parse PR URL from gh pr create output:"
    echo "$PR_OUT"
    exit 1
  fi

  echo "PR: $PR_URL"

  # 체크 통과 대기(있다면)
  wait_for_checks "$PR_URL"

  # merge (auto-merge 토글 없이도 됨)
  MERGE_FLAG="--$MERGE_METHOD"
  DELETE_FLAG=""
  if [ "$DELETE_BRANCH" = "1" ]; then
    DELETE_FLAG="--delete-branch"
  fi

  gh pr merge -R "$OWNER/$REPO" "$PR_URL" $MERGE_FLAG $DELETE_FLAG

  echo "✅ Merged PR for issue #$N. Moving to next issue..."
done

echo "Done."
>>>>>>> Stashed changes
