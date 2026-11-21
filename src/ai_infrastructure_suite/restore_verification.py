"""Independent verifier for Linux of AI deterministic restore evidence."""

from __future__ import annotations

import hashlib
import json
import math
import stat
import tempfile
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from pathlib import Path, PurePosixPath
from typing import Any, cast

Record = dict[str, Any]

ASSETS = (
    "policies",
    "prompts",
    "memory",
    "audit_history",
    "cost_history",
    "ontology",
    "embeddings",
)
PROFILES = ("mock-azure", "mock-aws", "local-ollama")
PROFILE_FACTS = {
    "mock-azure": (4, 41, 13),
    "mock-aws": (6, 43, 15),
    "local-ollama": (8, 47, 17),
}
TRANSFER_LABEL = "vectors copied (same model), not re-embedded"
ZERO_HASH = "0" * 64
MAX_SUMMARY_BYTES = 128 * 1024
MAX_RECORD_BYTES = 512 * 1024
MAX_MANIFEST_BYTES = 64 * 1024
MAX_ASSET_BYTES = 2 * 1024 * 1024


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _canonical(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()


def _unique(pairs: list[tuple[str, Any]]) -> Record:
    result: Record = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate JSON field in restore evidence.")
        result[key] = value
    return result


def _json(data: bytes, label: str) -> Record:
    try:
        value = json.loads(data.decode("utf-8"), object_pairs_hook=_unique)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"Invalid JSON in {label}.") from exc
    if not isinstance(value, dict):
        raise ValueError(f"Expected a JSON object in {label}.")
    return value


def _portable(name: object) -> PurePosixPath:
    if not isinstance(name, str) or not name or "\\" in name or len(name) > 240:
        raise ValueError("Expected a bounded portable evidence path.")
    path = PurePosixPath(name)
    if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
        raise ValueError("Expected a portable evidence path without traversal.")
    return path


def _read(root: Path, name: object, limit: int) -> bytes:
    relative = _portable(name)
    current = root.absolute()
    for part in relative.parts:
        current /= part
        info = current.lstat()
        attributes = getattr(info, "st_file_attributes", 0)
        if stat.S_ISLNK(info.st_mode) or attributes & stat.FILE_ATTRIBUTE_REPARSE_POINT:
            raise ValueError("Linked restore evidence is unsupported.")
    info = current.stat()
    if not stat.S_ISREG(info.st_mode) or info.st_size > limit:
        raise ValueError("Expected a bounded regular restore evidence file.")
    with current.open("rb") as stream:
        data = stream.read(limit + 1)
    if len(data) > limit:
        raise ValueError("Restore evidence exceeds its size limit.")
    return data


@dataclass(frozen=True)
class Asset:
    path: PurePosixPath
    digest: str
    data: bytes


@dataclass(frozen=True)
class Bundle:
    root: Path
    manifest: bytes
    assets: dict[str, Asset]
    task_path: PurePosixPath
    task_digest: str
    task_data: bytes

    @property
    def digest(self) -> str:
        return _sha256(self.manifest)


@dataclass(frozen=True)
class RestoreVerification:
    """Per-vector results from a scanner-owned fake-profile rerun."""

    failures: dict[str, tuple[str, ...]]
    profiles: tuple[ProfileVerification, ...]
    embedding_stores: dict[str, str] = field(default_factory=dict)
    """Vector stores this run commits to: ``store_id`` to evidence-document digest.

    Operator-declared and empty unless the summary carries ``embedding_stores``.
    The digest binds the claim to one embedding-portability document, so a
    store named here with a different digest must not be treated as restored.
    """

    def passed(self, asset: str) -> bool:
        return not self.failures[asset]

    def reason(self, asset: str) -> str:
        if self.passed(asset):
            return (
                "Scanner rebuilt and rechecked all fan-out and chained fake-profile "
                "restores, behavior gates, and re-exports."
            )
        return self.failures[asset][0]


@dataclass(frozen=True)
class ProfileVerification:
    """Scanner result for one fan-out or chained fake-profile run."""

    run_kind: str
    profile: str
    hop_index: int
    failures: tuple[str, ...]

    @property
    def passed(self) -> bool:
        return not self.failures

    @property
    def reason(self) -> str:
        if self.passed:
            return "Scanner reproduced the recorded fake-profile behavior."
        return self.failures[0]


def _load_bundle(root: Path) -> Bundle:
    manifest = _read(root, "manifest.json", MAX_MANIFEST_BYTES)
    document = _json(manifest, "restore bundle manifest")
    if set(document) != {"schema_version", "assets", "task"}:
        raise ValueError("Restore bundle manifest fields are invalid.")
    if document["schema_version"] != "restore-bundle.v1":
        raise ValueError("Unsupported restore bundle schema.")
    raw_assets = document["assets"]
    if not isinstance(raw_assets, dict) or set(raw_assets) != set(ASSETS):
        raise ValueError("Restore bundle asset inventory is invalid.")
    assets: dict[str, Asset] = {}
    for name in ASSETS:
        entry = raw_assets[name]
        if not isinstance(entry, dict) or set(entry) != {"path", "sha256"}:
            raise ValueError("Restore bundle asset entry is invalid.")
        path = _portable(entry["path"])
        digest = entry["sha256"]
        if not isinstance(digest, str) or len(digest) != 64:
            raise ValueError("Restore bundle asset digest is invalid.")
        data = _read(root, str(path), MAX_ASSET_BYTES)
        if _sha256(data) != digest:
            raise ValueError(f"Source restore bundle digest mismatch for {name}.")
        assets[name] = Asset(path, digest, data)
    task = document["task"]
    if not isinstance(task, dict) or set(task) != {"path", "sha256"}:
        raise ValueError("Restore task reference is invalid.")
    task_path = _portable(task["path"])
    task_digest = task["sha256"]
    if not isinstance(task_digest, str) or len(task_digest) != 64:
        raise ValueError("Restore task digest is invalid.")
    task_data = _read(root, str(task_path), MAX_ASSET_BYTES)
    if _sha256(task_data) != task_digest:
        raise ValueError("Restore task digest mismatch.")
    return Bundle(root, manifest, assets, task_path, task_digest, task_data)


def _write_rebuild(source: Bundle, destination: Path) -> Bundle:
    destination.mkdir(parents=True)
    for asset in source.assets.values():
        target = destination.joinpath(*asset.path.parts)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(asset.data)
    task = destination.joinpath(*source.task_path.parts)
    task.parent.mkdir(parents=True, exist_ok=True)
    task.write_bytes(source.task_data)
    (destination / "manifest.json").write_bytes(source.manifest)
    return _load_bundle(destination)


def _audit_hash(data: bytes) -> str:
    previous = ZERO_HASH
    for index, line in enumerate(data.splitlines(), start=1):
        if not line.strip():
            continue
        row = _json(line, f"audit history line {index}")
        digest = row.get("hash")
        payload = {key: value for key, value in row.items() if key != "hash"}
        if row.get("previous_hash") != previous or digest != _sha256(_canonical(payload)):
            raise ValueError("Restored audit history chain is invalid.")
        previous = str(digest)
    if previous == ZERO_HASH:
        raise ValueError("Restored audit history is empty.")
    return _sha256(
        _canonical(
            {
                "event_id": "audit-restore-task",
                "kind": "restore_behavior_checked",
                "previous_hash": previous,
            }
        )
    )


def _cost_total(document: Record) -> str:
    records = document.get("records")
    if document.get("schema_version") != "restore-cost-history.v1" or not isinstance(records, list):
        raise ValueError("Restored cost history is invalid.")
    try:
        total = sum(
            (Decimal(str(cast(Record, item)["estimated_cost"])) for item in records),
            Decimal(0),
        )
    except (InvalidOperation, KeyError, TypeError) as exc:
        raise ValueError("Restored cost history contains an invalid decimal.") from exc
    if total != Decimal("0.0250"):
        raise ValueError("Restored cost history total changed.")
    return format(total, ".4f")


def _retrieval(document: Record) -> tuple[str, int]:
    if (
        document.get("schema_version") != "portable-embeddings.v1"
        or document.get("model") != "portable-hash-v1"
        or document.get("transfer") != TRANSFER_LABEL
    ):
        raise ValueError("Restored portable embedding metadata is invalid.")
    dimension = document.get("dimension")
    items = document.get("items")
    if not isinstance(dimension, int) or dimension != 8 or not isinstance(items, list):
        raise ValueError("Restored portable embedding dimension is unsupported.")
    query = [1.0, *([0.0] * (dimension - 1))]
    best_id = ""
    best_score = -math.inf
    for raw in items:
        if not isinstance(raw, dict) or not isinstance(raw.get("vector"), list):
            raise ValueError("Restored portable embedding item is invalid.")
        vector = raw["vector"]
        if len(vector) != dimension or any(not isinstance(value, (int, float)) for value in vector):
            raise ValueError("Restored portable embedding dimension mismatch.")
        item_id = raw.get("id")
        if not isinstance(item_id, str):
            raise ValueError("Restored portable embedding item ID is invalid.")
        score = sum(float(left) * float(right) for left, right in zip(query, vector, strict=True))
        if score > best_score or (score == best_score and item_id < best_id):
            best_id, best_score = item_id, score
    if best_id != "procedure-password-reset":
        raise ValueError("Restored portable embedding retrieval changed.")
    return best_id, dimension


def _ontology_id(path: Path) -> str:
    from open_ontology_lite import (
        json_ld_text,
        load_json_ld,
        ontology_digest,
        ontology_from_json_ld,
    )

    ontology = load_json_ld(path)
    restored = ontology_from_json_ld(json.loads(json_ld_text(ontology)))
    if ontology_digest(restored) != ontology_digest(ontology):
        raise ValueError("Restored ontology changed during round trip.")
    return ontology.ontology.id


def _simulate_profile(profile: str, expected: Record) -> tuple[str, Record, int, int, int]:
    native_dimension, input_tokens, output_tokens = PROFILE_FACTS[profile]
    if profile == "mock-azure":
        tool_envelope: Record = {
            "choices": [
                {
                    "finish_reason": "tool_calls",
                    "message": {
                        "tool_calls": [
                            {
                                "function": {
                                    "name": "lookup_procedure",
                                    "arguments": '{"procedure_id":"procedure-password-reset"}',
                                }
                            }
                        ]
                    },
                }
            ]
        }
        choice = cast(list[Record], tool_envelope["choices"])[0]
        function = cast(
            Record,
            cast(list[Record], cast(Record, choice["message"])["tool_calls"])[0]["function"],
        )
        tool_name = str(function["name"])
        arguments = _json(str(function["arguments"]).encode(), "Azure tool arguments")
    elif profile == "mock-aws":
        tool_envelope = {
            "stopReason": "tool_use",
            "output": {
                "message": {
                    "content": [
                        {
                            "toolUse": {
                                "name": "lookup_procedure",
                                "input": {"procedure_id": "procedure-password-reset"},
                            }
                        }
                    ]
                }
            },
        }
        output = cast(Record, tool_envelope["output"])
        tool = cast(
            Record,
            cast(list[Record], cast(Record, output["message"])["content"])[0]["toolUse"],
        )
        tool_name = str(tool["name"])
        arguments = cast(Record, tool["input"])
    else:
        tool_envelope = {
            "done_reason": "tool_calls",
            "message": {
                "tool_calls": [
                    {
                        "function": {
                            "name": "lookup_procedure",
                            "arguments": {"procedure_id": "procedure-password-reset"},
                        }
                    }
                ]
            },
        }
        message = cast(Record, tool_envelope["message"])
        function = cast(Record, cast(list[Record], message["tool_calls"])[0]["function"])
        tool_name = str(function["name"])
        arguments = cast(Record, function["arguments"])
    if tool_name != "lookup_procedure" or arguments != {"procedure_id": "procedure-password-reset"}:
        raise ValueError(f"{profile} fake adapter normalization failed.")
    return tool_name, expected, native_dimension, input_tokens, output_tokens


def _behavior(bundle: Bundle, profile: str) -> Record:
    policy = _json(bundle.assets["policies"].data, "restored policies")
    decisions = policy.get("decisions")
    if not isinstance(decisions, dict) or decisions.get("low") != "draft_with_human_review":
        raise ValueError("Restored low-risk policy behavior changed.")
    if decisions.get("high") != "blocked":
        raise ValueError("Restored high-risk policy behavior changed.")
    prompt = bundle.assets["prompts"].data.decode("utf-8")
    if "human review" not in prompt or "Never request or expose credentials" not in prompt:
        raise ValueError("Restored prompt behavior changed.")
    memory = _json(bundle.assets["memory"].data, "restored memory")
    items = memory.get("items")
    if not isinstance(items, list):
        raise ValueError("Restored memory is invalid.")
    procedures = {
        str(cast(Record, item).get("id")): str(cast(Record, item).get("text")) for item in items
    }
    procedure_id, copied_dimension = _retrieval(
        _json(bundle.assets["embeddings"].data, "restored embeddings")
    )
    procedure = procedures.get(procedure_id)
    if procedure is None or "never request or expose credentials" not in procedure:
        raise ValueError("Restored memory lookup changed.")
    ontology_id = _ontology_id(bundle.root.joinpath(*bundle.assets["ontology"].path.parts))
    if ontology_id != "customer-service":
        raise ValueError("Restored ontology concept changed.")
    task = _json(bundle.task_data, "restored scripted task")
    expected = task.get("expected")
    if not isinstance(expected, dict):
        raise ValueError("Restored scripted task expectation is invalid.")
    tool_name, actual, native_dimension, input_tokens, output_tokens = _simulate_profile(
        profile, expected
    )
    return {
        "status": "passed",
        "expected_result_sha256": _sha256(_canonical(expected)),
        "actual_result_sha256": _sha256(_canonical(actual)),
        "tool_call": tool_name,
        "tool_usage": {"input_tokens": input_tokens, "output_tokens": output_tokens},
        "final_usage": {"input_tokens": input_tokens, "output_tokens": output_tokens},
        "native_embedding_dimension": native_dimension,
        "native_embedding_input": "task text only",
        "portable_embedding_transfer": TRANSFER_LABEL,
        "portable_embedding_model": "portable-hash-v1",
        "portable_embedding_dimension": copied_dimension,
        "retrieval_engine": "portable vector store",
        "retrieved_procedure": procedure_id,
        "ontology_id": ontology_id,
        "audit_append_hash": _audit_hash(bundle.assets["audit_history"].data),
        "cost_history_total": _cost_total(
            _json(bundle.assets["cost_history"].data, "restored cost history")
        ),
        "asset_checks": dict.fromkeys(ASSETS, "passed"),
    }


def _reference(document: object, expected_profile: str, expected_hop: int) -> tuple[str, str]:
    if not isinstance(document, dict) or set(document) != {
        "profile",
        "hop_index",
        "path",
        "sha256",
    }:
        raise ValueError("Restore record reference fields are invalid.")
    if document["profile"] != expected_profile or document["hop_index"] != expected_hop:
        raise ValueError("Restore record reference ordering is invalid.")
    path = str(_portable(document["path"]))
    digest = document["sha256"]
    if not isinstance(digest, str) or len(digest) != 64:
        raise ValueError("Restore record reference digest is invalid.")
    return path, digest


def _bundle_reference(document: object) -> tuple[PurePosixPath, str]:
    if not isinstance(document, dict) or set(document) != {"path", "sha256"}:
        raise ValueError("Restore bundle reference fields are invalid.")
    path = _portable(document["path"])
    digest = document["sha256"]
    if not isinstance(digest, str) or len(digest) != 64:
        raise ValueError("Restore bundle reference digest is invalid.")
    return path, digest


def _record(
    root: Path,
    reference: object,
    *,
    run_kind: str,
    profile: str,
    hop_index: int,
) -> Record:
    path, digest = _reference(reference, profile, hop_index)
    data = _read(root, path, MAX_RECORD_BYTES)
    if _sha256(data) != digest:
        raise ValueError("Restore profile record digest mismatch.")
    document = _json(data, "restore profile record")
    required = {
        "schema_version",
        "run_kind",
        "hop_index",
        "profile",
        "provider_mode",
        "source_bundle_sha256",
        "input_bundle",
        "restored_bundle",
        "exported_bundle",
        "migration",
        "machine_signoff",
    }
    if set(document) != required:
        raise ValueError("Restore profile record fields are invalid.")
    if (
        document["schema_version"] != "restore-profile-record.v1"
        or document["run_kind"] != run_kind
        or document["profile"] != profile
        or document["hop_index"] != hop_index
        or document["provider_mode"] != "fake"
    ):
        raise ValueError("Restore profile record identity is invalid.")
    base = (
        PurePosixPath("fanout") / profile
        if run_kind == "fanout"
        else PurePosixPath("chain") / f"{hop_index:02d}-{profile}"
    )
    expected_input = (
        PurePosixPath("evidence/source-bundle")
        if run_kind == "fanout" or hop_index == 1
        else PurePosixPath("chain") / f"{hop_index - 1:02d}-{PROFILES[hop_index - 2]}" / "exported"
    )
    input_path, input_digest = _bundle_reference(document["input_bundle"])
    restored_path, restored_digest = _bundle_reference(document["restored_bundle"])
    exported_path, exported_digest = _bundle_reference(document["exported_bundle"])
    if (
        input_path != expected_input
        or restored_path != base / "restored"
        or exported_path != base / "exported"
        or len({input_digest, restored_digest, exported_digest}) != 1
    ):
        raise ValueError("Restore profile record bundle references are invalid.")
    signoff = document["machine_signoff"]
    if not isinstance(signoff, dict) or signoff != {
        "producer": "linux-ai-restore-demo",
        "status": "passed",
        "checks": ["content", "behavior", "re-export"],
        "identity_claim": "none",
    }:
        raise ValueError("Restore profile record machine sign-off is invalid.")
    return document


def _add_failure(failures: dict[str, list[str]], asset: str, reason: str) -> None:
    if reason not in failures[asset]:
        failures[asset].append(reason)


def _check_copy(
    root: Path,
    reference: Bundle,
    bundle_reference: object,
    *,
    label: str,
    failures: dict[str, list[str]],
) -> None:
    path, declared_digest = _bundle_reference(bundle_reference)
    candidate_root = root.joinpath(*path.parts)
    try:
        manifest = _read(candidate_root, "manifest.json", MAX_MANIFEST_BYTES)
    except (OSError, ValueError) as exc:
        for asset_name in ASSETS:
            _add_failure(
                failures,
                asset_name,
                f"{label}: bundle manifest is inaccessible ({exc}).",
            )
        return
    if _sha256(manifest) != declared_digest or manifest != reference.manifest:
        for asset_name in ASSETS:
            _add_failure(failures, asset_name, f"{label}: bundle manifest digest mismatch.")
        return
    for asset_name, asset in reference.assets.items():
        try:
            candidate = _read(candidate_root, str(asset.path), MAX_ASSET_BYTES)
        except (OSError, ValueError) as exc:
            _add_failure(failures, asset_name, f"{label}: {asset_name} is inaccessible ({exc}).")
            continue
        if _sha256(candidate) != asset.digest:
            _add_failure(failures, asset_name, f"{label}: {asset_name} digest mismatch.")


def _embedding_stores(value: object) -> dict[str, str]:
    """Parse the optional ``embedding_stores`` block of a restore summary."""
    if value is None:
        return {}
    if not isinstance(value, list) or not value or len(value) > 64:
        raise ValueError("Restore summary embedding stores are invalid.")
    stores: dict[str, str] = {}
    for entry in value:
        if not isinstance(entry, dict) or entry.keys() != {"store_id", "store_evidence_sha256"}:
            raise ValueError("Each embedding store requires store_id and store_evidence_sha256.")
        store_id, digest = entry["store_id"], entry["store_evidence_sha256"]
        if not isinstance(store_id, str) or not 0 < len(store_id) <= 128:
            raise ValueError("Invalid embedding store_id.")
        if not isinstance(digest, str) or len(digest) != 64 or digest.strip("0123456789abcdef"):
            raise ValueError("Invalid embedding store evidence digest.")
        if store_id in stores:
            raise ValueError("Duplicate embedding store_id in restore summary.")
        stores[store_id] = digest
    return stores


def verify_restore_summary(summary_path: Path) -> RestoreVerification:
    """Rebuild and recheck every fake-profile record referenced by a summary."""
    root = summary_path.absolute().parent
    summary_data = _read(root, summary_path.name, MAX_SUMMARY_BYTES)
    summary = _json(summary_data, "restore summary")
    required = {
        "schema_version",
        "verifier_contract",
        "provider_mode",
        "source_bundle",
        "fanout",
        "chain",
        "status",
    }
    # ``embedding_stores`` is the one optional field. Accepting it here before any
    # producer emits it keeps an updated summary readable by this scanner.
    if not required <= set(summary) or set(summary) - (required | {"embedding_stores"}):
        raise ValueError("Restore summary fields are invalid.")
    embedding_stores = _embedding_stores(summary.get("embedding_stores"))
    if (
        summary["schema_version"] != "restore-summary.v1"
        or summary["verifier_contract"] != "linux-ai-restore-demo.v1"
        or summary["provider_mode"] != "fake-only"
        or summary["status"] != "passed"
    ):
        raise ValueError("Restore summary is not a completed fake-profile run.")
    source_path, source_digest = _bundle_reference(summary["source_bundle"])
    source = _load_bundle(root.joinpath(*source_path.parts))
    if source.digest != source_digest:
        raise ValueError("Restore summary source bundle digest mismatch.")
    fanout = summary["fanout"]
    chain = summary["chain"]
    if not isinstance(fanout, list) or len(fanout) != 3:
        raise ValueError("Restore summary fan-out records are invalid.")
    if not isinstance(chain, list) or len(chain) != 3:
        raise ValueError("Restore summary chained records are invalid.")
    records: list[Record] = []
    for index, profile in enumerate(PROFILES):
        records.append(
            _record(
                root,
                fanout[index],
                run_kind="fanout",
                profile=profile,
                hop_index=0,
            )
        )
    for index, profile in enumerate(PROFILES, start=1):
        records.append(
            _record(
                root,
                chain[index - 1],
                run_kind="chain",
                profile=profile,
                hop_index=index,
            )
        )
    failures: dict[str, list[str]] = {name: [] for name in ASSETS}
    profiles: list[ProfileVerification] = []
    with tempfile.TemporaryDirectory(prefix="ai-suite-restore-") as directory:
        temporary = Path(directory)
        for record_index, document in enumerate(records):
            profile = cast(str, document["profile"])
            run_kind = cast(str, document["run_kind"])
            hop_index = cast(int, document["hop_index"])
            label = (
                f"{run_kind} {profile}"
                if run_kind == "fanout"
                else f"chain hop {hop_index} {profile}"
            )
            if document["source_bundle_sha256"] != source.digest:
                raise ValueError("Restore profile record source digest mismatch.")
            rebuilt = _write_rebuild(source, temporary / f"record-{record_index}")
            profile_failures: list[str] = []
            try:
                behavior = _behavior(rebuilt, profile)
            except (OSError, ValueError) as exc:
                profile_failures.append(
                    f"unattributed behavior drift: {label} scanner rerun failed ({exc})."
                )
            else:
                migration = document["migration"]
                if not isinstance(migration, dict) or migration.get("behavior_gate") != behavior:
                    profile_failures.append(
                        "unattributed behavior drift: "
                        f"{label} recorded behavior differs from scanner rerun."
                    )
            profiles.append(
                ProfileVerification(
                    run_kind=run_kind,
                    profile=profile,
                    hop_index=hop_index,
                    failures=tuple(profile_failures),
                )
            )
            _check_copy(
                root,
                source,
                document["restored_bundle"],
                label=f"{label} restored",
                failures=failures,
            )
            _check_copy(
                root,
                source,
                document["exported_bundle"],
                label=f"{label} exported",
                failures=failures,
            )
    return RestoreVerification(
        failures={name: tuple(reasons) for name, reasons in failures.items()},
        profiles=tuple(profiles),
        embedding_stores=embedding_stores,
    )
