"""Driving the ``mael-agent-daemon`` group through the recording transport.

A copy of the root suite's ``agent_cli_support``: the daemon's suite stands alone.
"""

from pathlib import Path

from click import Group
from click.testing import CliRunner, Result

from mael_agent import agent_transport
from mael_agent.agent_transport import RecordingDaemonClient, SocketAsyncDaemonClient


def unreachable(root) -> dict:
    """The reply a client gets when nothing is listening on `root`'s socket.

    Built by the transport rather than written out here: these fakes stand in
    for a real connect failure, and a hand-written copy stops matching the
    moment the reply grows a field.
    """
    socket_path = str(agent_transport.DaemonPaths(Path(root)).socket)
    return agent_transport.connect_failure(
        socket_path, FileNotFoundError(2, "No such file")
    )


def drive(
    group: Group, argv: list[str], replies: list[dict] | None = None
) -> tuple[Result, RecordingDaemonClient]:
    """Invoke ``group`` with ``argv`` against scripted daemon replies.

    Returns the result and the fake client, which records every request and
    the keyword arguments the command built it with — so a test can assert the
    socket a command routed to.
    """
    client = RecordingDaemonClient(replies=list(replies or []))

    def factory(**kwargs):
        for key, value in kwargs.items():
            setattr(client, key, value)
        return client

    agent_transport.client_factory = factory
    try:
        return CliRunner().invoke(group, argv), client
    finally:
        agent_transport.client_factory = SocketAsyncDaemonClient
