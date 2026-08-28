"""Configuration loading for maelstrom projects."""

from dataclasses import dataclass, field
from pathlib import Path

import yaml

CONFIG_FILENAME = ".maelstrom.yaml"

# Container engines maelstrom knows how to drive. Presence of an ``engine:`` key
# is what makes a service a container (vs a shell ``command`` service).
CONTAINER_ENGINES = ("docker", "apple-container")


@dataclass
class PortSpec:
    """A named port a service owns.

    ``name`` is the port-name (e.g. ``"FRONTEND"``) whose ``${FRONTEND_PORT}``
    the existing allocator generates. ``container`` is the container-side port
    for a publish mapping, or ``None`` for a command service.
    """

    name: str
    container: int | None = None


@dataclass
class ServiceDef:
    """A structured service definition parsed from the ``services:`` map.

    Service *type* is inferred: an ``engine`` (docker / apple-container) makes it
    a container service; otherwise it is a shell ``command`` service.
    """

    name: str
    shared: bool = False
    optional: bool = False  # skipped by a default `env start`; started by name
    engine: str | None = None  # None=command, "docker", "apple-container"
    ports: list[PortSpec] = field(default_factory=list)
    publish: list[str] = field(default_factory=list)  # ["${DB_PORT}:5432"]
    env: dict[str, str] = field(default_factory=dict)
    dir: str | None = None  # command service
    command: str | None = None  # command service
    image: str | None = None  # container service
    volume: str | None = None  # container mount path
    host_var: str | None = None  # apple-container VM-IP target var

    @property
    def is_container(self) -> bool:
        """Whether this is a container service (has an engine)."""
        return self.engine is not None


def _parse_bool(name: str, data: dict, key: str) -> bool:
    """Read a bool service key, rejecting anything that is not a YAML boolean.

    Plain ``bool()`` would read ``optional: "no"`` as True, and the service would
    then vanish from ``mael env start`` with no error to explain it.

    Raises:
        ValueError: If the key holds a non-boolean value.
    """
    value = data.get(key, False)
    if not isinstance(value, bool):
        raise ValueError(
            f"Service {name!r}: {key!r} must be true or false, got {value!r}"
        )
    return value


def _parse_service(name: str, data: dict) -> ServiceDef:
    """Parse a single ``services:`` entry into a ServiceDef.

    Raises:
        ValueError: If the entry is malformed for its inferred type.
    """
    if not isinstance(data, dict):
        raise ValueError(f"Service {name!r}: definition must be a mapping")

    engine = data.get("engine")
    if engine is not None and engine not in CONTAINER_ENGINES:
        allowed = ", ".join(CONTAINER_ENGINES)
        raise ValueError(
            f"Service {name!r}: unknown engine {engine!r} (expected one of {allowed})"
        )

    raw_ports = data.get("ports", [])
    if not isinstance(raw_ports, list) or not all(
        isinstance(p, str) for p in raw_ports
    ):
        raise ValueError(f"Service {name!r}: 'ports' must be a list of names")
    ports = [PortSpec(name=p) for p in raw_ports]

    svc = ServiceDef(
        name=name,
        shared=_parse_bool(name, data, "shared"),
        optional=_parse_bool(name, data, "optional"),
        engine=engine,
        ports=ports,
        publish=list(data.get("publish", [])),
        env=dict(data.get("env", {})),
        dir=data.get("dir"),
        command=data.get("command"),
        image=data.get("image"),
        volume=data.get("volume"),
        host_var=data.get("host_var"),
    )

    if svc.shared and svc.optional:
        raise ValueError(
            f"Service {name!r}: cannot be both 'shared' and 'optional' — a shared "
            "service is reference-counted across worktrees, so it has no "
            "subscriber lifecycle when no default start ever starts it"
        )

    if svc.is_container:
        if not svc.image:
            raise ValueError(f"Service {name!r}: container service requires 'image'")
        if svc.host_var and svc.engine != "apple-container":
            raise ValueError(
                f"Service {name!r}: 'host_var' is only meaningful for "
                "apple-container services"
            )
    else:
        if not svc.command:
            raise ValueError(f"Service {name!r}: command service requires 'command'")

    return svc


@dataclass
class MaelstromConfig:
    """Configuration for a maelstrom-managed project."""

    port_names: list[str] = field(default_factory=list)
    shared_port_names: list[str] = field(default_factory=list)
    start_cmd: str = ""
    install_cmd: str = ""
    # Structured service definitions (preferred over Procfile / start_cmd).
    # Insertion order is preserved for deterministic port allocation.
    services: list[ServiceDef] = field(default_factory=list)
    # Linear integration
    linear_team_id: str | None = None
    linear_workspace_labels: list[str] | None = None
    linear_product_label: str | None = None
    # Sentry integration
    sentry_org: str | None = None
    sentry_project: str | None = None
    # UptimeRobot integration
    uptimerobot_monitors: list[str] | None = None

    @classmethod
    def from_dict(cls, data: dict) -> "MaelstromConfig":
        """Create a config from a dictionary.

        Raises:
            ValueError: If a ``services:`` entry is malformed.
        """
        linear_config = data.get("linear", {})
        if not isinstance(linear_config, dict):
            linear_config = {}

        sentry_config = data.get("sentry", {})
        if not isinstance(sentry_config, dict):
            sentry_config = {}

        ur_config = data.get("uptimerobot", {})
        if not isinstance(ur_config, dict):
            ur_config = {}

        services_data = data.get("services", {}) or {}
        services = [
            _parse_service(name, svc_data) for name, svc_data in services_data.items()
        ]

        return cls(
            port_names=data.get("port_names", []),
            shared_port_names=data.get("shared_port_names", []),
            start_cmd=data.get("start_cmd", ""),
            install_cmd=data.get("install_cmd", ""),
            services=services,
            linear_team_id=linear_config.get("team_id"),
            linear_workspace_labels=linear_config.get("workspace_labels"),
            linear_product_label=linear_config.get("product_label"),
            sentry_org=sentry_config.get("org"),
            sentry_project=sentry_config.get("project_id"),
            uptimerobot_monitors=ur_config.get("monitors"),
        )


def service_port_names(config: MaelstromConfig) -> list[str]:
    """Flat list of non-shared service port names, in declaration order.

    Reuses the existing port allocator unchanged: the returned list is the
    ordering the allocator maps onto ``${NAME_PORT}`` slots. Duplicates across
    services are dropped, keeping first-seen order.

    Optional services are *not* filtered out — skipping one here would renumber
    every port declared after it.
    """
    names: list[str] = []
    seen: set[str] = set()
    for svc in config.services:
        if svc.shared:
            continue
        for spec in svc.ports:
            if spec.name not in seen:
                seen.add(spec.name)
                names.append(spec.name)
    return names


def shared_service_port_names(config: MaelstromConfig) -> list[str]:
    """Flat list of shared service port names, in declaration order.

    See :func:`service_port_names`; this is the shared-service counterpart.
    """
    names: list[str] = []
    seen: set[str] = set()
    for svc in config.services:
        if not svc.shared:
            continue
        for spec in svc.ports:
            if spec.name not in seen:
                seen.add(spec.name)
                names.append(spec.name)
    return names


def find_config_file(path: Path) -> Path | None:
    """Find .maelstrom.yaml starting from the given path and searching upward.

    Args:
        path: Starting path (file or directory).

    Returns:
        Path to the config file, or None if not found.
    """
    if path.is_file():
        path = path.parent

    current = path.resolve()
    while current != current.parent:
        config_path = current / CONFIG_FILENAME
        if config_path.exists():
            return config_path
        current = current.parent

    return None


def load_config(worktree_path: Path) -> MaelstromConfig:
    """Load .maelstrom.yaml configuration from a worktree.

    Args:
        worktree_path: Path to the worktree directory.

    Returns:
        MaelstromConfig with the loaded configuration.

    Raises:
        FileNotFoundError: If no .maelstrom.yaml is found.
        yaml.YAMLError: If the YAML is invalid.
    """
    config_file = find_config_file(worktree_path)
    if config_file is None:
        raise FileNotFoundError(
            f"No {CONFIG_FILENAME} found in {worktree_path} or parent directories"
        )

    with open(config_file) as f:
        data = yaml.safe_load(f) or {}

    return MaelstromConfig.from_dict(data)


def load_config_or_default(worktree_path: Path) -> MaelstromConfig:
    """Load config or return default if not found.

    Args:
        worktree_path: Path to the worktree directory.

    Returns:
        MaelstromConfig with loaded or default configuration.
    """
    try:
        return load_config(worktree_path)
    except FileNotFoundError:
        return MaelstromConfig()
