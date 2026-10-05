"""The dev certificate: one tailnet certificate for the dev host.

``fake_tailscale`` puts a stand-in on ``PATH`` that records its argv, so the
test sees the command maelstrom runs.
"""

import stat
from pathlib import Path

import pytest

from mael_domain.dev_cert import DevCertError, ensure_dev_cert

HOST = "desk.tailnet.ts.net"


@pytest.fixture(autouse=True)
def home(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", lambda: tmp_path / "home")
    return tmp_path / "home"


def test_ensure_dev_cert_renews_into_the_maelstrom_dir(home, fake_tailscale):
    """Runs ``tailscale cert`` with ``--min-validity`` into
    ``~/.maelstrom/certs``, and leaves the key 0600."""
    cert, key = ensure_dev_cert(HOST)

    certs = home / ".maelstrom" / "certs"
    assert (cert, key) == (certs / f"{HOST}.crt", certs / f"{HOST}.key")
    assert fake_tailscale.read_text().split() == [
        "cert",
        "--min-validity",
        "720h",
        "--cert-file",
        str(cert),
        "--key-file",
        str(key),
        HOST,
    ]
    assert cert.read_text() == "CERT\n"
    assert stat.S_IMODE(key.stat().st_mode) == 0o600


@pytest.mark.usefixtures("fake_tailscale")
def test_a_refused_certificate_names_the_console_setting(monkeypatch):
    monkeypatch.setenv("REFUSE", "1")

    with pytest.raises(DevCertError) as err:
        ensure_dev_cert(HOST)

    assert "does not support getting TLS certs" in str(err.value)
    assert "HTTPS Certificates" in str(err.value)


def test_a_missing_tailscale_is_a_dev_cert_error(tmp_path, monkeypatch):
    monkeypatch.setenv("PATH", str(tmp_path / "empty"))

    with pytest.raises(DevCertError, match="tailscale"):
        ensure_dev_cert(HOST)
