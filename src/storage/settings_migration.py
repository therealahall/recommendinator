"""Nothing is written to the database here — the ``settings`` table holds only the
leaves a user explicitly set later (via the settings UI/CLI).
"""

from __future__ import annotations

import copy
from typing import TYPE_CHECKING, Any

from src.utils.deep_merge import deep_merge
from src.utils.dotted_path import set_leaf

if TYPE_CHECKING:
    from src.storage.manager import StorageManager

# Global/system config sections whose effective value is assembled here. The
# ``storage`` section is intentionally excluded — it bootstraps the database
# itself and must stay in YAML/env. ``inputs`` (sources) and credentials are
# owned by their own migrations (source_configs / credentials tables).
IN_SCOPE_SECTIONS: tuple[str, ...] = (
    "recommendations",
    "sync",
    "enrichment",
    "web",
    "logging",
)

# These are NEVER written to the plaintext ``settings`` table.
SENSITIVE_LEAF_KEYS: frozenset[str] = frozenset(
    {
        "api_key",
        "token",
        "password",
        "secret",
        "refresh_token",
        "access_token",
        "client_secret",
        "steam_id",
    }
)


#: Where ``logging.file`` pointed before the log moved under the ``data/`` mount.
_PRE_MOVE_LOG_DIR = "logs/"


def _relocate_pre_move_log_file(section: dict[str, Any]) -> None:
    """Nothing rewrites the row or the YAML holding it, so otherwise containment
    discards that file name for the default on every boot while the Settings
    page still shows the old path.
    """
    configured = section.get("file")
    if isinstance(configured, str) and configured.startswith(_PRE_MOVE_LOG_DIR):
        section["file"] = f"data/{configured}"


def migrate_config_settings(
    config: dict[str, Any],
    storage: StorageManager,
) -> None:
    """**Mutates *config* in place:** each in-scope section is replaced with the
    assembled result so existing ``config[section][key]`` read sites resolve the
    layered value.
    """
    # Deferred import: the metadata registry imports IN_SCOPE_SECTIONS /
    # SENSITIVE_LEAF_KEYS from this module, so importing it at module top would
    # be a circular import.
    from src.settings.metadata import default_config

    defaults = default_config()
    db_settings = storage.settings.list()

    for section in IN_SCOPE_SECTIONS:
        section_defaults = defaults.get(section, {})
        yaml_section = config.get(section)
        if isinstance(yaml_section, dict):
            merged = deep_merge(section_defaults, yaml_section)
        else:
            # A non-dict (or absent) YAML section cannot deep-merge onto the
            # dict defaults — fall back to the const defaults and let any DB
            # leaves overlay on top.
            merged = copy.deepcopy(section_defaults)

        section_prefix = f"{section}."
        for db_key, db_value in db_settings.items():
            if not db_key.startswith(section_prefix):
                continue
            rel_path = tuple(db_key[len(section_prefix) :].split("."))
            set_leaf(merged, rel_path, db_value)

        if section == "logging":
            _relocate_pre_move_log_file(merged)
        config[section] = merged
