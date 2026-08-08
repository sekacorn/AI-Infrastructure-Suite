# `ai-suite compatibility`

Reports compatibility as **five separate layers**. They are not the same claim, and collapsing them into one green checkmark is how a metapackage ends up overstating what it knows.

```bash
ai-suite compatibility
ai-suite compatibility --json
ai-suite compatibility --no-imports
```

## The five layers

| Layer | Proves | Does **not** prove |
|---|---|---|
| `installation` | The right distributions are present at versions inside the supported range. | That any of them work. |
| `imports` | Each installed component's public package imports and exposes its expected API. | That any workflow succeeds. |
| `cli` | Each component registers its expected console script in entry-point metadata. | That the script runs — **nothing is executed**. |
| `offline_contract` | This environment reproduces the portable data conventions the ecosystem exchanges. | That any component produced or consumed those artifacts. |
| `live_runtime` | **Nothing. Always `not_checked`.** | — |

Each layer reports its own status, a passed/total count, and a detail line.

## Why `live_runtime` is always `not_checked`

Exercising the seven components together against real models and services requires Ollama, PostgreSQL, Docker, or hosted provider credentials. This package starts none of them, contacts none of them, and reads no credentials.

So it reports the honest answer rather than a convenient one:

```
live_runtime  not_checked  -  Not tested. Running the seven components together
                              requires local or hosted services such as Ollama,
                              PostgreSQL, Docker, or provider credentials. This
                              suite never starts or contacts them, so no live
                              integration is claimed.
```

**A passing `ai-suite compatibility` does not mean the ecosystem has been run end to end.** It means the environment is correctly assembled and reproduces the shared conventions. Those are useful, checkable facts. They are not the same fact.

## The offline contract check

Six deterministic checks, run **in memory, on the standard library alone**. No file is opened, no socket is created, and no ecosystem component is imported.

| Check | Verifies |
|---|---|
| `canonical_json_deterministic` | Canonical JSON — sorted keys, no incidental whitespace, ASCII-escaped, no NaN — is byte-stable across a serialise/parse round trip. |
| `fixture_digest_stable` | A fixed fixture hashes to an anchored SHA-256 constant, catching silent drift in the fixture or the canonical form. |
| `hash_chain_verifies` | A three-link chain recomputes: contiguous sequence numbers, matching previous-digest links, recalculated event digests. |
| `jsonl_round_trip` | JSONL records survive serialisation and parsing without loss. |
| `decimal_money_exact` | Decimal money arithmetic preserves six-place precision with no float drift. |
| `manifest_ranges_parseable` | Every component version range parses as PEP 440. |

### Why these

They are not arbitrary. They are the conventions the ecosystem's portable artifacts actually rely on:

- Canonical JSON and SHA-256 digests underpin AIAuditLog's event digests, AgentPolicyPack's bundle and request digests, and OpenOntologyLite's ontology digests.
- Hash chaining is how AIAuditLog and AgentForge's audit log make tampering evident.
- JSONL is the ecosystem's canonical local stream format.
- Exact decimal money is why AIMeter and ModelSwapBench exchange cost as decimal strings rather than floats.

If an environment cannot reproduce these deterministically, portable artifacts written on one machine will not verify on another — a real, checkable failure mode that this layer catches offline.

### What it explicitly does not do

It builds an in-memory fixture and validates it. It does **not** call AIAuditLog to create an event, ask AgentPolicyPack to evaluate a bundle, or run a ModelSwapBench comparison. It verifies the *conventions*, not the *components*.

## Reading the output

```
┌──────────────────┬─────────────┬────────┬────────────────────────────────────────┐
│ Layer            │ Status      │ Passed │ Detail                                 │
├──────────────────┼─────────────┼────────┼────────────────────────────────────────┤
│ installation     │ ok          │ 7/7    │ 5 of 7 components installed; 7 of 7    │
│                  │             │        │ satisfy their range or are optional.   │
│ imports          │ ok          │ 5/5    │ 5 of 5 installed components import.    │
│ cli              │ ok          │ 5/5    │ Entry-point metadata read; nothing run.│
│ offline_contract │ ok          │ 6/6    │ 6 of 6 conventions verified in memory. │
│ live_runtime     │ not_checked │ -      │ Not tested.                            │
└──────────────────┴─────────────┴────────┴────────────────────────────────────────┘
```

`installation` counts all seven components — optional-and-absent counts as satisfied. `imports` and `cli` count only what is installed, since absent components cannot be imported.

## Exit codes

`0` every checked layer passed · `1` a checked layer failed · `2` the command could not complete.

`not_checked` layers never cause a failure. `--no-imports` therefore exits `0` on a healthy environment.

## JSON output

```json
{
  "schema_version": "1.0",
  "suite_version": "0.1.0a1",
  "ok": true,
  "layers": [
    {
      "layer": "installation",
      "status": "ok",
      "checked": true,
      "passed": 7,
      "total": 7,
      "detail": "..."
    }
  ],
  "contract_checks": [
    {
      "name": "canonical_json_deterministic",
      "passed": true,
      "detail": "Canonical JSON is byte-stable across a serialise/parse round trip."
    }
  ],
  "installation": { "...": "the full doctor report, embedded" }
}
```

The complete `doctor` report is embedded under `installation`, so one command gives you both views.

## Known limitations

- **Installed does not mean working.** The import layer proves a module loads, not that a workflow succeeds.
- **The CLI layer reads metadata only.** A registered console script that crashes on startup still reports `ok`.
- **The contract check is about this environment**, not about any component's behaviour.
- **Ranges are calibrated, not guaranteed.** A component could publish a patch inside its range that breaks something. The upper bound at the next minor limits blast radius; it does not eliminate it.
- **The ecosystem's own integration is looser than it looks.** Only one real runtime dependency edge exists (both ModelSwapBench and PrivateAIStack on `agentforge-oss`); every other connection is a file or dictionary contract with no shared schema package forcing agreement. Validate boundaries explicitly in production.

## Python API

```python
from ai_infrastructure_suite import (
    CompatibilityLayer,
    compatibility_report,
    offline_contract_check,
)

report = compatibility_report()
print(report.ok)

for layer in report.layers:
    print(layer.layer.value, layer.status.value, f"{layer.passed}/{layer.total}")

live = report.layer(CompatibilityLayer.LIVE_RUNTIME)
assert live.status.value == "not_checked"

for check in offline_contract_check():
    print(check.name, check.passed, check.detail)
```
