# AI Infrastructure Suite

**The unified installer and compatibility layer for portable, governed, measurable, and vendor-neutral AI infrastructure.**

`ai-infrastructure-suite` is a lightweight metapackage for the [Linux of AI](https://github.com/sekacorn) ecosystem: one install command for seven independent components, plus a small diagnostics layer that tells you exactly what is installed, whether the versions fit, and what still needs attention.

It contains **none of the seven projects' business logic**. It installs them, describes them, and diagnoses them. Each component remains independently installable and independently useful.

> **Status:** `0.1.0a1`, alpha. The ecosystem it installs is alpha too. Version ranges may tighten before 1.0.

> **Independent project.** Linux of AI and AI Infrastructure Suite are independent open-source projects. They are **not affiliated with, endorsed by, or sponsored by the Linux Foundation**. "Linux of AI" is used as a descriptive name for an open, portable infrastructure ecosystem. This package is not an operating system, not a kernel, and not a replacement for either.

---

## Why this exists

The seven Linux of AI projects are deliberately separate. That is good architecture and awkward onboarding: seven `pip install` lines, seven version ranges to keep straight, and no single answer to "is my environment actually set up correctly?"

This package gives you:

- **One install entry point** with layered extras, so a default install stays lightweight.
- **Verified version ranges** checked against what is actually published, not what is planned.
- **A real diagnostic** (`ai-suite doctor`) that classifies every component as `ok`, `missing`, `incompatible`, `import_error`, `optional`, `not_checked`, or `unknown`.
- **Honest compatibility reporting** that separates installation, imports, CLI, offline contracts, and live runtime integration instead of blurring them into one green checkmark.
- **A machine-readable manifest** you can consume from CI without importing anything.

---

## The seven components

| Component | Distribution | Import | CLI | Layer | What it does |
|---|---|---|---|---|---|
| **AgentForge** | `agentforge-oss` | `forge` | `forge` | core | Async multi-agent orchestration with model routing, budget caps, RBAC, tool sandboxing, policy rules, pluggable memory, and a hash-chained audit trail. |
| **AgentPolicyPack** | `agentpolicypack` | `agent_policy_pack` | `agentpolicy` | core | Deterministic, fail-closed policy-as-code evaluation for agent actions, with bundle validation, embedded policy tests, simulation, and diffing. |
| **AIAuditLog** | `aiauditlog` | `ai_audit_log` | `aiaudit` | core | Vendor-neutral tamper-evident audit events: RFC 8785 canonical JSON, SHA-256 chains, checkpoints, optional Ed25519 signatures, privacy profiles. |
| **OpenOntologyLite** | `openontologylite` | `open_ontology_lite` | `openontology` | core | Executable semantic contracts for entities, relationships, actions, and permissions, plus AI System Maps for workload risk and routing expectations. |
| **AIMeter** | `aimeter-oss` | `ai_meter` | `aimeter` | core | Decimal-precise measurement of AI usage, cost, allocation, outcomes, and budgets, reported as cost per successful outcome. |
| **ModelSwapBench** | `modelswapbench` | `model_swap_bench` | `modelswapbench` | benchmarking | Repeatable model portability and replacement benchmarking with deterministic evaluators, vendor-exit reports, and per-task routing plans. |
| **PrivateAIStack** | `privateaistack` | `private_ai_stack` | `privateaistack` | local | Local-first FastAPI service and deployment template combining Ollama, Forge orchestration, PostgreSQL-backed RAG, governed static review, and JSONL audit. |

---

## Installation

```bash
pip install ai-infrastructure-suite
```

The default install is the **lightweight core**: AgentForge, AgentPolicyPack, AIAuditLog, OpenOntologyLite, and AIMeter. All five are pure Python; the only compiled wheels in the resolution are `pydantic-core` and `cryptography`.

### Optional extras

```bash
pip install "ai-infrastructure-suite[benchmarking]"    # + ModelSwapBench
pip install "ai-infrastructure-suite[local]"           # + PrivateAIStack
pip install "ai-infrastructure-suite[governance]"      # policy + audit + ontology
pip install "ai-infrastructure-suite[observability]"   # + OpenTelemetry export
pip install "ai-infrastructure-suite[full]"            # all seven
pip install "ai-infrastructure-suite[dev]"             # test and lint tooling
```

| Extra | Adds | Weight |
|---|---|---|
| *(default)* | The five core components | ~30 packages |
| `governance` | Nothing new — names the policy, audit, and ontology subset explicitly | none |
| `benchmarking` | `modelswapbench` | small (adds `jsonschema`) |
| `local` | `privateaistack` | **heavy** — pulls FastAPI, Uvicorn, websockets, watchfiles |
| `observability` | OpenTelemetry API, SDK, and OTLP gRPC exporter | **heavy** — pulls `grpcio`, `protobuf` |
| `full` | All seven components | ~55 packages |

`governance` is intentionally a no-op superset of the default install. It exists so that `pip install "ai-infrastructure-suite[governance]"` is a valid, documented, self-describing request.

### What is deliberately *not* installed

The suite never installs infrastructure a component merely *supports*:

- Anthropic, OpenAI, or Bedrock SDKs
- Ollama, Docker, or Docker Compose
- PostgreSQL, `pgvector`, or `psycopg`
- OpenTelemetry (unless you ask for `[observability]`)

Install those yourself, or use the component's own extras — for example `pip install "agentforge-oss[anthropic,pgvector]"`.

### Which components need external services

| Component | Needs at runtime | Required for basic use? |
|---|---|---|
| AgentForge | Nothing. Ships a deterministic offline provider. | No |
| AgentPolicyPack | Nothing. Fully offline. | No |
| AIAuditLog | Nothing. Fully offline. | No |
| OpenOntologyLite | Nothing. Fully offline. | No |
| AIMeter | Nothing. Fully offline. | No |
| ModelSwapBench | Ollama for local model runs; hosted SDKs for hosted runs. | No — the default deterministic provider needs neither. |
| PrivateAIStack | **Ollama, PostgreSQL/pgvector, Docker** for the packaged deployment. | Yes, for the service to do useful work. |

Installing `[local]` installs a Python package. It does not start a container, a database, or a model server, and this suite never will.

### Installing components individually

Nothing here is mandatory. Every component is independently installable:

```bash
pip install agentforge-oss
pip install agentpolicypack
pip install aiauditlog
pip install openontologylite
pip install aimeter-oss
pip install modelswapbench
pip install privateaistack
```

Use the metapackage when you want one coherent, version-checked environment. Use individual installs when you want exactly one tool.

---

## CLI

```bash
ai-suite info             # what this is, extras, next steps
ai-suite components       # the seven components and their installed state
ai-suite extras           # optional dependency groups
ai-suite version          # suite, Python, and platform versions
ai-suite doctor           # full diagnosis
ai-suite doctor --json    # machine-readable diagnosis
ai-suite doctor --no-imports
ai-suite compatibility    # layered compatibility report
ai-suite compatibility --json
ai-suite manifest         # emit the ecosystem manifest as JSON
```

`python -m ai_infrastructure_suite` works identically.

**Exit codes:** `0` all checks passed · `1` completed with findings · `2` the command could not complete.

### `ai-suite components`

```
┌──────────────────┬──────────────────┬────────────────────┬────────────────┬──────────────┬─────────────────┬───────────┬──────────┐
│ Component        │ Distribution     │ Import             │ CLI            │ Layer        │ Supported range │ Installed │ Status   │
├──────────────────┼──────────────────┼────────────────────┼────────────────┼──────────────┼─────────────────┼───────────┼──────────┤
│ AgentForge       │ agentforge-oss   │ forge              │ forge          │ core         │ >=0.5.3,<0.6.0  │ 0.5.3     │ ok       │
│ AgentPolicyPack  │ agentpolicypack  │ agent_policy_pack  │ agentpolicy    │ core         │ >=0.1.0a2,<0.2.0│ 0.1.0a2   │ ok       │
│ AIAuditLog       │ aiauditlog       │ ai_audit_log       │ aiaudit        │ core         │ >=0.1.0a4,<0.2.0│ 0.1.0a4   │ ok       │
│ AIMeter          │ aimeter-oss      │ ai_meter           │ aimeter        │ core         │ >=0.1.0a5,<0.2.0│ 0.1.0a5   │ ok       │
│ ModelSwapBench   │ modelswapbench   │ model_swap_bench   │ modelswapbench │ benchmarking │ >=0.1.0a6,<0.2.0│ -         │ optional │
│ OpenOntologyLite │ openontologylite │ open_ontology_lite │ openontology   │ core         │ >=0.1.0a4,<0.2.0│ 0.1.0a4   │ ok       │
│ PrivateAIStack   │ privateaistack   │ private_ai_stack   │ privateaistack │ local        │ >=0.1.0a3,<0.2.0│ -         │ optional │
└──────────────────┴──────────────────┴────────────────────┴────────────────┴──────────────┴─────────────────┴───────────┴──────────┘
```

### `ai-suite doctor`

Each component gets five independently classified checks — install, import, API, CLI, and a folded overall status:

```
┌──────────────────┬───────────┬──────────┬─────────────┬─────────────┬─────────────┬──────────┐
│ Component        │ Installed │ Install  │ Import      │ API         │ CLI         │ Status   │
├──────────────────┼───────────┼──────────┼─────────────┼─────────────┼─────────────┼──────────┤
│ AgentForge       │ 0.5.3     │ ok       │ ok          │ ok          │ ok          │ ok       │
│ AgentPolicyPack  │ 0.1.0a2   │ ok       │ ok          │ ok          │ ok          │ ok       │
│ ModelSwapBench   │ -         │ optional │ not_checked │ not_checked │ not_checked │ optional │
└──────────────────┴───────────┴──────────┴─────────────┴─────────────┴─────────────┴──────────┘
```

Findings carry a stable code and a suggested action:

```
┌────────────────┬─────────┬───────────────────────────────────────────┬──────────────────────────────────────────────────────┐
│ Component      │ Code    │ Finding                                   │ Suggested action                                     │
├────────────────┼─────────┼───────────────────────────────────────────┼──────────────────────────────────────────────────────┤
│ ModelSwapBench │ AIS1006 │ modelswapbench is not installed. It is    │ pip install "ai-infrastructure-suite[benchmarking]"  │
│                │         │ optional and ships in the benchmarking    │                                                      │
│                │         │ layer.                                    │                                                      │
│ PrivateAIStack │ AIS1007 │ privateaistack expects external services  │ These services are never installed, started, or      │
│                │         │ at runtime: Ollama..., PostgreSQL...      │ contacted by this suite.                             │
└────────────────┴─────────┴───────────────────────────────────────────┴──────────────────────────────────────────────────────┘
```

**Diagnostic codes:** `AIS1001` missing · `AIS1002` version incompatible · `AIS1003` import failed · `AIS1004` API symbols missing · `AIS1005` console script missing · `AIS1006` optional component absent · `AIS1007` heavy infrastructure expected · `AIS1008` version metadata mismatch · `AIS2001` Python unsupported · `AIS2002` import checks skipped · `AIS2003` manifest range unparseable.

### `ai-suite compatibility`

Five layers, reported separately, because they are not the same claim:

```
┌──────────────────┬─────────────┬────────┬──────────────────────────────────────────────────────────────┐
│ Layer            │ Status      │ Passed │ Detail                                                       │
├──────────────────┼─────────────┼────────┼──────────────────────────────────────────────────────────────┤
│ installation     │ ok          │ 7/7    │ 5 of 7 components installed; 7 of 7 satisfy their range or   │
│                  │             │        │ are optional and absent.                                     │
│ imports          │ ok          │ 5/5    │ 5 of 5 installed components import and expose their API.     │
│ cli              │ ok          │ 5/5    │ Entry-point metadata is read; no script is executed.         │
│ offline_contract │ ok          │ 6/6    │ Verified deterministically in memory, stdlib only.           │
│ live_runtime     │ not_checked │ -      │ Not tested. Requires Ollama, PostgreSQL, Docker, or hosted   │
│                  │             │        │ credentials, which this suite never starts or contacts.      │
└──────────────────┴─────────────┴────────┴──────────────────────────────────────────────────────────────┘
```

---

## Python API

```python
from ai_infrastructure_suite import (
    compatibility_report,
    ecosystem_components,
    inspect_installation,
)

# Static description of the ecosystem — imports nothing.
for component in ecosystem_components():
    print(component.id, component.distribution, component.version_specifier)

# Diagnose the current environment.
report = inspect_installation()
print(report.status.value, report.ok)
print(report.summary())  # {'ok': 5, 'optional': 2, 'missing': 0, ...}

for item in report.components:
    if not item.is_healthy:
        for finding in item.diagnostics:
            print(finding.code.value, finding.message, "->", finding.action)

# Skip importing third-party packages entirely.
restricted = inspect_installation(check_imports=False)

# Layered compatibility, including the offline contract check.
compat = compatibility_report()
print(compat.layer(CompatibilityLayer.OFFLINE_CONTRACT).detail)

# Everything is JSON-ready and deterministic.
import json

print(json.dumps(report.to_dict(), indent=2))
```

Also exported: `load_manifest`, `manifest_text`, `available_extras`, `component_ids`, `offline_contract_check`, `python_supported`, `canonical_json`, `digest_value`, and the typed result classes `Component`, `ComponentStatus`, `InstallationReport`, `CompatibilityReport`, `Diagnostic`, `CheckStatus`, `DiagnosticCode`.

---

## Compatibility: what is and is not proven

This is the part most metapackages get wrong. The suite reports **five separate layers** and never collapses them:

| Layer | What it proves | What it does not prove |
|---|---|---|
| `installation` | The right distributions are present at versions inside the supported range. | That they work. |
| `imports` | Each public package imports and exposes its expected API. | That any workflow succeeds. |
| `cli` | Each component registers its expected console script in entry-point metadata. | That the script runs correctly — nothing is executed. |
| `offline_contract` | This environment reproduces the portable conventions the ecosystem exchanges: canonical JSON, SHA-256 digests, hash chains, JSONL, exact decimal money. Standard library only, in memory. | That any component produced or consumed those files. |
| `live_runtime` | **Nothing. Always reported as `not_checked`.** | — |

**Live runtime integration across all seven components is not tested by this package and is not claimed.** Doing so would require Ollama, PostgreSQL, Docker, or hosted provider credentials. This suite starts none of them.

Worth knowing about the ecosystem itself, since the suite does not paper over it:

- Only **one real runtime dependency edge** exists between the seven: ModelSwapBench and PrivateAIStack both depend on `agentforge-oss`. ModelSwapBench uses Forge's provider layer only, not full orchestration.
- Every other connection is a **portable file or dictionary contract**, not a live import. The components are interoperable by convention, not by a shared schema package.
- Hash chains are **tamper-evident, not immutable**. Calculated cost is **not invoice-confirmed**. Projected savings are **not realized savings**. Policy decisions are **evaluated, not enforced** — the caller enforces them.

---

## How version ranges work

Every component is pinned to a **compatible range**, never an exact version:

```
agentforge-oss    >=0.5.3,<0.6.0
agentpolicypack   >=0.1.0a2,<0.2.0
aiauditlog        >=0.1.0a4,<0.2.0
openontologylite  >=0.1.0a4,<0.2.0
aimeter-oss       >=0.1.0a5,<0.2.0
modelswapbench    >=0.1.0a6,<0.2.0
privateaistack    >=0.1.0a3,<0.2.0
```

Three rules govern these:

1. **Lower bound = the latest version actually published**, verified against PyPI, not the latest version that exists in a source tree. A range that selects an unreleased version makes the metapackage uninstallable.
2. **Upper bound = the next minor**, so patch and minor releases flow through without a suite release. `<0.2.0` on an alpha permits `0.1.0a7`, `0.1.0`, and every `0.1.x`.
3. **Alpha lower bounds are deliberate.** A specifier containing a pre-release makes pip consider pre-releases for that requirement, so `pip install ai-infrastructure-suite` resolves the alphas without anyone passing `--pre`.

The manifest records `latest_published_version` alongside `minimum_version` so you can always tell what the range was calibrated against, and `version_sources_verified_on` records when. See [docs/release-policy.md](docs/release-policy.md).

---

## Security

The diagnostics are built to be safe to run and safe to paste into an issue:

- **No network calls.** Ever, in any command.
- **No shell execution**, no subprocess, no container or service startup.
- **No environment variables, credentials, or tokens** in any output.
- **No absolute paths, home directories, or hostnames** in any output — diagnostics pass through a redaction filter that scrubs Windows and POSIX paths, credential-bearing URLs, and secret-looking assignments.
- **Bounded output.** Third-party exception text is truncated and stripped of control characters, so nothing can drive your terminal.
- **`--no-imports`** for restricted environments.

**One honest tradeoff:** verifying that a component imports means running `import`, which executes that package's import-time code. That is the only way to distinguish "installed" from "actually usable". Pass `--no-imports` to restrict checks to installed metadata. See [docs/security.md](docs/security.md).

---

## Documentation

- [docs/installation.md](docs/installation.md) — install paths and what each pulls in
- [docs/extras.md](docs/extras.md) — the dependency layers and why they are split this way
- [docs/components.md](docs/components.md) — the seven components in detail
- [docs/doctor.md](docs/doctor.md) — every status, code, and JSON field
- [docs/compatibility.md](docs/compatibility.md) — the five layers and their limits
- [docs/architecture.md](docs/architecture.md) — how the suite is built and why
- [docs/release-policy.md](docs/release-policy.md) — version ranges and release process
- [docs/security.md](docs/security.md) — threat model and output guarantees

---

## Contributing

Contributions are welcome. Useful ones:

- Version-range corrections when a component publishes a new release
- Additional diagnostic checks that stay offline, bounded, and deterministic
- Documentation improvements
- Tests across Python 3.11–3.13

Keep the package **typed** (`mypy --strict`), **offline**, and **free of the seven projects' business logic**. If a change would make the suite reimplement something a component already does, it belongs in that component instead.

See [CONTRIBUTING.md](CONTRIBUTING.md).

---

## License

[Apache License 2.0](LICENSE).

---

## Independence notice

Linux of AI and AI Infrastructure Suite are independent open-source projects. They are **not affiliated with, endorsed by, or sponsored by the Linux Foundation**, and use no Linux Foundation branding or artwork. The name describes an intent — open, portable, inspectable infrastructure — not an affiliation. This package is not an operating system and is not a replacement for the Linux kernel.

Maintained by `sekacorn`.
