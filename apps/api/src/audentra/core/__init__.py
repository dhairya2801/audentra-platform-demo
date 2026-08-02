"""Framework-neutral primitives shared by Audentra adapters."""

from .auth import AuthContext
from .errors import ApiError
from .ports import BinaryPayload, FileUpload, PlatformService, ServiceCall

__all__ = [
    "ApiError",
    "AuthContext",
    "BinaryPayload",
    "FileUpload",
    "PlatformService",
    "ServiceCall",
]
