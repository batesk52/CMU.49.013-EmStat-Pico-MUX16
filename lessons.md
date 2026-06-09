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
  **Mistake:** During the CMU.17.034 Batch-1 tournament, two of three coder worktrees were auto-torn-down after the agents returned — both their worktrees AND their branches (`worktree-agent-*`) were deleted, so `git branch` no longer listed them. A naive orchestrator would have concluded the work was lost and proceeded with a single-implementation "tournament".
  **Root Cause:** The harness's background-agent cleanup removed the worktree and deleted the branch ref on completion. The commits themselves survived as dangling (unreferenced) objects, recoverable until gc.
  **Rule:** After background worktree coders return, do NOT trust `git branch` / `git worktree list` to still show their branches. Verify each reported COMMIT_HASH with `git cat-file -t <hash>` and `git diff --stat <base>..<hash>`; if the commit is reachable but unbranched, re-anchor it with `git branch <recover-name> <hash>` and `git worktree add` before review. The commit hashes in the agents' return payloads are the source of truth, not the live branch list.

- **Date:** 2026-06-09
  **Mistake:** The main repo's checked-out branch silently changed from `plan/cmu-17-034-preset-sequencer` (the spawn-time branch) to `feature/e-cheMCP` during the background worktree run, and coders saw a wrong base SHA (`d0def15` instead of the intended `4f70319`).
  **Root Cause:** Worktree-isolation machinery interacting with the main repo's HEAD; the coders' Step-0 self-heal (Case 2 reset to EXPECTED_BASE_SHA) absorbed it, so no work was lost — but the orchestrator's assumption about "which branch is checked out" was wrong.
  **Rule:** Never assume the main worktree is still on the branch you left it on after spawning background worktree agents. Re-read `git -C <repo> branch --show-current` before any checkout/merge, and always pass coders an explicit EXPECTED_BASE_SHA so their Step-0 reset corrects a drifted base.
