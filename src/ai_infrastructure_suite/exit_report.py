"""Offline inventory of evidence an operator has collected for a vendor exit."""

from __future__ import annotations

import hashlib
import json
import re
import stat
from pathlib import Path
from typing import Any

from ai_infrastructure_suite._version import __version__
from ai_infrastructure_suite.doctor import inspect_installation

VECTORS = (
    "policies",
    "prompts",
    "embeddings",
    "fine_tunes",
    "memory",
    "audit_history",
    "cost_history",
    "tool_connectors",
    "identities",
    "ontology",
    "contract_terms",
)
CONTRACT_TERMS = (
    "data_export_rights",
    "fine_tune_adapter_ownership",
    "egress_fees",
    "termination_assistance",
)
TERM_SCORES = {"confirmed": 0, "limited": 50, "absent": 100, "unknown": 100}
RUBRIC = {
    "restore_tested": (
        0,
        "Operator supplied a record of a successful restore and functional test.",
    ),
    "importer_validated": (25, "Operator supplied evidence of export and importer validation."),
    "exported": (50, "Operator supplied evidence of a portable export."),
    "reconstruction": (75, "Operator documented a provider-specific reconstruction path."),
    "non_exportable": (100, "Operator documented a required asset that cannot be exported."),
    "inapplicable": (None, "Operator documented why this vector does not apply."),
}


def _read(path: Path, limit: int) -> bytes:
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


def _unique(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate JSON field.")
        result[key] = value
    return result


def _reference(root: Path, entry: object) -> dict[str, Any]:
    if not isinstance(entry, dict) or entry.keys() != {"state", "path", "sha256"}:
        raise ValueError("Each entry requires state, path and sha256.")
    state, name, digest = entry["state"], entry["path"], entry["sha256"]
    if not isinstance(state, str) or state not in RUBRIC:
        raise ValueError("Unrecognized evidence state.")
    if not isinstance(name, str) or len(name) > 240:
        raise ValueError("Invalid evidence path.")
    for part in name.split("/"):
        if (
            not re.fullmatch(r"[a-zA-Z0-9_][a-zA-Z0-9_.-]*", part)
            or part.endswith(".")
            or re.fullmatch(r"CON|PRN|AUX|NUL|(?:COM|LPT)[1-9]", part.split(".")[0], re.I)
        ):
            raise ValueError("Expected a portable project-relative evidence path.")
    if not isinstance(digest, str) or not re.fullmatch("[0-9a-f]{64}", digest):
        raise ValueError("Invalid SHA-256 digest.")
    data = _read(root / name, 4 * 1024 * 1024)
    if not data or hashlib.sha256(data).hexdigest() != digest:
        raise ValueError("Evidence is empty or differs from its declared SHA-256.")
    score, reason = RUBRIC[state]
    return {
        "state": state,
        "score": score,
        "verification_level": "attested",
        "reason": reason,
        "path": name,
        "sha256": digest,
    }


def _ontology_reference(root: Path, entry: object) -> dict[str, Any]:
    reference = _reference(root, entry)
    if reference["state"] != "importer_validated":
        raise ValueError("Ontology evidence must declare importer_validated.")
    from open_ontology_lite import (
        json_ld_text,
        load_json_ld,
        ontology_digest,
        ontology_from_json_ld,
    )
    from open_ontology_lite.errors import OpenOntologyLiteError

    try:
        ontology = load_json_ld(root / reference["path"])
        restored = ontology_from_json_ld(json.loads(json_ld_text(ontology)))
    except (OpenOntologyLiteError, json.JSONDecodeError) as exc:
        raise ValueError("OpenOntologyLite could not round-trip the JSON-LD evidence.") from exc
    if ontology_digest(restored) != ontology_digest(ontology):
        raise ValueError("OpenOntologyLite JSON-LD round trip changed the ontology.")
    reference.update(
        score=25,
        verification_level="verified",
        reason="OpenOntologyLite loaded and round-tripped the JSON-LD without changing its digest.",
    )
    return reference


def _contract_terms_reference(root: Path, entry: object) -> dict[str, Any]:
    if not isinstance(entry, dict) or entry.keys() != {"state", "path", "sha256"}:
        raise ValueError("Contract terms require state, path and sha256.")
    if entry["state"] != "attested":
        raise ValueError("Contract terms must be labeled attested.")
    synthetic = {**entry, "state": "exported"}
    reference = _reference(root, synthetic)
    raw = _read(root / reference["path"], 65536)
    document = json.loads(raw.decode("utf-8"), object_pairs_hook=_unique)
    if (
        not isinstance(document, dict)
        or set(document) != {"schema_version", "checklist"}
        or document["schema_version"] != "contract-terms-checklist.v1"
    ):
        raise ValueError("Expected a contract-terms-checklist.v1 document.")
    checklist = document["checklist"]
    if not isinstance(checklist, dict) or set(checklist) != set(CONTRACT_TERMS):
        raise ValueError("Contract terms checklist fields are invalid.")
    if any(not isinstance(value, str) or value not in TERM_SCORES for value in checklist.values()):
        raise ValueError("Contract terms checklist status is invalid.")
    score = max(TERM_SCORES[value] for value in checklist.values())
    summary = ", ".join(f"{name}={checklist[name]}" for name in CONTRACT_TERMS)
    return {
        **reference,
        "state": "attested",
        "score": score,
        "verification_level": "attested",
        "reason": f"Operator checklist: {summary}. Not legal advice.",
    }


def build_exit_report(manifest: Path, *, verify_restores: bool = False) -> dict[str, Any]:
    """Check local evidence digests and record installed component versions."""
    raw = _read(manifest, 65536)
    document = json.loads(raw.decode("utf-8"), object_pairs_hook=_unique)
    if (
        not isinstance(document, dict)
        or document.keys() != {"schema_version", "vectors"}
        or document["schema_version"] != "exit-manifest.v1"
    ):
        raise ValueError("Expected an exit-manifest.v1 document.")
    entries = document["vectors"]
    if not isinstance(entries, dict) or entries.keys() - set(VECTORS):
        raise ValueError("Invalid vector inventory.")
    rows = {}
    restore_cache: dict[tuple[str, str], Any] = {}
    restore_profiles: dict[tuple[str, str, str, int], dict[str, Any]] = {}
    for vector in VECTORS:
        if vector not in entries:
            rows[vector] = {
                "state": "unknown",
                "score": 100,
                "verification_level": "unverified",
                "reason": "No evidence supplied.",
            }
        elif isinstance(entries[vector], dict) and entries[vector].get("state") == "restore_tested":
            reference = _reference(manifest.parent, entries[vector])
            if not verify_restores:
                rows[vector] = reference
                continue
            if vector not in {
                "policies",
                "prompts",
                "embeddings",
                "memory",
                "audit_history",
                "cost_history",
                "ontology",
            }:
                raise ValueError("Restore verification does not cover this vector.")
            from ai_infrastructure_suite.restore_verification import verify_restore_summary

            key = (reference["path"], reference["sha256"])
            verification = restore_cache.get(key)
            if verification is None:
                verification = verify_restore_summary(manifest.parent / reference["path"])
                restore_cache[key] = verification
            for profile_result in verification.profiles:
                profile_key = (
                    reference["path"],
                    profile_result.run_kind,
                    profile_result.profile,
                    profile_result.hop_index,
                )
                restore_profiles[profile_key] = {
                    "evidence_path": reference["path"],
                    "run_kind": profile_result.run_kind,
                    "profile": profile_result.profile,
                    "hop_index": profile_result.hop_index,
                    "state": "passed" if profile_result.passed else "failed",
                    "reason": profile_result.reason,
                }
            passed = verification.passed(vector)
            rows[vector] = {
                **reference,
                "state": "restore_tested" if passed else "restore_failed",
                "score": 0 if passed else 100,
                "verification_level": "verified (fake profiles)",
                "reason": verification.reason(vector),
            }
        elif vector == "ontology":
            rows[vector] = _ontology_reference(manifest.parent, entries[vector])
        elif vector == "contract_terms":
            rows[vector] = _contract_terms_reference(manifest.parent, entries[vector])
        else:
            rows[vector] = _reference(manifest.parent, entries[vector])
    installation = inspect_installation(check_imports=False)
    report: dict[str, Any] = {
        "schema_version": "exit-report.v1",
        "rubric_version": "1",
        "suite_version": __version__,
        "manifest_sha256": hashlib.sha256(raw).hexdigest(),
        "vectors": rows,
        "components": {
            item.distribution: {
                "version": item.installed_version,
                "status": item.install_status.value,
            }
            for item in installation.components
        },
        "legal_notice": "Contract terms are operator-attested. This report is not legal advice.",
    }
    if restore_profiles:
        report["restore_profiles"] = list(restore_profiles.values())
    return report


def render_markdown(report: dict[str, Any]) -> str:
    lines = [
        "# Vendor exit evidence",
        "",
        f"Suite {report['suite_version']} | Rubric {report['rubric_version']}",
        f"Manifest SHA-256: `{report['manifest_sha256']}`",
        "",
        "Contract terms are operator-attested. This report is not legal advice.",
        "",
        "| Vector | Lock-in (0-100) | Verification | Evidence | Reference |",
        "|---|---:|---|---|---|",
    ]
    for name, row in report["vectors"].items():
        score = row["score"] if row["score"] is not None else "N/A"
        ref = f"`{row['path']}` (`{row['sha256']}`)" if "path" in row else "-"
        lines.append(
            f"| {name} | {score} | {row['verification_level']} | {row['reason']} | {ref} |"
        )
    if "restore_profiles" in report:
        lines.extend(
            [
                "",
                "## Restore profile verification",
                "",
                "| Run | Profile | Hop | Status | Reason |",
                "|---|---|---:|---|---|",
            ]
        )
        for profile in report["restore_profiles"]:
            lines.append(
                f"| {profile['run_kind']} | {profile['profile']} | "
                f"{profile['hop_index']} | {profile['state']} | {profile['reason']} |"
            )
    lines.extend(["", "## Installed components", ""])
    for name, item in report["components"].items():
        version = item["version"] or "absent"
        # Treat distribution metadata as untrusted display text.
        version = re.sub(r"[^A-Za-z0-9.+!-]", "?", version)[:128]
        lines.append(f"- {name}: {version} ({item['status']})")
    return "\n".join(lines)
