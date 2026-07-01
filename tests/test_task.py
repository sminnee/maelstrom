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


# --- child_chain_leaves ---


class TestChildChainLeaves:
    def test_no_children_is_empty(self):
        store = InMemoryStore()
        assert model.child_chain_leaves(store, "p", "linear.X") == []

    def test_single_child_is_leaf(self):
        store = InMemoryStore()
        a = model.create(store, project="p", title="a", parent="linear.X", now=NOW)
        assert model.child_chain_leaves(store, "p", "linear.X") == [a.id]

    def test_chained_children_only_tail_is_leaf(self):
        store = InMemoryStore()
        a = model.create(store, project="p", title="a", parent="linear.X", now=NOW)
        b = model.create(
            store, project="p", title="b", parent="linear.X", follows=[a.id], now=NOW
        )
        # b follows a, so only b is the end of the sibling chain.
        assert model.child_chain_leaves(store, "p", "linear.X") == [b.id]

    def test_branched_children_multiple_leaves(self):
        store = InMemoryStore()
        a = model.create(store, project="p", title="a", parent="linear.X", now=NOW)
        b = model.create(
            store, project="p", title="b", parent="linear.X", follows=[a.id], now=NOW
        )
        c = model.create(
            store, project="p", title="c", parent="linear.X", follows=[a.id], now=NOW
        )
        # b and c both follow a; neither is followed -> both are leaves.
        assert model.child_chain_leaves(store, "p", "linear.X") == sorted([b.id, c.id])

    def test_ignores_other_parents(self):
        store = InMemoryStore()
        mine = model.create(store, project="p", title="m", parent="linear.X", now=NOW)
        model.create(store, project="p", title="other", parent="linear.Y", now=NOW)
        assert model.child_chain_leaves(store, "p", "linear.X") == [mine.id]


# --- next_follower / running_follower ---


class TestNextFollower:
    def test_linear_chain_returns_follower(self):
        store = InMemoryStore()
        a = model.create(store, project="p", title="a", now=NOW, today=TODAY)
        b = model.create(store, project="p", title="b", follows=[a.id], now=NOW, today=TODAY)
        model.move(store, "p", a.id, model.STATUS_DONE, now=NOW2)
        nxt = model.next_follower(store, "p", a.id)
        assert nxt is not None and nxt.id == b.id

    def test_follower_not_actionable_returns_none(self):
        # b follows a and a second dep that is still todo -> not actionable.
        store = InMemoryStore()
        a = model.create(store, project="p", title="a", now=NOW, today=TODAY)
        dep2 = model.create(store, project="p", title="dep2", now=NOW, today=TODAY)
        model.create(store, project="p", title="b", follows=[a.id, dep2.id], now=NOW, today=TODAY)
        model.move(store, "p", a.id, model.STATUS_DONE, now=NOW2)
        assert model.next_follower(store, "p", a.id) is None

    def test_nothing_follows_returns_none(self):
        store = InMemoryStore()
        a = model.create(store, project="p", title="a", now=NOW, today=TODAY)
        model.create(store, project="p", title="b", now=NOW, today=TODAY)
        model.move(store, "p", a.id, model.STATUS_DONE, now=NOW2)
        assert model.next_follower(store, "p", a.id) is None

    def test_branching_returns_id_sorted_first(self):
        store = InMemoryStore()
        a = model.create(store, project="p", title="a", now=NOW, today=TODAY)
        b = model.create(store, project="p", title="b", follows=[a.id], now=NOW, today=TODAY)
        c = model.create(store, project="p", title="c", follows=[a.id], now=NOW, today=TODAY)
        model.move(store, "p", a.id, model.STATUS_DONE, now=NOW2)
        nxt = model.next_follower(store, "p", a.id)
        assert nxt is not None and nxt.id == sorted([b.id, c.id])[0]

    def test_non_todo_follower_excluded(self):
        # A follower already in-progress is not a todo, so next_follower skips it.
        store = InMemoryStore()
        a = model.create(store, project="p", title="a", now=NOW, today=TODAY)
        b = model.create(store, project="p", title="b", follows=[a.id], now=NOW, today=TODAY)
        model.move(store, "p", a.id, model.STATUS_DONE, now=NOW2)
        model.move(store, "p", b.id, model.STATUS_IN_PROGRESS, now=NOW2)
        assert model.next_follower(store, "p", a.id) is None


class TestRunningFollower:
    def test_returns_in_progress_follower(self):
        store = InMemoryStore()
        a = model.create(store, project="p", title="a", now=NOW, today=TODAY)
        b = model.create(store, project="p", title="b", follows=[a.id], now=NOW, today=TODAY)
        model.move(store, "p", a.id, model.STATUS_DONE, now=NOW2)
        model.move(store, "p", b.id, model.STATUS_IN_PROGRESS, now=NOW2)
        running = model.running_follower(store, "p", a.id)
        assert running is not None and running.id == b.id

    def test_todo_follower_not_returned(self):
        store = InMemoryStore()
        a = model.create(store, project="p", title="a", now=NOW, today=TODAY)
        model.create(store, project="p", title="b", follows=[a.id], now=NOW, today=TODAY)
        model.move(store, "p", a.id, model.STATUS_DONE, now=NOW2)
        assert model.running_follower(store, "p", a.id) is None

    def test_unrelated_in_progress_not_returned(self):
        store = InMemoryStore()
        a = model.create(store, project="p", title="a", now=NOW, today=TODAY)
        other = model.create(store, project="p", title="other", now=NOW, today=TODAY)
        model.move(store, "p", a.id, model.STATUS_DONE, now=NOW2)
        model.move(store, "p", other.id, model.STATUS_IN_PROGRESS, now=NOW2)
        assert model.running_follower(store, "p", a.id) is None

    def test_branching_returns_id_sorted_first(self):
        store = InMemoryStore()
        a = model.create(store, project="p", title="a", now=NOW, today=TODAY)
        b = model.create(store, project="p", title="b", follows=[a.id], now=NOW, today=TODAY)
        c = model.create(store, project="p", title="c", follows=[a.id], now=NOW, today=TODAY)
        model.move(store, "p", a.id, model.STATUS_DONE, now=NOW2)
        model.move(store, "p", b.id, model.STATUS_IN_PROGRESS, now=NOW2)
        model.move(store, "p", c.id, model.STATUS_IN_PROGRESS, now=NOW2)
        running = model.running_follower(store, "p", a.id)
        assert running is not None and running.id == sorted([b.id, c.id])[0]


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


# --- rename ---


class TestRename:
    def test_rename_relocates_file_key(self):
        store = InMemoryStore()
        a = model.create(store, project="p", title="a", now=NOW, today=TODAY)
        renamed = model.rename(store, "p", a.id, "new-id")
        assert not store.exists(f"p/todo/{a.id}.md")
        assert store.exists("p/todo/new-id.md")
        loaded = model.load(store, "p", "new-id")
        assert loaded.id == "new-id"
        assert renamed.id == "new-id"

    def test_rename_preserves_status_content_log(self):
        store = InMemoryStore()
        a = model.create(
            store, project="p", title="a", content="body text", now=NOW, today=TODAY
        )
        model.move(store, "p", a.id, "in-progress", now=NOW)
        model.append_log(store, "p", a.id, "did a thing", now=NOW)
        model.rename(store, "p", a.id, "new-id")
        loaded = model.load(store, "p", "new-id")
        assert loaded.status == "in-progress"
        assert loaded.content.strip() == "body text"
        assert "did a thing" in loaded.log
        assert store.exists("p/in-progress/new-id.md")

    def test_rename_bumps_updated(self):
        store = InMemoryStore()
        a = model.create(store, project="p", title="a", now=NOW, today=TODAY)
        later = "2026-07-02T00:00:00Z"
        model.rename(store, "p", a.id, "new-id", now=later)
        assert model.load(store, "p", "new-id").updated == later

    def test_rename_rewrites_dependent_follows(self):
        store = InMemoryStore()
        a = model.create(store, project="p", title="a", now=NOW, today=TODAY)
        b = model.create(
            store, project="p", title="b", follows=[a.id], now=NOW, today=TODAY
        )
        model.rename(store, "p", a.id, "new-id")
        assert model.load(store, "p", b.id).follows == ["new-id"]

    def test_rename_keeps_other_follows_entries(self):
        store = InMemoryStore()
        a = model.create(store, project="p", title="a", now=NOW, today=TODAY)
        b = model.create(store, project="p", title="b", now=NOW, today=TODAY)
        c = model.create(
            store, project="p", title="c", follows=[a.id, b.id], now=NOW, today=TODAY
        )
        model.rename(store, "p", a.id, "new-id")
        assert model.load(store, "p", c.id).follows == ["new-id", b.id]

    def test_rename_reparents_child(self):
        store = InMemoryStore()
        parent = model.create(store, project="p", title="parent", now=NOW, today=TODAY)
        child = model.create(
            store, project="p", title="child", parent=parent.id, now=NOW, today=TODAY
        )
        model.rename(store, "p", parent.id, "new-parent")
        loaded = model.load(store, "p", child.id)
        assert loaded.parent == "new-parent"
        # Child's own id is NOT cascaded.
        assert loaded.id == child.id

    def test_rename_ignores_terminal_dependents(self):
        store = InMemoryStore()
        a = model.create(store, project="p", title="a", now=NOW, today=TODAY)
        b = model.create(
            store, project="p", title="b", follows=[a.id], now=NOW, today=TODAY
        )
        model.move(store, "p", b.id, "done", now=NOW)
        model.rename(store, "p", a.id, "new-id")
        # b is terminal; its historical follows is preserved.
        assert model.load(store, "p", b.id).follows == [a.id]

    def test_rename_missing_raises_keyerror(self):
        store = InMemoryStore()
        with pytest.raises(KeyError):
            model.rename(store, "p", "nope", "new-id")

    def test_rename_unsafe_new_id_raises_valueerror(self):
        store = InMemoryStore()
        a = model.create(store, project="p", title="a", now=NOW, today=TODAY)
        with pytest.raises(ValueError):
            model.rename(store, "p", a.id, "../escape")

    def test_rename_collision_raises_valueerror(self):
        store = InMemoryStore()
        a = model.create(store, project="p", title="a", now=NOW, today=TODAY)
        b = model.create(store, project="p", title="b", now=NOW, today=TODAY)
        with pytest.raises(ValueError):
            model.rename(store, "p", a.id, b.id)

    def test_rename_same_id_is_noop(self):
        store = RecordingStore()
        a = model.create(store, project="p", title="a", now=NOW, today=TODAY)
        store.writes.clear()
        store.deletes.clear()
        result = model.rename(store, "p", a.id, a.id)
        assert result.id == a.id
        assert len(store.writes) == 0
        assert len(store.deletes) == 0

    def test_rename_mutation_accounting(self):
        # One write (new key) + one delete (old key) + one write per changed dependent.
        store = RecordingStore()
        a = model.create(store, project="p", title="a", now=NOW, today=TODAY)
        model.create(store, project="p", title="b", follows=[a.id], now=NOW, today=TODAY)
        child = model.create(
            store, project="p", title="c", parent=a.id, now=NOW, today=TODAY
        )
        store.writes.clear()
        store.deletes.clear()
        model.rename(store, "p", a.id, "new-id")
        assert len(store.deletes) == 1
        assert a.id in store.deletes[0][0]
        # 1 write for the relocated task + b (follows) + child (parent) = 3.
        assert len(store.writes) == 3
        # Sanity: the child kept its own id but got re-parented.
        assert model.load(store, "p", child.id).parent == "new-id"


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
    # The conftest autouse fixture forces branch generation down the
    # deterministic fallback (the ``claude`` CLI is blocked in tests), so these
    # assertions cover the offline shapes. Model-path generation is covered in
    # ``test_branch_name.py`` with an injected fake runner.

    def test_branch_defaults_to_generated_slug(self):
        # With the model call failing (autouse fixture), an orphan task falls
        # back to ``<default_type>/<slugify(title)>``.
        store = InMemoryStore()
        t = model.create(
            store, project="p", title="Fix flaky port test", now=NOW, today=TODAY
        )
        assert t.branch == "feat/fix-flaky-port-test"
        assert model.load(store, "p", t.id).branch == "feat/fix-flaky-port-test"

    def test_branch_override_respected(self):
        store = InMemoryStore()
        t = model.create(
            store, project="p", title="a", branch="fix/login", now=NOW, today=TODAY
        )
        assert t.branch == "fix/login"
        assert model.load(store, "p", t.id).branch == "fix/login"

    def test_linear_parent_yields_feat_number_branch(self):
        # Generic title + failing model call → the new deterministic Linear
        # fallback splices the bare number into the desc (no NORT- prefix).
        store = InMemoryStore()
        t = model.create(
            store, project="p", title="a", parent="linear.NORT-123",
            now=NOW, today=TODAY,
        )
        assert t.branch == "feat/123-a"
        assert model.load(store, "p", t.id).branch == "feat/123-a"

    def test_siblings_under_linear_parent_share_branch(self):
        store = InMemoryStore()
        a = model.create(
            store, project="p", title="a", parent="linear.NORT-123",
            now=NOW, today=TODAY,
        )
        b = model.create(
            store, project="p", title="b", parent="linear.NORT-123",
            now=NOW, today=TODAY,
        )
        # Both fall back to the same number-led branch (one PR per parent).
        assert a.branch == b.branch == "feat/123-a"

    def test_non_linear_parent_siblings_share_task_branch(self):
        store = InMemoryStore()
        a = model.create(
            store, project="p", title="a", parent="2026-06-09.3",
            now=NOW, today=TODAY,
        )
        b = model.create(
            store, project="p", title="b", parent="2026-06-09.3",
            now=NOW, today=TODAY,
        )
        assert a.branch == b.branch == "task/2026-06-09.3"

    def test_branch_override_beats_parent_derivation(self):
        store = InMemoryStore()
        t = model.create(
            store, project="p", title="a", parent="linear.NORT-123",
            branch="fix/login", now=NOW, today=TODAY,
        )
        assert t.branch == "fix/login"

    def test_default_branch_unit_cases(self):
        # Linear parent without a title → the new number-led fallback (no NORT-).
        assert model.default_branch("x", "linear.NORT-123") == "feat/123"
        assert model.default_branch("x", "linear.foo") == "task/linear.foo"
        assert model.default_branch("x", "2026-06-09.3") == "task/2026-06-09.3"
        assert model.default_branch("x") == "task/x"


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

    def test_auto_maps_to_auto(self):
        assert model._permission_mode_for("auto") == "auto"

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

    def test_excludes_in_progress(self):
        store = InMemoryStore()
        a = model.create(store, project="p", title="a", now=NOW, today=TODAY)
        model.move(store, "p", a.id, "in-progress", now=NOW)
        # An in-progress (already-running) task is not re-offered.
        assert model.next_task(store, "p") is None
        # A todo task is still returned alongside an unrelated in-progress one.
        b = model.create(store, project="p", title="b", now=NOW, today=TODAY)
        assert model.next_task(store, "p").id == b.id

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

    def test_branch_match_beats_lower_id_on_other_branch(self):
        store = InMemoryStore()
        # a has the lower id but is on another branch; b is on the wanted one.
        model.create(store, project="p", title="a", branch="other", now=NOW, today=TODAY)
        b = model.create(
            store, project="p", title="b", branch="feat/x", now=NOW, today=TODAY
        )
        assert model.next_task(store, "p", branch="feat/x").id == b.id

    def test_branch_no_match_falls_back_to_global(self):
        store = InMemoryStore()
        a = model.create(
            store, project="p", title="a", branch="other", now=NOW, today=TODAY
        )
        # No actionable task on feat/x -> fall back to the global next (a).
        assert model.next_task(store, "p", branch="feat/x", fallback=True).id == a.id

    def test_branch_no_match_no_fallback_returns_none(self):
        store = InMemoryStore()
        model.create(store, project="p", title="a", branch="other", now=NOW, today=TODAY)
        assert model.next_task(store, "p", branch="feat/x", fallback=False) is None

    def test_branch_none_unchanged(self):
        store = InMemoryStore()
        a = model.create(
            store, project="p", title="a", branch="other", now=NOW, today=TODAY
        )
        model.create(store, project="p", title="b", branch="feat/x", now=NOW, today=TODAY)
        # No branch preference -> first actionable, id-sorted.
        assert model.next_task(store, "p", branch=None).id == a.id


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

    def test_malformed_marker_name_raises(self):
        # A hyphenated name resembles a marker but fails the strict pattern; it
        # must error rather than silently becoming prose.
        text = "---CREATE TASK iter-1---\ntitle: A\n---\nbody\n"
        with pytest.raises(ValueError, match="Malformed task marker"):
            model.parse_task_blocks(text)

    def test_escaped_wildcard_follow_end_tolerated(self):
        # `follow-end: "\*"` is invalid YAML (bad escape) but the canonical form
        # is `"*"`; we salvage it and warn rather than hard-fail.
        text = (
            "---CREATE TASK a---\n"
            'title: A\n'
            'follow-end: "\\*"\n'
            "---\n"
            "body\n"
        )
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
        assert blocks[0]["args"]["title"] == "Execute: ... (view + unlink + attach file)"
        assert blocks[0]["args"]["follow-end"] == "*"
        assert warnings


# --- load_many ---


class TestLoadMany:
    def test_intra_file_follow_resolves_to_allocated_id(self):
        store = InMemoryStore()
        blocks = [
            {"name": "a", "args": {"title": "A"}, "content": "ca"},
            {"name": "b", "args": {"title": "B", "follow": "a"}, "content": "cb"},
        ]
        created = model.load_many(store, project="p", blocks=blocks, now=NOW, today=TODAY)
        assert len(created) == 2
        a, b = created
        # B's follows points at A's allocated id, not the block name "a".
        assert b.follows == [a.id]
        assert "a" not in b.follows

    def test_follow_end_resolves_against_live_store(self):
        store = InMemoryStore()
        seed = model.create(store, project="p", title="seed", now=NOW, today=TODAY)
        blocks = [
            {"name": "x", "args": {"title": "X", "follow-end": seed.id}, "content": ""},
        ]
        created = model.load_many(store, project="p", blocks=blocks, now=NOW, today=TODAY)
        assert created[0].follows == [seed.id]

    def test_block_actions_are_applied(self):
        store = InMemoryStore()
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
        created = model.load_many(
            store, project="p", blocks=blocks, now=NOW, today=TODAY
        )
        assert created[0].pre_action == "linear.in-progress"
        assert created[0].post_action == "linear.done"

    def test_block_actions_default_empty(self):
        store = InMemoryStore()
        blocks = [{"name": "a", "args": {"title": "A"}, "content": ""}]
        created = model.load_many(
            store, project="p", blocks=blocks, now=NOW, today=TODAY
        )
        assert created[0].pre_action == ""
        assert created[0].post_action == ""

    def test_block_mode_is_honored_and_defaults_to_plan(self):
        store = InMemoryStore()
        blocks = [
            {"name": "exec", "args": {"title": "E", "mode": "normal"}, "content": ""},
            {"name": "plan", "args": {"title": "P"}, "content": ""},
        ]
        created = model.load_many(store, project="p", blocks=blocks, now=NOW, today=TODAY)
        by_title = {t.title: t for t in created}
        assert by_title["E"].mode == "normal"          # explicit wins
        assert by_title["P"].mode == model.DEFAULT_MODE  # omitted falls through to plan

    def test_passthrough_real_id_follow(self):
        store = InMemoryStore()
        seed = model.create(store, project="p", title="seed", now=NOW, today=TODAY)
        blocks = [
            {"name": "x", "args": {"title": "X", "follow": seed.id}, "content": ""},
        ]
        created = model.load_many(store, project="p", blocks=blocks, now=NOW, today=TODAY)
        assert created[0].follows == [seed.id]

    def test_child_id_allocation_increments_across_batch(self):
        store = InMemoryStore()
        blocks = [
            {"name": "a", "args": {"title": "A", "parent": "linear.X"}, "content": ""},
            {"name": "b", "args": {"title": "B", "parent": "linear.X"}, "content": ""},
        ]
        created = model.load_many(store, project="p", blocks=blocks, now=NOW, today=TODAY)
        ids = [t.id for t in created]
        assert ids == ["linear.X.1", "linear.X.2"]

    def test_follow_list_value(self):
        # A list-valued follow with one block-name and one real id.
        store = InMemoryStore()
        seed = model.create(store, project="p", title="seed", now=NOW, today=TODAY)
        blocks = [
            {"name": "a", "args": {"title": "A"}, "content": ""},
            {"name": "b", "args": {"title": "B", "follow": ["a", seed.id]}, "content": ""},
        ]
        created = model.load_many(store, project="p", blocks=blocks, now=NOW, today=TODAY)
        a, b = created
        assert b.follows == [a.id, seed.id]

    def test_default_parent_applied_when_block_omits_parent(self):
        store = InMemoryStore()
        blocks = [{"name": "a", "args": {"title": "A"}, "content": ""}]
        created = model.load_many(
            store, project="p", blocks=blocks, default_parent="linear.X", now=NOW
        )
        assert created[0].parent == "linear.X"
        assert created[0].id == "linear.X.1"  # nested child id

    def test_block_parent_overrides_default(self):
        store = InMemoryStore()
        blocks = [
            {"name": "a", "args": {"title": "A", "parent": "linear.Y"}, "content": ""}
        ]
        created = model.load_many(
            store, project="p", blocks=blocks, default_parent="linear.X", now=NOW
        )
        assert created[0].parent == "linear.Y"

    def test_follow_end_wildcard_appends_to_sibling_chain(self):
        # An existing child of linear.X; a new block with follow-end: * should
        # follow it (the end of the parent's child-chain).
        store = InMemoryStore()
        existing = model.create(
            store, project="p", title="existing", parent="linear.X", now=NOW
        )
        blocks = [
            {"name": "step", "args": {"title": "Step", "follow-end": "*"}, "content": ""},
        ]
        created = model.load_many(
            store, project="p", blocks=blocks, default_parent="linear.X", now=NOW
        )
        assert created[0].follows == [existing.id]

    def test_follow_end_wildcard_empty_when_first_child(self):
        store = InMemoryStore()
        blocks = [
            {"name": "step", "args": {"title": "Step", "follow-end": "*"}, "content": ""},
        ]
        created = model.load_many(
            store, project="p", blocks=blocks, default_parent="linear.X", now=NOW
        )
        # No existing siblings -> nothing to follow.
        assert created[0].follows == []

    def test_wildcard_and_intra_file_follow_combine(self):
        # step: follow-end:* (appends after existing sibling); tail: follow:step.
        store = InMemoryStore()
        existing = model.create(
            store, project="p", title="existing", parent="linear.X", now=NOW
        )
        blocks = [
            {"name": "step", "args": {"title": "Step", "follow-end": "*"}, "content": ""},
            {"name": "tail", "args": {"title": "Tail", "follow": "step"}, "content": ""},
        ]
        created = model.load_many(
            store, project="p", blocks=blocks, default_parent="linear.X", now=NOW
        )
        step, tail = created
        assert step.follows == [existing.id]
        assert tail.follows == [step.id]
        # The wildcard for `tail` would have seen `step` as a new sibling leaf,
        # but `tail` uses intra-file `follow`, so it chains off step directly.


# --- update() ---


class TestUpdate:
    def test_update_changes_fields_and_bumps_updated(self):
        store = InMemoryStore()
        t = model.create(store, project="p", title="old", now=NOW)
        updated = model.update(
            store, "p", t.id, title="new", branch="feat/x", content="body", now=NOW2
        )
        assert updated.title == "new"
        assert updated.branch == "feat/x"
        assert updated.content == "body"
        assert updated.updated == NOW2
        reloaded = model.load(store, "p", t.id)
        assert reloaded.title == "new"
        assert reloaded.branch == "feat/x"
        assert reloaded.content == "body"

    def test_update_leaves_omitted_fields_untouched(self):
        store = InMemoryStore()
        t = model.create(
            store, project="p", title="keep", branch="b", content="body", now=NOW
        )
        model.update(store, "p", t.id, branch="b2", now=NOW2)
        reloaded = model.load(store, "p", t.id)
        assert reloaded.title == "keep"
        assert reloaded.content == "body"
        assert reloaded.branch == "b2"

    def test_update_changes_command_and_mode(self):
        store = InMemoryStore()
        t = model.create(
            store, project="p", title="t", command="plan-task", mode="plan", now=NOW
        )
        model.update(store, "p", t.id, command="execute", mode="normal", now=NOW2)
        reloaded = model.load(store, "p", t.id)
        assert reloaded.command == "execute"
        assert reloaded.mode == "normal"

    def test_update_command_to_empty(self):
        store = InMemoryStore()
        t = model.create(store, project="p", title="t", command="plan-task", now=NOW)
        model.update(store, "p", t.id, command="", now=NOW2)
        assert model.load(store, "p", t.id).command == ""

    def test_update_does_not_change_status(self):
        store = InMemoryStore()
        t = model.create(store, project="p", title="t", now=NOW)
        model.move(store, "p", t.id, model.STATUS_IN_PROGRESS, now=NOW)
        model.update(store, "p", t.id, branch="b", now=NOW2)
        assert model.load(store, "p", t.id).status == model.STATUS_IN_PROGRESS

    def test_update_unknown_id_raises(self):
        store = InMemoryStore()
        with pytest.raises(KeyError):
            model.update(store, "p", "nope", branch="x")

    def test_update_single_write(self):
        store = RecordingStore()
        t = model.create(store, project="p", title="t", now=NOW)
        store.writes.clear()
        model.update(store, "p", t.id, branch="b", now=NOW2)
        assert len(store.writes) == 1


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
    def test_changed_save_commits_and_bumps_updated(self, tmp_path):
        from maelstrom.task_store import GitFileStore

        store = GitFileStore(root=tmp_path / "tasks")
        t = model.create(store, project="p", title="orig", now=NOW)
        before_updated = model.load(store, "p", t.id).updated
        # Insert text under the ## Content heading so the edit lands in a
        # section the model parses back, mimicking a real editor change.
        editor = _editor_script(
            tmp_path,
            "p = sys.argv[1]\n"
            "t = open(p).read().replace('## Content\\n', '## Content\\nedited\\n')\n"
            "open(p, 'w').write(t)\n",
        )
        task, changed = model.edit_in_editor(store, "p", t.id, editor=editor)
        assert changed is True
        assert task.updated != before_updated
        # File stays canonical and the edit reached the Content section.
        assert "edited" in model.load(store, "p", t.id).content

    def test_noop_save_writes_nothing(self, tmp_path):
        from maelstrom.task_store import GitFileStore

        store = GitFileStore(root=tmp_path / "tasks")
        t = model.create(store, project="p", title="orig", now=NOW)
        before = model.load(store, "p", t.id)
        editor = _editor_script(tmp_path, "pass")  # no-op: open + quit, no change
        _task, changed = model.edit_in_editor(store, "p", t.id, editor=editor)
        assert changed is False
        after = model.load(store, "p", t.id)
        assert after.updated == before.updated
        assert after.content == before.content

    def test_unknown_id_raises(self, tmp_path):
        from maelstrom.task_store import GitFileStore

        store = GitFileStore(root=tmp_path / "tasks")
        with pytest.raises(KeyError):
            model.edit_in_editor(store, "p", "nope", editor="true")

    def test_missing_editor_raises_runtimeerror(self, tmp_path):
        from maelstrom.task_store import GitFileStore

        store = GitFileStore(root=tmp_path / "tasks")
        t = model.create(store, project="p", title="orig", now=NOW)
        with pytest.raises(RuntimeError):
            model.edit_in_editor(
                store, "p", t.id, editor="definitely-not-an-editor-xyz"
            )

    def test_editor_nonzero_exit_raises_runtimeerror(self, tmp_path):
        from maelstrom.task_store import GitFileStore

        store = GitFileStore(root=tmp_path / "tasks")
        t = model.create(store, project="p", title="orig", now=NOW)
        editor = _editor_script(tmp_path, "sys.exit(1)")
        with pytest.raises(RuntimeError):
            model.edit_in_editor(store, "p", t.id, editor=editor)

    def test_editor_launched_with_inherited_stdio(self, tmp_path, monkeypatch):
        # The editor must inherit the terminal (``stream=True``) so a full-screen
        # editor like ``vi`` can draw its screen; without it ``run_cmd`` captures
        # stdout/stderr into pipes and the editor is unusable. Spy on ``run_cmd``
        # to pin this contract — a non-interactive fake editor can't exercise it.
        from unittest.mock import MagicMock

        from maelstrom.task_store import GitFileStore

        store = GitFileStore(root=tmp_path / "tasks")
        t = model.create(store, project="p", title="orig", now=NOW)
        spy = MagicMock()
        monkeypatch.setattr(model, "run_cmd", spy)
        # The mocked run_cmd leaves the file untouched, so edit_in_editor returns
        # early with changed=False — irrelevant here; we only assert on the spy.
        model.edit_in_editor(store, "p", t.id, editor="some-editor")
        spy.assert_called_once()
        assert spy.call_args.kwargs.get("stream") is True


# --- duplicate + run-id (model-level) ---


class TestDuplicate:
    def test_copies_recipe_into_todo(self):
        store = InMemoryStore()
        src = model.create(
            store, project="p", title="Src", command="plan-task",
            mode="auto", content="body", pre_action="a", post_action="b",
            status=model.STATUS_TEMPLATE, id="tmpl",
        )
        dup = model.duplicate(store, "p", src.id)
        assert dup.id != src.id
        assert dup.status == model.STATUS_TODO
        assert dup.title == "Src"
        assert dup.command == "plan-task"
        assert dup.mode == "auto"
        assert dup.content == "body"
        assert dup.pre_action == "a"
        assert dup.post_action == "b"

    def test_does_not_copy_schedule(self):
        store = InMemoryStore()
        model.create(
            store, project="p", title="T", schedule="0 9 * * *",
            last_run="2026-01-01T00:00:00+00:00",
            status=model.STATUS_TEMPLATE, id="tmpl",
        )
        dup = model.duplicate(store, "p", "tmpl")
        assert dup.schedule == ""
        assert dup.last_run == ""

    def test_overrides_win(self):
        store = InMemoryStore()
        model.create(store, project="p", title="Src", command="c1", id="s")
        dup = model.duplicate(store, "p", "s", title="New", command="c2")
        assert dup.title == "New"
        assert dup.command == "c2"

    def test_source_untouched(self):
        store = InMemoryStore()
        model.create(store, project="p", title="Src", content="x", id="s")
        model.duplicate(store, "p", "s", title="other")
        assert model.load(store, "p", "s").title == "Src"

    def test_run_id_names_run_under_template_but_parent_is_blank(self):
        # A scheduled run is duplicated with parent="" and id=allocate_run_id:
        # the dot-id names/dedups it under the template, while the empty parent
        # lets it root its own chain (see docs/dev/tasks.md).
        store = InMemoryStore()
        model.create(
            store, project="p", title="Maint", status=model.STATUS_TEMPLATE,
            id="maintenance",
        )
        run_id = model.allocate_run_id("maintenance", "2026-06-18")
        assert run_id == "maintenance.2026-06-18"
        dup = model.duplicate(
            store, "p", "maintenance", parent="", id=run_id
        )
        assert dup.id == "maintenance.2026-06-18"
        assert dup.parent == ""
        # With no branch override and no parent, create() generates a descriptive
        # <type>/<desc> branch from the title (an accepted consequence of blanking
        # the parent: each firing gets its own branch/PR — see docs/dev/tasks.md).
        assert dup.branch == model.default_branch(
            run_id, "", title="Maint", generate=True
        )

    def test_branch_override_is_honored(self):
        store = InMemoryStore()
        model.create(
            store, project="p", title="Maint", status=model.STATUS_TEMPLATE,
            id="maintenance",
        )
        run_id = model.allocate_run_id("maintenance", "2026-06-18")
        dup = model.duplicate(
            store, "p", "maintenance", parent="",
            branch="chore/maint", id=run_id,
        )
        assert dup.branch == "chore/maint"


class TestTemplateStatus:
    def test_template_is_not_actionable(self):
        store = InMemoryStore()
        t = model.create(
            store, project="p", title="T", status=model.STATUS_TEMPLATE, id="t"
        )
        assert not model.is_actionable(t, store)

    def test_template_invisible_to_next_task(self):
        store = InMemoryStore()
        model.create(
            store, project="p", title="T", status=model.STATUS_TEMPLATE, id="t"
        )
        assert model.next_task(store, "p") is None

    def test_move_accepts_template(self):
        store = InMemoryStore()
        model.create(store, project="p", title="T", id="t")
        moved = model.move(store, "p", "t", model.STATUS_TEMPLATE)
        assert moved.status == model.STATUS_TEMPLATE

    def test_schedule_round_trips(self):
        store = InMemoryStore()
        model.create(
            store, project="p", title="T", schedule="0 9 * * *",
            last_run="2026-06-18T09:00:00+00:00", id="t",
        )
        reloaded = model.load(store, "p", "t")
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


class TestReconcile:
    def _in_progress(self, store, project, title, **kw):
        t = model.create(store, project=project, title=title, **kw)
        model.move(store, project, t.id, model.STATUS_IN_PROGRESS)
        return t

    def test_ok_row_for_in_progress_with_session(self):
        store = InMemoryStore()
        t = self._in_progress(store, "p", "a", id="t1")
        rows = model.reconcile(
            store, "p", session_task_ids={t.id: {"pid": 1}}
        )
        assert len(rows) == 1
        assert rows[0].state == model.RECONCILE_OK
        assert rows[0].fix_status is None

    def test_stale_in_progress_without_session(self):
        store = InMemoryStore()
        self._in_progress(store, "p", "a", id="t1")
        rows = model.reconcile(store, "p", session_task_ids={})
        assert len(rows) == 1
        assert rows[0].state == model.RECONCILE_STALE
        assert rows[0].fix_status == model.STATUS_DONE

    def test_orphan_session_on_todo_task(self):
        store = InMemoryStore()
        t = model.create(store, project="p", title="a", id="t1")  # stays todo
        rows = model.reconcile(
            store, "p", session_task_ids={t.id: {"pid": 9}}
        )
        assert len(rows) == 1
        assert rows[0].state == model.RECONCILE_ORPHAN
        assert rows[0].fix_status == model.STATUS_IN_PROGRESS

    def test_done_task_with_session_listed_but_not_flipped(self):
        store = InMemoryStore()
        t = model.create(store, project="p", title="a", id="t1")
        model.move(store, "p", t.id, model.STATUS_DONE)
        rows = model.reconcile(
            store, "p", session_task_ids={t.id: {"pid": 9}}
        )
        assert len(rows) == 1
        assert rows[0].state == model.RECONCILE_ORPHAN
        assert rows[0].fix_status is None  # finished window — not a corruption

    def test_missing_task_with_session_not_flipped(self):
        store = InMemoryStore()
        rows = model.reconcile(
            store, "p", session_task_ids={"ghost": {"pid": 9}}
        )
        assert len(rows) == 1
        assert rows[0].state == model.RECONCILE_ORPHAN
        assert rows[0].task_status == "(missing)"
        assert rows[0].fix_status is None

    def test_mixed_rows_sorted_by_task_id(self):
        store = InMemoryStore()
        self._in_progress(store, "p", "a", id="t1")  # stale (no session)
        t2 = self._in_progress(store, "p", "b", id="t2")  # ok
        rows = model.reconcile(
            store, "p", session_task_ids={t2.id: {"pid": 2}}
        )
        assert [r.task_id for r in rows] == ["t1", "t2"]
        assert rows[0].state == model.RECONCILE_STALE
        assert rows[1].state == model.RECONCILE_OK
