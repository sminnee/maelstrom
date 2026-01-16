# Maelstrom-based workflow

**Plan mode is required** for `/plan-task` and `/continue-task` commands. If not in plan mode,
instruct the user to enter plan mode first.

### For new large tasks

1. `/plan-task NORT-XXX` - Break down into sub-tasks (plan mode required)
2. `/continue-task` - Pick up first sub-task

### Standard workflow

1. **Pick up task**: `/continue-task NORT-XXX` (plan mode required)
   - Automatically marks task "In Progress" in Linear
2. **Execute plan**: Implementation, testing, `bin/ci`, `bin/e2e-test`
3. **Commit**: `git add . && git commit -m "..."`
4. **Create PR**: `mael gh create-pr`
5. **Submit PR**: `mael linear submit-pr NORT-XXX`
   - Auto-detects PR URL from current branch
   - Attaches PR URL to Linear task
   - Sets status to "In Review"

If an external plan hasn't been referenced, redirect the user to start in plan mode.

(maelstrom instructions end)
