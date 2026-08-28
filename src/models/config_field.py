from dataclasses import dataclass
from typing import Any


@dataclass
class ConfigField:
    name: str
    field_type: type
    required: bool = True
    default: Any = None
    description: str = ""
    sensitive: bool = False  # For API keys, passwords - don't log/display
    # This field names the host the source's credentials are sent to.
    credential_bound: bool = False
    # This field's value is opened from disk, so it must be resolved through
    # ``src.ingestion.paths``. Declared rather than guessed from the name: a
    # field the containment sweep cannot see is an arbitrary-read primitive.
    reads_path: bool = False
