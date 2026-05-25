"""User authentication models."""

from datetime import datetime, timezone
from typing import Optional
from sqlmodel import SQLModel, Field, Column, DateTime
from sqlalchemy import func
import bcrypt


class User(SQLModel, table=True):
    """User account for authentication."""

    __tablename__ = "users"

    id: Optional[int] = Field(default=None, primary_key=True)
    email: str = Field(unique=True, index=True, max_length=255)
    password_hash: str = Field(max_length=255)
    display_name: str = Field(max_length=100)
    is_owner: bool = Field(default=False)  # First user in single-owner mode
    is_active: bool = Field(default=True)
    created_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc), sa_column=Column(DateTime(timezone=True), server_default=func.now())
    )
    last_login: Optional[datetime] = Field(default=None, sa_column=Column(DateTime(timezone=True), nullable=True))

    def set_password(self, password: str) -> None:
        """Hash and set password using bcrypt."""
        salt = bcrypt.gensalt()
        self.password_hash = bcrypt.hashpw(password.encode("utf-8"), salt).decode("utf-8")

    def verify_password(self, password: str) -> bool:
        """Verify password against stored hash."""
        return bcrypt.checkpw(password.encode("utf-8"), self.password_hash.encode("utf-8"))

    @classmethod
    def create(cls, email: str, password: str, display_name: str, is_owner: bool = False) -> "User":
        """Factory method to create a user with hashed password."""
        user = cls(
            email=email.lower().strip(),
            password_hash="",  # Will be set below
            display_name=display_name.strip(),
            is_owner=is_owner,
        )
        user.set_password(password)
        return user
