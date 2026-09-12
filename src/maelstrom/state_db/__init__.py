"""The state database: one SQLite file, one revision counter, one notice path.

One SQLite file at ``~/.maelstrom/state.db`` holds every canonical and cached
table. One file gives one transaction, so a canonical write and its derived
rows commit or roll back together, and one revision counter names the cut.
See ``docs/dev/data-architecture.md``.

This package carries no code. Import from the module that holds what you need,
so import order is a property of the file rather than of this docstring:

============================ ==================================================
``state_db.types``           The errors, ``Migration``, ``PythonMigration``,
                             ``Rung``, ``TableSpec`` and ``Write``
``state_db.paths``           ``get_state_db_path``
``state_db.db``              ``StateDb`` and ``Txn``
``state_db.migrate``         ``open_state_db``, ``LADDERS`` and ``TABLES``
``state_db.migrations.*``    One ladder each
============================ ==================================================

A module imports only from a strictly lower layer: ``types`` reaches nothing,
``db`` reaches ``types`` and ``paths``, and ``migrate`` reaches the ladders.
``db`` must not import ``migrate`` — ``StateDb`` takes its ladders as
arguments, and :func:`state_db.migrate.open_state_db` is what supplies this
build's.
"""
