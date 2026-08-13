# Installation

## Requirements

- Python 3.11, 3.12, or 3.13
- pip

Python 3.14 is not supported yet: four of the seven components declare `requires-python = ">=3.11,<3.14"`, so the intersection across the ecosystem is `>=3.11,<3.14`. The suite declares the same range rather than promising something its dependencies cannot deliver.

## Default install

```bash
pip install ai-infrastructure-suite
```

This installs the **five core components**:

| Distribution | Import | Why it is in the default |
|---|---|---|
| `agentforge-oss` | `forge` | Orchestration core; two other components depend on it |
| `agentpolicypack` | `agent_policy_pack` | Pure-Python policy evaluation |
| `aiauditlog` | `ai_audit_log` | Audit envelope; only extra weight is the `cryptography` wheel |
| `openontologylite` | `open_ontology_lite` | Pure-Python semantic contracts |
| `aimeter-oss` | `ai_meter` | Depends on PyYAML alone |

Total resolution: roughly 30 packages. The only compiled wheels are `pydantic-core` and `cryptography`, both of which publish wheels for every supported platform and Python version.

Verify:

```bash
ai-suite doctor
```

## Adding components

```bash
pip install "ai-infrastructure-suite[benchmarking]"    # ModelSwapBench
pip install "ai-infrastructure-suite[local]"           # PrivateAIStack
pip install "ai-infrastructure-suite[observability]"   # OpenTelemetry export
pip install "ai-infrastructure-suite[full]"            # all seven
```

Extras combine:

```bash
pip install "ai-infrastructure-suite[benchmarking,observability]"
```

See [extras.md](extras.md) for what each group pulls in and why the split is drawn where it is.

## Pre-release resolution

Six of the seven components are published as pre-releases (beta). Under [PEP 440](https://peps.python.org/pep-0440/), pip normally ignores pre-releases — but a requirement whose specifier *contains* a pre-release makes pip consider pre-releases for that requirement.

Every component is pinned with a pre-release-aware range. The current Beta components use a beta lower bound and a next-minor upper bound above the whole line (`>=0.2.0b1,<0.3.0`), so:

```bash
pip install ai-infrastructure-suite      # resolves the Beta components correctly
```

**No `--pre` flag is needed**, and you are not opting into pre-releases for unrelated packages in your environment.

## Installing components individually

The suite is a convenience, not a requirement. Every component installs and works on its own:

```bash
pip install agentforge-oss
pip install agentpolicypack
pip install aiauditlog
pip install openontologylite
pip install aimeter-oss
pip install modelswapbench
pip install privateaistack
```

Use individual installs when you want exactly one tool and no opinion about the others. Use the metapackage when you want a version-checked environment and a diagnostic that explains it.

You can also mix: install the suite for the core, then add a component's own extras directly.

```bash
pip install ai-infrastructure-suite
pip install "agentforge-oss[anthropic]"     # add the Claude provider
```

## What is never installed

The suite installs Python distributions. It does not install, configure, start, or contact:

- Ollama or any model server
- Docker or Docker Compose
- PostgreSQL, `pgvector`, or any database
- Anthropic, OpenAI, or AWS Bedrock SDKs
- An OpenTelemetry collector or Jaeger

Components that *support* these expose them through their own extras. Request them explicitly:

```bash
pip install "agentforge-oss[anthropic,openai,bedrock,pgvector,otel]"
pip install "modelswapbench[openai,anthropic]"
pip install "privateaistack[postgres,observability]"
```

## Isolated environments

The suite pulls a real dependency tree. Use a virtual environment:

```bash
python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install ai-infrastructure-suite
```

For CLI-only use, `pipx` keeps it off your project environment entirely:

```bash
pipx install ai-infrastructure-suite
ai-suite doctor
```

## Verifying an install

```bash
ai-suite version           # suite, Python, platform
ai-suite components        # what is installed and at what version
ai-suite doctor            # full diagnosis with suggested actions
ai-suite doctor --json     # same, machine-readable
ai-suite compatibility     # layered compatibility report
```

In a restricted environment where importing third-party code is not acceptable:

```bash
ai-suite doctor --no-imports
```

See [doctor.md](doctor.md) for every status and diagnostic code.

## Upgrading

```bash
pip install --upgrade ai-infrastructure-suite
```

Upper bounds are set at the next minor (`<0.6.0`, `<0.2.0`), so component patch and minor releases flow through without waiting for a suite release. `ai-suite doctor` reports when an installed version falls outside its supported range. See [release-policy.md](release-policy.md).

## Uninstalling

Removing the metapackage leaves the components installed — that is how pip works, and it is deliberate: the components are independently useful.

```bash
pip uninstall ai-infrastructure-suite
```

To remove the components too, uninstall them by name.
