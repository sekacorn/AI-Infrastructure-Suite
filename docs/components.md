# The seven components

Every fact here comes from the manifest shipped inside the package. Read it programmatically instead of copying from this page:

```bash
ai-suite manifest > ecosystem.json
```

```python
from ai_infrastructure_suite import ecosystem_components

for component in ecosystem_components():
    print(component.id, component.distribution, component.version_specifier)
```

## Summary

| Component | Distribution | Import | CLI | Layer | Default |
|---|---|---|---|---|---|
| AgentForge | `agentforge-oss` | `forge` | `forge` | core | yes |
| AgentPolicyPack | `agentpolicypack` | `agent_policy_pack` | `agentpolicy` | core | yes |
| AIAuditLog | `aiauditlog` | `ai_audit_log` | `aiaudit` | core | yes |
| AIMeter | `aimeter-oss` | `ai_meter` | `aimeter` | core | yes |
| OpenOntologyLite | `openontologylite` | `open_ontology_lite` | `openontology` | core | yes |
| ModelSwapBench | `modelswapbench` | `model_swap_bench` | `modelswapbench` | benchmarking | no |
| PrivateAIStack | `privateaistack` | `private_ai_stack` | `privateaistack` | local | no |

Note that distribution names and import names differ for most components — `pip install agentforge-oss` then `import forge`. The manifest records both so diagnostics can check each independently.

---

## AgentForge

**`agentforge-oss`** · imports as `forge` · CLI `forge` · range `>=0.5.4,<0.6.0`

Async-first multi-agent orchestration. A supervisor decomposes a goal, spawns worker agents that run concurrently in bounded batches, and synthesises the results. Model routing picks between registered models by `cost_optimized`, `quality_first`, `balanced`, or `fixed` strategy. Budgets are enforced twice: a pessimistic pre-flight estimate before a worker batch starts, and a precise check after every model call.

Also ships tool sandboxing with dangerous tools denied by default, RBAC, prompt-injection heuristics, three memory backends, a 25-event lifecycle bus, and a SHA-256 hash-chained audit log.

**Offline:** yes. A deterministic echo provider makes the whole platform usable with zero configuration and no API key.

**Integration status:** `dependency-target`. Both ModelSwapBench and PrivateAIStack depend on this distribution at runtime — the only real runtime dependency edges in the ecosystem.

**Optional infrastructure:** Anthropic, OpenAI, and Bedrock SDKs; PostgreSQL with pgvector; an OpenTelemetry collector. All are that package's own extras and none are installed by this suite.

---

## AgentPolicyPack

**`agentpolicypack`** · imports as `agent_policy_pack` · CLI `agentpolicy` · range `>=0.2.0b1,<0.3.0`

Policy-as-code for agent actions. Bundles are YAML or JSON, loaded with duplicate-key-rejecting parsers into frozen Pydantic models that refuse unknown fields. Evaluation is deterministic: target matching, then bounded structured conditions, then conflict resolution (`deny_overrides` by default), then obligation and most-restrictive-limit aggregation.

Invalid bundles fail closed — `indeterminate` with a deny-equivalent outcome. Every decision carries stable bundle and request digests plus evidence for each policy considered.

**Offline:** yes. No `eval`, no `exec`, no module imports named by policy files, no remote URLs.

**Integration status:** `portable-contract`.

**Important boundary:** it *evaluates* decisions. Enforcement is the caller's responsibility. A returned `deny` blocks nothing on its own.

---

## AIAuditLog

**`aiauditlog`** · imports as `ai_audit_log` · CLI `aiaudit` · range `>=0.2.0b1,<0.3.0`

A vendor-neutral typed audit-event envelope plus local recording, verification, privacy, and signing tools. Canonical bytes use RFC 8785 after normalising decimals, dates, and model objects. Events are SHA-256 digested and optionally hash-chained; checkpoints bind stream identity, event count, and terminal digest, and can carry Ed25519 signatures.

Privacy profiles default to `minimal`: identifiers, hashes, counts, and outcomes are kept while prompts, responses, and common secret keys are redacted.

**Offline:** yes. Adds the `cryptography` wheel.

**Integration status:** `portable-contract`. Its sibling helpers accept plain dictionaries and import no other component.

**Important boundaries:** hash chaining is **tamper-evident, not immutable** — someone who can rewrite an entire unsigned log can recompute the chain. Signatures prove key possession and byte integrity, not real-world identity, legal non-repudiation, or compliance.

---

## AIMeter

**`aimeter-oss`** · imports as `ai_meter` · CLI `aimeter` · range `>=0.2.0b1,<0.3.0`

Measurement and accounting for AI usage, provider and infrastructure cost, allocation, outcomes, budgets, and reconciliation. All money is `Decimal` with stable six-place strings. Pricing tables resolve by provider, model, region, currency, and effective date, and report exact, fallback, ambiguous, missing, stale, or expired status.

The headline metric is cost per *successful outcome*, not cost per call, because a successful API response is not a successful business result.

**Offline:** yes. PyYAML is its only required runtime dependency.

**Integration status:** `portable-contract`.

**Important boundaries:** missing pricing is reported as unknown and **never treated as zero**. Calculated cost is not invoice-confirmed. Projected savings are not realized savings. Budgets are *evaluated*; AIMeter enforces nothing at an external runtime.

Previously published as `openaimeter`; `aimeter-oss` / `ai_meter` is current, with deprecated compatibility shims.

---

## OpenOntologyLite

**`openontologylite`** · imports as `open_ontology_lite` · CLI `openontology` · range `>=0.2.0b2,<0.3.0`

Portable semantic contracts: typed entities and properties, relationships with cardinality, action contracts with inputs and preconditions, and permissions with risk. Loading is local-only and defensive — safe YAML, duplicate-key rejection, 2 MB file cap, 40 nesting levels, 100,000 node ceiling, and diagnostics that omit raw input values.

Also defines **AI System Maps**: a declarative record of workload purpose, task risk levels, allowed model routes (`candidate_model`, `baseline_model`, `human_review`, `blocked_or_escalate`), review and escalation requirements, and expected audit events and cost metrics.

**Offline:** yes.

**Integration status:** `portable-contract`. Its `contracts/` module is the ecosystem's most formalised handoff layer — frozen contract models carrying an `OntologyIdentity` provenance stamp.

**Important boundary:** "executable" means deterministic validation and transformation. It executes no action, enforces no authorization, routes no model, and performs no inference. An AI System Map documents intended controls; it does not implement them.

---

## ModelSwapBench

**`modelswapbench`** · imports as `model_swap_bench` · CLI `modelswapbench` · range `>=0.1.0b1,<0.2.0` · extra `[benchmarking]`

Repeatable model portability and replacement benchmarking. Suites are strict YAML or JSON defining model candidates, cases, evaluators, constraints, and replacement rules. Evaluators cover exact and substring matching, regex, JSON parsing and schema, field match, tool selection, policy expectations, citations, latency, cost, and a keyword rubric.

Produces replacement recommendations gated on quality drop, success-rate drop, reliability, latency, policy pass rate, and cost reduction — plus **AI Vendor Exit Reports** and per-task **Model Routing Plans** that classify each task as candidate, baseline, human review, or blocked.

**Offline:** yes. The default deterministic provider needs no credentials and downloads no model. Hosted providers are denied unless explicitly enabled.

**Integration status:** `runtime-dependency` — it imports `agentforge-oss`. Note that its Forge adapter exercises **the provider layer only**, not supervisor, worker, memory, or tool orchestration.

**Optional infrastructure:** Ollama for local model runs; OpenAI, Anthropic, or Bedrock SDKs for hosted runs.

**Important boundary:** cost figures are estimates from editable local pricing metadata, not invoices.

---

## PrivateAIStack

**`privateaistack`** · imports as `private_ai_stack` · CLI `privateaistack` · range `>=0.2.0b1,<0.3.0` · extra `[local]`

A local-first FastAPI service and Docker Compose deployment template combining Ollama, Forge orchestration, PostgreSQL-backed document memory, governed static code review, JSONL audit records, and optional OpenTelemetry.

Its review mode is `safe-static`: it collects repository files, excludes VCS, `.env`, SSH material, private keys, binaries, and symlinks, then runs whichever of Ruff, mypy, Bandit, pip-audit, Radon, detect-secrets, yamllint, markdownlint, ShellCheck, and Hadolint are installed — without `shell=True` — and writes Markdown, JSON, and SARIF reports. `sandboxed-execution` mode is deliberately denied.

**Offline:** the package is; the service needs its infrastructure.

**Integration status:** `runtime-dependency` — it builds a real Forge `Orchestrator` pinned to a local Ollama provider, with a direct-Ollama fallback.

**Heavy infrastructure — required for the service to do real work:**
- **Ollama** for local model inference
- **PostgreSQL with pgvector** for persistent document memory
- **Docker and Docker Compose** for the packaged deployment

Installing `[local]` installs a Python package. It starts none of the above, and neither does `ai-suite doctor`.

**Important boundaries:** task and review registries are in-process and do not survive a restart. Its current RAG search is a full scan over deterministic hash embeddings, not an ANN index. Packaged defaults are development values requiring hardening before operational use. The Python package exposes only `__version__` — so the suite checks that it imports, and checks no further API surface.

---

## How the components actually relate

Worth stating plainly, because ecosystem diagrams tend to imply more than exists:

- **One real runtime dependency exists**, on `agentforge-oss`, used by ModelSwapBench and PrivateAIStack.
- **Every other connection is a portable file or dictionary contract.** The components interoperate by convention — canonical JSON, hash-chained JSONL, decimal money strings, shared vocabulary for routes and risk levels — not through a shared schema package.
- **No component imports another** except via that one edge.

That is a deliberate design choice: it keeps each project independently installable and independently replaceable. It also means integration boundaries should be validated explicitly in production rather than assumed. The suite's `offline_contract` compatibility layer verifies that *this environment* reproduces those shared conventions; it does not verify that any component produced or consumed them. See [compatibility.md](compatibility.md).
