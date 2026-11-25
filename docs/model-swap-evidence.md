# Reading a `model-swap-evidence.v1`

A swap-evidence document reports one benchmark comparison: a baseline model
against one candidate, the counts and rates measured, the thresholds the run
was judged against, and the producer's own verdict. ModelSwapBench writes it
with `modelswapbench swap-evidence`. It is the evidence that a **tested
alternative** exists for a model provider, for the coming `model_providers`
lock-in vector. This release reads, checks and assesses it; nothing feeds it
into an exit report yet, and no score changes.

```python
from ai_infrastructure_suite.model_swap_evidence import (
    assess,
    bind_to_inventory,
    load_model_swap_evidence,
)

evidence = load_model_swap_evidence(path, expected_sha256=digest)
provider_id, model = bind_to_inventory(evidence, inventory)
assessment = assess(evidence)
assessment.accepted, assessment.candidate_hosting, assessment.reasons
```

## The schema here is canonical

The JSON Schema ships as
`ai_infrastructure_suite/data/model-swap-evidence.schema.json` and is the
canonical definition of the format. A producer may ship its own schema, which
may be narrower, never wider: a document a producer emits that this schema or
this reader refuses is a bug in the producer.

Every object is closed. Rules a JSON Schema cannot express are enforced by the
reader, and the tests pin which rules are which.

Examples, the ten variants ModelSwapBench's writer emits, are in
`docs/examples/model-swap-evidence/`.

## What the reader checks

- **Shape and bounds.** At most 256 KiB, read through the bounded reader;
  duplicate keys, NaN and infinity refused; strings at most 256 characters; at
  most 16 constraints; rates between 0 and 1.
- **Consistency.** Outcome counts never exceed the sample count;
  `quality_score` is null exactly when no case executed; sufficient evidence is
  never claimed below the document's own stated minimum; a fixture provider
  states no endpoint; the two sides are different models.
- **The verdict.** The reader recomputes the decision from the numbers and
  **refuses** a document whose stated eligibility, recommendation, policy
  result or deployment compatibility disagrees. The gates are: quality drop,
  success-rate drop, minimum reliability, p95 latency, a measured policy
  violation, a hosted candidate where hosted is not permitted, and any failed
  constraint. Cost is not a gate.
- **Hosting.** Derived from each `endpoint_origin` with the same no-DNS rule
  used for a provider inventory. A declaration may only add hosted-ness. A real
  provider that states no endpoint is hosted. A candidate declared local or
  self-hosted at a public host is hosted, and `declaration_contradicted` says
  so.

## What the suite concludes: `assess`

The producer's verdict is checked, then the suite applies its own, stricter
rule. A candidate is **accepted** only when:

- it passed every gate;
- the producer marked the evidence sufficient;
- the sample reaches both the document's own recommended minimum and the
  suite's floor of 20;
- at least one case executed, so there is a quality score;
- neither side is a test fixture: the `deterministic` provider, or any model
  declared `test`. A fake candidate is reported (`candidate_hosting: fake`) and
  never accepted.

Cost plays no part: a more expensive alternative still shows the workload can
move. Each reason a candidate was not accepted is a fixed phrase, never a
value from the document.

The assessment also says whether tool calling was exercised (a
`tool_accuracy` was reported) and that streaming was not: the benchmark does
not exercise it.

## Binding to a provider inventory

`bind_to_inventory` names the one inventory provider and model the baseline
tested: same provider kind, same endpoint origin, and a model the provider
lists. `forge` binds to an `ollama` provider, because that is what it reaches.
Evidence that fits no provider, or more than one, is **refused, not skipped**.

## What stays attested

Everything measured. The reader can show that the numbers are consistent and
that the verdict follows from them; it cannot show that any model was run or
that any number was measured. The producer string is a label, not proof.

## Agreement with the producer

The suite never imports ModelSwapBench. Two CI checks prove the two decision
rules agree:

- **Here,** the `producer-agreement` workflow installs ModelSwapBench from its
  main branch and feeds a grid of more than a thousand real documents through
  this reader. Every one must validate against this schema and parse without a
  verdict disagreement.
- **There,** ModelSwapBench's `suite-contract` job installs this package and
  validates every variant it emits against this schema and reader.
