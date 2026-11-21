"""The shared evidence primitives, and both scanners that depend on them.

Path validation and bounded reading are security relevant and live in one
module, so these tests exercise the primitives directly and then prove that
`exit_report` and `embedding_portability` both inherit the same refusals.
"""

from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path

import pytest

from ai_infrastructure_suite._evidence import (
    MAX_PATH_CHARS,
    load_json_document,
    portable_relative_path,
    read_bounded_file,
    unique_json_pairs,
)
from ai_infrastructure_suite.embedding_portability import scan_embedding_portability
from ai_infrastructure_suite.exit_report import build_exit_report

# Paths no evidence document may reference, whichever scanner reads it.
HOSTILE_PATHS = (
    "../outside.txt",
    "nested/../../outside.txt",
    "/etc/passwd",
    "//server/share/evidence.txt",
    "C:/Windows/System32/drivers/etc/hosts",
    "a//b.txt",
    "./record.txt",
    "",
    "NUL.txt",
    "COM1.txt",
    "lpt9.json",
    "trailing..",
    ".hidden",
    "x" * (MAX_PATH_CHARS + 1),
)


def _link(source: Path, alias: Path) -> None:
    """Create a symlink, or skip when the platform refuses."""
    try:
        alias.symlink_to(source)
    except OSError:
        pytest.skip("symlink creation unavailable")


def _junction(target: Path, alias: Path) -> None:
    """Create a Windows directory junction, or skip when unavailable.

    A junction is a reparse point that is not a symlink, so it is the case the
    `st_file_attributes` check exists for.
    """
    if not hasattr(target.stat(), "st_file_attributes"):
        pytest.skip("reparse points are a Windows concept")
    result = subprocess.run(
        ["cmd", "/c", "mklink", "/J", str(alias), str(target)],
        capture_output=True,
        check=False,
    )
    if result.returncode != 0 or not alias.exists():
        pytest.skip("junction creation unavailable")


# --- The primitives directly ------------------------------------------------


@pytest.mark.parametrize("name", HOSTILE_PATHS)
def test_portable_relative_path_refuses_unsafe_names(name: str) -> None:
    with pytest.raises(ValueError, match="evidence path"):
        portable_relative_path(name)


@pytest.mark.parametrize("value", (None, 7, b"record.txt", ["record.txt"], {}))
def test_portable_relative_path_requires_a_string(value: object) -> None:
    with pytest.raises(ValueError, match="Invalid evidence path"):
        portable_relative_path(value)


@pytest.mark.parametrize(
    "name",
    ("record.txt", "a/b/c.json", "_leading/0digit.jsonl", "dash-dot.name.v1.json"),
)
def test_portable_relative_path_accepts_portable_names(name: str) -> None:
    assert portable_relative_path(name) == name


def test_read_bounded_file_refuses_traversal(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="without traversal"):
        read_bounded_file(tmp_path / ".." / "record.txt", 64)


@pytest.mark.parametrize("prefix", ("//server/share", "\\\\server\\share"))
def test_read_bounded_file_refuses_unc_paths(prefix: str) -> None:
    with pytest.raises(ValueError, match="without traversal"):
        read_bounded_file(Path(f"{prefix}/record.txt"), 64)


def test_read_bounded_file_refuses_a_symlinked_file(tmp_path: Path) -> None:
    target = tmp_path / "record.txt"
    target.write_bytes(b"payload")
    alias = tmp_path / "alias.txt"
    _link(target, alias)

    with pytest.raises(ValueError, match="Linked"):
        read_bounded_file(alias, 64)


def test_read_bounded_file_refuses_a_file_under_a_reparse_point(tmp_path: Path) -> None:
    real = tmp_path / "real"
    real.mkdir()
    (real / "record.txt").write_bytes(b"payload")
    alias = tmp_path / "alias"
    _junction(real, alias)

    # The file itself is ordinary; the directory above it is the reparse point.
    with pytest.raises(ValueError, match="Linked"):
        read_bounded_file(alias / "record.txt", 64)


def test_read_bounded_file_refuses_a_directory(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="bounded regular file"):
        read_bounded_file(tmp_path, 64)


def test_read_bounded_file_refuses_an_oversized_file(tmp_path: Path) -> None:
    path = tmp_path / "record.txt"
    path.write_bytes(b"x" * 65)

    with pytest.raises(ValueError, match="bounded regular file"):
        read_bounded_file(path, 64)


def test_read_bounded_file_accepts_a_file_at_the_limit(tmp_path: Path) -> None:
    path = tmp_path / "record.txt"
    path.write_bytes(b"x" * 64)

    assert read_bounded_file(path, 64) == b"x" * 64


def test_unique_json_pairs_refuses_duplicate_fields() -> None:
    with pytest.raises(ValueError, match="Duplicate JSON field"):
        unique_json_pairs([("a", 1), ("a", 2)])


@pytest.mark.parametrize("literal", (b'{"v":NaN}', b'{"v":Infinity}', b'{"v":-Infinity}'))
def test_load_json_document_refuses_non_finite_literals(literal: bytes) -> None:
    with pytest.raises(ValueError, match="Non-finite JSON number"):
        load_json_document(literal, "record")


def test_load_json_document_refuses_non_utf8_bytes() -> None:
    with pytest.raises(ValueError, match="Expected UTF-8 record"):
        load_json_document(b'{"v":"\xff\xfe"}', "record")


def test_load_json_document_refuses_malformed_json() -> None:
    with pytest.raises(ValueError, match="well-formed record"):
        load_json_document(b'{"v":', "record")


# --- Both scanners inherit the refusals -------------------------------------


def _manifest(root: Path, entry: dict[str, object]) -> Path:
    path = root / "manifest.json"
    path.write_text(
        json.dumps({"schema_version": "exit-manifest.v1", "vectors": {"memory": entry}}),
        encoding="utf-8",
    )
    return path


def _evidence(root: Path, vector_path: str, vector_bytes: bytes | None = None) -> tuple[Path, str]:
    data = vector_bytes if vector_bytes is not None else json.dumps({"values": [0.5]}).encode()
    (root / "vector.json").write_bytes(data)
    text = b"retained source\n"
    (root / "source.txt").write_bytes(text)
    source_digest = hashlib.sha256(text).hexdigest()
    document = {
        "schema_version": "embedding-portability.v1",
        "stores": [
            {
                "store_id": "store",
                "embedding_model": {
                    "id": "m",
                    "revision": "1",
                    "dimensions": 1,
                    "availability": "vendor_only",
                    "availability_verification": "attested",
                },
                "vectors": {
                    "layout": "single_file",
                    "records": [
                        {
                            "record_id": "r1",
                            "vector": {
                                "path": vector_path,
                                "sha256": hashlib.sha256(data).hexdigest(),
                            },
                            "source_text_sha256_in_vector_record": source_digest,
                            "source_text": {
                                "status": "retained",
                                "path": "source.txt",
                                "sha256": source_digest,
                            },
                        }
                    ],
                },
            }
        ],
    }
    path = root / "embeddings.json"
    raw = json.dumps(document).encode()
    path.write_bytes(raw)
    return path, hashlib.sha256(raw).hexdigest()


@pytest.mark.parametrize("name", HOSTILE_PATHS)
def test_the_exit_report_refuses_unsafe_evidence_paths(tmp_path: Path, name: str) -> None:
    manifest = _manifest(tmp_path, {"state": "exported", "path": name, "sha256": "0" * 64})

    with pytest.raises(ValueError, match="evidence path"):
        build_exit_report(manifest)


@pytest.mark.parametrize("name", HOSTILE_PATHS)
def test_the_embedding_scanner_refuses_unsafe_evidence_paths(tmp_path: Path, name: str) -> None:
    path, digest = _evidence(tmp_path, name)

    with pytest.raises(ValueError, match="evidence path"):
        scan_embedding_portability(path, evidence_sha256=digest)


def test_the_exit_report_refuses_a_symlinked_evidence_file(tmp_path: Path) -> None:
    data = b"operator record\n"
    (tmp_path / "record.txt").write_bytes(data)
    _link(tmp_path / "record.txt", tmp_path / "alias.txt")
    manifest = _manifest(
        tmp_path,
        {"state": "exported", "path": "alias.txt", "sha256": hashlib.sha256(data).hexdigest()},
    )

    with pytest.raises(ValueError, match="Linked"):
        build_exit_report(manifest)


def test_the_embedding_scanner_refuses_a_symlinked_vector_file(tmp_path: Path) -> None:
    path, digest = _evidence(tmp_path, "alias.json")
    _link(tmp_path / "vector.json", tmp_path / "alias.json")

    with pytest.raises(ValueError, match="Linked"):
        scan_embedding_portability(path, evidence_sha256=digest)


def test_the_exit_report_refuses_evidence_under_a_reparse_point(tmp_path: Path) -> None:
    real = tmp_path / "real"
    real.mkdir()
    data = b"operator record\n"
    (real / "record.txt").write_bytes(data)
    _junction(real, tmp_path / "alias")
    manifest = _manifest(
        tmp_path,
        {
            "state": "exported",
            "path": "alias/record.txt",
            "sha256": hashlib.sha256(data).hexdigest(),
        },
    )

    with pytest.raises(ValueError, match="Linked"):
        build_exit_report(manifest)


def test_the_embedding_scanner_refuses_a_vector_under_a_reparse_point(tmp_path: Path) -> None:
    real = tmp_path / "real"
    real.mkdir()
    path, digest = _evidence(tmp_path, "alias/vector.json")
    (real / "vector.json").write_bytes((tmp_path / "vector.json").read_bytes())
    _junction(real, tmp_path / "alias")

    with pytest.raises(ValueError, match="Linked"):
        scan_embedding_portability(path, evidence_sha256=digest)


def test_the_exit_report_refuses_an_oversized_manifest(tmp_path: Path) -> None:
    path = tmp_path / "manifest.json"
    padding = " " * (64 * 1024)
    path.write_text(
        json.dumps({"schema_version": "exit-manifest.v1", "vectors": {}, "pad": padding}),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="bounded regular file"):
        build_exit_report(path)


def test_the_embedding_scanner_refuses_an_oversized_vector_file(tmp_path: Path) -> None:
    path, digest = _evidence(tmp_path, "vector.json")
    oversized = b'{"values":[0.5],"pad":"' + b"x" * (1024 * 1024) + b'"}'
    (tmp_path / "vector.json").write_bytes(oversized)

    with pytest.raises(ValueError, match="bounded regular file"):
        scan_embedding_portability(path, evidence_sha256=digest)


def test_the_exit_report_refuses_duplicate_manifest_fields(tmp_path: Path) -> None:
    path = tmp_path / "manifest.json"
    path.write_bytes(b'{"schema_version":"exit-manifest.v1","vectors":{},"vectors":{}}')

    with pytest.raises(ValueError, match="Duplicate JSON field"):
        build_exit_report(path)


def test_the_embedding_scanner_refuses_duplicate_vector_file_fields(tmp_path: Path) -> None:
    # The digest is checked before the bytes are parsed, so the hostile file
    # has to be the one the evidence commits to.
    path, digest = _evidence(tmp_path, "vector.json", b'{"values":[0.5],"values":[0.5]}')

    with pytest.raises(ValueError, match="Duplicate JSON field"):
        scan_embedding_portability(path, evidence_sha256=digest)
