"""Scoring the ``tool_connectors`` vector from a ``tool-inventory.v1`` document.

The manifest's ``tool_connectors`` entry declares ``state: tool_inventory``
with the inventory's path and SHA-256, and optionally ``approvals``: the
``tool-approvals.v1`` file the operator committed.

**What the scanner verifies itself:** the inventory's schema rules and bounds;
its digest; that origins are credential-free canonical origins; the hosting of
each MCP server, *derived from its origin* by the shared locality rule (stdio
and in-process servers are local), never taken from a declaration; hash
format and uniqueness; and, when an approvals file is supplied, its digest and
whether each inventoried MCP tool's server, origin, name and hash match an
approval. Approvals naming tools the inventory does not list are reported as
stale.

**What stays attested:** that the inventory lists every tool; that any tool
works; that an MCP server is really self-hostable; that a documented
reconstruction path works; and that tool descriptions are benign. The scanner
never sees a tool definition, so it cannot recompute a hash.

Per tool (the worst tool sets the vector):

* 25: an MCP tool whose hash is approved and whose server is local (stdio,
  in-process, or an origin derived as local);
* 50: an MCP tool whose server is hosted, or whose hash is not approved (or no
  approvals file was supplied); the reason names which;
* 75: a vendor-only tool with a documented reconstruction path;
* 100: a vendor-only tool with no reconstruction path.

**Nothing reaches 0**: the scanner runs no tool, and 0 is never given on
attested evidence. Invalid evidence is refused, never scored.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final

from ai_infrastructure_suite._evidence import portable_relative_path
from ai_infrastructure_suite.provider_inventory import origin_is_local
from ai_infrastructure_suite.tool_inventory import (
    InventoryTool,
    ToolApprovals,
    load_tool_approvals,
    load_tool_inventory,
)

STATE: Final = "tool_inventory"
LEVEL: Final = "verified (tool inventory)"
_ENTRY_KEYS: Final = frozenset({"state", "path", "sha256"})
_OPTIONAL_KEYS: Final = frozenset({"approvals"})

CLAIM_COMPLETE: Final = "that the inventory lists every tool"
CLAIM_WORKS: Final = "that any tool works"
CLAIM_SELF_HOSTABLE: Final = "that an MCP server is self-hostable"
CLAIM_RECONSTRUCTION: Final = "that a documented reconstruction path works"
CLAIM_BENIGN: Final = "that tool descriptions are benign"

APPROVED: Final = "approved"
NOT_APPROVED: Final = "not_approved"
NOT_SUPPLIED: Final = "no_approvals_file"
NOT_APPLICABLE: Final = "not_applicable"


@dataclass(frozen=True)
class ToolVectorResult:
    """The vector row and its per-tool detail section."""

    row: dict[str, Any]
    section: list[dict[str, Any]]


def _reference(entry: object, key: str) -> tuple[str, str]:
    if not isinstance(entry, dict) or entry.keys() != {"path", "sha256"}:
        raise ValueError(f"Invalid {key} reference.")
    digest = entry["sha256"]
    if not isinstance(digest, str) or len(digest) != 64:
        raise ValueError(f"Invalid {key} digest.")
    return portable_relative_path(entry["path"]), digest


def _hosting(tool: InventoryTool) -> str:
    if tool.server_origin is None:
        return "local"
    return "local" if origin_is_local(tool.server_origin) else "hosted"


def _approval_state(tool: InventoryTool, approvals: ToolApprovals | None) -> str:
    if tool.portability != "mcp":
        return NOT_APPLICABLE
    if approvals is None:
        return NOT_SUPPLIED
    approval = approvals.get(tool.server_id, tool.name)
    if (
        approval is not None
        and approval.definition_sha256 == tool.definition_sha256
        and approval.server_origin == tool.server_origin
    ):
        return APPROVED
    return NOT_APPROVED


def score_tool(tool: InventoryTool, approvals: ToolApprovals | None) -> dict[str, Any]:
    """One tool's row: its derived hosting, approval state, score and reason."""
    hosting = _hosting(tool)
    approval = _approval_state(tool, approvals)
    if tool.portability == "vendor_only":
        if tool.reconstruction == "documented":
            score, reason = 75, "Vendor-only tool with a documented reconstruction path (attested)."
        else:
            score, reason = 100, "Vendor-only tool with no reconstruction path."
    else:
        blockers = []
        if hosting == "hosted":
            blockers.append("its MCP server is hosted (derived from the origin)")
        if approval == NOT_SUPPLIED:
            blockers.append("no approvals file was supplied")
        elif approval == NOT_APPROVED:
            blockers.append("its definition hash is not approved")
        if blockers:
            score = 50
            reason = "MCP tool; not 25 because " + " and ".join(blockers) + "."
        else:
            score = 25
            reason = (
                f"MCP tool with an approved definition hash on a local {tool.transport} server."
            )
    return {
        "tool_id": tool.tool_id,
        "server_id": tool.server_id,
        "name": tool.name,
        "transport": tool.transport,
        "server_origin": tool.server_origin,
        "hosting": hosting,
        "portability": tool.portability,
        "reconstruction": tool.reconstruction,
        "definition_sha256": tool.definition_sha256,
        "approval": approval,
        "score": score,
        "reason": reason,
    }


def build_vector(root: Path, entry: object) -> ToolVectorResult:
    """Score the tool_connectors vector from a manifest entry declaring ``tool_inventory``."""
    if not isinstance(entry, dict) or entry.get("state") != STATE:
        raise ValueError("Expected a tool_inventory entry.")
    if not entry.keys() >= _ENTRY_KEYS or entry.keys() - _ENTRY_KEYS - _OPTIONAL_KEYS:
        raise ValueError("A tool_inventory entry takes state, path, sha256 and approvals.")
    path, digest = _reference({"path": entry["path"], "sha256": entry["sha256"]}, "tool_connectors")
    inventory = load_tool_inventory(root / path, expected_sha256=digest)
    approvals: ToolApprovals | None = None
    approvals_ref: dict[str, str] | None = None
    if entry.get("approvals") is not None:
        approvals_path, approvals_digest = _reference(entry["approvals"], "approvals")
        approvals = load_tool_approvals(root / approvals_path, expected_sha256=approvals_digest)
        approvals_ref = {"path": approvals_path, "sha256": approvals_digest}

    section = [score_tool(tool, approvals) for tool in inventory.tools]
    listed = {(tool.server_id, tool.name) for tool in inventory.tools}
    stale = (
        0
        if approvals is None
        else sum(1 for a in approvals.approvals if (a.server_id, a.tool_name) not in listed)
    )
    worst = max(section, key=lambda row: row["score"])
    attested = [CLAIM_COMPLETE, CLAIM_WORKS]
    if any(t.portability == "mcp" for t in inventory.tools):
        attested.append(CLAIM_SELF_HOSTABLE)
    if any(t.reconstruction == "documented" for t in inventory.tools):
        attested.append(CLAIM_RECONSTRUCTION)
    attested.append(CLAIM_BENIGN)
    reason = (
        f"Worst of {len(section)} tool(s): {worst['tool_id']} scores {worst['score']}. "
        f"{worst['reason']} "
        + (f"{stale} approval(s) name tools the inventory does not list. " if stale else "")
        + f"Attested, not re-run: {', '.join(attested)}."
    )
    row: dict[str, Any] = {
        "state": STATE,
        "score": worst["score"],
        "verification_level": LEVEL,
        "reason": reason,
        "path": path,
        "sha256": digest,
        "attested_claims": attested,
        "stale_approvals": stale,
    }
    if approvals_ref is not None:
        row["approvals"] = approvals_ref
    return ToolVectorResult(row=row, section=section)
