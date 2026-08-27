"""Tests for maelstrom.services command synthesis."""

import json

import pytest

from maelstrom.config import ServiceDef
from maelstrom.services import (
    build_command_service,
    build_container_run,
    container_name,
    discover_container_ip,
    volume_name,
)


class TestBuildCommandService:
    """Tests for build_command_service."""

    def test_no_dir(self):
        """Command with no dir is passed through verbatim."""
        svc = ServiceDef(name="worker", command="uv run serve worker")
        assert build_command_service(svc) == "uv run serve worker"

    def test_with_dir(self):
        """A dir is prefixed as `cd <dir> && <command>`."""
        svc = ServiceDef(name="frontend", command="node server.ts", dir="frontend")
        assert build_command_service(svc) == "cd frontend && node server.ts"

    def test_port_var_preserved(self):
        """${VAR} references survive verbatim for later .env substitution."""
        svc = ServiceDef(name="server", command="env PORT=${SERVER_PORT} serve")
        assert build_command_service(svc) == "env PORT=${SERVER_PORT} serve"


class TestBuildContainerRun:
    """Tests for build_container_run."""

    def _db(self, engine: str) -> ServiceDef:
        return ServiceDef(
            name="db",
            shared=True,
            engine=engine,
            image="pgvector/pgvector:pg16",
            ports=[],
            publish=["${DB_PORT}:5432"],
            volume="/var/lib/postgresql/data",
            host_var="DB_HOST" if engine == "apple-container" else None,
            env={"POSTGRES_PASSWORD": "${POSTGRES_PASSWORD}"},
        )

    def test_docker_run_boilerplate(self):
        """Docker synthesises rm -f then a foreground run with all flags."""
        cmd = build_container_run(self._db("docker"), "proj-db")
        rm, run = cmd.split(";", 1)
        assert rm.strip() == "docker rm -f proj-db 2>/dev/null || true"
        assert "docker run --rm --name proj-db" in run
        assert "-p ${DB_PORT}:5432" in run
        assert "-v proj-db-data:/var/lib/postgresql/data" in run
        assert "-e POSTGRES_PASSWORD=${POSTGRES_PASSWORD}" in run
        assert run.rstrip().endswith("pgvector/pgvector:pg16")

    def test_docker_has_stop_timeout(self):
        """Docker adds --stop-timeout 2; apple-container omits it."""
        assert "--stop-timeout 2" in build_container_run(self._db("docker"), "proj-db")

    def test_apple_omits_stop_timeout(self):
        """apple-container has no --stop-timeout."""
        cmd = build_container_run(self._db("apple-container"), "proj-db")
        assert "--stop-timeout" not in cmd

    def test_apple_rm_force_spelling(self):
        """apple-container force-removes with `container delete --force`."""
        cmd = build_container_run(self._db("apple-container"), "proj-db")
        rm = cmd.split(";", 1)[0].strip()
        assert rm == "container delete --force proj-db 2>/dev/null || true"
        assert "container run --rm --name proj-db" in cmd

    def test_flag_parity(self):
        """Both engines emit --name -p -v -e --rm for the same service."""
        for engine in ("docker", "apple-container"):
            cmd = build_container_run(self._db(engine), "proj")
            for flag in ("--rm", "--name", "-p", "-v", "-e"):
                assert flag in cmd, f"{engine} missing {flag}"

    def test_no_volume(self):
        """A service without a volume emits no -v flag."""
        svc = ServiceDef(
            name="redis",
            shared=True,
            engine="apple-container",
            image="redis:8.4-alpine",
            publish=["${REDIS_PORT}:6379"],
        )
        cmd = build_container_run(svc, "proj-redis")
        assert " -v " not in cmd

    def test_not_a_container_raises(self):
        """A command service has no engine and cannot be built as a container."""
        svc = ServiceDef(name="worker", command="serve")
        with pytest.raises(ValueError, match="not a container"):
            build_container_run(svc, "proj")


class TestNames:
    """Tests for container_name / volume_name."""

    def test_container_name(self):
        assert container_name("proj", "db") == "proj-db"

    def test_volume_name(self):
        assert volume_name("proj-db") == "proj-db-data"


class TestDiscoverContainerIp:
    """Tests for discover_container_ip with a fake runner."""

    def _inspect(self, address):
        return json.dumps([{"networks": [{"address": address}]}])

    def test_success_strips_cidr(self):
        """The /24 suffix is stripped from the reported address."""

        def runner(argv):
            return self._inspect("192.168.64.3/24")

        assert discover_container_ip("proj-db", runner) == "192.168.64.3"

    def test_no_cidr(self):
        """An address without a CIDR is returned unchanged."""

        def runner(argv):
            return self._inspect("192.168.64.7")

        assert discover_container_ip("proj-db", runner) == "192.168.64.7"

    def test_runner_gets_inspect_argv(self):
        """The runner is invoked with `container inspect <name>`."""
        seen = []

        def runner(argv):
            seen.append(argv)
            return self._inspect("192.168.64.9/24")

        discover_container_ip("proj-db", runner)
        assert seen == [["container", "inspect", "proj-db"]]

    def test_polls_until_non_null(self):
        """Polls past a null address until one appears."""
        payloads = iter(
            [
                self._inspect(None),
                "[]",
                self._inspect("192.168.64.5/24"),
            ]
        )

        def runner(argv):
            return next(payloads)

        ip = discover_container_ip(
            "proj-db",
            runner,
            timeout=5.0,
            interval=0.0,
        )
        assert ip == "192.168.64.5"

    def test_timeout_raises(self):
        """A perpetually-null address raises TimeoutError past the deadline."""

        def runner(argv):
            return self._inspect(None)

        with pytest.raises(TimeoutError, match="did not report"):
            discover_container_ip("proj-db", runner, timeout=0.0, interval=0.0)

    def test_malformed_json_keeps_polling_then_times_out(self):
        """Unparseable inspect output is tolerated (treated as not-yet-ready)."""

        def runner(argv):
            return "not json"

        with pytest.raises(TimeoutError):
            discover_container_ip("proj-db", runner, timeout=0.0, interval=0.0)

    @pytest.mark.parametrize(
        "payload",
        [
            "[]",
            "[null]",
            '["not-a-dict"]',
            '[{"networks": null}]',
            '[{"networks": []}]',
            '[{"networks": ["not-a-dict"]}]',
            '[{"networks": [{"address": null}]}]',
            '[{"networks": [{"address": 42}]}]',
            '{"not": "a list"}',
        ],
    )
    def test_malformed_shapes_treated_as_not_ready(self, payload):
        """Any malformed inspect shape is treated as not-yet-ready, not a crash."""

        def runner(argv):
            return payload

        with pytest.raises(TimeoutError):
            discover_container_ip("proj-db", runner, timeout=0.0, interval=0.0)
