"""Vulture whitelist for the dead-code gate.

Each entry names something vulture finds no use for, but which is not dead.
Both passes read this file.

Every entry needs a comment saying why. Prefer a fix to an entry, and check no
other module has a finding of the same name — vulture matches these names
anywhere, not per file. See docs/dev/dead-code.md.
"""

# The wire contract the web UI reads (lib/domain/src/mael_domain/protocol.py).
# Python writes every field below and TypeScript reads it, so no Python call
# site exists for any of them. Vulture cannot see across that boundary. Knip
# guards the TypeScript half.
#
# Generated with:
#   uv run vulture lib/domain/src/mael_domain/protocol.py --make-whitelist
#
# Only the fields are listed. This file deliberately does not exclude
# protocol.py, because that would also stop the gate checking the functions
# in it.
Phase  # unused variable (lib/domain/src/mael_domain/protocol.py:12)
TaskStatus  # unused variable (lib/domain/src/mael_domain/protocol.py:13)
AgentStateName  # unused variable (lib/domain/src/mael_domain/protocol.py:14)
stackTip  # unused variable (lib/domain/src/mael_domain/protocol.py:27)
hasLinear  # unused variable (lib/domain/src/mael_domain/protocol.py:29)
isClosed  # unused variable (lib/domain/src/mael_domain/protocol.py:39)
dirtyFiles  # unused variable (lib/domain/src/mael_domain/protocol.py:40)
localCommits  # unused variable (lib/domain/src/mael_domain/protocol.py:41)
prNumber  # unused variable (lib/domain/src/mael_domain/protocol.py:42)
prCommits  # unused variable (lib/domain/src/mael_domain/protocol.py:46)
pushedCommits  # unused variable (lib/domain/src/mael_domain/protocol.py:50)
prUrl  # unused variable (lib/domain/src/mael_domain/protocol.py:43)
prState  # unused variable (lib/domain/src/mael_domain/protocol.py:45)
prDraft  # unused variable (lib/domain/src/mael_domain/protocol.py:46)
appUrl  # unused variable (lib/domain/src/mael_domain/protocol.py:47)
appRunning  # unused variable (lib/domain/src/mael_domain/protocol.py:48)
sessionCount  # unused variable (lib/domain/src/mael_domain/protocol.py:49)
shellUrl  # unused variable (lib/domain/src/mael_domain/protocol.py:60)
notebookId  # unused variable (lib/domain/src/mael_domain/protocol.py:70)
executeModel  # unused variable (lib/domain/src/mael_domain/protocol.py:84)
permissionMode  # unused variable (lib/domain/src/mael_domain/protocol.py:141)
waitingOn  # unused variable (lib/domain/src/mael_domain/protocol.py:142)
lastMessage  # unused variable (lib/domain/src/mael_domain/protocol.py:143)
lastMessageAt  # unused variable (lib/domain/src/mael_domain/protocol.py:144)
lastNote  # unused variable (lib/domain/src/mael_domain/protocol.py:147)
lastNoteAt  # unused variable (lib/domain/src/mael_domain/protocol.py:149)
costUsd  # unused variable (lib/domain/src/mael_domain/protocol.py:145)
totalTokens  # unused variable (lib/domain/src/mael_domain/protocol.py:150)
subagentTokens  # unused variable (lib/domain/src/mael_domain/protocol.py:164)
contextTokens  # unused variable (lib/domain/src/mael_domain/protocol.py:154)
taskId  # unused variable (lib/domain/src/mael_domain/protocol.py:155)
worktreeId  # unused variable (lib/domain/src/mael_domain/protocol.py:157)
exitCode  # unused variable (lib/domain/src/mael_domain/protocol.py:158)
pendingRequestIds  # unused variable (lib/domain/src/mael_domain/protocol.py:159)
agentId  # unused variable (lib/domain/src/mael_domain/protocol.py:161)
documentId  # unused variable (lib/domain/src/mael_domain/protocol.py:163)
requestId  # unused variable (lib/domain/src/mael_domain/protocol.py:164)
raisedAt  # unused variable (lib/domain/src/mael_domain/protocol.py:166)
clearedAt  # unused variable (lib/domain/src/mael_domain/protocol.py:167)
anchor  # unused variable (lib/domain/src/mael_domain/protocol.py:226)
createdAt  # unused variable (lib/domain/src/mael_domain/protocol.py:229)
addedAt  # unused variable (lib/domain/src/mael_domain/protocol.py:236)
resetsAt  # unused variable (lib/domain/src/mael_domain/protocol.py:252)
fiveHour  # unused variable (lib/domain/src/mael_domain/protocol.py:264)
sevenDay  # unused variable (lib/domain/src/mael_domain/protocol.py:265)

# Fields on dataclasses and TypedDicts that something outside Python reads, or
# that a constructor fills and only a caller reads back.
is_dirty  # unused variable (lib/domain/src/mael_domain/worktree.py WorktreeStatus)
head_ref  # unused variable (lib/domain/src/mael_domain/github_model.py PullRequest)
truncatedBefore  # unused variable (orchestrator-api/src/mael_orchestrator/transcript_log.py)

# sqlite3 reads this attribute off the connection to shape its rows.
_.row_factory  # unused attribute (lib/domain/src/mael_domain/state_db/db.py)

# unittest.mock reads these off a Mock. Assigning one is how a test arranges
# behaviour, so the assignment is the use.
_.side_effect  # unused attribute (tests/)

# Pytest fixtures a test requests for their side effect alone. Naming the
# fixture in the signature is how a test asks for it, so the parameter is the
# use, but vulture sees only a parameter nothing reads. Each name below is a
# fixture defined with @pytest.fixture in cli/tests/.
darwin  # unused variable (cli/tests/test_schedule_launchd.py)
no_power_commands  # unused variable (cli/tests/test_schedule_launchd.py)
quiet_finalize  # unused variable (cli/tests/test_sync_flags.py)
process_cleanup  # unused variable (cli/tests/e2e/conftest.py)

# Parameters a lambda must accept to match the signature it replaces.
pp  # unused variable (cli/tests/test_task_cli.py, lib/domain/tests/test_worktree_close.py)
num  # unused variable (lib/domain/tests/test_ports.py)


# The cost report's shape (lib/domain/src/mael_domain/agent_cost.py). `build_cost_report`
# writes every field and `agent_cli._draw_cost` or a `--json` reader takes it
# from there, through a subscript vulture does not count as a use.
delta_tokens  # unused variable (lib/domain/src/mael_domain/agent_cost.py:32)
subagent_delta  # unused variable (lib/domain/src/mael_domain/agent_cost.py:34)
cost_delta  # unused variable (lib/domain/src/mael_domain/agent_cost.py:35)
cost_is_parent_only  # unused variable (lib/domain/src/mael_domain/agent_cost.py:49)
cost_usd  # unused variable (lib/domain/src/mael_domain/agent_cost.py:48)
own_tokens  # unused variable (lib/domain/src/mael_domain/agent_cost.py:44)

# The daemon's detail shape (lib/agent/src/mael_agent/agent_wire.py PendingFields).
# `pending_fields` writes every key, and a client reads it off the wire JSON
# through a subscript vulture does not count as a use.
waiting_kind  # unused variable (lib/agent/src/mael_agent/agent_wire.py PendingFields)
waiting_tool  # unused variable (lib/agent/src/mael_agent/agent_wire.py PendingFields)
waiting_input  # unused variable (lib/agent/src/mael_agent/agent_wire.py PendingFields)
waiting_subagent  # unused variable (lib/agent/src/mael_agent/agent_wire.py PendingFields)
