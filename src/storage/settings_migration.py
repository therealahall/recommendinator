"""Seed global/system config into DB storage and overlay it back on boot.

**Key scheme: dotted leaf paths.** Each in-scope config section is flattened
to its leaf values and stored one row per leaf under a namespaced key
``"<section>.<path>.<to>.<leaf>"`` (e.g. ``"web.port"`` or
``"recommendations.scorer_weights.genre_match"``). A *leaf* is any value that
is not a dict — scalars, lists, and ``None`` are stored whole; only dicts are
descended into. This key-level granularity (rather than storing a whole
section under one key) is deliberate: it lets a future app version add a new
key to a section in ``example.yaml`` and have it seeded and flow through to the
running config, while any value already edited/stored in the DB still wins at
its own leaf.
"""

from __future__ import annotations

import copy
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from src.storage.manager import StorageManager

# Global/system config sections that are persisted in the database. The
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
# plaintext ``settings`` table — they stay in config.yaml (the status quo for
# global config) so a real API key is never persisted unencrypted. Sensitive
# *source* credentials are handled separately by ``credential_migration`` via
# the encrypted ``credentials`` table; this denylist only guards the global
# settings seed (today only ``enrichment.providers.*.api_key`` matches).
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


def _flatten_leaves(value: Any, prefix: str) -> dict[str, Any]:
    """Flatten a nested config value into dotted leaf-path -> leaf-value pairs.

    Dicts are descended into; every non-dict value (scalar, list, ``None``) is
    a leaf stored under its dotted path. Empty dicts produce no leaves. Leaves
    whose final key name is in :data:`SENSITIVE_LEAF_KEYS` are skipped — they
    must never reach the plaintext settings table.

    Args:
        value: The config (sub)tree to flatten.
        prefix: The dotted key prefix accumulated so far.

    Returns:
        Mapping of dotted leaf path to leaf value (sensitive leaves excluded).
    """
    if not isinstance(value, dict):
        return {prefix: value}
    leaves: dict[str, Any] = {}
    for key, child in value.items():
        if key in SENSITIVE_LEAF_KEYS:
            continue
        leaves.update(_flatten_leaves(child, f"{prefix}.{key}"))
    return leaves


def _set_leaf(section: dict[str, Any], path: tuple[str, ...], value: Any) -> None:
    """Write *value* into *section* at the nested *path*, creating dicts as needed.

    Args:
        section: The section dict to mutate.
        path: Tuple of keys describing the nested location (relative to section).
        value: The leaf value to set.
    """
    node = section
    for key in path[:-1]:
        existing = node.get(key)
        if not isinstance(existing, dict):
            existing = {}
            node[key] = existing
        node = existing
    node[path[-1]] = value


def migrate_config_settings(
    config: dict[str, Any],
    storage: StorageManager,
) -> None:
    """Seed missing global config leaves into the DB, then overlay the DB back.

    For each in-scope section present in *config*, every leaf (see the module
    docstring for the dotted-path scheme) is written to the ``settings`` table
    only when no row exists for that leaf key yet — a leaf already in the DB is
    never overwritten (an atomic ``INSERT OR IGNORE`` per leaf). After seeding,
    each in-scope section is rebuilt by deep-merging the DB leaves on top of the
    YAML/default section, so a leaf stored in the DB wins while new YAML leaves
    still flow through.

    Sensitive leaves (see :data:`SENSITIVE_LEAF_KEYS`, e.g. provider
    ``api_key``) are excluded from the seed entirely, so their plaintext value
    is never written to the DB. Because they are never in the DB, the overlay
    leaves the YAML value intact in the running *config*.

    This is safe to call on every startup and on config hot-reload.

    **Mutates *config* in place:** each in-scope section is replaced with the
    deep-merged result so existing ``config[section][key]`` read sites get
    DB-backed data.

    Args:
        config: Full application config dict (from ``load_config``).
            Mutated in place — in-scope sections are overlaid from the DB.
        storage: StorageManager instance (provides the settings store).
    """
    sections = [s for s in IN_SCOPE_SECTIONS if s in config]

    # Seed every missing leaf first so the single overlay pass below sees the
    # values it just wrote without re-scanning the table per section. seed_setting
    # is an atomic INSERT OR IGNORE — it never overwrites an existing leaf, so no
    # per-leaf has_setting read is needed (one write per leaf instead of two).
    for section in sections:
        raw_section = config[section]
        # Only dict sections have leaves to seed. A non-dict section (malformed
        # config) is left to the overlay pass below, which replaces it with an
        # empty dict — seeding it here would write an orphan, never-overlaid row.
        if not isinstance(raw_section, dict):
            continue
        for leaf_key, leaf_value in _flatten_leaves(raw_section, section).items():
            storage.seed_setting(leaf_key, leaf_value)

    db_settings = storage.list_settings()
    for section in sections:
        raw_section = config[section]
        # A leaf must be placed under a dict. If the YAML section value is not a
        # dict (malformed config), start from an empty dict so DB leaves still
        # overlay instead of raising.
        merged: dict[str, Any] = (
            copy.deepcopy(raw_section) if isinstance(raw_section, dict) else {}
        )
        section_prefix = f"{section}."
        for db_key, db_value in db_settings.items():
            if not db_key.startswith(section_prefix):
                continue
            rel_path = tuple(db_key[len(section_prefix) :].split("."))
            _set_leaf(merged, rel_path, db_value)
        config[section] = merged
