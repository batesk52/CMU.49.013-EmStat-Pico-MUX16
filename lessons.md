# Lessons Learned

Patterns captured from mistakes, corrections, and failed validations. Agents review this at session start.

## Format

Each lesson follows this pattern:
- **Date:** When it happened
- **Mistake:** What went wrong
- **Root Cause:** Why it happened
- **Rule:** How to prevent it (imperative, actionable)

## Lessons

- **Date:** 2026-06-09
  **Mistake:** (preventive, from the CMU.17.034 tournament incident on the sibling branch) Background-agent harness worktrees and their branches were auto-torn-down when coder agents returned, and the main repo's checked-out branch drifted mid-run.
  **Root Cause:** Harness-managed worktree isolation cleans up on agent exit; the main checkout is shared mutable state when another session runs concurrently.
  **Rule:** For coder tournaments, the orchestrator creates worktrees itself (`git branch -f <name> <BASE_SHA> && git worktree add ../wt-<name> <name>`), passes coders an explicit worktree path + EXPECTED_BASE_SHA with a Step-0 self-heal reset, and works only in its own linked worktree (never the main checkout). Used for all 10 CMU.17.042 coder runs: zero teardown or drift incidents.
