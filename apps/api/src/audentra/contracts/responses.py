"""Stable error contracts shared by every router."""

from pydantic import BaseModel, ConfigDict


class ApiErrorBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    code: str
    message: str
    requestId: str


class ApiErrorEnvelope(BaseModel):
    model_config = ConfigDict(extra="forbid")

    error: ApiErrorBody
