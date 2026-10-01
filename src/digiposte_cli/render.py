"""Human-readable rendering of the document list.

The `list` command prints the vault as a tree: one branch per location (SAFE,
INBOX…), the documents ordered by a chosen key (metadata date, file name or
size) and a check mark telling whether the document is already on disk.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from rich.console import Console
from rich.markup import escape
from rich.tree import Tree

from digiposte_cli.paths import display_path

# Metadata date fields exposed by the vault API, from the most meaningful to
# the least. The listing routes (`/documents/{location}`) answer a *different,
# snake_case* shape than the `Document` schema of the swagger (camelCase): the
# listing carries `creation_date`, so it is checked first, then the camelCase
# fields of the detail schema (`publishedOrCreationDate` is Digiposte's own
# "published or created" composite, then the explicit publication, creation,
# receipt (`timestamp`) and last-update dates). The first field present wins.
_DATE_FIELDS = (
    "creation_date",
    "publishedOrCreationDate",
    "publishedAt",
    "createdAt",
    "timestamp",
    "updatedAt",
)

# Documents without a usable date are sorted as if they were the oldest.
_UNKNOWN_DATE = datetime(1970, 1, 1)

# Accepted `--sort` keys and their **natural** direction (the one used without
# `--reverse`): date = newest first, name = A→Z (file name), size = largest
# first.
SORT_KEYS = ("date", "name", "size")
_SORT_REVERSE = {"date": True, "name": False, "size": True}

# Longest file name kept as-is for the column alignment; beyond that the name
# is not padded so a single very long name does not push the other columns far
# to the right of every line.
_MAX_NAME_WIDTH = 60

# Fixed width of the human-readable size column (e.g. "  1.2 MB").
_SIZE_WIDTH = 9

_MARK_DOWNLOADED = "[green]✓[/green]"
_MARK_MISSING = "[yellow]✗[/yellow]"

_UNITS = ("B", "KB", "MB", "GB", "TB")


@dataclass(frozen=True)
class DocumentEntry:
    """A document ready to render: file name, metadata date, size, local
    presence."""

    name: str
    date: datetime | None
    size: int | None
    downloaded: bool


def document_date(doc: dict[str, Any]) -> datetime | None:
    """Return the best metadata date of a document, or None when the API
    exposes none.

    The ISO-8601 value is normalized to a naive UTC datetime so values with
    and without a timezone stay mutually comparable."""
    for field in _DATE_FIELDS:
        raw = doc.get(field)
        if not isinstance(raw, str) or not raw:
            continue
        try:
            value = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        except ValueError:
            continue
        if value.tzinfo is not None:
            value = value.astimezone(timezone.utc).replace(tzinfo=None)
        return value
    return None


def document_size(doc: dict[str, Any]) -> int | None:
    """Size in bytes of a document, or None when the API does not report it."""
    size = doc.get("size")
    return size if isinstance(size, int) else None


def document_title(doc: dict[str, Any]) -> str:
    """Display title of a document (its vault title, else its id). Used as the
    default name source when no file-name builder is provided."""
    title = doc.get("title")
    if isinstance(title, str) and title:
        return title
    return str(doc.get("id") or "document")


def sort_documents(
    documents: list[dict[str, Any]],
    *,
    sort_key: str = "date",
    reverse: bool = False,
    name_of: Callable[[dict[str, Any]], str] | None = None,
) -> list[dict[str, Any]]:
    """Return the documents sorted by `sort_key` (`date`, `name` or `size`).

    Each key has a natural direction (`date`: newest first, `name`: A→Z,
    `size`: largest first); `reverse=True` flips it. Documents without a
    usable date/size are ordered as the oldest/smallest. `name_of` builds the
    file name used for `name` sorting (`api.file_name_for`), defaulting to the
    vault title."""
    get_name = name_of or document_title

    def key(doc: dict[str, Any]) -> Any:
        if sort_key == "name":
            return get_name(doc).casefold()
        if sort_key == "size":
            return document_size(doc) or 0
        return document_date(doc) or _UNKNOWN_DATE

    return sorted(documents, key=key, reverse=_SORT_REVERSE[sort_key] != reverse)


def human_size(size: int | None) -> str:
    """Human-readable size (`262 KB`, `1.2 MB`); `—` when unknown."""
    if size is None:
        return "—"
    value = float(size)
    for unit in _UNITS[:-1]:
        if value < 1024:
            return f"{value:.0f} {unit}" if unit == "B" else f"{value:.1f} {unit}"
        value /= 1024
    return f"{value:.1f} {_UNITS[-1]}"


def render_document_tree(
    entries_by_location: dict[str, list[DocumentEntry]],
    *,
    download_dir: Path,
    locations: list[str],
    console: Console | None = None,
) -> None:
    """Print the document tree: the download dir as root (home shortened to
    `~`), one branch per location (configured locations first, even when
    empty), one leaf per document (check mark + file name + size + metadata
    date)."""
    console = console or Console(soft_wrap=True)
    total = sum(len(entries) for entries in entries_by_location.values())
    root = Tree(
        f"[bold]{escape(display_path(download_dir))}[/bold] [dim]({total} document(s))[/dim]"
    )

    ordered = list(locations)
    ordered += [loc for loc in entries_by_location if loc not in ordered]
    name_width = _name_width(entries_by_location)
    for location in ordered:
        entries = entries_by_location.get(location, [])
        branch = root.add(f"[bold]{escape(location)}[/bold] [dim]({len(entries)})[/dim]")
        for entry in entries:
            branch.add(_entry_label(entry, name_width))
    console.print(root)


def _name_width(entries_by_location: dict[str, list[DocumentEntry]]) -> int:
    """Longest file name across all locations, capped for the other columns."""
    longest = max(
        (len(entry.name) for entries in entries_by_location.values() for entry in entries),
        default=0,
    )
    return min(longest, _MAX_NAME_WIDTH)


def _entry_label(entry: DocumentEntry, name_width: int) -> str:
    """One document line: check mark, padded file name, size, metadata date."""
    mark = _MARK_DOWNLOADED if entry.downloaded else _MARK_MISSING
    date = entry.date.strftime("%Y-%m-%d") if entry.date else "—"
    size = human_size(entry.size)
    # Pad the raw name before escaping so the displayed columns line up
    # (rich markup such as a literal "[" must not be interpreted).
    padded = f"{entry.name:<{name_width}}"
    return f"{mark} {escape(padded)}  [dim]{size:>{_SIZE_WIDTH}}  {date}[/dim]"
