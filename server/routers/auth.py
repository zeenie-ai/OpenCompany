"""Authentication routes for user login, registration, and session management."""

from fastapi import APIRouter, Depends, HTTPException, Response, Request
from pydantic import BaseModel, EmailStr

from core.container import container
from core.config import Settings
from core.logging import get_logger
from services.user_auth import UserAuthService
from services.auth import AuthService

logger = get_logger(__name__)
router = APIRouter(prefix="/api/auth", tags=["auth"])


class RegisterRequest(BaseModel):
    email: EmailStr
    password: str
    display_name: str


class LoginRequest(BaseModel):
    email: EmailStr
    password: str


class UserResponse(BaseModel):
    id: int
    email: str
    display_name: str
    is_owner: bool


def get_user_auth_service() -> UserAuthService:
    return container.user_auth_service()


def get_settings() -> Settings:
    return container.settings()


def get_auth_service() -> AuthService:
    return container.auth_service()


@router.get("/status")
async def get_auth_status(
    request: Request, user_auth: UserAuthService = Depends(get_user_auth_service), settings: Settings = Depends(get_settings)
):
    """
    Get authentication status.
    Returns auth mode and current user if authenticated.
    """
    status = user_auth.get_auth_status()

    # Check if user has a valid session
    token = request.cookies.get(settings.jwt_cookie_name)
    current_user = None

    if token:
        user = await user_auth.get_current_user(token)
        if user:
            current_user = {"id": user.id, "email": user.email, "display_name": user.display_name, "is_owner": user.is_owner}

    # Check if registration is available
    can_register = await user_auth.can_register()

    # Determine if auth is enabled from server config
    auth_enabled = True
    if settings.vite_auth_enabled and settings.vite_auth_enabled.lower() == "false":
        auth_enabled = False

    return {
        "auth_enabled": auth_enabled,
        "auth_mode": status["auth_mode"],
        "authenticated": current_user is not None,
        "user": current_user,
        "can_register": can_register,
    }


@router.post("/register")
async def register(
    request: RegisterRequest,
    response: Response,
    user_auth: UserAuthService = Depends(get_user_auth_service),
    settings: Settings = Depends(get_settings),
):
    """
    Register a new user.
    In single-owner mode, only the first user can register.
    In multi-user mode, anyone can register.
    """
    user, error = await user_auth.register(email=request.email, password=request.password, display_name=request.display_name)

    if error:
        raise HTTPException(status_code=400, detail=error)

    # Create token and set cookie
    token = user_auth.create_access_token(user)
    response.set_cookie(
        key=settings.jwt_cookie_name,
        value=token,
        httponly=True,
        secure=settings.jwt_cookie_secure,
        samesite=settings.jwt_cookie_samesite,
        max_age=settings.jwt_expire_minutes * 60,
    )

    return {"success": True, "user": {"id": user.id, "email": user.email, "display_name": user.display_name, "is_owner": user.is_owner}}


@router.post("/login")
async def login(
    request: LoginRequest,
    response: Response,
    user_auth: UserAuthService = Depends(get_user_auth_service),
    settings: Settings = Depends(get_settings),
):
    """
    Login with email and password.
    Sets HttpOnly cookie with JWT token.
    """
    user, error = await user_auth.login(email=request.email, password=request.password)

    if error:
        raise HTTPException(status_code=401, detail=error)

    # Create token and set cookie
    token = user_auth.create_access_token(user)
    response.set_cookie(
        key=settings.jwt_cookie_name,
        value=token,
        httponly=True,
        secure=settings.jwt_cookie_secure,
        samesite=settings.jwt_cookie_samesite,
        max_age=settings.jwt_expire_minutes * 60,
    )

    return {"success": True, "user": {"id": user.id, "email": user.email, "display_name": user.display_name, "is_owner": user.is_owner}}


@router.post("/logout")
async def logout(
    response: Response,
    user_auth: UserAuthService = Depends(get_user_auth_service),
    auth_service: AuthService = Depends(get_auth_service),
    settings: Settings = Depends(get_settings),
):
    """
    Logout by clearing the auth cookie, encryption key, and memory caches.

    Clears:
    - Auth cookie
    - Encryption service key (prevents credential decryption)
    - API key memory cache (removes decrypted keys from memory)
    """
    # Clear encryption key from memory
    user_auth.logout()

    # Clear API key memory cache
    auth_service.clear_cache()

    # Delete auth cookie
    response.delete_cookie(
        key=settings.jwt_cookie_name, httponly=True, secure=settings.jwt_cookie_secure, samesite=settings.jwt_cookie_samesite
    )
    return {"success": True}


@router.get("/me")
async def get_current_user(
    request: Request, user_auth: UserAuthService = Depends(get_user_auth_service), settings: Settings = Depends(get_settings)
):
    """
    Get current authenticated user.
    Requires valid session cookie.
    """
    token = request.cookies.get(settings.jwt_cookie_name)
    if not token:
        raise HTTPException(status_code=401, detail="Not authenticated")

    user = await user_auth.get_current_user(token)
    if not user:
        raise HTTPException(status_code=401, detail="Invalid or expired session")

    return {"id": user.id, "email": user.email, "display_name": user.display_name, "is_owner": user.is_owner}
