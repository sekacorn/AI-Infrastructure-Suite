"""Shared primitives for reading local, operator-supplied evidence files.

Every helper here is offline and bounded. Path validation is security relevant,
so it lives in exactly one place rather than being copied per scanner.
"""

from __future__ import annotations

import json
import re
import stat
from pathlib import Path
from typing import Any

MAX_PATH_CHARS = 240
_SEGMENT = re.compile(r"[a-zA-Z0-9_][a-zA-Z0-9_.-]*")
_RESERVED = re.compile(r"CON|PRN|AUX|NUL|(?:COM|LPT)[1-9]", re.IGNORECASE)


def portable_relative_path(name: object) -> str:
    """Return ``name`` when it is a portable, project-relative evidence path."""
    if not isinstance(name, str) or not name or len(name) > MAX_PATH_CHARS:
        raise ValueError("Invalid evidence path.")
    for part in name.split("/"):
        if (
            not _SEGMENT.fullmatch(part)
            or part.endswith(".")
            or _RESERVED.fullmatch(part.split(".")[0])
        ):
            raise ValueError("Expected a portable project-relative evidence path.")
    return name


def read_bounded_file(path: Path, limit: int) -> bytes:
    """Read at most ``limit`` bytes from a regular local file.

    Rejects traversal, UNC prefixes, symbolic links, Windows reparse points,
    non-regular files, and anything larger than ``limit``.
    """
    if str(path).startswith(("//", "\\\\")) or ".." in path.parts:
        raise ValueError("Expected a local path without traversal.")
    path = path.absolute()
    for part in (*reversed(path.parents), path):
        info = part.lstat()
        if stat.S_ISLNK(info.st_mode) or (
            getattr(info, "st_file_attributes", 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT
        ):
            raise ValueError("Linked files and directories are unsupported.")
    if not stat.S_ISREG(info.st_mode) or info.st_size > limit:
        raise ValueError("Expected a bounded regular file.")
    with path.open("rb") as stream:
        data = stream.read(limit + 1)
    if len(data) > limit:
        raise ValueError("Input exceeds the file size limit.")
    return data


def unique_json_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    """Build a JSON object, rejecting duplicate field names."""
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate JSON field.")
        result[key] = value
    return result


def _reject_constant(literal: str) -> Any:
    raise ValueError(f"Non-finite JSON number {literal} is unsupported.")


def load_json_document(data: bytes, label: str) -> Any:
    """Parse UTF-8 JSON, rejecting duplicate fields and non-finite literals."""
    try:
        return json.loads(
            data.decode("utf-8"),
            object_pairs_hook=unique_json_pairs,
            parse_constant=_reject_constant,
        )
    except UnicodeDecodeError as exc:
        raise ValueError(f"Expected UTF-8 {label}.") from exc
    except json.JSONDecodeError as exc:
        raise ValueError(f"Expected well-formed {label}.") from exc
