"""The auth domain: key hashing, verify codes, scopes, the store and the key files."""

import re
import stat

import pytest
from domain_fixtures import make_passkey

from mael_domain.auth.keys import ensure_keys, key_path, read_key
from mael_domain.auth.model import (
    HOST,
    LIVE_FEEDBACK,
    SignIn,
    hash_secret,
    new_key,
    new_verify_code,
    normalise_code,
    scope_permits,
)
from mael_domain.auth.store import InMemoryAuthStore, SqliteAuthStore

NOW = "2026-11-07T09:00:00+00:00"


class TestModel:
    def test_a_new_key_has_the_prefix_and_is_new_each_time(self):
        assert new_key().startswith("mael_")
        assert new_key() != new_key()

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
            (LIVE_FEEDBACK, "GET", "/api/live-feedback", True),
            (LIVE_FEEDBACK, "GET", "/api/tasks", False),
            (LIVE_FEEDBACK, "POST", "/api/live-feedback", False),
        ],
    )
    def test_the_feedback_scope_opens_only_the_socket(
        self, scope, method, path, permitted
    ):
        assert scope_permits(scope, method, path) is permitted


@pytest.fixture(params=["memory", "sqlite"])
def store(request, state_db):
    """Run each contract test against every backend."""
    if request.param == "memory":
        return InMemoryAuthStore()
    return SqliteAuthStore(state_db)


class TestStore:
    async def test_a_passkey_round_trips(self, store):
        await store.save_passkey(make_passkey())
        await store.save_passkey(
            make_passkey("cred2", created="2026-11-08T00:00:00+00:00")
        )

        assert await store.read_passkey("cred1") == make_passkey()
        assert [p.id for p in await store.list_passkeys()] == ["cred1", "cred2"]

    async def test_a_sign_in_round_trips_and_deletes(self, store):
        sign_in = SignIn(token_hash="h1", passkey_id="cred1", expires=NOW)
        await store.save_sign_in(sign_in)
        assert await store.read_sign_in("h1") == sign_in

        await store.delete_sign_in("h1")
        await store.delete_sign_in("h1")
        assert await store.read_sign_in("h1") is None

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

        assert minted == [HOST, LIVE_FEEDBACK]
        for scope in (HOST, LIVE_FEEDBACK):
            key = read_key(scope, tmp_path)
            assert key is not None and key.startswith("mael_")
            assert stat.S_IMODE(key_path(scope, tmp_path).stat().st_mode) == 0o600
            assert await store.find_api_key(hash_secret(key)) is not None
        assert read_key(HOST, tmp_path) != read_key(LIVE_FEEDBACK, tmp_path)

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
        assert await store.find_api_key(hash_secret(old)) is None

    async def test_a_key_whose_hash_is_gone_is_re_minted(self, tmp_path, state_db):
        await ensure_keys(SqliteAuthStore(state_db), tmp_path)
        old = read_key(HOST, tmp_path)

        # A fresh database: the files outlive it.
        assert await ensure_keys(InMemoryAuthStore(), tmp_path) == [
            HOST,
            LIVE_FEEDBACK,
        ]
        assert read_key(HOST, tmp_path) != old
