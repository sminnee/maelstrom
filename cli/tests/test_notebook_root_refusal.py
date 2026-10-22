"""A refused command writes nothing when the notebook root is unset.

The root itself is tested in ``lib/domain/tests/test_notebook_root.py``.
"""

from mael_domain.notebook_root import NOTEBOOK_ROOT_ENV


class TestARefusedCommandWritesNothing:
    """The point of refusing: no write lands anywhere.

    Asserting one absent directory would pass even if the command wrote
    somewhere else entirely, so this compares the whole tree before and after.
    """

    def test_a_refused_log_leaves_the_tree_untouched(
        self, monkeypatch, tmp_path, capsys
    ):
        """`task log` still writes the notebook itself, not through the server."""
        from mael_cli.cli import main

        monkeypatch.delenv(NOTEBOOK_ROOT_ENV, raising=False)
        monkeypatch.setenv("HOME", str(tmp_path))
        before = sorted(p.relative_to(tmp_path) for p in tmp_path.rglob("*"))

        assert main(["task", "log", "--project", "p", "t1", "a line"]) == 1
        assert NOTEBOOK_ROOT_ENV in capsys.readouterr().err

        after = sorted(p.relative_to(tmp_path) for p in tmp_path.rglob("*"))
        assert after == before
