"""The dev certificate. See CONTEXT.md, "Dev certificate"."""

import subprocess
from pathlib import Path

from mael_common.shell import run_cmd
from mael_common.util import get_maelstrom_dir, harden_path

from . import context

#: Renew when fewer days than this remain. Let's Encrypt certificates last 90.
_MIN_VALIDITY = "720h"

#: A first issue is an ACME round trip, which can take tens of seconds. A
#: wedged tailscaled must not hang every env start.
_ISSUE_TIMEOUT_SECS = 120

#: The ``.env`` keys that name the dev certificate, present only under HTTPS.
TLS_ENV_VARS = ("DEV_TLS_CERT", "DEV_TLS_KEY")

_CONSOLE_HINT = (
    "Enable MagicDNS and HTTPS Certificates in the Tailscale admin console "
    "(DNS page), or set dev_https: false."
)


class DevCertError(RuntimeError):
    """Tailscale did not issue the dev certificate."""


def cert_paths(host: str) -> tuple[Path, Path]:
    """The certificate and key files for ``host``."""
    certs = get_maelstrom_dir() / "certs"
    return certs / f"{host}.crt", certs / f"{host}.key"


def ensure_dev_cert(host: str) -> tuple[Path, Path]:
    """Issue or renew the certificate for ``host``; return its two paths.

    A no-op while the certificate has 30 days left, so it is safe to run on
    every env start.
    """
    cert, key = cert_paths(host)
    cert.parent.mkdir(parents=True, exist_ok=True)
    argv = [
        "tailscale",
        "cert",
        "--min-validity",
        _MIN_VALIDITY,
        "--cert-file",
        str(cert),
        "--key-file",
        str(key),
        host,
    ]
    try:
        run_cmd(argv, quiet=True, timeout=_ISSUE_TIMEOUT_SECS)
    except subprocess.TimeoutExpired as e:
        raise DevCertError(
            f"tailscale cert {host} did not finish in {_ISSUE_TIMEOUT_SECS}s. "
            "Is tailscaled running?"
        ) from e
    except FileNotFoundError as e:
        raise DevCertError(f"tailscale is not installed. {_CONSOLE_HINT}") from e
    except subprocess.CalledProcessError as e:
        detail = (e.stderr or e.stdout or "").strip()
        raise DevCertError(
            f"tailscale cert {host} failed: {detail}\n{_CONSOLE_HINT}"
        ) from e
    harden_path(key, 0o600)
    return cert, key


def ensure_configured_dev_cert() -> None:
    """Renew the dev certificate when the global config turns HTTPS on."""
    host = context.load_global_config().tls_host
    if host:
        ensure_dev_cert(host)


def dev_env_vars() -> dict[str, str]:
    """The dev host variables every worktree ``.env`` carries.

    ``DEV_HOST`` and ``DEV_SCHEME`` are always written, so a template can use
    them without a guard. The certificate paths are written only under HTTPS,
    so an app tests for their presence.
    """
    config = context.load_global_config()
    env = {"DEV_HOST": config.url_host, "DEV_SCHEME": config.dev_scheme}
    if config.tls_host:
        cert, key = cert_paths(config.tls_host)
        env |= {"DEV_TLS_CERT": str(cert), "DEV_TLS_KEY": str(key)}
    return env
