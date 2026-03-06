"""Recommendinator.

A privacy-focused recommendation engine for books, movies, TV shows, and video games.
"""

from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as _pkg_version

try:
    __version__: str = _pkg_version("recommendinator")
except PackageNotFoundError:
    __version__ = "0.0.0"
