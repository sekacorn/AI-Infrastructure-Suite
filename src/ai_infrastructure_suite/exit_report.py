"""Offline inventory of evidence an operator has collected for a vendor exit."""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any

from ai_infrastructure_suite._evidence import (
    portable_relative_path,
    read_bounded_file,
    unique_json_pairs,
)
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
RESTORE_VECTORS = frozenset(
    {
        "policies",
        "prompts",
        "embeddings",
        "memory",
        "audit_history",
        "cost_history",
        "ontology",
    }
)
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
    "embedding_inventory": (
        None,
        "Operator supplied an embedding portability inventory.",
    ),
}


def _reference(root: Path, entry: object) -> dict[str, Any]:
    if not isinstance(entry, dict) or entry.keys() != {"state", "path", "sha256"}:
        raise ValueError("Each entry requires state, path and sha256.")
    state, digest = entry["state"], entry["sha256"]
    if not isinstance(state, str) or state not in RUBRIC:
        raise ValueError("Unrecognized evidence state.")
    name = portable_relative_path(entry["path"])
    if not isinstance(digest, str) or not re.fullmatch("[0-9a-f]{64}", digest):
        raise ValueError("Invalid SHA-256 digest.")
    data = read_bounded_file(root / name, 4 * 1024 * 1024)
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
    raw = read_bounded_file(root / reference["path"], 65536)
    document = json.loads(raw.decode("utf-8"), object_pairs_hook=unique_json_pairs)
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


def _restore_evidence(verifications: list[Any], *, supplied: bool) -> Any:
    """Collect the store claims that passing restore verifications commit to."""
    from ai_infrastructure_suite.embedding_portability import RestoreEvidence

    stores: dict[str, str] = {}
    verified = False
    for verification in verifications:
        # Only a cleanly reproduced run may support a store claim. A failed
        # embeddings asset is disqualifying, and so is an unattributed
        # behavior drift in any profile: a score of 0 claims a verified
        # restore *and* a functional check, which drift puts in question.
        if not verification.passed("embeddings") or not all(
            profile.passed for profile in verification.profiles
        ):
            continue
        verified = True
        for store_id, digest in verification.embedding_stores.items():
            if stores.setdefault(store_id, digest) != digest:
                raise ValueError(
                    "Restore summaries disagree about an embedding store evidence digest."
                )
    return RestoreEvidence(supplied=supplied, verified=verified, stores=stores)


def _store_rows(result: Any) -> list[dict[str, Any]]:
    """Render one report section per scanned vector store."""
    rows: list[dict[str, Any]] = []
    for store in result.stores:
        model = store.model
        row: dict[str, Any] = {
            "store_id": store.store_id,
            "model_id": model.id,
            "model_revision": model.revision,
            "dimensions": model.dimensions,
            "availability": model.availability,
            "availability_verification": "attested",
            "layout": store.layout,
            "shard_count": store.shard_count,
            "vector_count": store.vector_count,
            "retained": store.retained,
            "missing": store.missing,
            "deleted_by_policy": store.deleted_by_policy,
            "deleted_by_policy_verification": "attested",
            "coverage_pct": store.coverage,
            "restore_link": store.restore_link,
            "re_embeddable": store.re_embeddable,
            "score": store.score,
            "reason": store.reason,
            "warnings": list(store.warnings),
            "findings": [
                {
                    "code": finding.code,
                    "record_count": finding.record_count,
                    "record_ids": list(finding.record_ids),
                    "record_ids_truncated": finding.truncated,
                }
                for finding in store.findings
            ],
        }
        if model.weights_sha256 is not None:
            row["weights_sha256"] = model.weights_sha256
            row["weights_verification"] = "attested"
        if model.license is not None:
            row["license"] = model.license
            row["license_verification"] = "attested"
        rows.append(row)
    return rows


def _embedding_reference(root: Path, entry: object, restore: Any) -> dict[str, Any]:
    """Scan an embedding portability inventory and build its vector row."""
    from ai_infrastructure_suite.embedding_portability import scan_embedding_portability

    reference = _reference(root, entry)
    result = scan_embedding_portability(
        root / reference["path"],
        evidence_sha256=reference["sha256"],
        restore=restore,
    )
    # The scanner re-ran every digest, dimension, and coverage check itself.
    # The model's availability, weight digest, license, and any policy
    # deletion remain operator declarations, so the row says both.
    attested = ["model availability"]
    if any(store.model.weights_sha256 is not None for store in result.stores):
        attested.append("model weights digest")
    if any(store.model.license is not None for store in result.stores):
        attested.append("model license")
    if any(store.deleted_by_policy for store in result.stores):
        attested.append("policy deletions")
    linked = any(store.restore_link == "verified" for store in result.stores)
    level = "verified (local evidence + fake profiles)" if linked else "verified (local evidence)"
    return {
        **reference,
        "state": "embedding_inventory",
        "score": result.score,
        "verification_level": level,
        "re_embeddable": result.re_embeddable,
        "attested_claims": attested,
        "reason": f"{result.reason} Attested, not re-run: {', '.join(attested)}.",
        "stores": _store_rows(result),
    }


def build_exit_report(manifest: Path, *, verify_restores: bool = False) -> dict[str, Any]:
    """Check local evidence digests and record installed component versions."""
    raw = read_bounded_file(manifest, 65536)
    document = json.loads(raw.decode("utf-8"), object_pairs_hook=unique_json_pairs)
    if (
        not isinstance(document, dict)
        or document.keys() != {"schema_version", "vectors"}
        or document["schema_version"] != "exit-manifest.v1"
    ):
        raise ValueError("Expected an exit-manifest.v1 document.")
    entries = document["vectors"]
    if not isinstance(entries, dict) or entries.keys() - set(VECTORS):
        raise ValueError("Invalid vector inventory.")
    rows: dict[str, dict[str, Any]] = {}
    restore_cache: dict[tuple[str, str], Any] = {}
    restore_profiles: dict[tuple[str, str, str, int], dict[str, Any]] = {}
    # Resolve restore summaries before any row is built. The embeddings vector
    # may be scored against a restore declared on a later vector, so lazy
    # verification inside the row loop would depend on vector ordering.
    restore_supplied = any(
        isinstance(entry, dict) and entry.get("state") == "restore_tested"
        for entry in entries.values()
    )
    if verify_restores:
        from ai_infrastructure_suite.restore_verification import verify_restore_summary

        for vector, entry in entries.items():
            if (
                not isinstance(entry, dict)
                or entry.get("state") != "restore_tested"
                or vector not in RESTORE_VECTORS
            ):
                continue
            reference = _reference(manifest.parent, entry)
            key = (reference["path"], reference["sha256"])
            if key not in restore_cache:
                restore_cache[key] = verify_restore_summary(manifest.parent / reference["path"])
    restore = _restore_evidence(list(restore_cache.values()), supplied=restore_supplied)
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
            if vector not in RESTORE_VECTORS:
                raise ValueError("Restore verification does not cover this vector.")
            verification = restore_cache[(reference["path"], reference["sha256"])]
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
        elif isinstance(entries[vector], dict) and (
            entries[vector].get("state") == "embedding_inventory"
        ):
            if vector != "embeddings":
                raise ValueError("Only the embeddings vector accepts an embedding inventory.")
            rows[vector] = _embedding_reference(manifest.parent, entries[vector], restore)
        elif vector == "ontology":
            rows[vector] = _ontology_reference(manifest.parent, entries[vector])
        elif vector == "contract_terms":
            rows[vector] = _contract_terms_reference(manifest.parent, entries[vector])
        else:
            rows[vector] = _reference(manifest.parent, entries[vector])
    # Per-store detail lives in its own top-level section so the vector table
    # keeps one row per vector.
    embedding_stores = rows["embeddings"].pop("stores", None)
    installation = inspect_installation(check_imports=False)
    failed_profile_names = sorted(
        {
            profile["profile"]
            for profile in restore_profiles.values()
            if profile["state"] == "failed"
        }
    )
    if failed_profile_names:
        annotation = "; ".join(
            f"profile failure: unattributed drift in {profile}" for profile in failed_profile_names
        )
        for row in rows.values():
            if row["verification_level"].startswith("verified"):
                row["reason"] = f"{row['reason']} {annotation}."
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
    if embedding_stores is not None:
        report["embedding_stores"] = embedding_stores
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
    if "embedding_stores" in report:
        lines.extend(
            [
                "",
                "## Embedding stores",
                "",
                "Counts and coverage are scanner-verified. Model availability, any weights",
                "digest, any license, and every policy deletion are operator-attested and",
                "were not re-run.",
                "",
                "| Store | Model | Dim | Availability | Layout | Vectors | Retained | "
                "Missing | Deleted (attested) | Coverage | Restore link | Re-embeddable | "
                "Lock-in |",
                "|---|---|---:|---|---|---:|---:|---:|---:|---:|---|---|---:|",
            ]
        )
        for store in report["embedding_stores"]:
            coverage = "N/A" if store["coverage_pct"] is None else f"{store['coverage_pct']:.2f}%"
            score = "N/A" if store["score"] is None else store["score"]
            shards = f"{store['layout']} ({store['shard_count']})"
            lines.append(
                f"| {store['store_id']} | {store['model_id']}@{store['model_revision']} | "
                f"{store['dimensions']} | {store['availability']} (attested) | {shards} | "
                f"{store['vector_count']} | {store['retained']} | {store['missing']} | "
                f"{store['deleted_by_policy']} | {coverage} | {store['restore_link']} | "
                f"{store['re_embeddable']} | {score} |"
            )
        for store in report["embedding_stores"]:
            details = [
                *(f"warning: {text}" for text in store["warnings"]),
                *(
                    f"finding: {finding['code']} ({finding['record_count']} record(s))"
                    for finding in store["findings"]
                ),
            ]
            if not details:
                continue
            lines.extend(["", f"### {store['store_id']}", ""])
            lines.extend(f"- {text}" for text in details)
            lines.append(f"- reason: {store['reason']}")
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
