"""The key that signs a Live feedback URL."""

import stat

import pytest

from mael_domain.live_feedback_key import sign, verify
from mael_domain.notebook_root import notebook_root


@pytest.fixture
def notebook():
    return notebook_root()


def test_the_key_is_made_once_and_only_its_owner_reads_it(notebook):
    first = sign("/p", "web")

    assert sign("/p", "web") == first
    key = notebook / "live-feedback.key"
    assert stat.S_IMODE(key.stat().st_mode) == 0o600
    assert len(key.read_bytes()) == 32
    # The draft the key is linked from is gone: a stray copy would be a second secret.
    assert [p.name for p in notebook.iterdir()] == ["live-feedback.key"]


def test_a_signature_verifies_for_its_own_path_and_service_alone():
    sig = sign("/p", "web")

    assert verify("/p", "web", sig)
    assert not verify("/q", "web", sig)
    assert not verify("/p", "ladle", sig)
    assert not verify("/p", "web", "")
    # A caller sends any text: a non-ASCII one is refused, not a TypeError.
    assert not verify("/p", "web", "é")


def test_a_short_key_file_is_refused_rather_than_signed_with(notebook):
    notebook.mkdir(parents=True, exist_ok=True)
    (notebook / "live-feedback.key").write_bytes(b"")

    with pytest.raises(ValueError, match="not a 32-byte key"):
        sign("/p", "web")


def test_another_notebook_has_another_key(notebook, monkeypatch):
    sig = sign("/p", "web")
    monkeypatch.setenv("MAEL_NOTEBOOK_ROOT", str(notebook / "playpen"))

    assert not verify("/p", "web", sig)
