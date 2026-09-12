"""One ladder per module, each declaring its own subsystem's schema.

This package carries no code, for the same reason its parent does not: import
order must be a property of the file that needs something, not of a package
that re-exports it. Import the ladder you need from its own module.
"""
