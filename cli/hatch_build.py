"""Bundle the libraries and ``shared/`` into the published ``mael`` wheel.

A hook, not ``force-include``: hatch applies ``force-include`` to the editable
build too, and there the copies in site-packages hide the checkout that
``dev-mode-dirs`` puts on the path.
"""

from hatchling.builders.hooks.plugin.interface import BuildHookInterface

BUNDLED = {
    "../lib/common/src/mael_common": "mael_common",
    "../lib/agent/src/mael_agent": "mael_agent",
    "../lib/domain/src/mael_domain": "mael_domain",
    # Where get_shared_dir() looks when no repository is around it.
    "../shared": "mael_domain/shared",
}


class CustomBuildHook(BuildHookInterface):
    def initialize(self, version, build_data):
        if version == "standard":
            build_data["force_include"].update(BUNDLED)
