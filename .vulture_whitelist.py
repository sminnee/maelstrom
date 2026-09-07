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
id  # unused variable (src/maelstrom/orchestrator/protocol.py:25)
name  # unused variable (src/maelstrom/orchestrator/protocol.py:26)
stackTip  # unused variable (src/maelstrom/orchestrator/protocol.py:27)
id  # unused variable (src/maelstrom/orchestrator/protocol.py:33)
project  # unused variable (src/maelstrom/orchestrator/protocol.py:34)
nato  # unused variable (src/maelstrom/orchestrator/protocol.py:35)
path  # unused variable (src/maelstrom/orchestrator/protocol.py:36)
branch  # unused variable (src/maelstrom/orchestrator/protocol.py:37)
base  # unused variable (src/maelstrom/orchestrator/protocol.py:38)
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
text  # unused variable (src/maelstrom/orchestrator/protocol.py:53)
done  # unused variable (src/maelstrom/orchestrator/protocol.py:54)
ts  # unused variable (src/maelstrom/orchestrator/protocol.py:58)
text  # unused variable (src/maelstrom/orchestrator/protocol.py:59)
id  # unused variable (src/maelstrom/orchestrator/protocol.py:69)
notebookId  # unused variable (src/maelstrom/orchestrator/protocol.py:70)
project  # unused variable (src/maelstrom/orchestrator/protocol.py:71)
title  # unused variable (src/maelstrom/orchestrator/protocol.py:72)
status  # unused variable (src/maelstrom/orchestrator/protocol.py:73)
command  # unused variable (src/maelstrom/orchestrator/protocol.py:74)
mode  # unused variable (src/maelstrom/orchestrator/protocol.py:75)
branch  # unused variable (src/maelstrom/orchestrator/protocol.py:76)
parent  # unused variable (src/maelstrom/orchestrator/protocol.py:77)
follows  # unused variable (src/maelstrom/orchestrator/protocol.py:78)
priority  # unused variable (src/maelstrom/orchestrator/protocol.py:79)
model  # unused variable (src/maelstrom/orchestrator/protocol.py:80)
base  # unused variable (src/maelstrom/orchestrator/protocol.py:81)
content  # unused variable (src/maelstrom/orchestrator/protocol.py:82)
steps  # unused variable (src/maelstrom/orchestrator/protocol.py:83)
log  # unused variable (src/maelstrom/orchestrator/protocol.py:84)
created  # unused variable (src/maelstrom/orchestrator/protocol.py:85)
updated  # unused variable (src/maelstrom/orchestrator/protocol.py:86)
actionable  # unused variable (src/maelstrom/orchestrator/protocol.py:87)
id  # unused variable (src/maelstrom/orchestrator/protocol.py:97)
notebookId  # unused variable (src/maelstrom/orchestrator/protocol.py:98)
project  # unused variable (src/maelstrom/orchestrator/protocol.py:99)
title  # unused variable (src/maelstrom/orchestrator/protocol.py:100)
status  # unused variable (src/maelstrom/orchestrator/protocol.py:101)
command  # unused variable (src/maelstrom/orchestrator/protocol.py:102)
mode  # unused variable (src/maelstrom/orchestrator/protocol.py:103)
branch  # unused variable (src/maelstrom/orchestrator/protocol.py:104)
parent  # unused variable (src/maelstrom/orchestrator/protocol.py:105)
follows  # unused variable (src/maelstrom/orchestrator/protocol.py:106)
priority  # unused variable (src/maelstrom/orchestrator/protocol.py:107)
model  # unused variable (src/maelstrom/orchestrator/protocol.py:108)
base  # unused variable (src/maelstrom/orchestrator/protocol.py:109)
steps  # unused variable (src/maelstrom/orchestrator/protocol.py:110)
created  # unused variable (src/maelstrom/orchestrator/protocol.py:111)
updated  # unused variable (src/maelstrom/orchestrator/protocol.py:112)
actionable  # unused variable (src/maelstrom/orchestrator/protocol.py:113)
id  # unused variable (src/maelstrom/orchestrator/protocol.py:132)
parent  # unused variable (src/maelstrom/orchestrator/protocol.py:133)
description  # unused variable (src/maelstrom/orchestrator/protocol.py:134)
session  # unused variable (src/maelstrom/orchestrator/protocol.py:136)
cwd  # unused variable (src/maelstrom/orchestrator/protocol.py:137)
model  # unused variable (src/maelstrom/orchestrator/protocol.py:138)
permissionMode  # unused variable (src/maelstrom/orchestrator/protocol.py:139)
waitingOn  # unused variable (src/maelstrom/orchestrator/protocol.py:140)
lastMessage  # unused variable (src/maelstrom/orchestrator/protocol.py:141)
lastMessageAt  # unused variable (src/maelstrom/orchestrator/protocol.py:142)
costUsd  # unused variable (src/maelstrom/orchestrator/protocol.py:143)
taskId  # unused variable (src/maelstrom/orchestrator/protocol.py:144)
project  # unused variable (src/maelstrom/orchestrator/protocol.py:145)
worktreeId  # unused variable (src/maelstrom/orchestrator/protocol.py:146)
exitCode  # unused variable (src/maelstrom/orchestrator/protocol.py:147)
pendingRequestIds  # unused variable (src/maelstrom/orchestrator/protocol.py:148)
pid  # unused variable (src/maelstrom/orchestrator/protocol.py:150)
id  # unused variable (src/maelstrom/orchestrator/protocol.py:154)
agentId  # unused variable (src/maelstrom/orchestrator/protocol.py:156)
taskId  # unused variable (src/maelstrom/orchestrator/protocol.py:157)
documentId  # unused variable (src/maelstrom/orchestrator/protocol.py:158)
requestId  # unused variable (src/maelstrom/orchestrator/protocol.py:159)
summary  # unused variable (src/maelstrom/orchestrator/protocol.py:160)
raisedAt  # unused variable (src/maelstrom/orchestrator/protocol.py:161)
clearedAt  # unused variable (src/maelstrom/orchestrator/protocol.py:162)
id  # unused variable (src/maelstrom/orchestrator/protocol.py:166)
agentId  # unused variable (src/maelstrom/orchestrator/protocol.py:167)
taskId  # unused variable (src/maelstrom/orchestrator/protocol.py:168)
title  # unused variable (src/maelstrom/orchestrator/protocol.py:170)
markdown  # unused variable (src/maelstrom/orchestrator/protocol.py:171)
version  # unused variable (src/maelstrom/orchestrator/protocol.py:172)
status  # unused variable (src/maelstrom/orchestrator/protocol.py:173)
source  # unused variable (src/maelstrom/orchestrator/protocol.py:174)
id  # unused variable (src/maelstrom/orchestrator/protocol.py:180)
agentId  # unused variable (src/maelstrom/orchestrator/protocol.py:181)
taskId  # unused variable (src/maelstrom/orchestrator/protocol.py:182)
title  # unused variable (src/maelstrom/orchestrator/protocol.py:184)
version  # unused variable (src/maelstrom/orchestrator/protocol.py:185)
status  # unused variable (src/maelstrom/orchestrator/protocol.py:186)
source  # unused variable (src/maelstrom/orchestrator/protocol.py:187)
TranscriptItem  # unused variable (src/maelstrom/orchestrator/protocol.py:202)
quote  # unused variable (src/maelstrom/orchestrator/protocol.py:208)
prefix  # unused variable (src/maelstrom/orchestrator/protocol.py:209)
suffix  # unused variable (src/maelstrom/orchestrator/protocol.py:210)
start  # unused variable (src/maelstrom/orchestrator/protocol.py:211)
end  # unused variable (src/maelstrom/orchestrator/protocol.py:212)
id  # unused variable (src/maelstrom/orchestrator/protocol.py:216)
documentId  # unused variable (src/maelstrom/orchestrator/protocol.py:217)
version  # unused variable (src/maelstrom/orchestrator/protocol.py:218)
author  # unused variable (src/maelstrom/orchestrator/protocol.py:220)
anchor  # unused variable (src/maelstrom/orchestrator/protocol.py:221)
body  # unused variable (src/maelstrom/orchestrator/protocol.py:222)
resolved  # unused variable (src/maelstrom/orchestrator/protocol.py:223)
createdAt  # unused variable (src/maelstrom/orchestrator/protocol.py:224)
id  # unused variable (src/maelstrom/orchestrator/protocol.py:230)
addedAt  # unused variable (src/maelstrom/orchestrator/protocol.py:231)
HOST_ID  # unused variable (src/maelstrom/orchestrator/protocol.py:235)
id  # unused variable (src/maelstrom/orchestrator/protocol.py:246)
reachable  # unused variable (src/maelstrom/orchestrator/protocol.py:247)
since  # unused variable (src/maelstrom/orchestrator/protocol.py:249)
socket  # unused variable (src/maelstrom/orchestrator/protocol.py:251)
projects  # unused variable (src/maelstrom/orchestrator/protocol.py:255)
worktrees  # unused variable (src/maelstrom/orchestrator/protocol.py:256)
tasks  # unused variable (src/maelstrom/orchestrator/protocol.py:257)
agents  # unused variable (src/maelstrom/orchestrator/protocol.py:258)
documents  # unused variable (src/maelstrom/orchestrator/protocol.py:259)
comments  # unused variable (src/maelstrom/orchestrator/protocol.py:260)
attention  # unused variable (src/maelstrom/orchestrator/protocol.py:261)
desk  # unused variable (src/maelstrom/orchestrator/protocol.py:262)
host  # unused variable (src/maelstrom/orchestrator/protocol.py:263)

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
_.return_value  # unused attribute (tests/)

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
