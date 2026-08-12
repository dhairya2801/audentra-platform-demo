"""Typed presentation blocks with plain-text fallbacks.

The portal renders these; `message` stays the plain-text channel. Deliberately
not markdown: the assistant never authors layout, so malformed markup is
unrepresentable and a table can never arrive as a wall of pipe characters.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

JsonDict = dict[str, Any]


def text_block(text: str) -> JsonDict:
    return {"type": "text", "fallbackText": text, "text": text}


def bullet_list_block(items: Sequence[Mapping[str, Any]], *, title: str | None = None) -> JsonDict:
    cleaned = [_list_item(item) for item in items if str(item.get("text", "")).strip()]
    fallback = "\n".join(f"- {item['text']}" for item in cleaned)
    if title:
        fallback = f"{title}\n{fallback}"
    block: JsonDict = {"type": "bullet_list", "fallbackText": fallback, "items": cleaned}
    if title:
        block["title"] = title
    return block


def numbered_list_block(
    items: Sequence[Mapping[str, Any]], *, title: str | None = None
) -> JsonDict:
    cleaned = [_list_item(item) for item in items if str(item.get("text", "")).strip()]
    fallback = "\n".join(f"{index + 1}. {item['text']}" for index, item in enumerate(cleaned))
    if title:
        fallback = f"{title}\n{fallback}"
    block: JsonDict = {"type": "numbered_list", "fallbackText": fallback, "items": cleaned}
    if title:
        block["title"] = title
    return block


def next_steps_block(items: Sequence[Mapping[str, Any]], *, title: str | None = None) -> JsonDict:
    cleaned = []
    for item in items:
        if not str(item.get("text", "")).strip():
            continue
        entry = _list_item(item)
        owner = item.get("owner")
        if owner in {"student", "university"}:
            entry["owner"] = owner
        cleaned.append(entry)
    fallback = "\n".join(f"{index + 1}. {item['text']}" for index, item in enumerate(cleaned))
    if title:
        fallback = f"{title}\n{fallback}"
    block: JsonDict = {"type": "next_steps", "fallbackText": fallback, "items": cleaned}
    if title:
        block["title"] = title
    return block


def table_block(
    columns: Sequence[Mapping[str, Any]],
    rows: Sequence[Mapping[str, str]],
    *,
    caption: str | None = None,
) -> JsonDict:
    cleaned_columns = [
        {
            "key": str(column["key"]),
            "label": str(column["label"]),
            **({"align": column["align"]} if column.get("align") in {"left", "right"} else {}),
        }
        for column in columns
    ]
    cleaned_rows = [
        {str(key): str(value) for key, value in row.items() if value is not None} for row in rows
    ]
    lines = [
        " | ".join(str(row.get(column["key"], "")) for column in cleaned_columns)
        for row in cleaned_rows
    ]
    fallback = "\n".join(
        [
            *([caption] if caption else []),
            " | ".join(column["label"] for column in cleaned_columns),
            *lines,
        ]
    )
    block: JsonDict = {
        "type": "table",
        "fallbackText": fallback,
        "columns": cleaned_columns,
        "rows": cleaned_rows,
    }
    if caption:
        block["caption"] = caption
    return block


def render_blocks_as_text(blocks: Sequence[Mapping[str, Any]]) -> str:
    """The plain-text rendering every client can fall back to."""

    return "\n\n".join(
        str(block.get("fallbackText", "")).strip()
        for block in blocks
        if str(block.get("fallbackText", "")).strip()
    )


def _list_item(item: Mapping[str, Any]) -> JsonDict:
    entry: JsonDict = {"text": str(item.get("text", "")).strip()}
    href = item.get("href")
    if isinstance(href, str) and href.startswith("/"):
        entry["href"] = href
    return entry
