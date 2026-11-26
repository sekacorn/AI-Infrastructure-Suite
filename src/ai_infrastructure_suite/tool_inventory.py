"""Bounded readers for ``tool-inventory.v1`` and ``tool-approvals.v1``.

``tool-inventory.v1`` lists the tools an agent system can call: for each MCP
tool its server, transport, origin and definition hash; for each vendor-only
tool whether a reconstruction path is documented. ``tool-approvals.v1`` is the
file a person commits to approve exact MCP tool definitions by hash. The JSON
Schemas for both ship in ``data/``; these readers enforce what a schema cannot:
uniqueness, canonical origins, and the pairing rules between fields.

Both are read strictly: unknown or duplicate keys, NaN, a malformed hash or an
origin carrying a path, query, fragment or credentials reject the document. No
message repeats document content beyond identifiers. The suite never imports
AgentForge; this is its own reading of the format.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final

from ai_infrastructure_suite._evidence import load_json_document, read_bounded_file
from ai_infrastructure_suite.provider_inventory import ProviderInventoryError, _origin

INVENTORY_SCHEMA_VERSION: Final = "tool-inventory.v1"
APPROVALS_SCHEMA_VERSION: Final = "tool-approvals.v1"
MAX_INVENTORY_BYTES: Final = 4 * 1024 * 1024
MAX_APPROVALS_BYTES: Final = 1024 * 1024
MAX_TOOLS: Final = 1024
MAX_APPROVALS: Final = 4096

TRANSPORTS: Final = frozenset({"in_process", "mcp_stdio", "mcp_streamable_http"})
PORTABILITY: Final = frozenset({"mcp", "vendor_only"})
RECONSTRUCTION: Final = frozenset({"documented", "none"})

_TOOL_ID: Final = re.compile(r"[A-Za-z0-9_-]{1,64}")
_TOOL_NAME: Final = re.compile(r"[A-Za-z0-9_./-]{1,128}")
_SERVER_ID: Final = re.compile(r"[a-z0-9][a-z0-9_-]{0,31}")
_HASH: Final = re.compile(r"[0-9a-f]{64}")
_PERSON: Final = re.compile(r"[^\x00-\x1f\x7f]{1,128}")
_FLAG: Final = re.compile(r"[a-z0-9_]{1,64}")
_TIMESTAMP: Final = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z")

_TOOL_KEYS: Final = frozenset(
    {
        "tool_id",
        "name",
        "server_id",
        "transport",
        "server_origin",
        "definition_sha256",
        "portability",
        "reconstruction",
    }
)
_APPROVAL_KEYS: Final = frozenset(
    {
        "server_id",
        "server_origin",
        "tool_name",
        "definition_sha256",
        "approved_by",
        "approved_at",
        "accepted_flags",
    }
)


class ToolInventoryError(ValueError):
    """A tool inventory or approvals document is invalid."""


@dataclass(frozen=True)
class InventoryTool:
    tool_id: str
    name: str
    server_id: str
    transport: str
    server_origin: str | None
    definition_sha256: str | None
    portability: str
    reconstruction: str | None


@dataclass(frozen=True)
class ToolInventory:
    document_sha256: str
    generated_by_tool: str
    generated_by_version: str
    tools: tuple[InventoryTool, ...]


@dataclass(frozen=True)
class ToolApproval:
    server_id: str
    server_origin: str | None
    tool_name: str
    definition_sha256: str
    approved_by: str
    approved_at: str
    accepted_flags: tuple[str, ...]


@dataclass(frozen=True)
class ToolApprovals:
    document_sha256: str
    approvals: tuple[ToolApproval, ...]

    def get(self, server_id: str, tool_name: str) -> ToolApproval | None:
        for approval in self.approvals:
            if (approval.server_id, approval.tool_name) == (server_id, tool_name):
                return approval
        return None


def _fail(message: str) -> ToolInventoryError:
    return ToolInventoryError(message)


def _keys(value: object, expected: frozenset[str], label: str) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != expected:
        raise _fail(f"{label} must have exactly the keys {sorted(expected)}.")
    return value


def _match(value: object, pattern: re.Pattern[str], label: str) -> str:
    if not isinstance(value, str) or not pattern.fullmatch(value):
        raise _fail(f"Invalid {label}.")
    return value


def _origin_or_none(value: object, label: str) -> str | None:
    if value is None:
        return None
    try:
        return _origin(value, label)
    except ProviderInventoryError as exc:
        raise _fail(str(exc)) from None


def _producer(value: object) -> tuple[str, str]:
    block = _keys(value, frozenset({"tool", "version"}), "generated_by")
    tool, version = block["tool"], block["version"]
    for item in (tool, version):
        if not isinstance(item, str) or not 1 <= len(item) <= 128:
            raise _fail("Invalid generated_by.")
    return tool, version


def _tool(value: object, index: int) -> InventoryTool:
    label = f"Tool {index}"
    raw = _keys(value, _TOOL_KEYS, label)
    tool_id = _match(raw["tool_id"], _TOOL_ID, f"{label} tool_id")
    name = _match(raw["name"], _TOOL_NAME, f"{label} name")
    server_id = _match(raw["server_id"], _SERVER_ID, f"{label} server_id")
    transport = raw["transport"]
    if transport not in TRANSPORTS:
        raise _fail(f"{label} has an unknown transport.")
    portability = raw["portability"]
    if portability not in PORTABILITY:
        raise _fail(f"{label} has an unknown portability.")
    origin = _origin_or_none(raw["server_origin"], f"{label} server_origin")
    if (transport == "mcp_streamable_http") != (origin is not None):
        raise _fail(f"{label}: server_origin is required for, and only for, mcp_streamable_http.")
    digest = raw["definition_sha256"]
    reconstruction = raw["reconstruction"]
    if portability == "mcp":
        _match(digest, _HASH, f"{label} definition_sha256")
        if reconstruction is not None:
            raise _fail(f"{label}: an MCP tool has no reconstruction path (null).")
    else:
        if transport != "in_process":
            raise _fail(
                f"{label}: a vendor-only tool is in_process; an MCP tool is portability mcp."
            )
        if digest is not None:
            raise _fail(f"{label}: a vendor-only tool has no definition hash (null).")
        if reconstruction not in RECONSTRUCTION:
            raise _fail(f"{label}: a vendor-only tool's reconstruction is documented or none.")
    return InventoryTool(
        tool_id, name, server_id, transport, origin, digest, portability, reconstruction
    )


def parse_tool_inventory(data: bytes) -> ToolInventory:
    if len(data) > MAX_INVENTORY_BYTES:
        raise _fail("The tool inventory is over 4 MiB.")
    try:
        document = load_json_document(data, "tool inventory")
    except ValueError as exc:
        raise _fail(str(exc)) from None
    root = _keys(
        document, frozenset({"schema_version", "generated_by", "tools"}), "The tool inventory"
    )
    if root["schema_version"] != INVENTORY_SCHEMA_VERSION:
        raise _fail(f"Expected a {INVENTORY_SCHEMA_VERSION} document.")
    producer, version = _producer(root["generated_by"])
    raw_tools = root["tools"]
    if not isinstance(raw_tools, list) or not raw_tools:
        raise _fail("tools must list at least one tool; a system with none is inapplicable.")
    if len(raw_tools) > MAX_TOOLS:
        raise _fail(f"More than {MAX_TOOLS} tools.")
    tools = tuple(_tool(item, index) for index, item in enumerate(raw_tools))
    for what, keys in (
        ("tool_id", [t.tool_id for t in tools]),
        ("server and name", [(t.server_id, t.name) for t in tools]),
        ("definition_sha256", [t.definition_sha256 for t in tools if t.definition_sha256]),
    ):
        if len(set(keys)) != len(keys):
            raise _fail(f"Two tools share a {what}.")
    return ToolInventory(hashlib.sha256(data).hexdigest(), producer, version, tools)


def load_tool_inventory(path: Path, *, expected_sha256: str | None = None) -> ToolInventory:
    inventory = parse_tool_inventory(read_bounded_file(path, MAX_INVENTORY_BYTES))
    if expected_sha256 is not None and inventory.document_sha256 != expected_sha256:
        raise _fail("The tool inventory does not match its declared SHA-256.")
    return inventory


def _approval(value: object, index: int) -> ToolApproval:
    label = f"Approval {index}"
    raw = _keys(value, _APPROVAL_KEYS, label)
    flags = raw["accepted_flags"]
    if (
        not isinstance(flags, list)
        or len(flags) > 16
        or not all(isinstance(flag, str) and _FLAG.fullmatch(flag) for flag in flags)
        or len(set(flags)) != len(flags)
    ):
        raise _fail(f"{label} has invalid accepted_flags.")
    return ToolApproval(
        server_id=_match(raw["server_id"], _SERVER_ID, f"{label} server_id"),
        server_origin=_origin_or_none(raw["server_origin"], f"{label} server_origin"),
        tool_name=_match(raw["tool_name"], _TOOL_NAME, f"{label} tool_name"),
        definition_sha256=_match(raw["definition_sha256"], _HASH, f"{label} definition_sha256"),
        approved_by=_match(raw["approved_by"], _PERSON, f"{label} approved_by"),
        approved_at=_match(raw["approved_at"], _TIMESTAMP, f"{label} approved_at"),
        accepted_flags=tuple(flags),
    )


def parse_tool_approvals(data: bytes) -> ToolApprovals:
    if len(data) > MAX_APPROVALS_BYTES:
        raise _fail("The approvals file is over 1 MiB.")
    try:
        document = load_json_document(data, "tool approvals")
    except ValueError as exc:
        raise _fail(str(exc)) from None
    root = _keys(document, frozenset({"schema_version", "approvals"}), "The approvals file")
    if root["schema_version"] != APPROVALS_SCHEMA_VERSION:
        raise _fail(f"Expected a {APPROVALS_SCHEMA_VERSION} document.")
    raw = root["approvals"]
    if not isinstance(raw, list) or len(raw) > MAX_APPROVALS:
        raise _fail(f"approvals must be a list of at most {MAX_APPROVALS} entries.")
    approvals = tuple(_approval(item, index) for index, item in enumerate(raw))
    keys = [(a.server_id, a.tool_name) for a in approvals]
    if len(set(keys)) != len(keys):
        raise _fail("Two approvals name the same server and tool.")
    return ToolApprovals(hashlib.sha256(data).hexdigest(), approvals)


def load_tool_approvals(path: Path, *, expected_sha256: str | None = None) -> ToolApprovals:
    approvals = parse_tool_approvals(read_bounded_file(path, MAX_APPROVALS_BYTES))
    if expected_sha256 is not None and approvals.document_sha256 != expected_sha256:
        raise _fail("The approvals file does not match its declared SHA-256.")
    return approvals
