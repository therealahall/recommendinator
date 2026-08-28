"""Each module is underscore-prefixed because the name it exports would shadow
it here: ``commands.auth`` has to be the group, not the module holding it.
"""

from src.cli.commands._account import account
from src.cli.commands._auth import auth
from src.cli.commands._complete import complete
from src.cli.commands._enrichment import enrichment
from src.cli.commands._import import import_command, import_formats, import_template
from src.cli.commands._library import library
from src.cli.commands._preferences import preferences
from src.cli.commands._profile import profile
from src.cli.commands._recommend import recommend
from src.cli.commands._settings import settings
from src.cli.commands._source import source
from src.cli.commands._status import status
from src.cli.commands._theme import theme
from src.cli.commands._update import update

__all__ = [
    "account",
    "auth",
    "complete",
    "enrichment",
    "import_command",
    "import_formats",
    "import_template",
    "library",
    "preferences",
    "profile",
    "recommend",
    "settings",
    "source",
    "status",
    "theme",
    "update",
]
