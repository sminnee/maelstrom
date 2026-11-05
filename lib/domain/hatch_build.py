"""Put the repository's ``shared/`` into the ``mael-domain`` wheel.

``get_shared_dir()`` looks for ``mael_domain/shared`` first, because an
installed release has no repository around it.

A hook, not ``force-include``: hatch applies ``force-include`` to the editable
build too, and there a copy in site-packages would hide the checkout's
``shared/``.
"""

from pathlib import Path

from hatchling.builders.hooks.plugin.interface import BuildHookInterface

#: The skills' own tests run in the repository, not in a release.
LEFT_OUT = {"tests"}


class CustomBuildHook(BuildHookInterface):
    def initialize(self, version, build_data):
        if version != "standard":
            return
        shared = Path(self.root, "..", "..", "shared").resolve()
        for path in sorted(shared.rglob("*")):
            relative = path.relative_to(shared)
            # Bytecode at any depth is a local artefact, not part of `shared/`.
            if relative.parts[0] in LEFT_OUT or "__pycache__" in relative.parts:
                continue
            if path.is_file():
                build_data["force_include"][str(path)] = (
                    f"mael_domain/shared/{relative}"
                )
