"""Tests for the task notebook core model, against an InMemoryStore."""

import pytest

from maelstrom import task as model
from maelstrom.task import Task
from maelstrom.task_store import InMemoryStore


# --- a recording store to assert mutation counts/messages ---


class RecordingStore(InMemoryStore):
    """InMemoryStore that records every write/delete call."""

    def __init__(self) -> None:
        super().__init__()
        self.writes: list[tuple[str, str | None]] = []  # (key, message)
        self.deletes: list[tuple[str, str | None]] = []  # (key, message)

    def write(self, key: str, text: str, *, message: str | None = None) -> None:
        super().write(key, text, message=message)
        self.writes.append((key, message))

    def delete(self, key: str, *, message: str | None = None) -> None:
        super().delete(key, message=message)
        self.deletes.append((key, message))


NOW = "2026-06-08T12:00:00+00:00"
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

    def test_branch_round_trips(self):
        t = Task(id="x", title="t", project="p", branch="fix/login")
        back = Task.from_markdown(t.to_markdown())
        assert back.branch == "fix/login"

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
            "id: x\ntitle: t\nproject: p\ncommand: \"\"\nmode: normal\n"
            "parent: \"\"\nfollows: only-one\ncreated: c\nupdated: u\n"
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


# --- id allocation ---


class TestIdAllocation:
    def test_orphan_first_id(self):
        store = InMemoryStore()
        assert model.allocate_orphan_id(store, "p", today=TODAY) == "2026-06-08.1"

    def test_orphan_increments(self):
        store = InMemoryStore()
        model.create(store, project="p", title="a", now=NOW, today=TODAY)
        model.create(store, project="p", title="b", now=NOW, today=TODAY)
        assert model.allocate_orphan_id(store, "p", today=TODAY) == "2026-06-08.3"

    def test_orphan_counts_across_statuses(self):
        store = InMemoryStore()
        t = model.create(store, project="p", title="a", now=NOW, today=TODAY)
        model.move(store, "p", t.id, "done", now=NOW)
        # The done task still counts toward the next counter.
        assert model.allocate_orphan_id(store, "p", today=TODAY) == "2026-06-08.2"

    def test_child_id(self):
        store = InMemoryStore()
        parent = model.create(store, project="p", title="a", now=NOW, today=TODAY)
        child = model.create(
            store, project="p", title="b", parent=parent.id, now=NOW
        )
        assert child.id == f"{parent.id}.1"

    def test_nested_child_counters_independent(self):
        store = InMemoryStore()
        p = model.create(store, project="p", title="a", now=NOW, today=TODAY)
        c1 = model.create(store, project="p", title="b", parent=p.id, now=NOW)
        c2 = model.create(store, project="p", title="c", parent=p.id, now=NOW)
        assert c2.id == f"{p.id}.2"
        # A grandchild under c1 starts its own counter at 1.
        gc = model.create(store, project="p", title="d", parent=c1.id, now=NOW)
        assert gc.id == f"{c1.id}.1"
        # Adding the grandchild did not bump the direct-child counter.
        c3 = model.create(store, project="p", title="e", parent=p.id, now=NOW)
        assert c3.id == f"{p.id}.3"

    def test_linear_virtual_parent_first_child(self):
        store = InMemoryStore()
        child = model.create(
            store, project="p", title="b", parent="linear.NORT-123", now=NOW
        )
        assert child.id == "linear.NORT-123.1"


# --- follow_end_leaves ---


class TestFollowEndLeaves:
    def test_no_followers_returns_self(self):
        store = InMemoryStore()
        a = model.create(store, project="p", title="a", now=NOW, today=TODAY)
        assert model.follow_end_leaves(store, "p", a.id) == [a.id]

    def test_linear_chain(self):
        store = InMemoryStore()
        a = model.create(store, project="p", title="a", now=NOW, today=TODAY)
        b = model.create(store, project="p", title="b", follows=[a.id], now=NOW, today=TODAY)
        c = model.create(store, project="p", title="c", follows=[b.id], now=NOW, today=TODAY)
        assert model.follow_end_leaves(store, "p", a.id) == [c.id]

    def test_branched_chain(self):
        store = InMemoryStore()
        a = model.create(store, project="p", title="a", now=NOW, today=TODAY)
        b = model.create(store, project="p", title="b", follows=[a.id], now=NOW, today=TODAY)
        c = model.create(store, project="p", title="c", follows=[a.id], now=NOW, today=TODAY)
        assert model.follow_end_leaves(store, "p", a.id) == sorted([b.id, c.id])

    def test_cycle_safe(self):
        # Construct a cycle manually: a follows b, b follows a.
        store = InMemoryStore()
        a = Task(id="a", title="a", project="p", follows=["b"], created=NOW, updated=NOW)
        b = Task(id="b", title="b", project="p", follows=["a"], created=NOW, updated=NOW)
        store.write("p/todo/a.md", a.to_markdown(), message="m")
        store.write("p/todo/b.md", b.to_markdown(), message="m")
        # Should terminate; both nodes are part of the cycle so no leaves emerge.
        result = model.follow_end_leaves(store, "p", "a")
        assert result == []  # cycle, no terminal leaf


# --- is_actionable / terminal ---


class TestActionable:
    def test_no_deps_is_actionable(self):
        store = InMemoryStore()
        a = model.create(store, project="p", title="a", now=NOW, today=TODAY)
        assert model.is_actionable(model.load(store, "p", a.id), store)

    def test_blocked_by_undone_dep(self):
        store = InMemoryStore()
        a = model.create(store, project="p", title="a", now=NOW, today=TODAY)
        b = model.create(store, project="p", title="b", follows=[a.id], now=NOW, today=TODAY)
        assert not model.is_actionable(model.load(store, "p", b.id), store)

    def test_unblocked_when_dep_done(self):
        store = InMemoryStore()
        a = model.create(store, project="p", title="a", now=NOW, today=TODAY)
        b = model.create(store, project="p", title="b", follows=[a.id], now=NOW, today=TODAY)
        model.move(store, "p", a.id, "done", now=NOW)
        assert model.is_actionable(model.load(store, "p", b.id), store)

    def test_terminal_not_actionable(self):
        store = InMemoryStore()
        a = model.create(store, project="p", title="a", now=NOW, today=TODAY)
        model.move(store, "p", a.id, "done", now=NOW)
        assert not model.is_actionable(model.load(store, "p", a.id), store)
        model.move(store, "p", a.id, "cancelled", now=NOW)
        assert not model.is_actionable(model.load(store, "p", a.id), store)


# --- status moves ---


class TestMove:
    def test_move_relocates_key(self):
        store = InMemoryStore()
        a = model.create(store, project="p", title="a", now=NOW, today=TODAY)
        assert store.exists(f"p/todo/{a.id}.md")
        model.move(store, "p", a.id, "in-progress", now="2026-06-09T00:00:00+00:00")
        assert not store.exists(f"p/todo/{a.id}.md")
        assert store.exists(f"p/in-progress/{a.id}.md")

    def test_move_bumps_updated(self):
        store = InMemoryStore()
        a = model.create(store, project="p", title="a", now=NOW, today=TODAY)
        moved = model.move(store, "p", a.id, "in-progress", now="2026-06-09T00:00:00+00:00")
        assert moved.updated == "2026-06-09T00:00:00+00:00"
        assert moved.created == NOW

    def test_move_invalid_status_rejected(self):
        store = InMemoryStore()
        a = model.create(store, project="p", title="a", now=NOW, today=TODAY)
        with pytest.raises(ValueError):
            model.move(store, "p", a.id, "bogus", now=NOW)

    def test_move_missing_task(self):
        store = InMemoryStore()
        with pytest.raises(KeyError):
            model.move(store, "p", "nope", "in-progress", now=NOW)


# --- is_safe_id ---


class TestSafeId:
    @pytest.mark.parametrize("good", ["a", "2026-06-08.1", "linear.NORT-123.1", "a_b.c-d"])
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

    def test_find_key_rejects_traversal(self):
        store = InMemoryStore()
        with pytest.raises(ValueError):
            model.find_key(store, "p", "../escape")


# --- mutation write/delete counts and messages ---


class TestMutationAccounting:
    def test_create_one_write(self):
        store = RecordingStore()
        t = model.create(store, project="p", title="hi", now=NOW, today=TODAY)
        assert len(store.writes) == 1
        assert len(store.deletes) == 0
        key, message = store.writes[0]
        assert key == f"p/todo/{t.id}.md"
        assert message is not None
        assert t.id in message and "add" in message

    def test_append_log_one_write(self):
        store = RecordingStore()
        t = model.create(store, project="p", title="hi", now=NOW, today=TODAY)
        store.writes.clear()
        model.append_log(store, "p", t.id, "a note", now=NOW)
        assert len(store.writes) == 1
        assert len(store.deletes) == 0
        msg = store.writes[0][1]
        assert msg is not None and t.id in msg

    def test_append_log_records_message(self):
        store = InMemoryStore()
        t = model.create(store, project="p", title="hi", now=NOW, today=TODAY)
        logged = model.append_log(store, "p", t.id, "a note", now=NOW)
        assert "a note" in logged.log
        assert NOW in logged.log

    def test_move_one_write_one_delete(self):
        store = RecordingStore()
        t = model.create(store, project="p", title="hi", now=NOW, today=TODAY)
        store.writes.clear()
        store.deletes.clear()
        model.move(store, "p", t.id, "done", now=NOW)
        assert len(store.writes) == 1
        assert len(store.deletes) == 1
        # The new key/old key reflect the move; the commit subject is now owned by
        # the transaction (asserted at the GitFileStore level), not per-call.
        assert store.writes[0][0] == f"p/done/{t.id}.md"
        assert store.deletes[0][0] == f"p/todo/{t.id}.md"

    def test_move_noop_when_same_status(self):
        store = RecordingStore()
        t = model.create(store, project="p", title="hi", now=NOW, today=TODAY)
        store.writes.clear()
        store.deletes.clear()
        model.move(store, "p", t.id, "todo", now=NOW)
        assert len(store.writes) == 0
        assert len(store.deletes) == 0


# --- delete ---


class TestDelete:
    def test_delete_removes_file(self):
        store = InMemoryStore()
        a = model.create(store, project="p", title="a", now=NOW, today=TODAY)
        model.delete(store, "p", a.id)
        assert not store.exists(f"p/todo/{a.id}.md")
        with pytest.raises(KeyError):
            model.load(store, "p", a.id)

    def test_delete_returns_task(self):
        store = InMemoryStore()
        a = model.create(store, project="p", title="a", now=NOW, today=TODAY)
        deleted = model.delete(store, "p", a.id)
        assert deleted.id == a.id
        assert deleted.title == "a"

    def test_delete_missing_raises(self):
        store = InMemoryStore()
        with pytest.raises(KeyError):
            model.delete(store, "p", "nope")

    def test_delete_finds_task_in_any_status(self):
        store = InMemoryStore()
        a = model.create(store, project="p", title="a", now=NOW, today=TODAY)
        model.move(store, "p", a.id, "in-progress", now=NOW)
        model.delete(store, "p", a.id)
        assert not store.exists(f"p/in-progress/{a.id}.md")

    def test_delete_strips_dep_from_dependent(self):
        store = InMemoryStore()
        a = model.create(store, project="p", title="a", now=NOW, today=TODAY)
        b = model.create(
            store, project="p", title="b", follows=[a.id], now=NOW, today=TODAY
        )
        model.delete(store, "p", a.id)
        assert model.load(store, "p", b.id).follows == []

    def test_delete_keeps_other_deps(self):
        store = InMemoryStore()
        a = model.create(store, project="p", title="a", now=NOW, today=TODAY)
        b = model.create(store, project="p", title="b", now=NOW, today=TODAY)
        c = model.create(
            store, project="p", title="c", follows=[a.id, b.id], now=NOW, today=TODAY
        )
        model.delete(store, "p", a.id)
        assert model.load(store, "p", c.id).follows == [b.id]

    def test_delete_unblocks_dependent(self):
        store = InMemoryStore()
        a = model.create(store, project="p", title="a", now=NOW, today=TODAY)
        b = model.create(
            store, project="p", title="b", follows=[a.id], now=NOW, today=TODAY
        )
        # b is blocked while a (undone) exists; deleting a makes b actionable.
        assert not model.is_actionable(model.load(store, "p", b.id), store)
        model.delete(store, "p", a.id)
        assert model.is_actionable(model.load(store, "p", b.id), store)

    def test_delete_ignores_terminal_dependents(self):
        # A done task that follows the deleted id is left untouched.
        store = InMemoryStore()
        a = model.create(store, project="p", title="a", now=NOW, today=TODAY)
        b = model.create(
            store, project="p", title="b", follows=[a.id], now=NOW, today=TODAY
        )
        model.move(store, "p", b.id, "done", now=NOW)
        model.delete(store, "p", a.id)
        # b is terminal; its historical follows is preserved.
        assert model.load(store, "p", b.id).follows == [a.id]

    def test_delete_mutation_accounting(self):
        # One delete for the task, plus one write per non-terminal dependent.
        store = RecordingStore()
        a = model.create(store, project="p", title="a", now=NOW, today=TODAY)
        model.create(store, project="p", title="b", follows=[a.id], now=NOW, today=TODAY)
        model.create(store, project="p", title="c", follows=[a.id], now=NOW, today=TODAY)
        store.writes.clear()
        store.deletes.clear()
        model.delete(store, "p", a.id)
        assert len(store.deletes) == 1
        # The deleted key carries the id; the commit subject is the transaction's
        # (asserted at the GitFileStore level), so it's no longer passed per-call.
        assert a.id in store.deletes[0][0]
        assert len(store.writes) == 2  # b and c rewritten

    def test_delete_no_dependents_no_extra_writes(self):
        store = RecordingStore()
        a = model.create(store, project="p", title="a", now=NOW, today=TODAY)
        model.create(store, project="p", title="b", now=NOW, today=TODAY)
        store.writes.clear()
        store.deletes.clear()
        model.delete(store, "p", a.id)
        assert len(store.deletes) == 1
        assert len(store.writes) == 0


# --- load / list ---


class TestLoadList:
    def test_load_round_trip(self):
        store = InMemoryStore()
        t = model.create(
            store, project="p", title="hi", command="claude", now=NOW, today=TODAY
        )
        loaded = model.load(store, "p", t.id)
        assert loaded.id == t.id
        assert loaded.title == "hi"
        assert loaded.command == "claude"
        assert loaded.status == "todo"

    def test_load_missing(self):
        store = InMemoryStore()
        with pytest.raises(KeyError):
            model.load(store, "p", "nope")

    def test_list_filters_by_status(self):
        store = InMemoryStore()
        a = model.create(store, project="p", title="a", now=NOW, today=TODAY)
        model.create(store, project="p", title="b", now=NOW, today=TODAY)
        model.move(store, "p", a.id, "done", now=NOW)
        todo = model.list_tasks(store, project="p", status="todo")
        done = model.list_tasks(store, project="p", status="done")
        assert [t.id for t in todo] == [t.id for t in todo if t.status == "todo"]
        assert len(todo) == 1
        assert len(done) == 1
        assert done[0].id == a.id

    def test_list_filters_by_parent(self):
        store = InMemoryStore()
        p = model.create(store, project="p", title="a", now=NOW, today=TODAY)
        model.create(store, project="p", title="b", parent=p.id, now=NOW)
        model.create(store, project="p", title="c", now=NOW, today=TODAY)
        children = model.list_tasks(store, project="p", parent=p.id)
        assert len(children) == 1
        assert children[0].parent == p.id

    def test_list_does_not_leak_across_projects(self):
        store = InMemoryStore()
        model.create(store, project="p", title="a", now=NOW, today=TODAY)
        model.create(store, project="other", title="b", now=NOW, today=TODAY)
        assert len(model.list_tasks(store, project="p")) == 1


# --- branch defaulting on create ---


class TestBranchDefault:
    def test_branch_defaults_to_task_slash_id(self):
        store = InMemoryStore()
        t = model.create(store, project="p", title="a", now=NOW, today=TODAY)
        assert t.branch == f"task/{t.id}"
        assert t.branch == model.default_branch(t.id)
        assert model.load(store, "p", t.id).branch == f"task/{t.id}"

    def test_branch_override_respected(self):
        store = InMemoryStore()
        t = model.create(
            store, project="p", title="a", branch="fix/login", now=NOW, today=TODAY
        )
        assert t.branch == "fix/login"
        assert model.load(store, "p", t.id).branch == "fix/login"


# --- build_prompt ---


class TestBuildPrompt:
    def test_command_title_and_content(self):
        t = Task(id="x", title="Do thing", project="p", command="plan-task",
                 content="Details here.")
        assert model.build_prompt(t) == "/plan-task Do thing\n\nDetails here."

    def test_no_command_omits_leading_space(self):
        t = Task(id="x", title="Do thing", project="p", content="Details.")
        assert model.build_prompt(t) == "Do thing\n\nDetails."

    def test_no_content_omits_trailing_block(self):
        t = Task(id="x", title="Do thing", project="p", command="plan-task")
        assert model.build_prompt(t) == "/plan-task Do thing"

    def test_title_only(self):
        t = Task(id="x", title="Just a title", project="p")
        assert model.build_prompt(t) == "Just a title"


# --- _permission_mode_for ---


class TestPermissionMode:
    def test_plan_maps_to_plan(self):
        assert model._permission_mode_for("plan") == "plan"

    def test_normal_maps_to_none(self):
        assert model._permission_mode_for("normal") is None

    def test_unknown_maps_to_none(self):
        assert model._permission_mode_for("anything-else") is None


# --- next_task ---


class TestNextTask:
    def test_none_when_empty(self):
        store = InMemoryStore()
        assert model.next_task(store, "p") is None

    def test_returns_first_actionable_by_id(self):
        store = InMemoryStore()
        a = model.create(store, project="p", title="a", now=NOW, today=TODAY)
        model.create(store, project="p", title="b", now=NOW, today=TODAY)
        assert model.next_task(store, "p").id == a.id

    def test_skips_blocked_by_unfinished_dep(self):
        store = InMemoryStore()
        a = model.create(store, project="p", title="a", now=NOW, today=TODAY)
        b = model.create(
            store, project="p", title="b", follows=[a.id], now=NOW, today=TODAY
        )
        # a is actionable, b is not (follows undone a) -> next is a.
        assert model.next_task(store, "p").id == a.id
        # With a done, b becomes the next actionable.
        model.move(store, "p", a.id, "done", now=NOW)
        assert model.next_task(store, "p").id == b.id

    def test_includes_in_progress(self):
        store = InMemoryStore()
        a = model.create(store, project="p", title="a", now=NOW, today=TODAY)
        model.move(store, "p", a.id, "in-progress", now=NOW)
        # An interrupted (in-progress) task re-surfaces as next.
        assert model.next_task(store, "p").id == a.id

    def test_excludes_terminal(self):
        store = InMemoryStore()
        a = model.create(store, project="p", title="a", now=NOW, today=TODAY)
        model.move(store, "p", a.id, "done", now=NOW)
        assert model.next_task(store, "p") is None

    def test_filters_by_parent(self):
        store = InMemoryStore()
        p = model.create(store, project="p", title="parent", now=NOW, today=TODAY)
        child = model.create(store, project="p", title="child", parent=p.id, now=NOW)
        # Without filter, the (lower-id) parent comes first.
        assert model.next_task(store, "p").id == p.id
        # Filtered to the parent's children, only the child qualifies.
        assert model.next_task(store, "p", parent=p.id).id == child.id
