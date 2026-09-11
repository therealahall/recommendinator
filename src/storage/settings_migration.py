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
# itself and must stay in YAML/env.
IN_SCOPE_SECTIONS: tuple[str, ...] = (
    "recommendations",
    "sync",
    "enrichment",
    "web",
    "logging",
)


#: Where ``logging.file`` pointed before the log moved under the ``data/`` mount.
_PRE_MOVE_LOG_DIR = "logs/"


def _relocate_pre_move_log_file(section: dict[str, Any]) -> None:
    """Nothing rewrites the row holding it, so otherwise containment discards
    that file name for the default on every boot while the Settings page still
    shows the old path.
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
    # Deferred import: the metadata registry imports IN_SCOPE_SECTIONS from this
    # module, so importing it at module top would be a circular import.
    from src.settings.metadata import default_config

    defaults = default_config()
    db_settings = storage.settings.list()

    for section in IN_SCOPE_SECTIONS:
        section_defaults = defaults.get(section, {})
        loaded_section = config.get(section)
        if isinstance(loaded_section, dict):
            # Merged rather than replaced so the bootstrap leaves ``web`` also
            # carries — host, port, debug — survive the overlay.
            merged = deep_merge(section_defaults, loaded_section)
        else:
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
