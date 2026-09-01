from fastapi import APIRouter, Depends

from src.web.api import (
    _duplicates,
    _enrichment,
    _import,
    _library,
    _oauth,
    _preferences,
    _profile,
    _recommendations,
    _settings,
    _sources,
    _status,
    _sync,
    _themes,
    _users,
)
from src.web.auth import require_session
from src.web.csrf import refuse_cross_origin

# On the router rather than at ``include_router``: a route is then
# authenticated by being registered, even where a test mounts this router bare.
router = APIRouter(
    prefix="/api",
    tags=["api"],
    dependencies=[Depends(require_session), Depends(refuse_cross_origin)],
)

router.include_router(_duplicates.router)
router.include_router(_enrichment.router)
router.include_router(_import.router)
router.include_router(_library.router)
router.include_router(_oauth.router)
router.include_router(_preferences.router)
router.include_router(_profile.router)
router.include_router(_recommendations.router)
router.include_router(_settings.router)
router.include_router(_sources.router)
router.include_router(_status.router)
router.include_router(_sync.router)
router.include_router(_themes.router)
router.include_router(_users.router)
