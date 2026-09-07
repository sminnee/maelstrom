"""The registry that decides which files an agent may show the user."""

from maelstrom.orchestrator.file_registry import FileRegistry


def test_a_file_in_the_worktree_registers_and_resolves_back(tmp_path):
    """The path an id resolves to is the file the agent named."""
    (tmp_path / "shot.png").write_bytes(b"\x89PNG\r\n\x1a\n")
    registry = FileRegistry()
    file_id = registry.register("ag1-1", str(tmp_path), "shot.png")
    assert file_id is not None
    assert registry.resolve(file_id) == tmp_path / "shot.png"


def test_a_path_that_escapes_the_worktree_registers_nothing(tmp_path):
    """Absence from the registry is what makes an outside file unreachable."""
    worktree = tmp_path / "wt"
    worktree.mkdir()
    (tmp_path / "secret.env").write_text("KEY=1\n")
    registry = FileRegistry()
    assert registry.register("ag1-1", str(worktree), "../secret.env") is None
    assert (
        registry.register("ag1-2", str(worktree), str(tmp_path / "secret.env")) is None
    )


def test_a_symlink_out_of_the_worktree_registers_nothing(tmp_path):
    """``resolve`` follows the link, so the guard sees where it really lands."""
    worktree = tmp_path / "wt"
    worktree.mkdir()
    (tmp_path / "secret.env").write_text("KEY=1\n")
    (worktree / "link.png").symlink_to(tmp_path / "secret.env")
    registry = FileRegistry()
    assert registry.register("ag1-1", str(worktree), "link.png") is None


def test_an_id_is_looked_up_by_exact_match_never_by_path_join(tmp_path):
    """The lookup is a dict get, so an id with a path appended reaches nothing."""
    (tmp_path / "shot.png").write_bytes(b"\x89PNG\r\n\x1a\n")
    registry = FileRegistry()
    file_id = registry.register("ag1-1", str(tmp_path), "shot.png")
    assert registry.resolve(f"{file_id}/../../etc/passwd") is None
    assert registry.resolve(f"{file_id}x") is None
    assert registry.resolve("") is None


def test_a_file_that_does_not_exist_registers_nothing(tmp_path):
    """A typo in a path is the likeliest mistake, and it must not mint an id.

    An id for a missing file yields a working-looking ref whose fetch 404s —
    a broken picture, which is what the refusal path exists to prevent.
    """
    registry = FileRegistry()
    assert registry.register("ag1-1", str(tmp_path), "typo.png") is None


def test_a_directory_registers_nothing(tmp_path):
    """A directory is inside the worktree and is still not a picture."""
    (tmp_path / "docs").mkdir()
    registry = FileRegistry()
    assert registry.register("ag1-1", str(tmp_path), "docs") is None


def test_an_unregistered_id_resolves_to_nothing():
    assert FileRegistry().resolve("ag1-1-nope.png") is None


def test_registering_one_file_twice_returns_the_same_id(tmp_path):
    """A replayed transcript registers its files again, and must not grow.

    The key is the resolved path, so a second registration of the same file
    reuses the id the first minted rather than minting another.
    """
    (tmp_path / "shot.png").write_bytes(b"\x89PNG\r\n\x1a\n")
    registry = FileRegistry()
    first = registry.register("ag1-1", str(tmp_path), "shot.png")
    second = registry.register("ag1-9", str(tmp_path), "shot.png")
    assert first == second
    assert registry.resolve(first) == tmp_path / "shot.png"


def test_two_spellings_of_one_path_share_an_id(tmp_path):
    """The realpath is the file's identity, whatever route named it."""
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "shot.png").write_bytes(b"\x89PNG\r\n\x1a\n")
    registry = FileRegistry()
    direct = registry.register("ag1-1", str(tmp_path), "docs/shot.png")
    roundabout = registry.register("ag1-2", str(tmp_path), "docs/../docs/shot.png")
    assert direct == roundabout


def test_two_files_sharing_a_basename_get_distinct_ids(tmp_path):
    """The id carries the name for readability, so it must not collide on it."""
    (tmp_path / "a").mkdir()
    (tmp_path / "b").mkdir()
    (tmp_path / "a" / "shot.png").write_bytes(b"\x89PNG\r\n\x1a\n")
    (tmp_path / "b" / "shot.png").write_bytes(b"\x89PNG\r\n\x1a\n")
    registry = FileRegistry()
    first = registry.register("ag1-1", str(tmp_path), "a/shot.png")
    second = registry.register("ag1-2", str(tmp_path), "b/shot.png")
    assert first != second
    assert registry.resolve(first) == tmp_path / "a" / "shot.png"
    assert registry.resolve(second) == tmp_path / "b" / "shot.png"
