"""Authentication middleware for route protection."""

import logging
from fastapi import Request
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware

from core.container import container

logger = logging.getLogger(__name__)

# Public routes that don't require authentication
PUBLIC_PATHS = frozenset(
    [
        "/health",
        "/docs",
        "/openapi.json",
        "/redoc",
        "/api/auth/status",
        "/api/auth/login",
        "/api/auth/register",
        "/api/auth/logout",
        "/ws/internal",  # Internal WebSocket for Temporal workers
    ]
)

# Path prefixes that are public
PUBLIC_PREFIXES = ("/webhook/",)


class AuthMiddleware(BaseHTTPMiddleware):
    """Middleware to protect routes requiring authentication."""

    async def dispatch(self, request: Request, call_next):
        path = request.url.path

        # Allow public paths
        if self._is_public_path(path):
            return await call_next(request)

        # Get settings
        settings = container.settings()

        # Check if auth is disabled (VITE_AUTH_ENABLED=false)
        if settings.vite_auth_enabled and settings.vite_auth_enabled.lower() == "false":
            # Auth disabled - set anonymous user and allow request
            request.state.user_id = 0
            request.state.user_email = "anonymous"
            request.state.is_owner = True
            return await call_next(request)

        # Auth enabled - check token
        token = request.cookies.get(settings.jwt_cookie_name)

        if not token:
            return JSONResponse(status_code=401, content={"detail": "Not authenticated"})

        # Verify token
        user_auth = container.user_auth_service()
        payload = user_auth.verify_token(token)

        if not payload:
            return JSONResponse(status_code=401, content={"detail": "Invalid or expired session"})

        # Attach user info to request state for downstream handlers
        request.state.user_id = payload.get("sub")
        request.state.user_email = payload.get("email")
        request.state.is_owner = payload.get("is_owner", False)

        return await call_next(request)

    def _is_public_path(self, path: str) -> bool:
        """Check if path is public (no auth required)."""
        # Exact match
        if path in PUBLIC_PATHS:
            return True

        # Prefix match
        for prefix in PUBLIC_PREFIXES:
            if path.startswith(prefix):
                return True

        return False
