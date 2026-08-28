"""The leading underscore keeps ``PluginRegistry._discover_builtin_plugins`` from
importing this package while it scans ``src/ingestion/sources/`` for plugins.
"""
