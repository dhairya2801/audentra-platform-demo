"""Production PostgreSQL repositories and application service."""

from .platform_repository import PostgresPlatformRepository
from .portal_repository import PostgresPortalRepository
from .postgres_service import (
    PostgresPlatformService,
    PostgresRepositoryBundle,
    PostgresSignedDocumentGenerator,
)
from .staff_repository import PostgresStaffRepository

__all__ = [
    "PostgresPlatformRepository",
    "PostgresPlatformService",
    "PostgresPortalRepository",
    "PostgresRepositoryBundle",
    "PostgresSignedDocumentGenerator",
    "PostgresStaffRepository",
]
