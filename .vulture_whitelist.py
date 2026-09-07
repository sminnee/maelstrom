"""Vulture whitelist for the dead-code gate.

Each entry names something vulture finds no use for, but which is not dead.
Both passes read this file.

Every entry needs a comment saying why. Prefer a fix to an entry, and check no
other module has a finding of the same name — vulture matches these names
anywhere, not per file. See docs/dev/dead-code.md.
"""

# The wire contract the web UI reads (src/maelstrom/orchestrator/protocol.py).
# Python writes every field below and TypeScript reads it, so no Python call
# site exists for any of them. Vulture cannot see across that boundary. Knip
# guards the TypeScript half.
#
# Generated with:
#   uv run vulture src/maelstrom/orchestrator/protocol.py --make-whitelist
#
# Only the fields are listed. This file deliberately does not exclude
# protocol.py, because that would also stop the gate checking the functions
# in it.
Phase  # unused variable (src/maelstrom/orchestrator/protocol.py:12)
TaskStatus  # unused variable (src/maelstrom/orchestrator/protocol.py:13)
AgentStateName  # unused variable (src/maelstrom/orchestrator/protocol.py:14)
stackTip  # unused variable (src/maelstrom/orchestrator/protocol.py:27)
isClosed  # unused variable (src/maelstrom/orchestrator/protocol.py:39)
dirtyFiles  # unused variable (src/maelstrom/orchestrator/protocol.py:40)
localCommits  # unused variable (src/maelstrom/orchestrator/protocol.py:41)
prNumber  # unused variable (src/maelstrom/orchestrator/protocol.py:42)
prUrl  # unused variable (src/maelstrom/orchestrator/protocol.py:43)
prState  # unused variable (src/maelstrom/orchestrator/protocol.py:45)
prDraft  # unused variable (src/maelstrom/orchestrator/protocol.py:46)
appUrl  # unused variable (src/maelstrom/orchestrator/protocol.py:47)
appRunning  # unused variable (src/maelstrom/orchestrator/protocol.py:48)
sessionCount  # unused variable (src/maelstrom/orchestrator/protocol.py:49)
notebookId  # unused variable (src/maelstrom/orchestrator/protocol.py:70)
permissionMode  # unused variable (src/maelstrom/orchestrator/protocol.py:139)
waitingOn  # unused variable (src/maelstrom/orchestrator/protocol.py:140)
lastMessage  # unused variable (src/maelstrom/orchestrator/protocol.py:141)
lastMessageAt  # unused variable (src/maelstrom/orchestrator/protocol.py:142)
costUsd  # unused variable (src/maelstrom/orchestrator/protocol.py:143)
taskId  # unused variable (src/maelstrom/orchestrator/protocol.py:144)
worktreeId  # unused variable (src/maelstrom/orchestrator/protocol.py:146)
exitCode  # unused variable (src/maelstrom/orchestrator/protocol.py:147)
pendingRequestIds  # unused variable (src/maelstrom/orchestrator/protocol.py:148)
agentId  # unused variable (src/maelstrom/orchestrator/protocol.py:156)
documentId  # unused variable (src/maelstrom/orchestrator/protocol.py:158)
requestId  # unused variable (src/maelstrom/orchestrator/protocol.py:159)
raisedAt  # unused variable (src/maelstrom/orchestrator/protocol.py:161)
clearedAt  # unused variable (src/maelstrom/orchestrator/protocol.py:162)
anchor  # unused variable (src/maelstrom/orchestrator/protocol.py:221)
createdAt  # unused variable (src/maelstrom/orchestrator/protocol.py:224)
addedAt  # unused variable (src/maelstrom/orchestrator/protocol.py:231)

# Fields on dataclasses and TypedDicts that something outside Python reads, or
# that a constructor fills and only a caller reads back.
is_dirty  # unused variable (src/maelstrom/worktree.py WorktreeStatus)
head_ref  # unused variable (src/maelstrom/github_model.py PullRequest)
truncatedBefore  # unused variable (src/maelstrom/orchestrator/transcript_log.py)

# sqlite3 reads this attribute off the connection to shape its rows.
_.row_factory  # unused attribute (src/maelstrom/task_index.py)

# unittest.mock reads these off a Mock. Assigning one is how a test arranges
# behaviour, so the assignment is the use.
_.side_effect  # unused attribute (tests/)

# Pytest fixtures a test requests for their side effect alone. Naming the
# fixture in the signature is how a test asks for it, so the parameter is the
# use, but vulture sees only a parameter nothing reads. Each name below is a
# fixture defined with @pytest.fixture in tests/.
darwin  # unused variable (tests/test_schedule_launchd.py)
no_power_commands  # unused variable (tests/test_schedule_launchd.py)
quiet_finalize  # unused variable (tests/test_sync_flags.py)
process_cleanup  # unused variable (tests/e2e/conftest.py)

# Parameters a lambda must accept to match the signature it replaces.
pp  # unused variable (tests/test_task_cli.py, tests/test_worktree_close.py)
num  # unused variable (tests/test_ports.py)
