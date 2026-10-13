"""Where the PR watch keeps what each Registered PR's agents were last told.

:mod:`mael_domain.external_event` decides what is news; this module only
stores the :class:`~mael_domain.external_event.PrLedger` it returns, by PR URL.
"""

from abc import ABC, abstractmethod

from .external_event import PrLedger
from .state_db.db import StateDb

PR_EVENTS = "pr_events"


class PrLedgerStore(ABC):
    @abstractmethod
    async def read(self, pr_url: str) -> PrLedger | None:
        """The ledger for ``pr_url``, or ``None`` before the PR was first seen."""

    @abstractmethod
    async def save(self, pr_url: str, ledger: PrLedger) -> None:
        """Store ``ledger`` for ``pr_url``, replacing the last one."""


class InMemoryPrLedgerStore(PrLedgerStore):
    """Ledgers for a test or a server with no state database."""

    def __init__(self) -> None:
        self._rows: dict[str, PrLedger] = {}

    async def read(self, pr_url: str) -> PrLedger | None:
        return self._rows.get(pr_url)

    async def save(self, pr_url: str, ledger: PrLedger) -> None:
        self._rows[pr_url] = ledger


class SqlitePrLedgerStore(PrLedgerStore):
    """Ledgers in the state database's ``pr_events`` table."""

    def __init__(self, db: StateDb) -> None:
        self._db = db

    async def read(self, pr_url: str) -> PrLedger | None:
        row = await self._db.read(PR_EVENTS, pr_url)
        if row is None:
            return None
        return PrLedger(
            state=row["state"],
            conflicting=bool(row["conflicting"]),
            told_failed_oid=row["told_failed_oid"],
            told_green_oid=row["told_green_oid"],
        )

    async def save(self, pr_url: str, ledger: PrLedger) -> None:
        await self._db.upsert(
            PR_EVENTS,
            pr_url,
            state=ledger.state,
            conflicting=int(ledger.conflicting),
            told_failed_oid=ledger.told_failed_oid,
            told_green_oid=ledger.told_green_oid,
        )
