"""Where Claude Code keeps its per-project data: the slug and the transcript path."""

from pathlib import Path

from maelstrom.claude_paths import (
    claude_transcript_path,
    has_claude_transcript,
    sanitise_path_for_claude,
)


class TestSanitisePathForClaude:
    """Tests for sanitise_path_for_claude."""

    def test_basic_path(self):
        result = sanitise_path_for_claude(Path("/Users/sminnee/Projects/foo"))
        assert result == "-Users-sminnee-Projects-foo"

    def test_worktree_path(self):
        result = sanitise_path_for_claude(Path("/Users/sminnee/Projects/foo/foo-alpha"))
        assert result == "-Users-sminnee-Projects-foo-foo-alpha"

    def test_collapses_dot_like_claude(self):
        # Claude's own slug replaces '.' with '-' too, so a real temp path like
        # /private/tmp/claude.501/... must match. Pinning this locks the fix
        # against regressing back to a '/'-only replacement.
        result = sanitise_path_for_claude(Path("/private/tmp/claude.501/x"))
        assert result == "-private-tmp-claude-501-x"


class TestClaudeTranscript:
    """Tests for claude_transcript_path / has_claude_transcript."""

    def test_transcript_path_uses_sanitised_slug(self, tmp_path):
        worktree = Path("/Users/sminnee/Projects/foo/foo-alpha")
        path = claude_transcript_path(worktree, "sid-1", home=tmp_path)
        assert path == (
            tmp_path
            / ".claude"
            / "projects"
            / "-Users-sminnee-Projects-foo-foo-alpha"
            / "sid-1.jsonl"
        )

    def test_has_transcript_true_when_file_exists(self, tmp_path):
        worktree = Path("/Users/sminnee/Projects/foo/foo-alpha")
        transcript = claude_transcript_path(worktree, "sid-1", home=tmp_path)
        transcript.parent.mkdir(parents=True)
        transcript.write_text("{}\n")
        assert has_claude_transcript(worktree, "sid-1", home=tmp_path) is True

    def test_has_transcript_false_when_absent(self, tmp_path):
        worktree = Path("/Users/sminnee/Projects/foo/foo-alpha")
        assert has_claude_transcript(worktree, "never-run", home=tmp_path) is False

    def test_has_transcript_distinguishes_session_ids(self, tmp_path):
        worktree = Path("/Users/sminnee/Projects/foo/foo-alpha")
        ran = claude_transcript_path(worktree, "ran", home=tmp_path)
        ran.parent.mkdir(parents=True)
        ran.write_text("{}\n")
        assert has_claude_transcript(worktree, "ran", home=tmp_path) is True
        assert has_claude_transcript(worktree, "other", home=tmp_path) is False
