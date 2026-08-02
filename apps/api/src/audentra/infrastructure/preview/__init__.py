"""Development-preview adapters that are never enabled in production."""

from .staff_workspace import PreviewStaffWorkspaceRepository

__all__ = ["PreviewStaffWorkspaceRepository"]
