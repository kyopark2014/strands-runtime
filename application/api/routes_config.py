import logging

from fastapi import APIRouter, HTTPException, Request

from application.api.routes_auth import get_optional_user_id
from application.services.config_service import (
    get_application_config,
    get_public_config,
)

logger = logging.getLogger("routes_config")
router = APIRouter(prefix="/api/config", tags=["config"])


@router.get("")
def get_config(request: Request):
    """Return public auth bootstrap fields; full catalogs only when logged in.

    Unauthenticated clients get only what the login screen needs (project name).
    Model / MCP / skill / strands catalogs require a session.
    """
    try:
        user_id = get_optional_user_id(request)
        if not user_id:
            return get_public_config()
        return get_application_config(user_id)
    except Exception:
        logger.exception("Failed to load application config")
        raise HTTPException(
            status_code=500, detail="Failed to load application config"
        ) from None
