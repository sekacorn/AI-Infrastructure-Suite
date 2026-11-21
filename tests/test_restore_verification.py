from __future__ import annotations

import hashlib
import json
import shutil
from importlib.resources import files
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from ai_infrastructure_suite.cli import app
from ai_infrastructure_suite.exit_report import build_exit_report
from ai_infrastructure_suite.restore_verification import ASSETS, _behavior, _load_bundle

Record = dict[str, Any]
RESTORED_VECTORS = (
    "policies",
    "prompts",
    "embeddings",
    "memory",
    "audit_history",
    "cost_history",
    "ontology",
)
PROFILES = ("mock-azure", "mock-aws", "local-ollama")


def _canonical(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode()


def _write_json(path: Path, value: object) -> bytes:
    data = (json.dumps(value, indent=2, sort_keys=True) + "\n").encode()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return data


def _audit_rows() -> bytes:
    previous = "0" * 64
    rows = []
    for event_id, kind in (
        ("audit-001", "bundle_created"),
        ("audit-002", "policy_approved"),
    ):
        payload = {"event_id": event_id, "kind": kind, "previous_hash": previous}
        digest = hashlib.sha256(_canonical(payload)).hexdigest()
        rows.append({**payload, "hash": digest})
        previous = digest
    return b"".join(_canonical(row) + b"\n" for row in rows)


def _source_bundle(root: Path) -> Path:
    source = root / "evidence" / "source-bundle"
    assets = source / "assets"
    assets.mkdir(parents=True)
    payloads = {
        "policies": (
            "assets/policies.json",
            _write_json(
                assets / "policies.json",
                {
                    "schema_version": "restore-policy.v1",
                    "decisions": {
                        "high": "blocked",
                        "low": "draft_with_human_review",
                    },
                },
            ),
        ),
        "prompts": (
            "assets/prompt.txt",
            b"Use the restored procedure for human review. Never request or expose credentials.\n",
        ),
        "memory": (
            "assets/memory.json",
            _write_json(
                assets / "memory.json",
                {
                    "schema_version": "restore-memory.v1",
                    "items": [
                        {
                            "id": "procedure-password-reset",
                            "text": (
                                "Use the approved password-reset flow and never request "
                                "or expose credentials."
                            ),
                        }
                    ],
                },
            ),
        ),
        "audit_history": ("assets/audit-history.jsonl", _audit_rows()),
        "cost_history": (
            "assets/cost-history.json",
            _write_json(
                assets / "cost-history.json",
                {
                    "schema_version": "restore-cost-history.v1",
                    "currency": "USD",
                    "records": [
                        {"id": "cost-001", "estimated_cost": "0.0100"},
                        {"id": "cost-002", "estimated_cost": "0.0150"},
                    ],
                },
            ),
        ),
        "embeddings": (
            "assets/embeddings.json",
            _write_json(
                assets / "embeddings.json",
                {
                    "schema_version": "portable-embeddings.v1",
                    "model": "portable-hash-v1",
                    "dimension": 8,
                    "transfer": "vectors copied (same model), not re-embedded",
                    "items": [
                        {
                            "id": "procedure-password-reset",
                            "vector": [1, 0, 0, 0, 0, 0, 0, 0],
                        }
                    ],
                },
            ),
        ),
        "ontology": (
            "assets/ontology.jsonld",
            (
                Path(__file__).parents[1] / "docs" / "examples" / "exit-report" / "ontology.jsonld"
            ).read_bytes(),
        ),
    }
    for _, (name, data) in payloads.items():
        path = source / name
        if not path.exists():
            path.write_bytes(data)
    task = {
        "schema_version": "restore-task.v1",
        "task_id": "restore-password-reset-001",
        "risk": "low",
        "message": "I forgot my portal password. What is the approved internal procedure?",
        "expected": {
            "action": "draft_support_response",
            "final_response_id": "password-reset-draft-v1",
            "human_review": True,
            "ontology_id": "customer-service",
            "policy_decision": "draft_with_human_review",
            "procedure_id": "procedure-password-reset",
        },
    }
    task_data = _write_json(source / "task.json", task)
    manifest = {
        "schema_version": "restore-bundle.v1",
        "assets": {
            name: {"path": path, "sha256": hashlib.sha256(data).hexdigest()}
            for name, (path, data) in payloads.items()
        },
        "task": {"path": "task.json", "sha256": hashlib.sha256(task_data).hexdigest()},
    }
    _write_json(source / "manifest.json", manifest)
    return source


def _migration(bundle_digest: str, behavior: Record, profile: str) -> Record:
    matched = dict.fromkeys(ASSETS, "matched")
    return {
        "profile": profile,
        "provider_mode": "fake",
        "input_bundle_sha256": bundle_digest,
        "output_bundle_sha256": bundle_digest,
        "content_gate": matched,
        "original_content_gate": matched,
        "reexport_gate": matched,
        "original_reexport_gate": matched,
        "behavior_gate": behavior,
        "status": "passed",
    }


def make_restore_evidence(root: Path) -> Path:
    source = _source_bundle(root)
    bundle = _load_bundle(source)
    fanout_refs = []
    chain_refs = []
    for run_kind in ("fanout", "chain"):
        for offset, profile in enumerate(PROFILES, start=1):
            hop = 0 if run_kind == "fanout" else offset
            base = (
                Path("fanout") / profile
                if run_kind == "fanout"
                else Path("chain") / f"{hop:02d}-{profile}"
            )
            for stage in ("restored", "exported"):
                shutil.copytree(source, root / base / stage)
            input_path = (
                Path("evidence/source-bundle")
                if run_kind == "fanout" or hop == 1
                else Path("chain") / f"{hop - 1:02d}-{PROFILES[hop - 2]}" / "exported"
            )
            migration = _migration(bundle.digest, _behavior(bundle, profile), profile)
            record = {
                "schema_version": "restore-profile-record.v1",
                "run_kind": run_kind,
                "hop_index": hop,
                "profile": profile,
                "provider_mode": "fake",
                "source_bundle_sha256": bundle.digest,
                "input_bundle": {"path": input_path.as_posix(), "sha256": bundle.digest},
                "restored_bundle": {
                    "path": (base / "restored").as_posix(),
                    "sha256": bundle.digest,
                },
                "exported_bundle": {
                    "path": (base / "exported").as_posix(),
                    "sha256": bundle.digest,
                },
                "migration": migration,
                "machine_signoff": {
                    "producer": "linux-ai-restore-demo",
                    "status": "passed",
                    "checks": ["content", "behavior", "re-export"],
                    "identity_claim": "none",
                },
            }
            record_path = (
                Path("records")
                / run_kind
                / (f"{profile}.json" if run_kind == "fanout" else f"{hop:02d}-{profile}.json")
            )
            record_data = _write_json(root / record_path, record)
            reference = {
                "profile": profile,
                "hop_index": hop,
                "path": record_path.as_posix(),
                "sha256": hashlib.sha256(record_data).hexdigest(),
            }
            (fanout_refs if run_kind == "fanout" else chain_refs).append(reference)
    summary = {
        "schema_version": "restore-summary.v1",
        "verifier_contract": "linux-ai-restore-demo.v1",
        "provider_mode": "fake-only",
        "source_bundle": {"path": "evidence/source-bundle", "sha256": bundle.digest},
        "fanout": fanout_refs,
        "chain": chain_refs,
        "status": "passed",
    }
    summary_data = _write_json(root / "restore-summary.json", summary)
    entry = {
        "state": "restore_tested",
        "path": "restore-summary.json",
        "sha256": hashlib.sha256(summary_data).hexdigest(),
    }
    manifest = {
        "schema_version": "exit-manifest.v1",
        "vectors": dict.fromkeys(RESTORED_VECTORS, entry),
    }
    _write_json(root / "exit-manifest.json", manifest)
    return root / "exit-manifest.json"


def _assert_only_restore_vector_failed(
    before: Record,
    after: Record,
    target: str,
    reason: str,
) -> None:
    for vector in RESTORED_VECTORS:
        row = after["vectors"][vector]
        if vector == target:
            assert row["state"] == "restore_failed"
            assert row["score"] == 100
            assert row["verification_level"] == "verified (fake profiles)"
            assert row["reason"] == reason
        else:
            assert row == before["vectors"][vector]


def test_restore_records_are_attested_until_scanner_rechecks(tmp_path: Path) -> None:
    manifest = make_restore_evidence(tmp_path)
    unchecked = build_exit_report(manifest)
    checked = build_exit_report(manifest, verify_restores=True)
    for vector in RESTORED_VECTORS:
        assert unchecked["vectors"][vector]["verification_level"] == "attested"
        assert checked["vectors"][vector]["verification_level"] == "verified (fake profiles)"
        assert checked["vectors"][vector]["state"] == "restore_tested"
        assert checked["vectors"][vector]["score"] == 0
    assert checked["vectors"]["fine_tunes"]["verification_level"] == "unverified"


def test_cli_explicit_restore_verification(tmp_path: Path) -> None:
    manifest = make_restore_evidence(tmp_path)
    result = CliRunner().invoke(
        app,
        ["exit-report", str(manifest), "--verify-restores", "--json"],
    )
    assert result.exit_code == 0, result.exception
    payload = json.loads(result.stdout)
    assert payload["vectors"]["memory"]["verification_level"] == "verified (fake profiles)"


def test_restore_contract_schemas_are_packaged_and_match_generated_evidence(
    tmp_path: Path,
) -> None:
    manifest = make_restore_evidence(tmp_path)
    data_root = files("ai_infrastructure_suite").joinpath("data")
    summary = json.loads((tmp_path / "restore-summary.json").read_text(encoding="utf-8"))
    record = json.loads((tmp_path / summary["fanout"][0]["path"]).read_text(encoding="utf-8"))
    summary_schema = json.loads(
        data_root.joinpath("restore-summary-v1.schema.json").read_text(encoding="utf-8")
    )
    record_schema = json.loads(
        data_root.joinpath("restore-profile-record-v1.schema.json").read_text(encoding="utf-8")
    )
    assert summary_schema["properties"]["schema_version"]["const"] == summary["schema_version"]
    assert record_schema["properties"]["schema_version"]["const"] == record["schema_version"]
    assert set(summary_schema["required"]) == set(summary)
    assert set(record_schema["required"]) == set(record)
    assert manifest.exists()


@pytest.mark.parametrize(
    ("profile", "vector", "asset_path"),
    (
        ("mock-azure", "policies", "assets/policies.json"),
        ("mock-aws", "prompts", "assets/prompt.txt"),
        ("local-ollama", "memory", "assets/memory.json"),
    ),
)
def test_post_record_profile_corruption_downgrades_only_its_vector(
    tmp_path: Path,
    profile: str,
    vector: str,
    asset_path: str,
) -> None:
    manifest = make_restore_evidence(tmp_path)
    before = build_exit_report(manifest, verify_restores=True)
    corrupted = tmp_path / "fanout" / profile / "restored" / asset_path
    corrupted.write_bytes(corrupted.read_bytes() + b"post-record corruption\n")

    after = build_exit_report(manifest, verify_restores=True)

    _assert_only_restore_vector_failed(
        before,
        after,
        vector,
        f"fanout {profile} restored: {vector} digest mismatch.",
    )


def test_chained_mock_aws_export_corruption_is_attributed_to_hop_two(
    tmp_path: Path,
) -> None:
    manifest = make_restore_evidence(tmp_path)
    before = build_exit_report(manifest, verify_restores=True)
    corrupted = tmp_path / "chain" / "02-mock-aws" / "exported" / "assets" / "cost-history.json"
    corrupted.write_bytes(corrupted.read_bytes() + b"post-record corruption\n")

    after = build_exit_report(manifest, verify_restores=True)

    _assert_only_restore_vector_failed(
        before,
        after,
        "cost_history",
        "chain hop 2 mock-aws exported: cost_history digest mismatch.",
    )
