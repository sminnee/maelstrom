"""Fakes for the ``mael self`` tests."""

from dataclasses import dataclass, field

from mael_cli.self.host_service import Applied, ServiceStatus


@dataclass
class FakeSupervisor:
    calls: list[str] = field(default_factory=list)
    #: Whether ``apply`` reports each unit as started, as on a first install.
    start_on_apply: bool = True
    applied: list = field(default_factory=list)

    def _record(self, verb, specs):
        self.calls.append(f"{verb} {' '.join(s.name for s in specs)}")

    def apply(self, specs):
        self._record("apply", specs)
        self.applied += specs
        return Applied(started=[s.name for s in specs] if self.start_on_apply else [])

    def remove(self, specs):
        self._record("remove", specs)
        return []

    def restart(self, specs):
        self._record("restart", specs)
        return Applied()

    def status(self, specs):
        return [ServiceStatus(s.name, loaded=True, pid=7) for s in specs]

    def log_hint(self, spec):
        return f"log of {spec.name}"

    def submit(self, spec):
        self.calls.append(f"submit {spec.name} {' '.join(spec.argv)}")
