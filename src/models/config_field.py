"""Shared configuration field definition for plugins and providers."""

from dataclasses import dataclass
from typing import Any


@dataclass
class ConfigField:
    """Configuration field definition for a plugin or provider.

    Describes a configuration option that a plugin/provider requires or accepts.
    Used for validation, documentation, and UI generation.
    """

    name: str
    field_type: type
    required: bool = True
    default: Any = None
    description: str = ""
    sensitive: bool = False  # For API keys, passwords - don't log/display
    # Changing this field invalidates the source's stored credentials: they were
    # issued for one host under one set of transport guarantees, and replaying
    # them elsewhere is how a rewritable url becomes credential theft.
    credential_bound: bool = False
    # This field's value is opened from disk, so it must be resolved through
    # ``src.ingestion.paths``. Declared rather than guessed from the name: a
    # field the containment sweep cannot see is an arbitrary-read primitive.
    reads_path: bool = False
