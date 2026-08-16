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
    row_hrefs: Sequence[str | None] | None = None,
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
    if row_hrefs is not None:
        # Same rule as list-item hrefs: an internal portal route or nothing.
        cleaned_hrefs: list[str | None] = [
            href if isinstance(href, str) and href.startswith("/") else None
            for href in row_hrefs[: len(cleaned_rows)]
        ]
        cleaned_hrefs.extend([None] * (len(cleaned_rows) - len(cleaned_hrefs)))
        if any(cleaned_hrefs):
            block["rowHrefs"] = cleaned_hrefs
    return block


def describe_blocks_for_prompt(blocks: Sequence[Mapping[str, Any]]) -> list[str]:
    """One line per non-text block, for the rewrite model's context.

    The rewrite model writes the prose that renders *above* these blocks, so
    it needs to know a table or list is already carrying the collection —
    shape and size only, never the row contents, which would invite
    re-listing them.
    """

    lines: list[str] = []
    for block in blocks:
        kind = str(block.get("type") or "")
        if kind in {"", "text"}:
            continue
        if kind == "table":
            rows = block.get("rows") or []
            columns = ", ".join(str(column.get("label", "")) for column in block.get("columns", []))
            name = str(block.get("caption") or "").strip()
            lines.append(
                f"A table{f' “{name}”' if name else ''} with {len(rows)} row(s)"
                + (f" (columns: {columns})" if columns else "")
                + "."
            )
        elif kind in {"bullet_list", "numbered_list", "next_steps"}:
            items = block.get("items") or []
            noun = "a next-steps list" if kind == "next_steps" else "a list"
            name = str(block.get("title") or "").strip()
            lines.append(
                f"{noun[0].upper()}{noun[1:]}{f' “{name}”' if name else ''} "
                f"with {len(items)} item(s)."
            )
        elif kind == "draft":
            channel = str(block.get("channel") or "message")
            lines.append(f"A reviewable {channel} draft panel.")
        else:
            lines.append(f"A {kind} block.")
    return lines


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
