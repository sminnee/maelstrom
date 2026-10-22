"""The **Gate watch**'s table: the unfinished gate jobs, kept from tangier's events.

Pure: it reads the lines ``tangier gate status --watch --json --all`` prints,
and says which jobs ended. See "The gate watch" in
docs/dev/orchestrator-server.md.
"""

from dataclasses import dataclass
from typing import Any

#: The job states tangier has not finished. See ``UNFINISHED`` in tangier's jobs.py.
UNFINISHED = frozenset({"queued", "running", "stopping", "orphaned"})


@dataclass(frozen=True)
class Ended:
    """A job the table held, or never saw, that has finished: the raw job."""

    job: dict[str, Any]


class GateTable:
    """The unfinished jobs on the machine, by id, as tangier prints them.

    In memory only. The first ``status`` line of each watch rebuilds it.
    """

    def __init__(self) -> None:
        self.jobs: dict[Any, dict[str, Any]] = {}

    def apply(self, event: dict[str, Any]) -> list[Ended]:
        """Apply one watch line, and return the jobs it ended."""
        match event.get("type"):
            case "status":
                return self._status(event)
            case "job":
                return self._job(event)
            case "gate":
                self._gate(event)
        return []

    def _status(self, event: dict[str, Any]) -> list[Ended]:
        # A restarted watch: a job held as unfinished may have ended between.
        ended = [
            Ended(job)
            for job in event.get("recent") or []
            if job.get("id") in self.jobs and job.get("state") not in UNFINISHED
        ]
        self.jobs = {
            job["id"]: job
            for job in [*(event.get("running") or []), *(event.get("queued") or [])]
        }
        return ended

    def _job(self, event: dict[str, Any]) -> list[Ended]:
        job = event["job"]
        if job.get("state") in UNFINISHED:
            self.jobs[job["id"]] = job
            return []
        # A finished job never changes state again. A restarted watch can repeat it.
        self.jobs.pop(job["id"], None)
        return [Ended(job)]

    def _gate(self, event: dict[str, Any]) -> None:
        job = self.jobs.get(event.get("job_id"))
        if job is None:
            return
        gate = event["gate"]
        gates = [
            gate if g.get("name") == gate.get("name") else g
            for g in job.get("gates") or []
        ]
        if not any(g.get("name") == gate.get("name") for g in gates):
            gates.append(gate)
        self.jobs[job["id"]] = {**job, "gates": gates}
