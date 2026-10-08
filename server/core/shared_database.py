"""PostgreSQL timestamp compatibility for existing SQLModel UTC values."""
from datetime import datetime, timezone

from sqlalchemy import DateTime
from sqlalchemy.types import TypeDecorator


class PostgreSQLUTCDateTime(TypeDecorator):
    """Keep SQLite unchanged; accept both existing UTC timestamp conventions."""
    impl = DateTime
    cache_ok = True

    def __init__(self, original: DateTime):
        super().__init__()
        self.original = original

    def load_dialect_impl(self, dialect):
        return dialect.type_descriptor(DateTime(timezone=True) if dialect.name == "postgresql" else self.original)

    def process_bind_param(self, value: datetime | None, dialect):
        if value is not None and dialect.name == "postgresql":
            return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)
        return value


def install_postgresql_timestamp_types(metadata):
    for table in metadata.tables.values():
        for column in table.columns:
            if isinstance(column.type, DateTime):
                column.type = PostgreSQLUTCDateTime(column.type)
