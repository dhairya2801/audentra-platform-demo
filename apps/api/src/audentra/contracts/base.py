"""Pydantic conventions for the camelCase public API."""

from pydantic import BaseModel, ConfigDict


def to_camel(value: str) -> str:
    head, *tail = value.split("_")
    return head + "".join(part.capitalize() for part in tail)


class StrictRequest(BaseModel):
    model_config = ConfigDict(
        alias_generator=to_camel,
        extra="forbid",
        populate_by_name=False,
        validate_by_alias=True,
        validate_by_name=False,
    )

    def public_payload(self) -> dict[str, object]:  # type: ignore[pydantic-alias]
        return self.model_dump(mode="json", by_alias=True, exclude_unset=True)
