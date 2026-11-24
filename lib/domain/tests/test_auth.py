"""The auth domain: key hashing, verify codes, scopes, the store and the key files."""

import re
import stat
from datetime import datetime, timezone

import pytest
from domain_fixtures import make_passkey

from mael_domain.auth.keys import ensure_keys, key_path, read_key, sweep
from mael_domain.auth.model import (
    DELEGATE,
    HOST,
    ApiKey,
    SignIn,
    hash_secret,
    new_verify_code,
    normalise_code,
    scope_permits,
)
from mael_domain.auth.store import InMemoryAuthStore, SqliteAuthStore

NOW = "2026-11-07T09:00:00+00:00"


class TestModel:
    def test_a_verify_code_is_two_groups_of_four_with_no_ambiguous_letters(self):
        codes = [new_verify_code() for _ in range(200)]
        assert all(re.fullmatch(r"[A-Z2-9]{4}-[A-Z2-9]{4}", c) for c in codes)
        assert not set("".join(codes)) & set("01OIL")

    @pytest.mark.parametrize("typed", ["k7qf2m9d", "K7QF 2M9D", "k7qf-2m9d"])
    def test_a_typed_code_reads_as_the_shown_one(self, typed):
        assert normalise_code(typed) == "K7QF-2M9D"

    @pytest.mark.parametrize(
        ("scope", "method", "path", "permitted"),
        [
            (HOST, "GET", "/api/tasks", True),
            (HOST, "POST", "/api/auth/verify", True),
            # What a delegated agent's `mael` calls, through `for_tasks()`:
            (DELEGATE, "GET", "/api/tasks", True),  # mael task list
            (DELEGATE, "PUT", "/api/tasks/northwind/NORT-7/pr", True),  # gh create-pr
            (
                DELEGATE,
                "POST",
                "/api/tasks/northwind/NORT-7/documents",
                True,
            ),  # doc add
            (DELEGATE, "GET", "/api/task-documents", True),  # doc show
            (DELEGATE, "POST", "/api/task-documents/archive", True),  # doc archive
            (DELEGATE, "GET", "/api/operations", True),  # git merge waits
            # and nothing else:
            (DELEGATE, "POST", "/api/operations/op1", False),
            (DELEGATE, "GET", "/api/agents", False),
            (DELEGATE, "POST", "/api/auth/verify", False),
            (DELEGATE, "GET", "/api/tasksx", False),
        ],
    )
    def test_a_scope_permits_its_routes(self, scope, method, path, permitted):
        assert scope_permits(scope, method, path) is permitted


@pytest.fixture(params=["memory", "sqlite"])
def store(request, state_db):
    """Run each contract test against every backend."""
    if request.param == "memory":
        return InMemoryAuthStore()
    return SqliteAuthStore(state_db)


class TestStore:
    async def test_a_passkey_round_trips_and_lists_oldest_first(self, store):
        # Saved newest first: the pending cap evicts by this order, not by id.
        await store.save_passkey(
            make_passkey("cred2", created="2026-11-08T00:00:00+00:00")
        )
        await store.save_passkey(make_passkey())

        assert await store.read_passkey("cred1") == make_passkey()
        assert [p.id for p in await store.list_passkeys()] == ["cred1", "cred2"]

    async def test_a_sign_in_round_trips_and_deletes(self, store):
        sign_in = SignIn(token_hash="h1", passkey_id="cred1", expires=NOW)
        await store.save_sign_in(sign_in)
        assert await store.read_sign_in("h1") == sign_in

        await store.delete_sign_in("h1")
        await store.delete_sign_in("h1")
        assert await store.read_sign_in("h1") is None

    async def test_api_keys_go_by_hash_and_list_by_scope(self, store):
        host = ApiKey("h0", HOST, NOW)
        one = ApiKey("h1", DELEGATE, NOW, "northwind/NORT-7", "cobalt", "cobalt:a1")
        two = ApiKey("h2", DELEGATE, NOW, "northwind/NORT-7", "cobalt")
        for key in (host, one, two):
            await store.save_api_key(key)
        assert await store.read_api_key("h1") == one
        assert await store.list_api_keys(HOST) == [host]

        await store.delete_api_keys(["h2", "gone"])
        await store.delete_api_keys([])

        assert await store.list_api_keys(DELEGATE) == [one]

    async def test_a_revoked_passkey_takes_its_sign_ins_with_it(self, store):
        await store.save_passkey(make_passkey("cred1"))
        await store.save_passkey(make_passkey("cred2"))
        await store.save_sign_in(SignIn("h1", "cred1", NOW))
        await store.save_sign_in(SignIn("h2", "cred1", NOW))
        await store.save_sign_in(SignIn("h3", "cred2", NOW))

        await store.delete_passkey("cred1")

        assert await store.read_passkey("cred1") is None
        assert await store.read_sign_in("h1") is None
        assert await store.read_sign_in("h2") is None
        assert await store.read_sign_in("h3") is not None


class TestKeyFiles:
    async def test_the_server_mints_each_missing_key_into_a_private_file(
        self, store, tmp_path
    ):
        minted = await ensure_keys(store, tmp_path)

        assert minted == [HOST]
        key = read_key(HOST, tmp_path)
        assert key is not None and key.startswith("mael_")
        assert stat.S_IMODE(key_path(HOST, tmp_path).stat().st_mode) == 0o600
        assert await store.read_api_key(hash_secret(key)) is not None

    async def test_a_known_key_is_kept(self, store, tmp_path):
        await ensure_keys(store, tmp_path)
        before = read_key(HOST, tmp_path)

        assert await ensure_keys(store, tmp_path) == []
        assert read_key(HOST, tmp_path) == before

    async def test_a_deleted_file_re_mints_and_revokes_the_old_key(
        self, store, tmp_path
    ):
        await ensure_keys(store, tmp_path)
        old = read_key(HOST, tmp_path)
        assert old is not None
        key_path(HOST, tmp_path).unlink()

        assert await ensure_keys(store, tmp_path) == [HOST]
        assert await store.read_api_key(hash_secret(old)) is None

    async def test_a_key_whose_hash_is_gone_is_re_minted(self, tmp_path, state_db):
        await ensure_keys(SqliteAuthStore(state_db), tmp_path)
        old = read_key(HOST, tmp_path)

        # A fresh database: the files outlive it.
        fresh = InMemoryAuthStore()
        assert await ensure_keys(fresh, tmp_path) == [HOST]
        new = read_key(HOST, tmp_path)
        assert new is not None and new != old
        assert await fresh.read_api_key(hash_secret(new)) is not None


class TestSweep:
    async def test_a_start_drops_ended_sign_ins_and_long_pending_passkeys(self, store):
        now = datetime(2026, 11, 10, 9, tzinfo=timezone.utc)
        await store.save_passkey(make_passkey("verified", state="verified"))
        await store.save_passkey(
            make_passkey("fresh", created="2026-11-10T08:00:00+00:00")
        )
        await store.save_passkey(
            make_passkey("stale", created="2026-11-09T08:59:00+00:00")
        )
        await store.save_sign_in(
            SignIn("ended", "verified", "2026-11-10T08:00:00+00:00")
        )
        await store.save_sign_in(
            SignIn("live", "verified", "2026-12-01T00:00:00+00:00")
        )

        await sweep(store, now)

        # A verified passkey never goes by age; a pending one goes after a day.
        assert [p.id for p in await store.list_passkeys()] == ["verified", "fresh"]
        assert [s.token_hash for s in await store.list_sign_ins()] == ["live"]
