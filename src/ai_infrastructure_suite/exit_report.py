"""Offline inventory of evidence an operator has collected for a vendor exit."""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any

from ai_infrastructure_suite._dependencies import missing_ontology_api
from ai_infrastructure_suite._evidence import (
    portable_relative_path,
    read_bounded_file,
    unique_json_pairs,
)
from ai_infrastructure_suite._version import __version__
from ai_infrastructure_suite.doctor import inspect_installation

MAX_RE_EMBED_RECORDS = 64
"""Matches embedding-portability.v1's store cap: one record per store, rebuilt more than once."""

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
    # Rubric 2. Appended, so the first eleven rows keep their order.
    "model_providers",
)
REPORT_SCHEMA_VERSION = "exit-report.v2"
RUBRIC_VERSION = "2"
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
    # re_embed_records is optional and handled separately, so it is allowed
    # here but never required.
    if not isinstance(entry, dict) or not {"state", "path", "sha256"} <= entry.keys():
        raise ValueError("Each entry requires state, path and sha256.")
    if entry.keys() - {"state", "path", "sha256", "re_embed_records"}:
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
    try:
        from open_ontology_lite import (
            json_ld_text,
            load_json_ld,
            ontology_digest,
            ontology_from_json_ld,
        )
        from open_ontology_lite.errors import OpenOntologyLiteError
    except ImportError as exc:
        raise missing_ontology_api() from exc

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


def _re_embed_declarations(entry: object) -> list[dict[str, str]]:
    """Read the optional re_embed_records list, refusing a malformed one."""
    if not isinstance(entry, dict):
        return []
    declared = entry.get("re_embed_records")
    if declared is None:
        return []
    if not isinstance(declared, list) or len(declared) > MAX_RE_EMBED_RECORDS:
        raise ValueError("Invalid re_embed_records list.")
    references: list[dict[str, str]] = []
    for item in declared:
        if not isinstance(item, dict) or item.keys() != {"path", "sha256"}:
            raise ValueError("Each re_embed_records entry requires path and sha256.")
        digest = item["sha256"]
        if not isinstance(digest, str) or not re.fullmatch("[0-9a-f]{64}", digest):
            raise ValueError("Invalid re-embed record SHA-256 digest.")
        references.append({"path": portable_relative_path(item["path"]), "sha256": digest})
    return references


def _re_embed_rows(
    root: Path, entry: object, result: Any, evidence_sha256: str
) -> tuple[list[dict[str, Any]], frozenset[str]]:
    """Assess every declared re-embed record and say which stores improve."""
    from ai_infrastructure_suite.re_embed_scan import ReEmbedScanError, scan_re_embed_record

    declarations = _re_embed_declarations(entry)
    if not declarations:
        return [], frozenset()
    store_ids = frozenset(store.store_id for store in result.stores)
    rows: list[dict[str, Any]] = []
    improved: set[str] = set()
    for reference in declarations:
        try:
            assessment = scan_re_embed_record(
                root / reference["path"],
                declared_sha256=reference["sha256"],
                source_store_sha256=evidence_sha256,
                source_store_ids=store_ids,
            )
        except ReEmbedScanError as exc:
            # Refused, not skipped: a record that does not bind to this
            # evidence would otherwise go unmentioned.
            raise ValueError(f"Invalid re-embed record {reference['path']}: {exc}") from None
        rows.append(_re_embed_row(assessment, reference))
        if assessment.improves_score:
            improved.add(assessment.record.source_store.store_id)
    return rows, frozenset(improved)


def _re_embed_row(assessment: Any, reference: dict[str, str]) -> dict[str, Any]:
    """Render one re-embed record for the report."""
    record = assessment.record
    quality = record.quality
    egress = record.egress
    row: dict[str, Any] = {
        **reference,
        "source_store_id": record.source_store.store_id,
        "target_store_id": record.target_store.store_id,
        "target_model": f"{record.target_model.id}@{record.target_model.revision}",
        "target_dimensions": record.target_model.dimensions,
        "availability": record.target_model.availability,
        "availability_verification": "attested",
        "verification_level": assessment.label,
        "rebuild_reproduced": assessment.reproduced,
        "reproduction_sample_size": assessment.reproduction.sample_size,
        "anchor_selection": assessment.anchor_selection,
        "anchor_seed_was_declared_not_derived": assessment.seed_was_ground,
        "score_effect": "50_to_25" if assessment.improves_score else "none",
        "quality_status": record.quality_status,
        "coverage_complete": record.coverage.complete,
        "attested_claims": list(record.attested_claims()),
        "reason": assessment.reason,
        "egress": {
            "executor_kind": egress.executor_kind or "not stated",
            "execution_verification": egress.execution_verification or "not stated",
            "hosting_mode": egress.hosting_mode or "not stated",
            "external_execution_confirmed": egress.external_execution_confirmed,
            "hosted_execution_confirmed": egress.hosted_execution_confirmed,
            "records_submitted_to_external_executor": egress.submission_count,
            "transmission_basis": egress.transmission_basis or "not stated",
            "delivery_verification": egress.delivery_verification or "not stated",
            "delivery_established": egress.delivery_established,
            "executor_id": egress.executor_id,
            "endpoint_origin": egress.endpoint_origin,
            "dataset_privacy_classification": egress.dataset_privacy_classification,
            "warnings": list(egress.warnings),
        },
    }
    if quality is not None:
        row["metric_basis"] = quality.metric_basis or "not stated"
        row["mode"] = quality.mode or "not stated"
        row["measures_user_search_quality"] = quality.measures_user_search_quality
        row["thresholds"] = dict(quality.thresholds or {})
        row["threshold_summary"] = quality.threshold_summary()
        row["metrics"] = dict(quality.metrics)
        row["query_set_size"] = quality.query_set_size
        row["top_k"] = quality.top_k
    return row


def _embedding_reference(root: Path, entry: object, restore: Any) -> dict[str, Any]:
    """Scan an embedding portability inventory and build its vector row."""
    from ai_infrastructure_suite.embedding_portability import (
        apply_re_embedding,
        scan_embedding_portability,
    )

    reference = _reference(root, entry)
    result = scan_embedding_portability(
        root / reference["path"],
        evidence_sha256=reference["sha256"],
        restore=restore,
    )
    re_embed_rows, improved = _re_embed_rows(root, entry, result, reference["sha256"])
    result = apply_re_embedding(result, improved)
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
    reason = f"{result.reason} Attested, not re-run: {', '.join(attested)}."
    # External execution and hosted data reach the summary row's reason, not
    # only the detail section, so a reader skimming the table still sees them.
    alerts = _egress_alerts(re_embed_rows)
    if alerts:
        reason = f"{reason} {' '.join(alerts)}"
    row: dict[str, Any] = {
        **reference,
        "state": "embedding_inventory",
        "score": result.score,
        "verification_level": level,
        "re_embeddable": result.re_embeddable,
        "attested_claims": attested,
        "reason": reason,
        "stores": _store_rows(result),
    }
    if re_embed_rows:
        row["re_embed_records"] = re_embed_rows
    return row


def _model_providers_row(root: Path, entry: object) -> tuple[dict[str, Any], dict[str, Any] | None]:
    """The model_providers row, and its detail section when there is one.

    Only two states are accepted: a provider inventory, or a documented reason
    the vector does not apply.
    """
    from ai_infrastructure_suite.model_providers import STATES, build_vector

    state = entry.get("state") if isinstance(entry, dict) else None
    if state not in STATES:
        raise ValueError("model_providers accepts only provider_inventory or inapplicable.")
    if state == "inapplicable":
        if not isinstance(entry, dict) or entry.keys() != {"state", "path", "sha256"}:
            raise ValueError("An inapplicable model_providers entry takes state, path and sha256.")
        return _reference(root, entry), None
    result = build_vector(root, entry)
    return result.row, result.section


def _egress_alerts(rows: list[dict[str, Any]]) -> list[str]:
    """One sentence per record whose rebuild ran external code or sent text out.

    Deliberately in the summary reason as well as the detail: the alarming case
    should be impossible to miss, and a reader who only reads the table is the
    reader most likely to miss it.
    """
    alerts: list[str] = []
    for row in rows:
        egress = row["egress"]
        if egress["executor_kind"] == "external_unverified":
            alerts.append(
                f"Operator-supplied code ran the rebuild of {row['target_store_id']}; "
                "its network behaviour is attested, not verified."
            )
        if egress["hosting_mode"] == "hosted" and egress["records_submitted_to_external_executor"]:
            alerts.append(
                f"Source text left the machine for {row['target_store_id']}: "
                f"{egress['records_submitted_to_external_executor']} record(s) handed to an "
                "external executor, delivery unverified."
            )
    return alerts


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
    model_providers: dict[str, Any] | None = None
    memory_stores: list[dict[str, Any]] | None = None
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
        elif vector == "model_providers":
            # First, so no other state's branch can score this vector.
            rows[vector], model_providers = _model_providers_row(manifest.parent, entries[vector])
        elif (
            isinstance(entries[vector], dict) and entries[vector].get("state") == "memory_inventory"
        ):
            if vector != "memory":
                raise ValueError("Only the memory vector accepts a memory inventory.")
            from ai_infrastructure_suite.memory_vector import build_vector as build_memory_vector

            memory = build_memory_vector(manifest.parent, entries[vector])
            rows[vector], memory_stores = memory.row, memory.section
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
    re_embed_records = rows["embeddings"].pop("re_embed_records", None)
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
        "schema_version": REPORT_SCHEMA_VERSION,
        "rubric_version": RUBRIC_VERSION,
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
    if re_embed_records:
        report["re_embed_records"] = re_embed_records
    if model_providers is not None:
        report["model_providers"] = model_providers
    if memory_stores is not None:
        report["memory_stores"] = memory_stores
    return report


def _tick(value: object) -> str:
    """Render a tri-state consent flag without inventing a default.

    An absent flag is not a denial and not a grant: a record written before
    the field existed says nothing, and printing a tick or a cross for it would
    put a claim in the report nobody made.
    """
    if value is True:
        return "yes"
    if value is False:
        return "no"
    return "not stated"


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
    if "re_embed_records" in report:
        lines.extend(
            [
                "",
                "## Re-embedding",
                "",
                "Each record below says one store was rebuilt with a different model. A",
                "verified level means this scanner recomputed a **sample** of the rebuilt",
                "store's vectors and they matched; a sample is not a whole-store digest.",
                "Thresholds are the operator's own, so they are printed beside every",
                "verdict rather than left out of it.",
            ]
        )
        for row in report["re_embed_records"]:
            egress = row["egress"]
            lines.extend(
                [
                    "",
                    f"### {row['source_store_id']} to {row['target_store_id']}",
                    "",
                ]
            )
            # The loud block first, before anything a reader might stop at.
            if egress["executor_kind"] == "external_unverified":
                lines.extend(
                    [
                        f"> **Operator-supplied code ran this rebuild.** Executor "
                        f"`{egress['executor_id'] or 'unnamed'}`"
                        + (
                            f", declared endpoint `{egress['endpoint_origin']}`"
                            if egress["endpoint_origin"]
                            else ""
                        )
                        + ". Network behaviour is attested and was not verified; this",
                        "> package cannot sandbox injected code.",
                    ]
                )
            if (
                egress["hosting_mode"] == "hosted"
                and egress["records_submitted_to_external_executor"]
            ):
                lines.extend(
                    [
                        ">",
                        f"> **Source text left the machine.** "
                        f"{egress['records_submitted_to_external_executor']} record(s) handed to "
                        f"that executor (`{egress['transmission_basis']}`; attempts may exceed",
                        f"> this). Delivery to any provider is "
                        f"`{egress['delivery_verification']}`. "
                        f"Consent: external {_tick(egress['external_execution_confirmed'])}, "
                        f"hosted {_tick(egress['hosted_execution_confirmed'])}. Classification "
                        f"`{egress['dataset_privacy_classification'] or 'not stated'}` (attested).",
                    ]
                )
            if egress["executor_kind"] == "local_builtin":
                lines.append("Registry-owned embedder; nothing was handed to external code.")
            elif egress["executor_kind"] == "not stated":
                lines.append(
                    "The record does not state which executor ran, so nothing is assumed about it."
                )
            effect = "moved 50 to 25" if row["score_effect"] == "50_to_25" else "moves no score"
            lines.extend(
                [
                    "",
                    f"- **{row['verification_level']}** — {effect}",
                    f"- target model: `{row['target_model']}` at {row['target_dimensions']} "
                    f"dimensions, {row['availability']} (attested)",
                    "- reproduction: "
                    + ("sample matched" if row["rebuild_reproduced"] else "not reproduced")
                    + (
                        f", {row['reproduction_sample_size']} vector(s) recomputed"
                        if row["reproduction_sample_size"]
                        else ""
                    ),
                ]
            )
            if row.get("threshold_summary"):
                lines.append(f"- {row['threshold_summary']}")
                lines.append(
                    "- thresholds are the operator's; passing means the bar they set was cleared"
                )
            if "metric_basis" in row:
                suffix = (
                    " — not a measure of search quality"
                    if not row.get("measures_user_search_quality", False)
                    else ""
                )
                lines.append(f"- measurement: {row['metric_basis']} ({row['mode']}){suffix}")
            selection = row["anchor_selection"]
            note = (
                "redrawable by a third party"
                if selection == "seeded_random"
                else "reported in full, moves no score"
            )
            lines.append(f"- query set: {selection} — {note}")
            if row["anchor_seed_was_declared_not_derived"]:
                lines.append(
                    "- the declared anchor seed is not the derived one, so the query set is "
                    "read as operator-chosen"
                )
            lines.append(f"- reason: {row['reason']}")
            if row["attested_claims"]:
                lines.append("- attested, not re-run:")
                lines.extend(f"  - {claim}" for claim in row["attested_claims"])
            if egress["warnings"]:
                lines.append("- egress warnings the operator saw:")
                lines.extend(f"  - {text}" for text in egress["warnings"])
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
    if "model_providers" in report:
        lines.extend(_model_providers_markdown(report["model_providers"]))
    if "memory_stores" in report:
        lines.extend(_memory_markdown(report["memory_stores"]))
    lines.extend(["", "## Installed components", ""])
    for name, item in report["components"].items():
        version = item["version"] or "absent"
        # Treat distribution metadata as untrusted display text.
        version = re.sub(r"[^A-Za-z0-9.+!-]", "?", version)[:128]
        lines.append(f"- {name}: {version} ({item['status']})")
    return "\n".join(lines)


def _cell(value: object) -> str:
    """Operator-supplied text, made safe for one Markdown table cell."""
    text = re.sub(r"[\x00-\x1f\x7f|`]", "?", str(value))
    return text[:128]


def _model_providers_markdown(section: dict[str, Any]) -> list[str]:
    lines = [
        "",
        "## Model providers",
        "",
        "Each provider is scored on its own; the vector takes the worst. Hosting is",
        "derived from each endpoint by this scanner. Models, features, fallbacks,",
        "jurisdiction and every benchmark number are operator- or producer-attested.",
        "0 is not reachable in rubric 2: the suite never runs a model.",
        "",
        "| Provider | Kind | Hosting | Jurisdiction (attested) | Models covered | "
        "Lock-in | Verification |",
        "|---|---|---|---|---:|---:|---|",
    ]
    for provider in section["providers"]:
        lines.append(
            f"| {provider['provider_id']} | {provider['kind']} | {provider['hosting']} | "
            f"{provider['jurisdiction']} | "
            f"{provider['models_with_accepted_alternative']} of {len(provider['models'])} | "
            f"{provider['score']} | {provider['verification_level']} |"
        )
    lines.append("")
    lines.extend(f"- {provider['reason']}" for provider in section["providers"])
    if section["tested_alternatives"]:
        lines.extend(
            [
                "",
                "### Tested alternatives",
                "",
                "Acceptance is recomputed here from the producer's numbers, with a floor",
                "of 20 samples; cost is ignored. That any model ran is attested.",
                "",
                "| Evidence | Provider | Model | Candidate | Hosting | Samples | Accepted |",
                "|---|---|---|---|---|---:|---|",
            ]
        )
        for row in section["tested_alternatives"]:
            accepted = "yes" if row["accepted"] else "no: " + "; ".join(row["reasons"])
            lines.append(
                f"| `{_cell(row['path'])}` | {row['provider_id']} | {_cell(row['model'])} | "
                f"{row['candidate_provider_kind']}:{_cell(row['candidate_model'])} | "
                f"{row['candidate_hosting']} | {row['sample_count']} | {accepted} |"
            )
    return lines


def _memory_markdown(stores: list[dict[str, Any]]) -> list[str]:
    lines = [
        "",
        "## Memory stores",
        "",
        "Digests, counts, tombstones and the item-to-vector binding are",
        "scanner-verified. That the export is complete, that deletions happened, and",
        "that any import ran are attested. 0 is not reachable: the scanner never",
        "restores memory.",
        "",
        "| Store | Kind | Layout | Active | Quarantined | Deleted (tombstones) | "
        "Vectors bound | Import refused | Lock-in |",
        "|---|---|---|---:|---:|---:|---|---|---:|",
    ]
    for store in stores:
        embeddings = store["embeddings"]
        bound = (
            "not linked"
            if embeddings is None
            else f"{embeddings['items_bound']} of "
            f"{embeddings['items_bound'] + embeddings['items_without_vector']}"
        )
        imported = "no report" if store["import"] is None else str(store["import"]["refused"])
        lines.append(
            f"| {store['store_id']} | {store['kind']} | {store['layout']} "
            f"({store['shard_count']}) | {store['active']} | {store['quarantined']} | "
            f"{store['deleted_by_policy']} | {bound} | {imported} | {store['score']} |"
        )
    lines.append("")
    for store in stores:
        lines.append(f"- {store['store_id']}: {store['reason']}")
        if store["embeddings"] is not None:
            lines.extend(
                f"  - finding: {finding['finding']} ({finding['count']} record(s))"
                for finding in store["embeddings"]["findings"]
            )
    return lines
