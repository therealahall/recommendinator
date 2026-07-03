"""Assemble the effective global/system config from const, YAML, and DB layers.

The in-scope global-config sections resolve with precedence
**const default < YAML < database**:

1. Start from the registry const defaults (:func:`src.settings.metadata.default_config`).
2. Deep-merge the loaded YAML config's in-scope sections on top (YAML overrides
   consts).
3. Overlay the database ``settings`` leaves on top (the DB is authoritative).

**Key scheme: dotted leaf paths.** Each stored leaf lives under a namespaced
key ``"<section>.<path>.<to>.<leaf>"`` (e.g. ``"web.port"`` or
``"recommendations.scorer_weights.genre_match"``). A stored leaf wins at its
own path; unknown/legacy DB leaves still overlay.

Nothing is written to the database here — the ``settings`` table holds only the
leaves a user explicitly set later (via the settings UI/CLI). On a fresh
install the table is empty and the app runs purely on the const defaults plus
whatever the operator kept in ``config.yaml``.

Sensitive leaves (see :data:`SENSITIVE_LEAF_KEYS`, e.g. provider ``api_key``)
are never persisted in the plaintext ``settings`` table. This module leaves
them untouched in the assembled config; a companion pass
(:func:`src.storage.global_secrets.migrate_config_secrets`) then sweeps them
into the encrypted ``credentials`` table and strips them from the in-memory
config so no plaintext secret lingers.
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
    "features",
    "ollama",
    "ingestion",
    "recommendations",
    "conversation",
    "sync",
    "enrichment",
    "web",
    "logging",
)

# Leaf key names that may hold a secret. These are NEVER written to the
# plaintext ``settings`` table. Both global settings secrets and per-source
# credentials are relocated into the encrypted ``credentials`` table —
# ``global_secrets.migrate_config_secrets`` handles the global settings surface
# (today only ``enrichment.providers.*.api_key`` matches) and
# ``credential_migration`` handles sensitive *source* credentials.
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


def migrate_config_settings(
    config: dict[str, Any],
    storage: StorageManager,
) -> None:
    """Assemble the effective in-scope config from const, YAML, and DB layers.

    For each in-scope section (see :data:`IN_SCOPE_SECTIONS`) the effective
    value is the registry const default deep-merged with the YAML section
    (YAML wins), then overlaid with the database leaves for that section (the
    DB wins). A section absent from the YAML still resolves fully from the const
    defaults, so a user may trim ``config.yaml`` to bootstrap-only.

    Nothing is written to the database — the ``settings`` table is read-only
    here and stays empty until a user explicitly sets a leaf via the settings
    UI/CLI. This is safe to call on every startup and on config hot-reload.

    The const defaults are merged in here even though ``load_config`` already
    layered them under the YAML. That re-merge is intentional and idempotent: it
    keeps this function independently callable on a bare ``{}`` or a partial
    config (the test suite and hot-reload paths rely on that) without first
    routing through ``load_config``.

    **Mutates *config* in place:** each in-scope section is replaced with the
    assembled result so existing ``config[section][key]`` read sites resolve the
    layered value. Out-of-scope sections (``storage``, ``inputs``) are untouched.

    Args:
        config: Full application config dict (from ``load_config``).
            Mutated in place — in-scope sections are rebuilt from the layers.
        storage: StorageManager instance (provides the settings store).
    """
    # Deferred import: the metadata registry imports IN_SCOPE_SECTIONS /
    # SENSITIVE_LEAF_KEYS from this module, so importing it at module top would
    # be a circular import.
    from src.settings.metadata import default_config

    defaults = default_config()
    db_settings = storage.list_settings()

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

        config[section] = merged
