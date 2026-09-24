"""Tests for the task notebook core model, against an InMemoryTaskTable."""

import pytest

from mael_domain import task as model
from mael_domain.task import Task
from mael_domain.task_table import InMemoryTaskTable

# --- a recording store to assert mutation counts/messages ---


NOW = "2026-06-08T12:00:00+00:00"
NOW2 = "2026-06-09T12:00:00+00:00"
TODAY = "2026-06-08"


# --- frontmatter round-trip ---


class TestRoundTrip:
    def test_basic_round_trip(self):
        t = Task(
            id="2026-06-08.1",
            title="Hello world",
            project="maelstrom",
            command="claude",
            mode="normal",
            parent="",
            follows=["2026-06-08.2", "2026-06-08.3"],
            created=NOW,
            updated=NOW,
            content="Some content.",
            steps="1. do a thing",
            log="- did the thing",
            status="todo",
        )
        text = t.to_markdown()
        back = Task.from_markdown(text, status="todo")
        assert back == t

    def test_all_frontmatter_keys_emitted(self):
        t = Task(id="x", title="t", project="p")
        text = t.to_markdown()
        for key in model.FRONTMATTER_KEYS:
            assert f"\n{key}:" in "\n" + text

    def test_every_field_spec_maps_to_a_task_attr(self):
        # TASK_FIELDS is the single declaration the key tables derive from, so a
        # spec whose attr doesn't exist on Task would silently serialize nothing.
        t = Task(id="x", title="t", project="p")
        for f in model.TASK_FIELDS:
            assert hasattr(t, f.attr), f.key

    def test_branch_round_trips(self):
        t = Task(id="x", title="t", project="p", branch="fix/login")
        back = Task.from_markdown(t.to_markdown())
        assert back.branch == "fix/login"

    @pytest.mark.parametrize("value", ["opus", "claude-opus-5"])
    def test_model_round_trips(self, value):
        # Free-form passthrough: an alias and a full id must both survive
        # unchanged (no validation, nothing to keep in sync as models ship).
        t = Task(id="x", title="t", project="p", model=value)
        back = Task.from_markdown(t.to_markdown())
        assert back.model == value

    def test_missing_model_defaults_to_empty(self):
        # An old/hand-edited file with no ``model`` key must still load; empty
        # means "inherit the user's Claude Code default" (no --model emitted).
        text = (
            "---\n"
            'id: x\ntitle: t\nproject: p\ncommand: ""\nmode: normal\n'
            "created: c\nupdated: u\n"
            "---\n\n## Content\n\n\n## Steps\n\n\n## Log\n\n"
        )
        assert Task.from_markdown(text).model == ""

    def test_base_round_trips(self):
        # `base` is a declarative input: it seeds the branch's stored base when
        # the worktree is set up. Near-identical name to `parent`, near-opposite
        # meaning -- `parent` shares one branch and one PR, `base` stacks a
        # different branch as a separate PR.
        t = Task(id="x", title="t", project="p", base="feat/parent")
        back = Task.from_markdown(t.to_markdown())
        assert back.base == "feat/parent"

    def test_missing_base_defaults_to_empty(self):
        # An old file with no `base` key must still load; empty means "use the
        # project's stack tip", which is what every task did before stacking.
        text = (
            "---\n"
            'id: x\ntitle: t\nproject: p\ncommand: ""\nmode: normal\n'
            "created: c\nupdated: u\n"
            "---\n\n## Content\n\n\n## Steps\n\n\n## Log\n\n"
        )
        assert Task.from_markdown(text).base == ""

    def test_execute_model_is_last_in_the_frontmatter_order(self):
        # Field order is load-bearing for stable diffs, so a new field appends.
        assert model.FRONTMATTER_KEYS[-1] == "execute-model"

    def test_execute_model_round_trips(self):
        # The model the session switches to when its plan is approved. Free-form
        # like ``model``, and kebab-case in the frontmatter.
        t = Task(id="x", title="t", project="p", execute_model="sonnet")
        back = Task.from_markdown(t.to_markdown())
        assert back.execute_model == "sonnet"

    def test_missing_execute_model_defaults_to_empty(self):
        # Empty means "no switch" -- the pre-feature behaviour. There is no
        # inheritance from ``model`` and no fallback to ``DEFAULT_MODEL``.
        text = (
            "---\n"
            'id: x\ntitle: t\nproject: p\ncommand: ""\nmode: normal\n'
            "created: c\nupdated: u\n"
            "---\n\n## Content\n\n\n## Steps\n\n\n## Log\n\n"
        )
        assert Task.from_markdown(text).execute_model == ""

    @pytest.mark.parametrize("priority", ["critical", "high", "low"])
    def test_priority_round_trips(self, priority):
        t = Task(id="x", title="t", project="p", priority=priority)
        back = Task.from_markdown(t.to_markdown())
        assert back.priority == priority

    def test_missing_priority_defaults_to_medium(self):
        # A frontmatter block with no ``priority`` key is the back-compat case:
        # old/hand-edited files must still load and report medium.
        text = (
            "---\n"
            'id: x\ntitle: t\nproject: p\ncommand: ""\nmode: normal\n'
            "created: c\nupdated: u\n"
            "---\n\n## Content\n\n\n## Steps\n\n\n## Log\n\n"
        )
        back = Task.from_markdown(text)
        assert back.priority == model.DEFAULT_PRIORITY == "medium"

    def test_blank_priority_defaults_to_medium(self):
        text = (
            "---\n"
            'id: x\ntitle: t\nproject: p\npriority: ""\n'
            "created: c\nupdated: u\n"
            "---\n\n## Content\n\n\n## Steps\n\n\n## Log\n\n"
        )
        assert Task.from_markdown(text).priority == "medium"

    def test_body_line_that_looks_like_heading_preserved(self):
        # A "## Something" line inside Content that isn't a known section.
        t = Task(
            id="x",
            title="t",
            project="p",
            content="intro\n\n## Not a section\n\nmore",
        )
        back = Task.from_markdown(t.to_markdown())
        assert "## Not a section" in back.content
        assert back.content == "intro\n\n## Not a section\n\nmore"

    def test_empty_sections_round_trip(self):
        t = Task(id="x", title="t", project="p")
        back = Task.from_markdown(t.to_markdown())
        assert back.content == ""
        assert back.steps == ""
        assert back.log == ""

    def test_follows_scalar_coerced_to_list(self):
        text = (
            "---\n"
            'id: x\ntitle: t\nproject: p\ncommand: ""\nmode: normal\n'
            'parent: ""\nfollows: only-one\ncreated: c\nupdated: u\n'
            "---\n\n## Content\n\n\n## Steps\n\n\n## Log\n\n"
        )
        back = Task.from_markdown(text)
        assert back.follows == ["only-one"]

    def test_follows_empty_is_list(self):
        back = Task.from_markdown(Task(id="x", title="t", project="p").to_markdown())
        assert back.follows == []

    def test_title_with_colon_round_trips(self):
        t = Task(id="x", title="feat: do thing", project="p")
        back = Task.from_markdown(t.to_markdown())
        assert back.title == "feat: do thing"

    def test_status_not_serialized(self):
        t = Task(id="x", title="t", project="p", status="in-progress")
        assert "status:" not in t.to_markdown()

    def test_lifecycle_actions_round_trip_as_kebab_keys(self):
        t = Task(
            id="x",
            title="t",
            project="p",
            pre_action="linear.in-progress",
            post_action="linear.done",
        )
        text = t.to_markdown()
        # Frontmatter keys are kebab-case; attrs are snake_case.
        assert "pre-action: linear.in-progress" in text
        assert "post-action: linear.done" in text
        assert "pre_action:" not in text
        back = Task.from_markdown(text)
        assert back.pre_action == "linear.in-progress"
        assert back.post_action == "linear.done"

    def test_missing_actions_default_to_empty(self):
        # Back-compat: a task file without the action keys parses to "".
        t = Task(id="x", title="t", project="p")
        back = Task.from_markdown(t.to_markdown())
        assert back.pre_action == ""
        assert back.post_action == ""


# --- draft files ---


class TestDraftMarkdown:
    def test_recipe_fields_round_trip(self):
        text = model.draft_markdown(
            title="Execute: demo",
            command="plan-next-step",
            mode="auto",
            model="opus",
            priority="high",
            pre_action="linear.in-progress",
            post_action="linear.done",
            content="The plan body.",
        )
        back = Task.from_markdown(text)
        assert back.title == "Execute: demo"
        assert back.command == "plan-next-step"
        assert back.mode == "auto"
        assert back.model == "opus"
        assert back.priority == "high"
        assert back.pre_action == "linear.in-progress"
        assert back.post_action == "linear.done"
        assert back.content == "The plan body."

    def test_identity_fields_are_empty(self):
        # A draft is not in the notebook: no id/project/timestamps, and no
        # follows — chain wiring happens at promote time.
        text = model.draft_markdown(title="T")
        back = Task.from_markdown(text)
        assert back.id == ""
        assert back.project == ""
        assert back.created == ""
        assert back.updated == ""
        assert back.follows == []

    def test_unset_mode_and_priority_take_defaults(self):
        back = Task.from_markdown(model.draft_markdown(title="T"))
        assert back.mode == model.DEFAULT_MODE
        assert back.priority == model.DEFAULT_PRIORITY

    def test_invalid_priority_rejected(self):
        with pytest.raises(ValueError):
            model.draft_markdown(title="T", priority="urgent")


class TestParseDraft:
    def test_parses_a_drafted_file(self):
        text = model.draft_markdown(title="T", mode="auto")
        t = model.parse_draft(text)
        assert t.title == "T"
        assert t.mode == "auto"

    def test_minimal_hand_written_draft_loads(self):
        # from_markdown tolerates missing keys, so a hand-written draft with
        # only a title still parses; unset fields take their defaults.
        t = model.parse_draft("---\ntitle: Quick fix\n---\n\n## Content\n\nBody.\n")
        assert t.title == "Quick fix"
        assert t.mode == model.DEFAULT_MODE
        assert t.content == "Body."

    def test_missing_title_raises(self):
        with pytest.raises(ValueError, match="title"):
            model.parse_draft("---\nmode: auto\n---\n\n## Content\n\nBody.\n")

    def test_bad_frontmatter_raises(self):
        with pytest.raises(ValueError):
            model.parse_draft('---\ntitle: "unclosed\n---\n\nBody.\n')


class TestPromoteDraft:
    """The promote step the CLI and the orchestrator share."""

    def _draft(self, tmp_path, name="d.md", **fields):
        path = tmp_path / name
        path.write_text(model.draft_markdown(**fields))
        return path

    async def test_creates_the_task_the_draft_describes(self, store, tmp_path):
        path = self._draft(
            tmp_path,
            title="Execute: demo",
            command="plan-next-step",
            mode="auto",
            model="opus",
            pre_action="linear.in-progress",
            content="The plan body.",
        )
        task = await model.promote_draft(store, project="p", path=path)
        loaded = await model.load(store, "p", task.id)
        assert loaded.title == "Execute: demo"
        assert loaded.command == "plan-next-step"
        assert loaded.mode == "auto"
        assert loaded.model == "opus"
        assert loaded.pre_action == "linear.in-progress"
        assert loaded.content == "The plan body."
        assert loaded.status == model.STATUS_TODO

    async def test_consumes_the_file_once_the_task_exists(self, store, tmp_path):
        path = self._draft(tmp_path, title="T")
        await model.promote_draft(store, project="p", path=path)
        assert not path.exists()

    async def test_an_override_wins_over_the_file(self, store, tmp_path):
        path = self._draft(tmp_path, title="T", mode="auto")
        task = await model.promote_draft(
            store, project="p", path=path, overrides={"mode": "normal"}
        )
        assert (await model.load(store, "p", task.id)).mode == "normal"

    async def test_an_override_of_none_leaves_the_file_field_alone(
        self, store, tmp_path
    ):
        # The CLI passes None for a flag the user did not give.
        path = self._draft(tmp_path, title="T", mode="auto")
        task = await model.promote_draft(
            store, project="p", path=path, overrides={"mode": None}
        )
        assert (await model.load(store, "p", task.id)).mode == "auto"

    async def test_wires_the_follows_it_is_given(self, store, tmp_path):
        first = await model.create(store, project="p", title="first")
        path = self._draft(tmp_path, title="T")
        task = await model.promote_draft(
            store, project="p", path=path, follows=[first.id]
        )
        assert (await model.load(store, "p", task.id)).follows == [first.id]

    async def test_a_missing_file_raises_and_creates_nothing(self, store, tmp_path):
        with pytest.raises(FileNotFoundError):
            await model.promote_draft(store, project="p", path=tmp_path / "absent.md")
        assert await model.list_tasks(store, project="p") == []

    async def test_a_bad_draft_raises_and_leaves_the_file(self, store, tmp_path):
        path = tmp_path / "d.md"
        path.write_text('---\ntitle: "unclosed\n---\n\nBody.\n')
        with pytest.raises(ValueError):
            await model.promote_draft(store, project="p", path=path)
        assert path.exists()
        assert await model.list_tasks(store, project="p") == []


# --- id allocation ---


class TestIdAllocation:
    async def test_orphan_first_id(self, store):
        assert await model.allocate_orphan_id(store, "p", today=TODAY) == "2026-06-08.1"

    async def test_orphan_increments(self, store):
        await model.create(store, project="p", title="a", now=NOW, today=TODAY)
        await model.create(store, project="p", title="b", now=NOW, today=TODAY)
        assert await model.allocate_orphan_id(store, "p", today=TODAY) == "2026-06-08.3"

    async def test_orphan_counts_across_statuses(self, store):
        t = await model.create(store, project="p", title="a", now=NOW, today=TODAY)
        await model.move(store, "p", t.id, "done", now=NOW)
        # The done task still counts toward the next counter.
        assert await model.allocate_orphan_id(store, "p", today=TODAY) == "2026-06-08.2"

    async def test_child_id(self, store):
        parent = await model.create(store, project="p", title="a", now=NOW, today=TODAY)
        child = await model.create(
            store, project="p", title="b", parent=parent.id, now=NOW
        )
        assert child.id == f"{parent.id}.1"

    async def test_nested_child_counters_independent(self, store):
        p = await model.create(store, project="p", title="a", now=NOW, today=TODAY)
        c1 = await model.create(store, project="p", title="b", parent=p.id, now=NOW)
        c2 = await model.create(store, project="p", title="c", parent=p.id, now=NOW)
        assert c2.id == f"{p.id}.2"
        # A grandchild under c1 starts its own counter at 1.
        gc = await model.create(store, project="p", title="d", parent=c1.id, now=NOW)
        assert gc.id == f"{c1.id}.1"
        # Adding the grandchild did not bump the direct-child counter.
        c3 = await model.create(store, project="p", title="e", parent=p.id, now=NOW)
        assert c3.id == f"{p.id}.3"

    async def test_linear_virtual_parent_first_child(self, store):
        child = await model.create(
            store, project="p", title="b", parent="linear.NORT-123", now=NOW
        )
        assert child.id == "linear.NORT-123.1"

    def test_today_uses_local_date(self, monkeypatch):
        # The id date prefix follows the machine's *local* calendar day, not
        # UTC — so near midnight the id doesn't jump to the wrong day. Freeze a
        # UTC instant that is a *different* calendar day locally (23:30 UTC ->
        # next day in any positive-offset zone) and assert the local date wins.
        from datetime import datetime, timezone

        real_dt = datetime
        # 2026-06-08 23:30 UTC — in +12 (NZ) this is 2026-06-09 local.
        frozen_utc = real_dt(2026, 6, 8, 23, 30, tzinfo=timezone.utc)

        class FrozenDateTime(datetime):
            @classmethod
            def now(cls, tz=None):
                return frozen_utc

        monkeypatch.setattr(model, "datetime", FrozenDateTime)
        assert model._today() == frozen_utc.astimezone().date().isoformat()


# --- follow_end_leaves ---


class TestFollowEndLeaves:
    async def test_no_followers_returns_self(self, store):
        a = await model.create(store, project="p", title="a", now=NOW, today=TODAY)
        assert await model.follow_end_leaves(store, "p", a.id) == [a.id]

    async def test_linear_chain(self, store):
        a = await model.create(store, project="p", title="a", now=NOW, today=TODAY)
        b = await model.create(
            store, project="p", title="b", follows=[a.id], now=NOW, today=TODAY
        )
        c = await model.create(
            store, project="p", title="c", follows=[b.id], now=NOW, today=TODAY
        )
        assert await model.follow_end_leaves(store, "p", a.id) == [c.id]

    async def test_branched_chain(self, store):
        a = await model.create(store, project="p", title="a", now=NOW, today=TODAY)
        b = await model.create(
            store, project="p", title="b", follows=[a.id], now=NOW, today=TODAY
        )
        c = await model.create(
            store, project="p", title="c", follows=[a.id], now=NOW, today=TODAY
        )
        assert await model.follow_end_leaves(store, "p", a.id) == sorted([b.id, c.id])

    async def test_cycle_safe(self, store):
        # Construct a cycle manually: a follows b, b follows a.
        a = Task(
            id="a", title="a", project="p", follows=["b"], created=NOW, updated=NOW
        )
        b = Task(
            id="b", title="b", project="p", follows=["a"], created=NOW, updated=NOW
        )
        await store.save(a)
        await store.save(b)
        # Should terminate; both nodes are part of the cycle so no leaves emerge.
        result = await model.follow_end_leaves(store, "p", "a")
        assert result == []  # cycle, no terminal leaf


# --- child_chain_leaves ---


class TestChildChainLeaves:
    async def test_no_children_is_empty(self, store):
        assert await model.child_chain_leaves(store, "p", "linear.X") == []

    async def test_single_child_is_leaf(self, store):
        a = await model.create(
            store, project="p", title="a", parent="linear.X", now=NOW
        )
        assert await model.child_chain_leaves(store, "p", "linear.X") == [a.id]

    async def test_chained_children_only_tail_is_leaf(self, store):
        a = await model.create(
            store, project="p", title="a", parent="linear.X", now=NOW
        )
        b = await model.create(
            store, project="p", title="b", parent="linear.X", follows=[a.id], now=NOW
        )
        # b follows a, so only b is the end of the sibling chain.
        assert await model.child_chain_leaves(store, "p", "linear.X") == [b.id]

    async def test_branched_children_multiple_leaves(self, store):
        a = await model.create(
            store, project="p", title="a", parent="linear.X", now=NOW
        )
        b = await model.create(
            store, project="p", title="b", parent="linear.X", follows=[a.id], now=NOW
        )
        c = await model.create(
            store, project="p", title="c", parent="linear.X", follows=[a.id], now=NOW
        )
        # b and c both follow a; neither is followed -> both are leaves.
        assert await model.child_chain_leaves(store, "p", "linear.X") == sorted(
            [b.id, c.id]
        )

    async def test_ignores_other_parents(self, store):
        mine = await model.create(
            store, project="p", title="m", parent="linear.X", now=NOW
        )
        await model.create(
            store, project="p", title="other", parent="linear.Y", now=NOW
        )
        assert await model.child_chain_leaves(store, "p", "linear.X") == [mine.id]


# --- next_follower / running_follower ---


class TestNextFollower:
    async def test_linear_chain_returns_follower(self, store):
        a = await model.create(store, project="p", title="a", now=NOW, today=TODAY)
        b = await model.create(
            store, project="p", title="b", follows=[a.id], now=NOW, today=TODAY
        )
        await model.move(store, "p", a.id, model.STATUS_DONE, now=NOW2)
        nxt = await model.next_follower(store, "p", a.id)
        assert nxt is not None and nxt.id == b.id

    async def test_follower_not_actionable_returns_none(self, store):
        # b follows a and a second dep that is still todo -> not actionable.
        a = await model.create(store, project="p", title="a", now=NOW, today=TODAY)
        dep2 = await model.create(
            store, project="p", title="dep2", now=NOW, today=TODAY
        )
        await model.create(
            store, project="p", title="b", follows=[a.id, dep2.id], now=NOW, today=TODAY
        )
        await model.move(store, "p", a.id, model.STATUS_DONE, now=NOW2)
        assert await model.next_follower(store, "p", a.id) is None

    async def test_nothing_follows_returns_none(self, store):
        a = await model.create(store, project="p", title="a", now=NOW, today=TODAY)
        await model.create(store, project="p", title="b", now=NOW, today=TODAY)
        await model.move(store, "p", a.id, model.STATUS_DONE, now=NOW2)
        assert await model.next_follower(store, "p", a.id) is None

    async def test_branching_returns_id_sorted_first(self, store):
        a = await model.create(store, project="p", title="a", now=NOW, today=TODAY)
        b = await model.create(
            store, project="p", title="b", follows=[a.id], now=NOW, today=TODAY
        )
        c = await model.create(
            store, project="p", title="c", follows=[a.id], now=NOW, today=TODAY
        )
        await model.move(store, "p", a.id, model.STATUS_DONE, now=NOW2)
        nxt = await model.next_follower(store, "p", a.id)
        assert nxt is not None and nxt.id == sorted([b.id, c.id])[0]

    async def test_non_todo_follower_excluded(self, store):
        # A follower already in-progress is not a todo, so next_follower skips it.
        a = await model.create(store, project="p", title="a", now=NOW, today=TODAY)
        b = await model.create(
            store, project="p", title="b", follows=[a.id], now=NOW, today=TODAY
        )
        await model.move(store, "p", a.id, model.STATUS_DONE, now=NOW2)
        await model.move(store, "p", b.id, model.STATUS_IN_PROGRESS, now=NOW2)
        assert await model.next_follower(store, "p", a.id) is None


class TestRunningFollower:
    async def test_returns_in_progress_follower(self, store):
        a = await model.create(store, project="p", title="a", now=NOW, today=TODAY)
        b = await model.create(
            store, project="p", title="b", follows=[a.id], now=NOW, today=TODAY
        )
        await model.move(store, "p", a.id, model.STATUS_DONE, now=NOW2)
        await model.move(store, "p", b.id, model.STATUS_IN_PROGRESS, now=NOW2)
        running = await model.running_follower(store, "p", a.id)
        assert running is not None and running.id == b.id

    async def test_todo_follower_not_returned(self, store):
        a = await model.create(store, project="p", title="a", now=NOW, today=TODAY)
        await model.create(
            store, project="p", title="b", follows=[a.id], now=NOW, today=TODAY
        )
        await model.move(store, "p", a.id, model.STATUS_DONE, now=NOW2)
        assert await model.running_follower(store, "p", a.id) is None

    async def test_unrelated_in_progress_not_returned(self, store):
        a = await model.create(store, project="p", title="a", now=NOW, today=TODAY)
        other = await model.create(
            store, project="p", title="other", now=NOW, today=TODAY
        )
        await model.move(store, "p", a.id, model.STATUS_DONE, now=NOW2)
        await model.move(store, "p", other.id, model.STATUS_IN_PROGRESS, now=NOW2)
        assert await model.running_follower(store, "p", a.id) is None

    async def test_branching_returns_id_sorted_first(self, store):
        a = await model.create(store, project="p", title="a", now=NOW, today=TODAY)
        b = await model.create(
            store, project="p", title="b", follows=[a.id], now=NOW, today=TODAY
        )
        c = await model.create(
            store, project="p", title="c", follows=[a.id], now=NOW, today=TODAY
        )
        await model.move(store, "p", a.id, model.STATUS_DONE, now=NOW2)
        await model.move(store, "p", b.id, model.STATUS_IN_PROGRESS, now=NOW2)
        await model.move(store, "p", c.id, model.STATUS_IN_PROGRESS, now=NOW2)
        running = await model.running_follower(store, "p", a.id)
        assert running is not None and running.id == sorted([b.id, c.id])[0]


# --- is_actionable / terminal ---


class TestActionable:
    async def test_no_deps_is_actionable(self, store):
        a = await model.create(store, project="p", title="a", now=NOW, today=TODAY)
        assert await model.is_actionable(await model.load(store, "p", a.id), store)

    async def test_blocked_by_undone_dep(self, store):
        a = await model.create(store, project="p", title="a", now=NOW, today=TODAY)
        b = await model.create(
            store, project="p", title="b", follows=[a.id], now=NOW, today=TODAY
        )
        assert not await model.is_actionable(await model.load(store, "p", b.id), store)

    async def test_unblocked_when_dep_done(self, store):
        a = await model.create(store, project="p", title="a", now=NOW, today=TODAY)
        b = await model.create(
            store, project="p", title="b", follows=[a.id], now=NOW, today=TODAY
        )
        await model.move(store, "p", a.id, "done", now=NOW)
        assert await model.is_actionable(await model.load(store, "p", b.id), store)

    async def test_terminal_not_actionable(self, store):
        a = await model.create(store, project="p", title="a", now=NOW, today=TODAY)
        await model.move(store, "p", a.id, "done", now=NOW)
        assert not await model.is_actionable(await model.load(store, "p", a.id), store)
        await model.move(store, "p", a.id, "cancelled", now=NOW)
        assert not await model.is_actionable(await model.load(store, "p", a.id), store)

    async def test_blocked_status_not_actionable(self, store):
        """A task parked in ``blocked/`` never launches, deps satisfied or not."""
        a = await model.create(store, project="p", title="a", now=NOW, today=TODAY)
        await model.move(store, "p", a.id, model.STATUS_BLOCKED, now=NOW)
        assert not await model.is_actionable(await model.load(store, "p", a.id), store)


# --- status moves ---


class TestMove:
    async def test_move_changes_the_status_column(self, store):
        """Status is a column, so a move is an update rather than a relocation."""
        a = await model.create(store, project="p", title="a", now=NOW, today=TODAY)
        assert (await model.load(store, "p", a.id)).status == "todo"
        await model.move(
            store, "p", a.id, "in-progress", now="2026-06-09T00:00:00+00:00"
        )
        assert (await model.load(store, "p", a.id)).status == "in-progress"
        assert [t.id for t in await model.list_tasks(store, project="p")] == [a.id]

    async def test_move_bumps_updated(self, store):
        a = await model.create(store, project="p", title="a", now=NOW, today=TODAY)
        moved = await model.move(
            store, "p", a.id, "in-progress", now="2026-06-09T00:00:00+00:00"
        )
        assert moved.updated == "2026-06-09T00:00:00+00:00"
        assert moved.created == NOW

    async def test_move_invalid_status_rejected(self, store):
        a = await model.create(store, project="p", title="a", now=NOW, today=TODAY)
        with pytest.raises(ValueError):
            await model.move(store, "p", a.id, "bogus", now=NOW)

    async def test_move_missing_task(self, store):
        with pytest.raises(KeyError):
            await model.move(store, "p", "nope", "in-progress", now=NOW)


# --- is_safe_id ---


class TestSafeId:
    @pytest.mark.parametrize(
        "good", ["a", "2026-06-08.1", "linear.NORT-123.1", "a_b.c-d"]
    )
    def test_accepts_safe(self, good):
        assert model.is_safe_id(good)

    @pytest.mark.parametrize(
        "bad", ["", ".", "..", "a/b", "../x", "a b", "a\tb", "a/../b"]
    )
    def test_rejects_unsafe(self, bad):
        assert not model.is_safe_id(bad)

    def test_task_key_rejects_traversal(self):
        with pytest.raises(ValueError):
            model.task_key("p", "todo", "../escape")

    async def test_load_rejects_traversal(self, store):
        with pytest.raises(ValueError):
            await model.load(store, "p", "../escape")


# --- mutation write/delete counts and messages ---


class TestMutationAccounting:
    """What a mutation costs, counted in revisions rather than in commits.

    These used to count ``.md`` writes and read the commit subject. Status is a
    column now, so the facts worth pinning are that a create is one revision, a
    move is one more (not a write plus a delete), and a no-op move is none —
    which is what keeps a poller quiet.
    """

    async def test_create_is_one_revision(self, store):
        before = await store.revision()
        await model.create(store, project="p", title="hi", now=NOW, today=TODAY)
        assert await store.revision() == before + 1

    async def test_append_log_is_one_revision(self, store):
        t = await model.create(store, project="p", title="hi", now=NOW, today=TODAY)
        before = await store.revision()
        await model.append_log(store, "p", t.id, "a note", now=NOW)
        assert await store.revision() == before + 1

    async def test_append_log_records_message(self, store):
        t = await model.create(store, project="p", title="hi", now=NOW, today=TODAY)
        logged = await model.append_log(store, "p", t.id, "a note", now=NOW)
        assert "a note" in logged.log
        assert NOW in logged.log

    async def test_move_is_one_revision_not_a_write_and_a_delete(self, store):
        t = await model.create(store, project="p", title="hi", now=NOW, today=TODAY)
        before = await store.revision()
        await model.move(store, "p", t.id, "done", now=NOW)
        assert await store.revision() == before + 1
        assert (await model.load(store, "p", t.id)).status == "done"

    async def test_move_noop_when_same_status(self, store):
        t = await model.create(store, project="p", title="hi", now=NOW, today=TODAY)
        before = await store.revision()
        await model.move(store, "p", t.id, "todo", now=NOW)
        assert await store.revision() == before


# --- delete ---


class TestDelete:
    async def test_delete_removes_the_row(self, store):
        a = await model.create(store, project="p", title="a", now=NOW, today=TODAY)
        await model.delete(store, "p", a.id)
        with pytest.raises(KeyError):
            await model.load(store, "p", a.id)

    async def test_delete_returns_task(self, store):
        a = await model.create(store, project="p", title="a", now=NOW, today=TODAY)
        deleted = await model.delete(store, "p", a.id)
        assert deleted.id == a.id
        assert deleted.title == "a"

    async def test_delete_missing_raises(self, store):
        with pytest.raises(KeyError):
            await model.delete(store, "p", "nope")

    async def test_delete_finds_task_in_any_status(self, store):
        a = await model.create(store, project="p", title="a", now=NOW, today=TODAY)
        await model.move(store, "p", a.id, "in-progress", now=NOW)
        await model.delete(store, "p", a.id)
        assert await store.load("p", a.id) is None

    async def test_delete_strips_dep_from_dependent(self, store):
        a = await model.create(store, project="p", title="a", now=NOW, today=TODAY)
        b = await model.create(
            store, project="p", title="b", follows=[a.id], now=NOW, today=TODAY
        )
        await model.delete(store, "p", a.id)
        assert (await model.load(store, "p", b.id)).follows == []

    async def test_delete_keeps_other_deps(self, store):
        a = await model.create(store, project="p", title="a", now=NOW, today=TODAY)
        b = await model.create(store, project="p", title="b", now=NOW, today=TODAY)
        c = await model.create(
            store, project="p", title="c", follows=[a.id, b.id], now=NOW, today=TODAY
        )
        await model.delete(store, "p", a.id)
        assert (await model.load(store, "p", c.id)).follows == [b.id]

    async def test_delete_unblocks_dependent(self, store):
        a = await model.create(store, project="p", title="a", now=NOW, today=TODAY)
        b = await model.create(
            store, project="p", title="b", follows=[a.id], now=NOW, today=TODAY
        )
        # b is blocked while a (undone) exists; deleting a makes b actionable.
        assert not await model.is_actionable(await model.load(store, "p", b.id), store)
        await model.delete(store, "p", a.id)
        assert await model.is_actionable(await model.load(store, "p", b.id), store)

    async def test_delete_ignores_terminal_dependents(self, store):
        # A done task that follows the deleted id is left untouched.
        a = await model.create(store, project="p", title="a", now=NOW, today=TODAY)
        b = await model.create(
            store, project="p", title="b", follows=[a.id], now=NOW, today=TODAY
        )
        await model.move(store, "p", b.id, "done", now=NOW)
        await model.delete(store, "p", a.id)
        # b is terminal; its historical follows is preserved.
        assert (await model.load(store, "p", b.id)).follows == [a.id]

    async def test_delete_rewrites_every_dependent(self, store):
        """The row goes, and every non-terminal dependent loses the edge."""
        a = await model.create(store, project="p", title="a", now=NOW, today=TODAY)
        b = await model.create(
            store, project="p", title="b", follows=[a.id], now=NOW, today=TODAY
        )
        c = await model.create(
            store, project="p", title="c", follows=[a.id], now=NOW, today=TODAY
        )
        await model.delete(store, "p", a.id)
        assert await store.load("p", a.id) is None
        assert (await model.load(store, "p", b.id)).follows == []
        assert (await model.load(store, "p", c.id)).follows == []

    async def test_delete_leaves_an_unrelated_task_alone(self, store):
        a = await model.create(store, project="p", title="a", now=NOW, today=TODAY)
        b = await model.create(store, project="p", title="b", now=NOW, today=TODAY)
        before = await model.load(store, "p", b.id)
        await model.delete(store, "p", a.id)
        assert await model.load(store, "p", b.id) == before


# --- rename ---


class TestRename:
    async def test_rename_re_keys_the_row(self, store):
        a = await model.create(store, project="p", title="a", now=NOW, today=TODAY)
        renamed = await model.rename(store, "p", a.id, "new-id")
        assert await store.load("p", a.id) is None
        assert await store.load("p", "new-id") is not None
        loaded = await model.load(store, "p", "new-id")
        assert loaded.id == "new-id"
        assert renamed.id == "new-id"

    async def test_rename_preserves_status_content_log(self, store):
        a = await model.create(
            store, project="p", title="a", content="body text", now=NOW, today=TODAY
        )
        await model.move(store, "p", a.id, "in-progress", now=NOW)
        await model.append_log(store, "p", a.id, "did a thing", now=NOW)
        await model.rename(store, "p", a.id, "new-id")
        loaded = await model.load(store, "p", "new-id")
        assert loaded.status == "in-progress"
        assert loaded.content.strip() == "body text"
        assert "did a thing" in loaded.log

    async def test_rename_bumps_updated(self, store):
        a = await model.create(store, project="p", title="a", now=NOW, today=TODAY)
        later = "2026-07-02T00:00:00Z"
        await model.rename(store, "p", a.id, "new-id", now=later)
        assert (await model.load(store, "p", "new-id")).updated == later

    async def test_rename_rewrites_dependent_follows(self, store):
        a = await model.create(store, project="p", title="a", now=NOW, today=TODAY)
        b = await model.create(
            store, project="p", title="b", follows=[a.id], now=NOW, today=TODAY
        )
        await model.rename(store, "p", a.id, "new-id")
        assert (await model.load(store, "p", b.id)).follows == ["new-id"]

    async def test_rename_keeps_other_follows_entries(self, store):
        a = await model.create(store, project="p", title="a", now=NOW, today=TODAY)
        b = await model.create(store, project="p", title="b", now=NOW, today=TODAY)
        c = await model.create(
            store, project="p", title="c", follows=[a.id, b.id], now=NOW, today=TODAY
        )
        await model.rename(store, "p", a.id, "new-id")
        assert (await model.load(store, "p", c.id)).follows == ["new-id", b.id]

    async def test_rename_reparents_child(self, store):
        parent = await model.create(
            store, project="p", title="parent", now=NOW, today=TODAY
        )
        child = await model.create(
            store, project="p", title="child", parent=parent.id, now=NOW, today=TODAY
        )
        await model.rename(store, "p", parent.id, "new-parent")
        loaded = await model.load(store, "p", child.id)
        assert loaded.parent == "new-parent"
        # Child's own id is NOT cascaded.
        assert loaded.id == child.id

    async def test_rename_ignores_terminal_dependents(self, store):
        a = await model.create(store, project="p", title="a", now=NOW, today=TODAY)
        b = await model.create(
            store, project="p", title="b", follows=[a.id], now=NOW, today=TODAY
        )
        await model.move(store, "p", b.id, "done", now=NOW)
        await model.rename(store, "p", a.id, "new-id")
        # b is terminal; its historical follows is preserved.
        assert (await model.load(store, "p", b.id)).follows == [a.id]

    async def test_rename_missing_raises_keyerror(self, store):
        with pytest.raises(KeyError):
            await model.rename(store, "p", "nope", "new-id")

    async def test_rename_unsafe_new_id_raises_valueerror(self, store):
        a = await model.create(store, project="p", title="a", now=NOW, today=TODAY)
        with pytest.raises(ValueError):
            await model.rename(store, "p", a.id, "../escape")

    async def test_rename_collision_raises_valueerror(self, store):
        a = await model.create(store, project="p", title="a", now=NOW, today=TODAY)
        b = await model.create(store, project="p", title="b", now=NOW, today=TODAY)
        with pytest.raises(ValueError):
            await model.rename(store, "p", a.id, b.id)

    async def test_rename_same_id_is_noop(self, store):
        a = await model.create(store, project="p", title="a", now=NOW, today=TODAY)
        before = await store.revision()
        result = await model.rename(store, "p", a.id, a.id)
        assert result.id == a.id
        assert await store.revision() == before

    async def test_rename_fixes_every_reference(self, store):
        """The row re-keys, and both kinds of reference follow it."""
        a = await model.create(store, project="p", title="a", now=NOW, today=TODAY)
        b = await model.create(
            store, project="p", title="b", follows=[a.id], now=NOW, today=TODAY
        )
        child = await model.create(
            store, project="p", title="c", parent=a.id, now=NOW, today=TODAY
        )
        await model.rename(store, "p", a.id, "new-id")
        assert await store.load("p", a.id) is None
        assert (await model.load(store, "p", b.id)).follows == ["new-id"]
        # The child kept its own id but got re-parented.
        assert (await model.load(store, "p", child.id)).parent == "new-id"


# --- load / list ---


class TestLoadList:
    async def test_load_round_trip(self, store):
        t = await model.create(
            store, project="p", title="hi", command="claude", now=NOW, today=TODAY
        )
        loaded = await model.load(store, "p", t.id)
        assert loaded.id == t.id
        assert loaded.title == "hi"
        assert loaded.command == "claude"
        assert loaded.status == "todo"

    async def test_load_missing(self, store):
        with pytest.raises(KeyError):
            await model.load(store, "p", "nope")

    async def test_list_filters_by_status(self, store):
        a = await model.create(store, project="p", title="a", now=NOW, today=TODAY)
        await model.create(store, project="p", title="b", now=NOW, today=TODAY)
        await model.move(store, "p", a.id, "done", now=NOW)
        todo = await model.list_tasks(store, project="p", status="todo")
        done = await model.list_tasks(store, project="p", status="done")
        assert [t.id for t in todo] == [t.id for t in todo if t.status == "todo"]
        assert len(todo) == 1
        assert len(done) == 1
        assert done[0].id == a.id

    async def test_list_filters_by_parent(self, store):
        p = await model.create(store, project="p", title="a", now=NOW, today=TODAY)
        await model.create(store, project="p", title="b", parent=p.id, now=NOW)
        await model.create(store, project="p", title="c", now=NOW, today=TODAY)
        children = await model.list_tasks(store, project="p", parent=p.id)
        assert len(children) == 1
        assert children[0].parent == p.id

    async def test_list_does_not_leak_across_projects(self, store):
        await model.create(store, project="p", title="a", now=NOW, today=TODAY)
        await model.create(store, project="other", title="b", now=NOW, today=TODAY)
        assert len(await model.list_tasks(store, project="p")) == 1


# --- branch defaulting on create ---


class TestBranchDefault:
    # The conftest autouse fixture forces branch generation down the
    # deterministic fallback (the ``claude`` CLI is blocked in tests), so these
    # assertions cover the offline shapes. Model-path generation is covered in
    # ``test_branch_name.py`` with an injected fake runner.

    async def test_branch_defaults_to_generated_slug(self, store):
        # With the model call failing (autouse fixture), an orphan task falls
        # back to ``<default_type>/<slugify(title)>``.
        t = await model.create(
            store, project="p", title="Fix flaky port test", now=NOW, today=TODAY
        )
        assert t.branch == "feat/fix-flaky-port-test"
        assert (await model.load(store, "p", t.id)).branch == "feat/fix-flaky-port-test"

    async def test_branch_override_respected(self, store):
        t = await model.create(
            store, project="p", title="a", branch="fix/login", now=NOW, today=TODAY
        )
        assert t.branch == "fix/login"
        assert (await model.load(store, "p", t.id)).branch == "fix/login"

    async def test_linear_parent_yields_feat_number_branch(self, store):
        # Generic title + failing model call → the new deterministic Linear
        # fallback splices the bare number into the desc (no NORT- prefix).
        t = await model.create(
            store,
            project="p",
            title="a",
            parent="linear.NORT-123",
            now=NOW,
            today=TODAY,
        )
        assert t.branch == "feat/123-a"
        assert (await model.load(store, "p", t.id)).branch == "feat/123-a"

    async def test_siblings_under_linear_parent_share_branch(self, store):
        a = await model.create(
            store,
            project="p",
            title="a",
            parent="linear.NORT-123",
            now=NOW,
            today=TODAY,
        )
        b = await model.create(
            store,
            project="p",
            title="b",
            parent="linear.NORT-123",
            now=NOW,
            today=TODAY,
        )
        # Both fall back to the same number-led branch (one PR per parent).
        assert a.branch == b.branch == "feat/123-a"

    async def test_non_linear_parent_siblings_share_task_branch(self, store):
        a = await model.create(
            store,
            project="p",
            title="a",
            parent="2026-06-09.3",
            now=NOW,
            today=TODAY,
        )
        b = await model.create(
            store,
            project="p",
            title="b",
            parent="2026-06-09.3",
            now=NOW,
            today=TODAY,
        )
        assert a.branch == b.branch == "task/2026-06-09.3"

    async def test_branch_override_beats_parent_derivation(self, store):
        t = await model.create(
            store,
            project="p",
            title="a",
            parent="linear.NORT-123",
            branch="fix/login",
            now=NOW,
            today=TODAY,
        )
        assert t.branch == "fix/login"

    async def test_first_child_inherits_existing_parent_task_branch(self):
        # A real parent task owns a branch; its FIRST child (no sibling yet)
        # must inherit that branch, not regenerate a divergent task/<parent>.
        store = InMemoryTaskTable()
        parent = await model.create(
            store,
            project="p",
            title="daily maintenance",
            id="daily.maintenance.2026-07-03",
            branch="chore/daily-maintenance",
            now=NOW,
            today=TODAY,
        )
        child = await model.create(
            store,
            project="p",
            title="warehouse writes",
            parent=parent.id,
            now=NOW,
            today=TODAY,
        )
        assert child.branch == "chore/daily-maintenance"
        assert (
            await model.load(store, "p", child.id)
        ).branch == "chore/daily-maintenance"

    async def test_second_child_still_shares_parent_branch(self):
        # Sibling path and parent path agree: all children of one parent share
        # the same branch.
        store = InMemoryTaskTable()
        parent = await model.create(
            store,
            project="p",
            title="run",
            id="run.2026-07-03",
            branch="chore/run",
            now=NOW,
            today=TODAY,
        )
        a = await model.create(
            store, project="p", title="a", parent=parent.id, now=NOW, today=TODAY
        )
        b = await model.create(
            store, project="p", title="b", parent=parent.id, now=NOW, today=TODAY
        )
        assert a.branch == b.branch == "chore/run"

    async def test_linear_virtual_parent_with_no_task_file_still_derives_feat(self):
        # linear.NORT-123 has no task file: parent lookup must fall through to
        # the deterministic Linear derivation.
        store = InMemoryTaskTable()
        t = await model.create(
            store,
            project="p",
            title="a",
            parent="linear.NORT-123",
            now=NOW,
            today=TODAY,
        )
        assert t.branch == "feat/123-a"

    async def test_explicit_branch_still_wins_over_parent_branch(self):
        store = InMemoryTaskTable()
        await model.create(
            store,
            project="p",
            title="p0",
            id="grp.2",
            branch="chore/grp",
            now=NOW,
            today=TODAY,
        )
        child = await model.create(
            store,
            project="p",
            title="c",
            parent="grp.2",
            branch="fix/override",
            now=NOW,
            today=TODAY,
        )
        assert child.branch == "fix/override"

    def test_default_branch_unit_cases(self):
        # Linear parent without a title → the new number-led fallback (no NORT-).
        assert model.default_branch("x", "linear.NORT-123") == "feat/123"
        assert model.default_branch("x", "linear.foo") == "task/linear.foo"
        assert model.default_branch("x", "2026-06-09.3") == "task/2026-06-09.3"
        assert model.default_branch("x") == "task/x"


# --- build_prompt ---


class TestBuildPrompt:
    def test_command_title_and_content(self):
        t = Task(
            id="x",
            title="Do thing",
            project="p",
            command="plan-task",
            content="Details here.",
        )
        assert model.build_prompt(t) == "/plan-task Do thing\n\nDetails here."

    def test_a_command_may_carry_arguments(self):
        t = Task(
            id="x",
            title="Redo the settings page",
            project="p",
            command="impeccable shape",
        )
        assert model.build_prompt(t) == "/impeccable shape Redo the settings page"

    def test_no_command_omits_leading_space(self):
        t = Task(id="x", title="Do thing", project="p", content="Details.")
        assert model.build_prompt(t) == "Do thing\n\nDetails."

    def test_no_content_omits_trailing_block(self):
        t = Task(id="x", title="Do thing", project="p", command="plan-task")
        assert model.build_prompt(t) == "/plan-task Do thing"

    def test_title_only(self):
        t = Task(id="x", title="Just a title", project="p")
        assert model.build_prompt(t) == "Just a title"

    def test_expands_task_dir_token(self, tmp_path, monkeypatch):
        monkeypatch.setattr(model, "tasks_root", lambda: tmp_path / "tasks")
        t = Task(
            id="x",
            title="Do thing",
            project="proj",
            content="See ![img]({{MAEL_TASK_DIR}}/images/NORT-1/a.png)",
        )
        expected_dir = tmp_path / "tasks" / "proj"
        assert model.build_prompt(t) == (
            f"Do thing\n\nSee ![img]({expected_dir}/images/NORT-1/a.png)"
        )

    def test_content_without_token_unchanged(self, tmp_path, monkeypatch):
        monkeypatch.setattr(model, "tasks_root", lambda: tmp_path / "tasks")
        t = Task(id="x", title="Do thing", project="proj", content="No token here.")
        assert model.build_prompt(t) == "Do thing\n\nNo token here."


# --- permission_mode_for ---


class TestPermissionMode:
    def test_plan_maps_to_plan(self):
        assert model.permission_mode_for("plan") == "plan"

    def test_auto_maps_to_auto(self):
        assert model.permission_mode_for("auto") == "auto"

    def test_normal_maps_to_none(self):
        assert model.permission_mode_for("normal") is None

    def test_unknown_maps_to_none(self):
        assert model.permission_mode_for("anything-else") is None


# --- mode_for_command ---


class TestModeForCommand:
    def test_execute_task_runs_unattended(self):
        assert model.mode_for_command("") == "auto"

    @pytest.mark.parametrize("command", ["plan-task", "plan-next-step"])
    def test_a_planning_skill_runs_normal(self, command):
        assert model.mode_for_command(command) == "normal"

    def test_unknown_command_keeps_the_default(self):
        assert model.mode_for_command("anything-else") == model.DEFAULT_MODE


# --- next_task ---


class TestNextTask:
    async def test_none_when_empty(self, store):
        assert await model.next_task(store, "p") is None

    async def test_returns_first_actionable_by_id(self, store):
        a = await model.create(store, project="p", title="a", now=NOW, today=TODAY)
        await model.create(store, project="p", title="b", now=NOW, today=TODAY)
        assert (await model.next_task(store, "p")).id == a.id

    async def test_skips_blocked_by_unfinished_dep(self, store):
        a = await model.create(store, project="p", title="a", now=NOW, today=TODAY)
        b = await model.create(
            store, project="p", title="b", follows=[a.id], now=NOW, today=TODAY
        )
        # a is actionable, b is not (follows undone a) -> next is a.
        assert (await model.next_task(store, "p")).id == a.id
        # With a done, b becomes the next actionable.
        await model.move(store, "p", a.id, "done", now=NOW)
        assert (await model.next_task(store, "p")).id == b.id

    async def test_excludes_in_progress(self, store):
        a = await model.create(store, project="p", title="a", now=NOW, today=TODAY)
        await model.move(store, "p", a.id, "in-progress", now=NOW)
        # An in-progress (already-running) task is not re-offered.
        assert await model.next_task(store, "p") is None
        # A todo task is still returned alongside an unrelated in-progress one.
        b = await model.create(store, project="p", title="b", now=NOW, today=TODAY)
        assert (await model.next_task(store, "p")).id == b.id

    async def test_excludes_terminal(self, store):
        a = await model.create(store, project="p", title="a", now=NOW, today=TODAY)
        await model.move(store, "p", a.id, "done", now=NOW)
        assert await model.next_task(store, "p") is None

    async def test_filters_by_parent(self, store):
        p = await model.create(store, project="p", title="parent", now=NOW, today=TODAY)
        child = await model.create(
            store, project="p", title="child", parent=p.id, now=NOW
        )
        # Without filter, the (lower-id) parent comes first.
        assert (await model.next_task(store, "p")).id == p.id
        # Filtered to the parent's children, only the child qualifies.
        assert (await model.next_task(store, "p", parent=p.id)).id == child.id

    async def test_branch_match_beats_lower_id_on_other_branch(self, store):
        # a has the lower id but is on another branch; b is on the wanted one.
        await model.create(
            store, project="p", title="a", branch="other", now=NOW, today=TODAY
        )
        b = await model.create(
            store, project="p", title="b", branch="feat/x", now=NOW, today=TODAY
        )
        assert (await model.next_task(store, "p", branch="feat/x")).id == b.id

    async def test_branch_no_match_falls_back_to_global(self, store):
        a = await model.create(
            store, project="p", title="a", branch="other", now=NOW, today=TODAY
        )
        # No actionable task on feat/x -> fall back to the global next (a).
        assert (
            await model.next_task(store, "p", branch="feat/x", fallback=True)
        ).id == a.id

    async def test_branch_no_match_no_fallback_returns_none(self, store):
        await model.create(
            store, project="p", title="a", branch="other", now=NOW, today=TODAY
        )
        assert (
            await model.next_task(store, "p", branch="feat/x", fallback=False) is None
        )

    async def test_branch_none_unchanged(self, store):
        a = await model.create(
            store, project="p", title="a", branch="other", now=NOW, today=TODAY
        )
        await model.create(
            store, project="p", title="b", branch="feat/x", now=NOW, today=TODAY
        )
        # No branch preference -> first actionable, id-sorted.
        assert (await model.next_task(store, "p", branch=None)).id == a.id


# --- priority ordering in selectors ---


class TestPriorityOrdering:
    async def test_next_task_prefers_higher_priority(self, store):
        # low is created first (lower id), but critical outranks it.
        await model.create(
            store, project="p", title="low", priority="low", now=NOW, today=TODAY
        )
        crit = await model.create(
            store, project="p", title="crit", priority="critical", now=NOW, today=TODAY
        )
        assert (await model.next_task(store, "p")).id == crit.id

    async def test_next_task_ties_broken_by_id(self, store):
        # Two same-priority tasks: the lower id wins (chronological tie-break).
        a = await model.create(
            store, project="p", title="a", priority="high", now=NOW, today=TODAY
        )
        await model.create(
            store, project="p", title="b", priority="high", now=NOW, today=TODAY
        )
        assert (await model.next_task(store, "p")).id == a.id

    async def test_default_medium_outranks_low(self, store):
        await model.create(
            store, project="p", title="low", priority="low", now=NOW, today=TODAY
        )
        med = await model.create(store, project="p", title="med", now=NOW, today=TODAY)
        assert (await model.next_task(store, "p")).id == med.id

    async def test_next_follower_prefers_higher_priority(self, store):
        a = await model.create(store, project="p", title="a", now=NOW, today=TODAY)
        # Two followers of a, differing priority; the higher one is chosen.
        await model.create(
            store,
            project="p",
            title="lo",
            priority="low",
            follows=[a.id],
            now=NOW,
            today=TODAY,
        )
        hi = await model.create(
            store,
            project="p",
            title="hi",
            priority="high",
            follows=[a.id],
            now=NOW,
            today=TODAY,
        )
        await model.move(store, "p", a.id, "done", now=NOW)
        assert (await model.next_follower(store, "p", a.id)).id == hi.id


# --- parse_task_blocks ---


class TestParseTaskBlocks:
    def test_preamble_ignored(self):
        text = (
            "This is human-readable preamble.\n"
            "    mael task load-many <file>\n"
            "\n"
            "---CREATE TASK iter1---\n"
            "title: Do the thing\n"
            "---\n"
            "## Scope\n"
            "the body\n"
        )
        blocks, _ = model.parse_task_blocks(text)
        assert len(blocks) == 1
        assert blocks[0]["name"] == "iter1"
        assert blocks[0]["args"]["title"] == "Do the thing"
        assert "the body" in blocks[0]["content"]
        assert blocks[0]["content"].startswith("## Scope")

    def test_open_marker_only_closure(self):
        # No END marker — block A runs until block B's open marker.
        text = (
            "---CREATE TASK a---\n"
            "title: A\n"
            "---\n"
            "body a\n"
            "---CREATE TASK b---\n"
            "title: B\n"
            "---\n"
            "body b\n"
        )
        blocks, _ = model.parse_task_blocks(text)
        assert [b["name"] for b in blocks] == ["a", "b"]
        assert blocks[0]["content"] == "body a"
        assert blocks[1]["content"] == "body b"

    def test_explicit_end_marker_closure(self):
        # Text between END and the next open marker is ignored (preamble again).
        text = (
            "---CREATE TASK a---\n"
            "title: A\n"
            "---\n"
            "body a\n"
            "---END TASK a---\n"
            "ignored interstitial text\n"
            "---CREATE TASK b---\n"
            "title: B\n"
            "---\n"
            "body b\n"
        )
        blocks, _ = model.parse_task_blocks(text)
        assert [b["name"] for b in blocks] == ["a", "b"]
        assert blocks[0]["content"] == "body a"
        assert "ignored" not in blocks[1]["content"]

    def test_multiple_blocks(self):
        text = (
            "---CREATE TASK one---\n"
            "title: One\n"
            "---\n"
            "c1\n"
            "---CREATE TASK two---\n"
            "title: Two\n"
            "command: plan-next-step\n"
            "---\n"
            "c2\n"
            "---CREATE TASK three---\n"
            "title: Three\n"
            "---\n"
            "c3\n"
        )
        blocks, _ = model.parse_task_blocks(text)
        assert len(blocks) == 3
        assert blocks[1]["args"]["command"] == "plan-next-step"

    def test_no_blocks_raises(self):
        with pytest.raises(ValueError, match="No task blocks"):
            model.parse_task_blocks("just some preamble, no markers")

    def test_duplicate_name_raises(self):
        text = (
            "---CREATE TASK a---\n"
            "title: A\n"
            "---\n"
            "x\n"
            "---CREATE TASK a---\n"
            "title: A2\n"
            "---\n"
            "y\n"
        )
        with pytest.raises(ValueError, match="Duplicate block name"):
            model.parse_task_blocks(text)

    def test_missing_title_raises(self):
        text = "---CREATE TASK a---\ncommand: plan-task\n---\nbody\n"
        with pytest.raises(ValueError, match="missing a title"):
            model.parse_task_blocks(text)

    def test_unknown_key_raises(self):
        # A typo like `follows:` (should be `follow:`) must fail loudly.
        text = "---CREATE TASK a---\ntitle: A\nfollows: b\n---\nbody\n"
        with pytest.raises(ValueError, match="Unknown key"):
            model.parse_task_blocks(text)

    def test_mode_is_an_accepted_key(self):
        text = "---CREATE TASK a---\ntitle: A\nmode: normal\n---\nbody\n"
        blocks, _ = model.parse_task_blocks(text)
        assert blocks[0]["args"]["mode"] == "normal"

    def test_action_keys_are_accepted(self):
        text = (
            "---CREATE TASK a---\n"
            "title: A\n"
            "pre-action: linear.in-progress\n"
            "post-action: linear.done\n"
            "---\nbody\n"
        )
        blocks, _ = model.parse_task_blocks(text)
        assert blocks[0]["args"]["pre-action"] == "linear.in-progress"
        assert blocks[0]["args"]["post-action"] == "linear.done"

    def test_model_is_block_settable(self):
        # ``model`` joined _BLOCK_KEYS via TASK_FIELDS, so a block may set it —
        # this is the vocabulary the plan templates write.
        text = "---CREATE TASK a---\ntitle: A\nmodel: opus\n---\nbody\n"
        blocks, _ = model.parse_task_blocks(text)
        assert blocks[0]["args"]["model"] == "opus"

    def test_hyphenated_marker_name_parses(self):
        # A hyphenated name is now valid (block names share is_safe_id's
        # character class, [A-Za-z0-9._-]+) — it must parse, not be rejected.
        text = "---CREATE TASK iter-1---\ntitle: A\n---\nbody\n"
        blocks, _ = model.parse_task_blocks(text)
        assert blocks[0]["name"] == "iter-1"

    def test_malformed_marker_name_raises(self):
        # A name with a space still fails the strict pattern; it must error
        # rather than silently becoming prose.
        text = "---CREATE TASK bad name---\ntitle: A\n---\nbody\n"
        with pytest.raises(ValueError, match="Malformed task marker"):
            model.parse_task_blocks(text)

    def test_escaped_wildcard_follow_end_tolerated(self):
        # `follow-end: "\*"` is invalid YAML (bad escape) but the canonical form
        # is `"*"`; we salvage it and warn rather than hard-fail.
        text = '---CREATE TASK a---\ntitle: A\nfollow-end: "\\*"\n---\nbody\n'
        blocks, warnings = model.parse_task_blocks(text)
        assert blocks[0]["args"]["follow-end"] == "*"
        assert warnings
        assert any("a" in w for w in warnings)

    def test_invalid_yaml_raises_precise_error(self):
        # A *different* invalid escape must error precisely (naming the block and
        # the YAML problem), not the misleading "missing a title".
        text = '---CREATE TASK a---\ntitle: "x \\q"\n---\nbody\n'
        with pytest.raises(ValueError, match="invalid frontmatter") as exc:
            model.parse_task_blocks(text)
        assert "missing a title" not in str(exc.value)

    def test_reported_file_regression(self):
        # The exact failing frontmatter from the reported plan file: a real
        # title plus the escaped wildcard. Title survives, wildcard normalises.
        text = (
            "---CREATE TASK step---\n"
            'title: "Execute: ... (view + unlink + attach file)"\n'
            'follow-end: "\\*"\n'
            "---\n"
            "do the work\n"
        )
        blocks, warnings = model.parse_task_blocks(text)
        assert (
            blocks[0]["args"]["title"] == "Execute: ... (view + unlink + attach file)"
        )
        assert blocks[0]["args"]["follow-end"] == "*"
        assert warnings


# --- load_many ---


class TestLoadMany:
    async def test_intra_file_follow_resolves_to_allocated_id(self, store):
        blocks = [
            {"name": "a", "args": {"title": "A"}, "content": "ca"},
            {"name": "b", "args": {"title": "B", "follow": "a"}, "content": "cb"},
        ]
        created = await model.load_many(
            store, project="p", blocks=blocks, now=NOW, today=TODAY
        )
        assert len(created) == 2
        a, b = created
        # B's follows points at A's allocated id, not the block name "a".
        assert b.follows == [a.id]
        assert "a" not in b.follows

    async def test_hyphenated_block_name_round_trips_in_follow(self, store):
        # A hyphenated handle must resolve end-to-end: the follow reference
        # `iter-1` maps to the allocated id of the block named "iter-1".
        blocks = [
            {"name": "iter-1", "args": {"title": "One"}, "content": "c1"},
            {
                "name": "iter-2",
                "args": {"title": "Two", "follow": "iter-1"},
                "content": "c2",
            },
        ]
        created = await model.load_many(
            store, project="p", blocks=blocks, now=NOW, today=TODAY
        )
        one, two = created
        assert two.follows == [one.id]
        assert "iter-1" not in two.follows

    async def test_follow_end_resolves_against_live_store(self, store):
        seed = await model.create(
            store, project="p", title="seed", now=NOW, today=TODAY
        )
        blocks = [
            {"name": "x", "args": {"title": "X", "follow-end": seed.id}, "content": ""},
        ]
        created = await model.load_many(
            store, project="p", blocks=blocks, now=NOW, today=TODAY
        )
        assert created[0].follows == [seed.id]

    async def test_block_model_reaches_the_created_task(self, store):
        blocks = [
            {"name": "a", "args": {"title": "A", "model": "opus"}, "content": ""},
            {"name": "b", "args": {"title": "B"}, "content": ""},
        ]
        created = await model.load_many(
            store, project="p", blocks=blocks, now=NOW, today=TODAY
        )
        a, b = created
        assert a.model == "opus"
        # An omitted key leaves the task inheriting the user's default.
        assert b.model == ""

    async def test_block_actions_are_applied(self, store):
        blocks = [
            {
                "name": "exec",
                "args": {
                    "title": "E",
                    "pre-action": "linear.in-progress",
                    "post-action": "linear.done",
                },
                "content": "",
            },
        ]
        created = await model.load_many(
            store, project="p", blocks=blocks, now=NOW, today=TODAY
        )
        assert created[0].pre_action == "linear.in-progress"
        assert created[0].post_action == "linear.done"

    async def test_block_branch_is_applied(self, store):
        blocks = [
            {"name": "a", "args": {"title": "A", "branch": "my-branch"}, "content": ""}
        ]
        created = await model.load_many(
            store, project="p", blocks=blocks, now=NOW, today=TODAY
        )
        assert created[0].branch == "my-branch"

    async def test_block_branch_overrides_sibling_inheritance(self, store):
        # A sibling under the same parent already owns a branch (one PR per
        # parent), but an explicit `branch:` opts this task out of it.
        sibling = await model.create(
            store, project="p", title="sib", parent="par", now=NOW, today=TODAY
        )
        assert sibling.branch
        blocks = [
            {
                "name": "a",
                "args": {"title": "A", "parent": "par", "branch": "own-branch"},
                "content": "",
            }
        ]
        created = await model.load_many(
            store, project="p", blocks=blocks, now=NOW, today=TODAY
        )
        assert created[0].branch == "own-branch"
        assert created[0].branch != sibling.branch

    async def test_block_without_branch_still_inherits_sibling_branch(self, store):
        sibling = await model.create(
            store, project="p", title="sib", parent="par", now=NOW, today=TODAY
        )
        blocks = [{"name": "a", "args": {"title": "A", "parent": "par"}, "content": ""}]
        created = await model.load_many(
            store, project="p", blocks=blocks, now=NOW, today=TODAY
        )
        assert created[0].branch == sibling.branch

    async def test_block_actions_default_empty(self, store):
        blocks = [{"name": "a", "args": {"title": "A"}, "content": ""}]
        created = await model.load_many(
            store, project="p", blocks=blocks, now=NOW, today=TODAY
        )
        assert created[0].pre_action == ""
        assert created[0].post_action == ""

    async def test_block_mode_is_honored_and_defaults_to_plan(self, store):
        blocks = [
            {"name": "exec", "args": {"title": "E", "mode": "normal"}, "content": ""},
            {"name": "plan", "args": {"title": "P"}, "content": ""},
        ]
        created = await model.load_many(
            store, project="p", blocks=blocks, now=NOW, today=TODAY
        )
        by_title = {t.title: t for t in created}
        assert by_title["E"].mode == "normal"  # explicit wins
        assert by_title["P"].mode == model.DEFAULT_MODE  # omitted falls through to plan

    async def test_passthrough_real_id_follow(self, store):
        seed = await model.create(
            store, project="p", title="seed", now=NOW, today=TODAY
        )
        blocks = [
            {"name": "x", "args": {"title": "X", "follow": seed.id}, "content": ""},
        ]
        created = await model.load_many(
            store, project="p", blocks=blocks, now=NOW, today=TODAY
        )
        assert created[0].follows == [seed.id]

    async def test_child_id_allocation_increments_across_batch(self, store):
        blocks = [
            {"name": "a", "args": {"title": "A", "parent": "linear.X"}, "content": ""},
            {"name": "b", "args": {"title": "B", "parent": "linear.X"}, "content": ""},
        ]
        created = await model.load_many(
            store, project="p", blocks=blocks, now=NOW, today=TODAY
        )
        ids = [t.id for t in created]
        assert ids == ["linear.X.1", "linear.X.2"]

    async def test_follow_list_value(self, store):
        # A list-valued follow with one block-name and one real id.
        seed = await model.create(
            store, project="p", title="seed", now=NOW, today=TODAY
        )
        blocks = [
            {"name": "a", "args": {"title": "A"}, "content": ""},
            {
                "name": "b",
                "args": {"title": "B", "follow": ["a", seed.id]},
                "content": "",
            },
        ]
        created = await model.load_many(
            store, project="p", blocks=blocks, now=NOW, today=TODAY
        )
        a, b = created
        assert b.follows == [a.id, seed.id]

    async def test_default_parent_applied_when_block_omits_parent(self, store):
        blocks = [{"name": "a", "args": {"title": "A"}, "content": ""}]
        created = await model.load_many(
            store, project="p", blocks=blocks, default_parent="linear.X", now=NOW
        )
        assert created[0].parent == "linear.X"
        assert created[0].id == "linear.X.1"  # nested child id

    async def test_block_parent_overrides_default(self, store):
        blocks = [
            {"name": "a", "args": {"title": "A", "parent": "linear.Y"}, "content": ""}
        ]
        created = await model.load_many(
            store, project="p", blocks=blocks, default_parent="linear.X", now=NOW
        )
        assert created[0].parent == "linear.Y"

    async def test_follow_end_wildcard_appends_to_sibling_chain(self, store):
        # An existing child of linear.X; a new block with follow-end: * should
        # follow it (the end of the parent's child-chain).
        existing = await model.create(
            store, project="p", title="existing", parent="linear.X", now=NOW
        )
        blocks = [
            {
                "name": "step",
                "args": {"title": "Step", "follow-end": "*"},
                "content": "",
            },
        ]
        created = await model.load_many(
            store, project="p", blocks=blocks, default_parent="linear.X", now=NOW
        )
        assert created[0].follows == [existing.id]

    async def test_follow_end_wildcard_empty_when_first_child(self, store):
        blocks = [
            {
                "name": "step",
                "args": {"title": "Step", "follow-end": "*"},
                "content": "",
            },
        ]
        created = await model.load_many(
            store, project="p", blocks=blocks, default_parent="linear.X", now=NOW
        )
        # No existing siblings -> nothing to follow.
        assert created[0].follows == []

    async def test_wildcard_and_intra_file_follow_combine(self, store):
        # step: follow-end:* (appends after existing sibling); tail: follow:step.
        existing = await model.create(
            store, project="p", title="existing", parent="linear.X", now=NOW
        )
        blocks = [
            {
                "name": "step",
                "args": {"title": "Step", "follow-end": "*"},
                "content": "",
            },
            {
                "name": "tail",
                "args": {"title": "Tail", "follow": "step"},
                "content": "",
            },
        ]
        created = await model.load_many(
            store, project="p", blocks=blocks, default_parent="linear.X", now=NOW
        )
        step, tail = created
        assert step.follows == [existing.id]
        assert tail.follows == [step.id]
        # The wildcard for `tail` would have seen `step` as a new sibling leaf,
        # but `tail` uses intra-file `follow`, so it chains off step directly.


# --- create() priority ---


class TestCreatePriority:
    async def test_default_is_medium(self, store):
        t = await model.create(store, project="p", title="t", now=NOW)
        assert t.priority == "medium"
        assert (await model.load(store, "p", t.id)).priority == "medium"

    async def test_explicit_priority_persists(self, store):
        t = await model.create(store, project="p", title="t", priority="high", now=NOW)
        assert t.priority == "high"
        assert (await model.load(store, "p", t.id)).priority == "high"

    async def test_invalid_priority_raises(self, store):
        with pytest.raises(ValueError):
            await model.create(store, project="p", title="t", priority="bogus", now=NOW)


# --- update() ---


class TestUpdate:
    async def test_update_changes_fields_and_bumps_updated(self, store):
        t = await model.create(store, project="p", title="old", now=NOW)
        updated = await model.update(
            store, "p", t.id, title="new", branch="feat/x", content="body", now=NOW2
        )
        assert updated.title == "new"
        assert updated.branch == "feat/x"
        assert updated.content == "body"
        assert updated.updated == NOW2
        reloaded = await model.load(store, "p", t.id)
        assert reloaded.title == "new"
        assert reloaded.branch == "feat/x"
        assert reloaded.content == "body"

    async def test_update_leaves_omitted_fields_untouched(self, store):
        t = await model.create(
            store, project="p", title="keep", branch="b", content="body", now=NOW
        )
        await model.update(store, "p", t.id, branch="b2", now=NOW2)
        reloaded = await model.load(store, "p", t.id)
        assert reloaded.title == "keep"
        assert reloaded.content == "body"
        assert reloaded.branch == "b2"

    async def test_update_changes_command_and_mode(self, store):
        t = await model.create(
            store, project="p", title="t", command="plan-task", mode="plan", now=NOW
        )
        await model.update(store, "p", t.id, command="execute", mode="normal", now=NOW2)
        reloaded = await model.load(store, "p", t.id)
        assert reloaded.command == "execute"
        assert reloaded.mode == "normal"

    async def test_update_command_to_empty(self, store):
        t = await model.create(
            store, project="p", title="t", command="plan-task", now=NOW
        )
        await model.update(store, "p", t.id, command="", now=NOW2)
        assert (await model.load(store, "p", t.id)).command == ""

    async def test_update_changes_priority(self, store):
        t = await model.create(store, project="p", title="t", now=NOW)
        await model.update(store, "p", t.id, priority="critical", now=NOW2)
        assert (await model.load(store, "p", t.id)).priority == "critical"

    async def test_update_invalid_priority_raises(self, store):
        t = await model.create(store, project="p", title="t", now=NOW)
        with pytest.raises(ValueError):
            await model.update(store, "p", t.id, priority="bogus", now=NOW2)

    async def test_update_omitting_priority_leaves_it(self, store):
        t = await model.create(store, project="p", title="t", priority="high", now=NOW)
        await model.update(store, "p", t.id, branch="b", now=NOW2)
        assert (await model.load(store, "p", t.id)).priority == "high"

    async def test_update_changes_follows(self, store):
        t = await model.create(store, project="p", title="t", now=NOW)
        await model.update(store, "p", t.id, follows=["a", "b"], now=NOW2)
        assert (await model.load(store, "p", t.id)).follows == ["a", "b"]

    async def test_update_clears_follows_with_an_empty_list(self, store):
        t = await model.create(store, project="p", title="t", follows=["a"], now=NOW)
        await model.update(store, "p", t.id, follows=[], now=NOW2)
        assert (await model.load(store, "p", t.id)).follows == []

    async def test_update_omitting_follows_leaves_it(self, store):
        t = await model.create(store, project="p", title="t", follows=["a"], now=NOW)
        await model.update(store, "p", t.id, branch="b", now=NOW2)
        assert (await model.load(store, "p", t.id)).follows == ["a"]

    async def test_update_does_not_change_status(self, store):
        t = await model.create(store, project="p", title="t", now=NOW)
        await model.move(store, "p", t.id, model.STATUS_IN_PROGRESS, now=NOW)
        await model.update(store, "p", t.id, branch="b", now=NOW2)
        assert (await model.load(store, "p", t.id)).status == model.STATUS_IN_PROGRESS

    async def test_update_unknown_id_raises(self, store):
        with pytest.raises(KeyError):
            await model.update(store, "p", "nope", branch="x")

    async def test_update_is_one_revision(self, store):
        t = await model.create(store, project="p", title="t", now=NOW)
        before = await store.revision()
        await model.update(store, "p", t.id, branch="b", now=NOW2)
        assert await store.revision() == before + 1


# --- edit_in_editor() (needs a GitFileStore for the on-disk path) ---


def _editor_script(tmp_path, py_body: str):
    """Write an executable fake-editor (Python) script and return its path.

    ``py_body`` runs with ``sys.argv[1]`` bound to the task file path, so it can
    rewrite or leave the file untouched to simulate a real editor session.
    Python keeps the fake editor OS-portable (no sed/`-i ''` quirks).
    """
    import sys as _sys

    script = tmp_path / "fake_editor.py"
    script.write_text(f"#!{_sys.executable}\nimport sys\n" + py_body + "\n")
    script.chmod(0o755)
    return str(script)


class TestEditInEditor:
    """The one ``$EDITOR`` round trip.

    The row is rendered to a temp file for the editor and re-parsed afterwards,
    so these need no on-disk store — only a table and a fake editor.
    """

    async def test_changed_save_bumps_updated(self, store, tmp_path):
        t = await model.create(store, project="p", title="orig", now=NOW)
        before_updated = (await model.load(store, "p", t.id)).updated
        # Insert text under the ## Content heading so the edit lands in a
        # section the model parses back, mimicking a real editor change.
        editor = _editor_script(
            tmp_path,
            "p = sys.argv[1]\n"
            "t = open(p).read().replace('## Content\\n', '## Content\\nedited\\n')\n"
            "open(p, 'w').write(t)\n",
        )
        task, changed = await model.edit_in_editor(store, "p", t.id, editor=editor)
        assert changed is True
        assert task.updated != before_updated
        # The row stays canonical and the edit reached the Content section.
        assert "edited" in (await model.load(store, "p", t.id)).content

    async def test_noop_save_writes_nothing(self, store, tmp_path):
        t = await model.create(store, project="p", title="orig", now=NOW)
        before = await model.load(store, "p", t.id)
        editor = _editor_script(tmp_path, "pass")  # no-op: open + quit, no change
        _task, changed = await model.edit_in_editor(store, "p", t.id, editor=editor)
        assert changed is False
        after = await model.load(store, "p", t.id)
        assert after.updated == before.updated
        assert after.content == before.content

    async def test_an_edit_cannot_re_key_the_row(self, store, tmp_path):
        """Identity is the table's: an edited ``id`` must not orphan the task."""
        t = await model.create(store, project="p", title="orig", now=NOW)
        editor = _editor_script(
            tmp_path,
            "p = sys.argv[1]\n"
            "t = open(p).read().replace('id: ', 'id: hijacked-', 1)\n"
            "open(p, 'w').write(t)\n",
        )
        edited, changed = await model.edit_in_editor(store, "p", t.id, editor=editor)
        assert changed is True
        assert edited.id == t.id
        assert await store.load("p", t.id) is not None

    async def test_unknown_id_raises(self, store):
        with pytest.raises(KeyError):
            await model.edit_in_editor(store, "p", "nope", editor="true")

    async def test_missing_editor_raises_runtimeerror(self, store):
        t = await model.create(store, project="p", title="orig", now=NOW)
        with pytest.raises(RuntimeError):
            await model.edit_in_editor(
                store, "p", t.id, editor="definitely-not-an-editor-xyz"
            )

    async def test_editor_nonzero_exit_raises_runtimeerror(self, store, tmp_path):
        t = await model.create(store, project="p", title="orig", now=NOW)
        editor = _editor_script(tmp_path, "sys.exit(1)")
        with pytest.raises(RuntimeError):
            await model.edit_in_editor(store, "p", t.id, editor=editor)

    async def test_editor_launched_with_inherited_stdio(self, store, monkeypatch):
        # The editor must inherit the terminal (``stream=True``) so a full-screen
        # editor like ``vi`` can draw its screen; without it ``run_cmd`` captures
        # stdout/stderr into pipes and the editor is unusable. Spy on ``run_cmd``
        # to pin this contract — a non-interactive fake editor can't exercise it.
        from unittest.mock import MagicMock

        t = await model.create(store, project="p", title="orig", now=NOW)
        spy = MagicMock()
        monkeypatch.setattr(model, "run_cmd", spy)
        # The mocked run_cmd leaves the file untouched, so edit_in_editor returns
        # early with changed=False — irrelevant here; we only assert on the spy.
        await model.edit_in_editor(store, "p", t.id, editor="some-editor")
        spy.assert_called_once()
        assert spy.call_args.kwargs.get("stream") is True


# --- duplicate + run-id (model-level) ---


class TestDuplicate:
    async def test_copies_recipe_into_todo(self, store):
        src = await model.create(
            store,
            project="p",
            title="Src",
            command="plan-task",
            mode="auto",
            content="body",
            pre_action="a",
            post_action="b",
            status=model.STATUS_TEMPLATE,
            id="tmpl",
        )
        dup = await model.duplicate(store, "p", src.id)
        assert dup.id != src.id
        assert dup.status == model.STATUS_TODO
        assert dup.title == "Src"
        assert dup.command == "plan-task"
        assert dup.mode == "auto"
        assert dup.content == "body"
        assert dup.pre_action == "a"
        assert dup.post_action == "b"

    async def test_does_not_copy_schedule(self, store):
        await model.create(
            store,
            project="p",
            title="T",
            schedule="0 9 * * *",
            last_run="2026-01-01T00:00:00+00:00",
            status=model.STATUS_TEMPLATE,
            id="tmpl",
        )
        dup = await model.duplicate(store, "p", "tmpl")
        assert dup.schedule == ""
        assert dup.last_run == ""

    async def test_overrides_win(self, store):
        await model.create(store, project="p", title="Src", command="c1", id="s")
        dup = await model.duplicate(store, "p", "s", title="New", command="c2")
        assert dup.title == "New"
        assert dup.command == "c2"

    async def test_inherits_source_priority(self, store):
        await model.create(store, project="p", title="Src", priority="high", id="s")
        dup = await model.duplicate(store, "p", "s")
        assert dup.priority == "high"

    async def test_priority_override_wins(self, store):
        await model.create(store, project="p", title="Src", priority="high", id="s")
        dup = await model.duplicate(store, "p", "s", priority="low")
        assert dup.priority == "low"

    async def test_source_untouched(self, store):
        await model.create(store, project="p", title="Src", content="x", id="s")
        await model.duplicate(store, "p", "s", title="other")
        assert (await model.load(store, "p", "s")).title == "Src"

    async def test_run_id_names_run_under_template_but_parent_is_blank(self, store):
        # A scheduled run is duplicated with parent="" and id=allocate_run_id:
        # the dot-id names/dedups it under the template, while the empty parent
        # lets it root its own chain (see docs/dev/tasks.md).
        await model.create(
            store,
            project="p",
            title="Maint",
            status=model.STATUS_TEMPLATE,
            id="maintenance",
        )
        run_id = model.allocate_run_id("maintenance", "2026-06-18")
        assert run_id == "maintenance.2026-06-18"
        dup = await model.duplicate(store, "p", "maintenance", parent="", id=run_id)
        assert dup.id == "maintenance.2026-06-18"
        assert dup.parent == ""
        # With no branch override and no parent, create() generates a descriptive
        # <type>/<desc> branch from the title (an accepted consequence of blanking
        # the parent: each firing gets its own branch/PR — see docs/dev/tasks.md).
        assert dup.branch == model.default_branch(
            run_id, "", title="Maint", generate=True
        )

    async def test_branch_override_is_honored(self, store):
        await model.create(
            store,
            project="p",
            title="Maint",
            status=model.STATUS_TEMPLATE,
            id="maintenance",
        )
        run_id = model.allocate_run_id("maintenance", "2026-06-18")
        dup = await model.duplicate(
            store,
            "p",
            "maintenance",
            parent="",
            branch="chore/maint",
            id=run_id,
        )
        assert dup.branch == "chore/maint"


class TestTemplateStatus:
    async def test_template_is_not_actionable(self, store):
        t = await model.create(
            store, project="p", title="T", status=model.STATUS_TEMPLATE, id="t"
        )
        assert not await model.is_actionable(t, store)

    async def test_template_invisible_to_next_task(self, store):
        await model.create(
            store, project="p", title="T", status=model.STATUS_TEMPLATE, id="t"
        )
        assert await model.next_task(store, "p") is None

    async def test_move_accepts_template(self, store):
        await model.create(store, project="p", title="T", id="t")
        moved = await model.move(store, "p", "t", model.STATUS_TEMPLATE)
        assert moved.status == model.STATUS_TEMPLATE

    async def test_schedule_round_trips(self, store):
        await model.create(
            store,
            project="p",
            title="T",
            schedule="0 9 * * *",
            last_run="2026-06-18T09:00:00+00:00",
            id="t",
        )
        reloaded = await model.load(store, "p", "t")
        assert reloaded.schedule == "0 9 * * *"
        assert reloaded.last_run == "2026-06-18T09:00:00+00:00"


class TestSessionIdFor:
    def test_deterministic(self):
        a = model.session_id_for("proj", "2026-06-30.1")
        b = model.session_id_for("proj", "2026-06-30.1")
        assert a == b

    def test_valid_uuid(self):
        import uuid

        # Round-trips through UUID() → it is a well-formed UUID string.
        uid = model.session_id_for("proj", "2026-06-30.1")
        assert str(uuid.UUID(uid)) == uid

    def test_differs_across_tasks(self):
        a = model.session_id_for("proj", "2026-06-30.1")
        b = model.session_id_for("proj", "2026-06-30.2")
        assert a != b

    def test_differs_across_projects(self):
        a = model.session_id_for("proj-a", "x")
        b = model.session_id_for("proj-b", "x")
        assert a != b

    async def test_a_saved_row_carries_the_session_id(self, store):
        # The row derives session_id on the way in, so the reverse lookup
        # resolves a task nobody stamped by hand.
        t = await model.create(store, project="proj", title="t", id="2026-06-30.1")
        found = await store.find_by_session_id(
            model.session_id_for("proj", "2026-06-30.1")
        )
        assert found is not None and found.id == t.id


class TestReconcile:
    async def _in_progress(self, store, project, title, **kw):
        t = await model.create(store, project=project, title=title, **kw)
        await model.move(store, project, t.id, model.STATUS_IN_PROGRESS)
        return t

    async def test_ok_row_for_in_progress_with_session(self, store):
        t = await self._in_progress(store, "p", "a", id="t1")
        rows = await model.reconcile(store, "p", session_task_ids={t.id: {"pid": 1}})
        assert len(rows) == 1
        assert rows[0].state == model.RECONCILE_OK
        assert rows[0].fix_status is None

    async def test_stale_that_ran_is_finished_suggests_done(self, store):
        # A stale in-progress task whose transcript exists ran at some point;
        # stopped = finished, so it is closed.
        t = await self._in_progress(store, "p", "a", id="t1")
        rows = await model.reconcile(store, "p", session_task_ids={}, ran_ids={t.id})
        assert len(rows) == 1
        assert rows[0].state == model.RECONCILE_FINISHED
        assert rows[0].fix_status == model.STATUS_DONE

    async def test_stale_that_never_ran_suggests_todo(self, store):
        # A stale in-progress task with no transcript never launched; its
        # in-progress status is bogus, so it goes back to todo to be run.
        await self._in_progress(store, "p", "a", id="t1")
        rows = await model.reconcile(store, "p", session_task_ids={}, ran_ids=set())
        assert len(rows) == 1
        assert rows[0].state == model.RECONCILE_NEVER_RAN
        assert rows[0].fix_status == model.STATUS_TODO

    async def test_ran_ids_only_affects_stale_rows(self, store):
        # An OK row (live session) is unaffected even if its id is in ran_ids.
        t = await self._in_progress(store, "p", "a", id="t1")
        rows = await model.reconcile(
            store,
            "p",
            session_task_ids={t.id: {"pid": 1}},
            ran_ids={t.id},
        )
        assert rows[0].state == model.RECONCILE_OK
        assert rows[0].fix_status is None

    async def test_orphan_session_on_todo_task(self, store):
        t = await model.create(store, project="p", title="a", id="t1")  # stays todo
        rows = await model.reconcile(store, "p", session_task_ids={t.id: {"pid": 9}})
        assert len(rows) == 1
        assert rows[0].state == model.RECONCILE_ORPHAN
        assert rows[0].fix_status == model.STATUS_IN_PROGRESS

    async def test_done_task_with_session_listed_but_not_flipped(self, store):
        t = await model.create(store, project="p", title="a", id="t1")
        await model.move(store, "p", t.id, model.STATUS_DONE)
        rows = await model.reconcile(store, "p", session_task_ids={t.id: {"pid": 9}})
        assert len(rows) == 1
        assert rows[0].state == model.RECONCILE_ORPHAN
        assert rows[0].fix_status is None  # finished window — not a corruption

    async def test_missing_task_with_session_not_flipped(self, store):
        rows = await model.reconcile(store, "p", session_task_ids={"ghost": {"pid": 9}})
        assert len(rows) == 1
        assert rows[0].state == model.RECONCILE_ORPHAN
        assert rows[0].task_status == "(missing)"
        assert rows[0].fix_status is None

    async def test_mixed_rows_sorted_by_task_id(self, store):
        await self._in_progress(store, "p", "a", id="t1")  # stale (no session)
        t2 = await self._in_progress(store, "p", "b", id="t2")  # ok
        rows = await model.reconcile(store, "p", session_task_ids={t2.id: {"pid": 2}})
        assert [r.task_id for r in rows] == ["t1", "t2"]
        assert rows[0].state == model.RECONCILE_NEVER_RAN
        assert rows[1].state == model.RECONCILE_OK

    async def test_reads_every_in_progress_task(self, store):
        """The listing comes from the table, so nothing can serve it from empty."""
        await self._in_progress(store, "p", "a", id="t1")
        rows = await model.reconcile(store, "p", session_task_ids={})
        assert len(rows) == 1
        assert rows[0].state == model.RECONCILE_NEVER_RAN
