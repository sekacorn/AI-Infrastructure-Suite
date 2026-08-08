# Optional dependency groups

## The layering rule

One question decides whether a component belongs in the default install:

> Does installing it drag in heavyweight dependencies or imply external infrastructure?

If no, it is core. If yes, it goes behind an extra. Nothing is in the default install merely because it is part of the ecosystem.

This matters concretely. Measured resolutions on Python 3.13:

| Install | Packages resolved | Adds |
|---|---|---|
| Default | 30 | — |
| `[full]` | 55 | fastapi, uvicorn, websockets, watchfiles, httptools, grpcio, protobuf, opentelemetry-* |

Putting all seven in the default install would have made `pip install ai-infrastructure-suite` pull `grpcio` and a web server for someone who only wanted to validate a policy bundle.

## The groups

### Default (no extra)

```bash
pip install ai-infrastructure-suite
```

`agentforge-oss` · `agentpolicypack` · `aiauditlog` · `openontologylite` · `aimeter-oss`

Orchestration, governance, audit, ontology, and metering. All five run fully offline with no external services. Their combined third-party surface is pydantic, httpx, typer, rich, PyYAML, cryptography, rfc8785, and packaging.

### `governance`

```bash
pip install "ai-infrastructure-suite[governance]"
```

`agentpolicypack` · `aiauditlog` · `openontologylite`

**This adds nothing to a default install.** All three are already core. The extra exists so that the intent is expressible and documented — a `requirements.txt` line that says `ai-infrastructure-suite[governance]` states *why* the dependency is there, and stays correct if the layering ever changes.

### `benchmarking`

```bash
pip install "ai-infrastructure-suite[benchmarking]"
```

`modelswapbench`

Adds `jsonschema` and its transitive dependencies. Light, but specialised: most users installing the ecosystem are not running model-replacement benchmarks, so it is opt-in.

ModelSwapBench declares `agentforge-oss>=0.5.1,<0.6.0`, compatible with the core pin, so it never forces a different Forge version.

Its default provider is deterministic and offline. Ollama and hosted-provider SDKs are needed only for real model runs and are not installed here.

### `local`

```bash
pip install "ai-infrastructure-suite[local]"
```

`privateaistack`

**Heavyweight.** Pulls FastAPI, Uvicorn with its `[standard]` extra, `websockets`, `watchfiles`, `httptools`, and `python-dotenv`.

Also the only component that genuinely expects external infrastructure at runtime: **Ollama**, **PostgreSQL with pgvector**, and **Docker** for the packaged deployment template. Installing this extra installs a Python package and nothing else — no container starts, no database is created, no model is pulled.

Its PostgreSQL and observability support are its own extras:

```bash
pip install "privateaistack[postgres,observability]"
```

### `observability`

```bash
pip install "ai-infrastructure-suite[observability]"
```

`aimeter-oss[otel]` · `agentforge-oss[otel]`

**Heavyweight.** The OTLP gRPC exporter pulls `grpcio` and `protobuf`, both large compiled wheels.

The lighter half — `aimeter-oss[otel]` — is only `opentelemetry-api`. Both are included because an observability extra that cannot actually export a span is not an observability extra. If you want the metering fields without the export chain, install `aimeter-oss[otel]` directly.

This is also why OpenTelemetry is *not* in the default install: several components support it, but supporting a thing is not a reason to make everyone carry it.

### `full`

```bash
pip install "ai-infrastructure-suite[full]"
```

All seven components at their supported ranges. Does **not** include `[observability]` — telemetry export is an orthogonal choice, not a consequence of wanting every component. Combine if you want both:

```bash
pip install "ai-infrastructure-suite[full,observability]"
```

### `dev`

```bash
pip install "ai-infrastructure-suite[dev]"
```

`pytest` · `pytest-cov` · `ruff` · `mypy` · `bandit` · `pip-audit` · `build` · `twine`

For working on the suite itself. Installs no ecosystem component beyond the default.

## What is deliberately excluded

The suite never installs, by default or through any extra:

| Excluded | Where to get it |
|---|---|
| Anthropic SDK | `pip install "agentforge-oss[anthropic]"` |
| OpenAI SDK | `pip install "agentforge-oss[openai]"` |
| AWS Bedrock (`boto3`) | `pip install "agentforge-oss[bedrock]"` |
| PostgreSQL / pgvector | `pip install "agentforge-oss[pgvector]"` |
| Ollama | Install the Ollama runtime yourself |
| Docker | Install Docker yourself |

A component *supporting* a provider is not a reason to install that provider's SDK for everyone.

## Checking what you have

```bash
ai-suite extras       # the groups and what each installs
ai-suite components   # what is actually installed right now
```

Absent optional components are reported as `optional`, not `missing`, and never cause a non-zero exit:

```
ModelSwapBench   -   optional   AIS1006   pip install "ai-infrastructure-suite[benchmarking]"
```
