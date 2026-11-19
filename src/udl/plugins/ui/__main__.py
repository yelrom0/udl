"""
----------------
- __main__.py -
----------------

`python -m udl.plugins.ui --schema`: write the UI manifest's JSON Schema to
`web/packages/sdk/src/manifest.schema.json`, from which `json-schema-to-typescript` makes the
TypeScript type the SDK (and every UI plugin) is written against. Both are committed, so a change
to the manifest's model is a change to them.
"""

# Local Imports
from udl.plugins.ui.base import main

if __name__ == "__main__":
    raise SystemExit(main())
