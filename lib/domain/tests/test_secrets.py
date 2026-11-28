"""The Secret store's contract, at the model: ``mael_domain.secrets``."""

import stat

import pytest

from mael_domain.secret_store import FileSecretStore, InMemorySecretStore
from mael_domain.secrets import (
    UNLOCK_COMMAND,
    BadSecret,
    DamagedSecret,
    DamagedStore,
    Kdf,
    MissingSecrets,
    SecretInfo,
    SecretVault,
    StoreExists,
    StoreLocked,
    StoreMissing,
    UnknownSecret,
    WrongPassphrase,
)

PASSPHRASE = "correct horse battery"
NOW = "2026-11-28T09:00:00+00:00"
#: The cheapest scrypt the cipher takes: these tests do not test its cost.
CHEAP = Kdf(n=2, r=1, p=1)


def vault_over(store) -> SecretVault:
    """A new vault, as a server start makes one: locked."""
    return SecretVault(store, kdf=CHEAP, clock=lambda: NOW)


@pytest.fixture
def store():
    return InMemorySecretStore()


@pytest.fixture
def vault(store) -> SecretVault:
    """An unlocked vault over a new store."""
    vault = vault_over(store)
    vault.unlock(PASSPHRASE, create=True)
    return vault


# -- the files --


def test_no_file_holds_a_value_or_the_passphrase(tmp_path):
    vault = vault_over(FileSecretStore(tmp_path / "secrets"))
    vault.unlock(PASSPHRASE, create=True)
    vault.set("API_KEY", "canary-global")
    vault.set("API_KEY", "canary-project", "northwind")

    files = [p for p in (tmp_path / "secrets").rglob("*") if p.is_file()]
    written = b"".join(p.read_bytes() for p in files)

    assert len(files) == 3
    for plain in (b"canary-global", b"canary-project", PASSPHRASE.encode()):
        assert plain not in written


def test_each_file_of_the_store_is_private_to_the_user(tmp_path):
    vault = vault_over(FileSecretStore(tmp_path / "secrets"))
    vault.unlock(PASSPHRASE, create=True)
    vault.set("API_KEY", "canary", "northwind")

    modes = {
        p.relative_to(tmp_path).as_posix(): stat.S_IMODE(p.stat().st_mode)
        for p in (tmp_path / "secrets").rglob("*")
    }

    assert modes == {
        "secrets/store.json": 0o600,
        "secrets/projects": 0o700,
        "secrets/projects/northwind.json": 0o600,
    }
    assert stat.S_IMODE((tmp_path / "secrets").stat().st_mode) == 0o700


# -- the lock --


@pytest.mark.parametrize(
    "use",
    [
        lambda v: v.set("API_KEY", "new"),
        lambda v: v.remove("API_KEY"),
        lambda v: v.resolve(["API_KEY"], None),
    ],
    ids=["set", "remove", "resolve"],
)
def test_a_locked_store_refuses_each_use_of_a_value(store, vault, use):
    vault.set("API_KEY", "old")
    locked = vault_over(store)

    with pytest.raises(StoreLocked) as refusal:
        use(locked)

    assert UNLOCK_COMMAND in str(refusal.value)
    assert vault.resolve(["API_KEY"], None) == {"API_KEY": "old"}


def test_the_correct_passphrase_unlocks_a_store_that_exists(store, vault):
    vault.set("API_KEY", "value")
    later = vault_over(store)

    later.unlock(PASSPHRASE)

    assert later.locked is False
    assert later.resolve(["API_KEY"], None) == {"API_KEY": "value"}


def test_a_wrong_passphrase_leaves_the_store_locked(store, vault):
    later = vault_over(store)

    with pytest.raises(WrongPassphrase):
        later.unlock("correct horse batterx")

    assert later.locked is True


def test_an_unlock_never_makes_a_store_unless_it_says_create(store):
    vault = vault_over(store)

    with pytest.raises(StoreMissing):
        vault.unlock(PASSPHRASE)

    assert (vault.exists, vault.locked) == (False, True)


def test_a_create_does_not_replace_a_store_that_exists(store, vault):
    vault.set("API_KEY", "value")

    with pytest.raises(StoreExists):
        vault_over(store).unlock("another passphrase", create=True)

    assert vault_over(store).list() == [SecretInfo("API_KEY", None, NOW)]


def test_a_create_refuses_sealed_values_that_a_lost_header_left(store, vault):
    """A new store key would open none of them, and `list` would still show them."""
    vault.set("API_KEY", "value", "northwind")
    orphans = InMemorySecretStore()
    orphans.write("projects/northwind", store.read("projects/northwind"))

    with pytest.raises(DamagedStore):
        vault_over(orphans).unlock(PASSPHRASE, create=True)

    assert vault_over(orphans).exists is False


@pytest.mark.parametrize(
    "damage",
    [
        lambda h: h.update(version=2),
        lambda h: h["kdf"].update(n=2**30),
        lambda h: h["kdf"].pop("salt"),
        lambda h: h["key"].update(wrapped="not base64!"),
    ],
    ids=["version", "cost", "no-salt", "not-base64"],
)
def test_a_header_this_build_cannot_read_is_a_refusal(store, vault, damage):
    header = store.read("store")
    damage(header)
    store.write("store", header)

    with pytest.raises(DamagedStore):
        vault_over(store).unlock(PASSPHRASE)


def test_an_empty_passphrase_is_refused(store):
    with pytest.raises(BadSecret):
        vault_over(store).unlock("", create=True)


# -- set, list, remove --


def test_a_second_set_replaces_the_value(vault):
    vault.set("API_KEY", "first", "northwind")
    vault.set("API_KEY", "second", "northwind")

    assert vault.resolve(["API_KEY"], "northwind") == {"API_KEY": "second"}
    assert vault.list() == [SecretInfo("API_KEY", "northwind", NOW)]


@pytest.mark.parametrize("name", ["", "9LIVES", "API-KEY", "API KEY", "../store"])
def test_a_name_that_is_not_an_environment_variable_is_refused(vault, name):
    with pytest.raises(BadSecret):
        vault.set(name, "value")


def test_a_project_name_that_is_a_path_is_refused(vault):
    with pytest.raises(BadSecret):
        vault.set("API_KEY", "value", "../global")


@pytest.mark.parametrize("value", ["", "\ud800"], ids=["empty", "lone-surrogate"])
def test_a_value_that_is_empty_or_not_text_is_refused(vault, value):
    with pytest.raises(BadSecret):
        vault.set("API_KEY", value)


def test_list_shows_the_name_scope_and_date_on_a_locked_store(store, vault):
    vault.set("ZED", "z")
    vault.set("API_KEY", "a")
    vault.set("DB_URL", "d", "northwind")

    assert vault_over(store).list() == [
        SecretInfo("API_KEY", None, NOW),
        SecretInfo("ZED", None, NOW),
        SecretInfo("DB_URL", "northwind", NOW),
    ]


def test_list_reads_past_a_file_that_is_no_scope(store, vault):
    vault.set("API_KEY", "a")
    store.write("backup", {"note": "left by hand"})
    store.write("projects/a/b", {"secrets": {}})

    assert vault.list() == [SecretInfo("API_KEY", None, NOW)]


def test_remove_takes_one_secret_of_one_scope(vault):
    vault.set("API_KEY", "global")
    vault.set("API_KEY", "project", "northwind")

    vault.remove("API_KEY", "northwind")

    assert vault.list() == [SecretInfo("API_KEY", None, NOW)]


def test_remove_of_an_unknown_name_is_an_error(vault):
    vault.set("API_KEY", "value", "northwind")

    with pytest.raises(UnknownSecret):
        vault.remove("API_KEY")


# -- resolve --


def test_a_project_value_wins_over_a_global_one(vault):
    vault.set("API_KEY", "global")
    vault.set("API_KEY", "project", "northwind")
    vault.set("SHARED", "shared")

    assert vault.resolve(["API_KEY", "SHARED"], "northwind") == {
        "API_KEY": "project",
        "SHARED": "shared",
    }
    assert vault.resolve(["API_KEY"], "tailspin") == {"API_KEY": "global"}


def test_resolve_names_each_missing_secret(vault):
    vault.set("API_KEY", "value")
    vault.set("OTHER", "value", "tailspin")

    with pytest.raises(MissingSecrets) as missing:
        vault.resolve(["API_KEY", "OTHER", "ABSENT"], "northwind")

    assert missing.value.names == ["OTHER", "ABSENT"]


@pytest.mark.parametrize(
    ("to_scope", "to_name", "project"),
    [
        ("projects/northwind", "OTHER", "northwind"),
        ("projects/tailspin", "API_KEY", "tailspin"),
        ("global", "API_KEY", None),
    ],
    ids=["another-name", "another-project", "the-global-scope"],
)
def test_a_sealed_value_opens_only_at_its_scope_and_name(
    store, vault, to_scope, to_name, project
):
    """A writer of the files cannot move a value to where a Service reads it."""
    vault.set("API_KEY", "value", "northwind")
    sealed = store.read("projects/northwind")["secrets"]["API_KEY"]
    store.write(to_scope, {"version": 1, "secrets": {to_name: sealed}})

    with pytest.raises(DamagedSecret):
        vault.resolve([to_name], project)
