#!/usr/bin/env bash
# Triage (CONTRIBUTING.md): syncs the `failure` issues, releases a claim an interrupted run left,
# then claims the oldest open issue no triage has taken and runs one agent on it, headless, in a
# worktree of its own at origin/main. Run hourly from the main checkout by a systemd user timer,
# under flock, so one triage runs at a time and a claim found at start is always stale.
set -euo pipefail

REPO=$(cd "$(dirname "$0")/.." && pwd)
WORKTREE="$REPO/.claude/worktrees/triage"
BUDGET_USD=${TRIAGE_BUDGET_USD:-10}
TRIAGE_LABELS='["agent:working","agent:done","agent:skip","agent:stuck","cannot-reproduce"]'

[ -d "$WORKTREE" ] || git -C "$REPO" worktree add -q --detach "$WORKTREE" origin/main
cd "$WORKTREE"
git fetch -q --force origin main --tags
git reset -q --hard
git clean -qfd
git checkout -q --detach origin/main
ln -sf "$REPO/.env" .env

POSTHOG_READ_KEY=$(sed -n 's/^POSTHOG_READ_KEY=//p' .env | tr -d "\"'") \
  uv run python -m scripts.failures --sync

for n in $(gh issue list --label failure --label agent:working --state open \
  --json number --jq '.[].number'); do
  if [ -n "$(gh pr list --state open --search "#$n in:body" --json number --jq '.[].number')" ]; then
    gh issue edit "$n" --remove-label agent:working --add-label agent:done
  elif gh issue view "$n" --json comments --jq '.comments[].body' | grep -q '^Triage: interrupted'; then
    gh issue edit "$n" --remove-label agent:working --add-label agent:stuck
    gh issue comment "$n" --body "Triage: interrupted twice, left for a person."
  else
    gh issue edit "$n" --remove-label agent:working
    gh issue comment "$n" --body "Triage: interrupted, released for one more try."
  fi
done

issue=$(gh issue list --label failure --state open --json number,labels --jq \
  "[.[] | select([.labels[].name] - $TRIAGE_LABELS == [.labels[].name])] | sort_by(.number) | .[0].number // empty")
[ -n "$issue" ] || exit 0

gh issue edit "$issue" --add-label agent:working
gh issue comment "$issue" --body "Triage: claimed."

claude -p "Triage failure issue #$issue. $(cat scripts/triage.md)" \
  --permission-mode dontAsk \
  --max-budget-usd "$BUDGET_USD" \
  --allowedTools Read Edit Write Glob Grep Skill \
  "Bash(uv run *)" "Bash(git *)" "Bash(gh issue *)" "Bash(gh pr create *)" \
  "Bash(gh pr list *)" "Bash(gh pr view *)" "Bash(gh pr checks *)" "Bash(gh run *)" \
  "Bash(buzon *)" "Bash(freehire *)" "Bash(ls *)" "Bash(cat *)" "Bash(grep *)" \
  --disallowedTools "Bash(*atsfill send*)" "Bash(*gh pr merge*)"
