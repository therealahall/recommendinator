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
