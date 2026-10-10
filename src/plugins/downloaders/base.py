"""
-----------
- base.py -
-----------

Custom base plugin object (BasePlugin) that all plugins inherit from
"""

class BasePlugin(object):
    def view(url: str|list[str]):
        # TODO: Add database connection in __init__ (for grabbing download methods)
