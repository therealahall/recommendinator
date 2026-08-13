"""The CLI's command groups, one module each, re-exported for ``src.cli.main``.

Each module is underscore-prefixed because the name it exports would shadow
it here: ``commands.auth`` has to be the group, not the module holding it.
"""

from src.cli.commands._auth import auth
from src.cli.commands._chat import chat
from src.cli.commands._complete import complete
from src.cli.commands._enrichment import enrichment
from src.cli.commands._library import library
from src.cli.commands._memory import memory
from src.cli.commands._preferences import preferences
from src.cli.commands._profile import profile
from src.cli.commands._recommend import recommend
from src.cli.commands._settings import settings
from src.cli.commands._source import source
from src.cli.commands._status import status
from src.cli.commands._update import update

__all__ = [
    "auth",
    "chat",
    "complete",
    "enrichment",
    "library",
    "memory",
    "preferences",
    "profile",
    "recommend",
    "settings",
    "source",
    "status",
    "update",
]
