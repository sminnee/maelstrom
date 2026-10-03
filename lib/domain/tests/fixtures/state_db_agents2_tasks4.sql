-- A copy of a real state database, as `sqlite3 state.db .dump` wrote it, at
-- agents ladder version 2 and tasks ladder version 4. The last released shape
-- before the Agent record became the link between a task and its sessions.
--
-- `.dump` leaves out `user_version`, the spine's version, so the last line
-- sets it to the value the real database had.
--
-- Two Agent rows are added by hand, marked below. The real database held one
-- agent, and that one ran on no task.
PRAGMA foreign_keys=OFF;
BEGIN TRANSACTION;
CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
INSERT INTO meta VALUES('revision','19');
CREATE TABLE schema_version (name TEXT PRIMARY KEY, version INTEGER NOT NULL);
INSERT INTO schema_version VALUES('desk',2);
INSERT INTO schema_version VALUES('task_export',1);
INSERT INTO schema_version VALUES('agent_sessions',1);
INSERT INTO schema_version VALUES('agents',2);
INSERT INTO schema_version VALUES('tasks',4);
CREATE TABLE removals (table_name TEXT NOT NULL, id TEXT NOT NULL, revision INTEGER NOT NULL, PRIMARY KEY (table_name, id));
INSERT INTO removals VALUES('desk','agent:01a0b10c-c8ff-7c41-9180-2d8f4472e6dc',2);
INSERT INTO removals VALUES('desk','agent:13a30a55',10);
INSERT INTO removals VALUES('task_export','maelstrom/2026-10-01.1',12);
INSERT INTO removals VALUES('task_export','maelstrom/2026-10-01.2',14);
INSERT INTO removals VALUES('task_export','maelstrom/2026-10-01.3',16);
CREATE TABLE refresher_health (name TEXT PRIMARY KEY, reachable INTEGER NOT NULL DEFAULT 1, since TEXT NOT NULL DEFAULT '', last_attempt TEXT NOT NULL DEFAULT '', last_success TEXT NOT NULL DEFAULT '', stand_off_until TEXT NOT NULL DEFAULT '', detail TEXT NOT NULL DEFAULT '');
CREATE TABLE desk (id TEXT PRIMARY KEY, revision INTEGER NOT NULL, body TEXT NOT NULL DEFAULT '');
INSERT INTO desk VALUES('task:maelstrom/2026-10-01.1',17,'{"addedAt": "2026-10-01T05:04:30.750497+00:00", "id": "task:maelstrom/2026-10-01.1"}');
CREATE TABLE tasks (id TEXT PRIMARY KEY, revision INTEGER NOT NULL, project TEXT NOT NULL DEFAULT '', task_id TEXT NOT NULL DEFAULT '', title TEXT NOT NULL DEFAULT '', status TEXT NOT NULL DEFAULT '', command TEXT NOT NULL DEFAULT '', mode TEXT NOT NULL DEFAULT '', branch TEXT NOT NULL DEFAULT '', parent TEXT NOT NULL DEFAULT '', pre_action TEXT NOT NULL DEFAULT '', post_action TEXT NOT NULL DEFAULT '', follows TEXT NOT NULL DEFAULT '[]', created TEXT NOT NULL DEFAULT '', updated TEXT NOT NULL DEFAULT '', schedule TEXT NOT NULL DEFAULT '', last_run TEXT NOT NULL DEFAULT '', priority TEXT NOT NULL DEFAULT '', model TEXT NOT NULL DEFAULT '', base TEXT NOT NULL DEFAULT '', session_id TEXT NOT NULL DEFAULT '', content TEXT NOT NULL DEFAULT '', log TEXT NOT NULL DEFAULT '', execute_model TEXT NOT NULL DEFAULT '');
INSERT INTO tasks VALUES('maelstrom/2026-10-01.1',11,'maelstrom','2026-10-01.1','Colour the desk arrows','todo','','plan','feat/colour-desk-arrows','','','','[]','2026-10-01T05:03:54.957086+00:00','2026-10-01T05:03:54.957086+00:00','','','medium','','','a29d4e5b-2e6c-5f58-8e30-899b83fb3983','','','');
INSERT INTO tasks VALUES('maelstrom/2026-10-01.2',13,'maelstrom','2026-10-01.2','Draw a trapezoid desk','todo','','plan','feat/draw-trapezoid-desk','','','','[]','2026-10-01T05:04:00.586706+00:00','2026-10-01T05:04:00.586706+00:00','','','medium','','','732cf960-9c1b-5851-aa6e-000522f02c7a','','','');
INSERT INTO tasks VALUES('maelstrom/2026-10-01.3',15,'maelstrom','2026-10-01.3','Check the light scheme','todo','','plan','chore/check-light-scheme','','','','[]','2026-10-01T05:04:04.593714+00:00','2026-10-01T05:04:04.593714+00:00','','','medium','','','c4f3585a-38b0-5da0-b204-a6933d1c3b73','','','');
CREATE TABLE task_export (id TEXT PRIMARY KEY, revision INTEGER NOT NULL, path TEXT NOT NULL DEFAULT '', deleted INTEGER NOT NULL DEFAULT 0, queued_at TEXT NOT NULL DEFAULT '');
CREATE TABLE agent_sessions (id TEXT PRIMARY KEY, revision INTEGER NOT NULL, body TEXT NOT NULL DEFAULT '');
CREATE TABLE agents (id TEXT PRIMARY KEY, revision INTEGER NOT NULL, body TEXT NOT NULL DEFAULT '');
INSERT INTO agents VALUES('13a30a55',9,'{"cwd": "/private/tmp/claude/resume-probe", "ended_at": "2026-09-23T02:48:48.478288+00:00", "harness": "claude", "id": "13a30a55", "mode": "normal", "model": "claude-haiku-4-5-20251001", "started_at": "2026-09-23T02:47:32.574493+00:00", "status": "ended", "swept": false, "task_id": "", "task_session_id": "fb1c3ad2-fa96-4605-9740-97c4441d4053"}');
-- Added by hand: an agent the router started for task 2026-10-01.1. Its
-- session id is that task's `tasks.session_id`.
INSERT INTO agents VALUES('7c1e02af',18,'{"cwd": "/Users/sam/Projects/maelstrom/maelstrom-alpha", "ended_at": "", "harness": "claude", "id": "7c1e02af", "mode": "plan", "model": "opus", "started_at": "2026-10-01T05:10:00.000000+00:00", "status": "running", "task_id": "2026-10-01.1", "task_session_id": "a29d4e5b-2e6c-5f58-8e30-899b83fb3983"}');
-- Added by hand: an adopted agent. Adoption wrote no task id, so the session
-- id is the only link to task 2026-10-01.2.
INSERT INTO agents VALUES('b40d91e6',19,'{"cwd": "/Users/sam/Projects/maelstrom/maelstrom-bravo", "ended_at": "2026-10-01T05:30:00.000000+00:00", "harness": "claude", "id": "b40d91e6", "mode": "normal", "model": "opus", "started_at": "2026-10-01T05:20:00.000000+00:00", "status": "ended", "swept": true, "task_id": "", "task_session_id": "732cf960-9c1b-5851-aa6e-000522f02c7a"}');
CREATE TABLE agent_milestones (id TEXT PRIMARY KEY, revision INTEGER NOT NULL, agent_id TEXT NOT NULL DEFAULT '', name TEXT NOT NULL DEFAULT '', at TEXT NOT NULL DEFAULT '', recognised INTEGER NOT NULL DEFAULT 1, own_total INTEGER NOT NULL DEFAULT 0, sub_total INTEGER NOT NULL DEFAULT 0, cost_usd REAL NOT NULL DEFAULT 0, own_delta INTEGER NOT NULL DEFAULT 0, sub_delta INTEGER NOT NULL DEFAULT 0, cost_delta REAL NOT NULL DEFAULT 0);
INSERT INTO agent_milestones VALUES('13a30a55#0001',5,'13a30a55','<final>','2026-09-23T02:47:48.524945+00:00',1,25278,0,0.02222090000000000177,25278,0,0.02222090000000000177);
INSERT INTO agent_milestones VALUES('13a30a55#0002',8,'13a30a55','<final>','2026-09-23T02:48:48.477498+00:00',1,50879,0,0.02549330000000000346,25601,0,0.003272400000000001696);
CREATE INDEX removals_revision ON removals (revision);
CREATE INDEX desk_revision ON desk (revision);
CREATE INDEX tasks_revision ON tasks (revision);
CREATE INDEX tasks_project_status ON tasks (project, status);
CREATE INDEX tasks_session_id ON tasks (session_id);
CREATE INDEX task_export_revision ON task_export (revision);
CREATE INDEX agent_sessions_revision ON agent_sessions (revision);
CREATE INDEX agents_revision ON agents (revision);
CREATE INDEX agent_milestones_revision ON agent_milestones (revision);
CREATE INDEX agent_milestones_agent ON agent_milestones (agent_id);
COMMIT;
PRAGMA user_version = 1;
