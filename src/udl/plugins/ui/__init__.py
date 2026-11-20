"""
----
- ui -
----

UI plugins: a bundle (an ES module, its stylesheets and a `manifest.json`) that the shell loads
into its pages. The manifest and the `udl.ui` kind's base are here; what finds the bundles is
`udl.plugins.ui.discovery`, and the JSON Schema's writer is `python -m udl.plugins.ui --schema`.

(This module imports neither udl.config nor the registry, on purpose: every plugin kind's base
imports it, and the config and the registry import those.)
"""

# Local Imports
from udl.plugins.ui.base import (
    POINTS,
    SDK_VERSION,
    TABS,
    Contributions,
    Generated,
    GeneratedNav,
    NavItem,
    Tab,
    UIBundle,
    UIBundleProblem,
    UIManifest,
    UIPlugin,
    UIPluginInfo,
    UIPluginList,
    Widget,
    guidance_problems,
    main,
    matches,
    schema,
    write_schema,
)

__all__ = [
    "POINTS",
    "SDK_VERSION",
    "TABS",
    "Contributions",
    "Generated",
    "GeneratedNav",
    "NavItem",
    "Tab",
    "UIBundle",
    "UIBundleProblem",
    "UIManifest",
    "UIPlugin",
    "UIPluginInfo",
    "UIPluginList",
    "Widget",
    "guidance_problems",
    "main",
    "matches",
    "schema",
    "write_schema",
]
