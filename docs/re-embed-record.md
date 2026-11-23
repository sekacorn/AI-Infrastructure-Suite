# Reading a `re-embed-record.v1`

A re-embed record says one embedding store was rebuilt with a different model.
The suite only reads it. It never embeds text, loads weights, opens a socket or
imports a provider SDK, so reproducing a rebuild is always the job of the tool
that produced it — today, PrivateAIStack's `privateaistack reembed` command.

```python
from ai_infrastructure_suite.re_embed_record import load_re_embed_record

record = load_re_embed_record(path)
record.label(rebuild_reproduced=False)  # 'attested'
record.attested_claims()
```

## The quality block

`quality` is absent when no comparison was attempted. That absence is not
`not_measured`: `not_measured` means a comparison was set up and found
insufficient, and silence means nothing was tried.

| Status | Means | Can move 50 → 25 |
|---|---|---|
| `sampled` | The comparison ran and every declared threshold was met. | Yes, with the other four conditions |
| `below_threshold` | The comparison ran and at least one threshold was not met. | No |
| `unjudged` | The comparison ran and produced metrics, and no agreement floor was declared, so there is no verdict. | No |
| `not_measured` | No query set, none usable, or too few queries for sufficient evidence. | No |

`Quality.judged` is true only for `sampled` and `below_threshold`.

### `unjudged` is metrics without a verdict

An operator who declares no agreement floor still gets their numbers. What
they do not get is a pass, because nothing was held against anything. Reading
`unjudged` as approval would be reading silence as consent, so the reader
enforces that such a record carries the metrics it measured, contains no failed
check, declares no insufficiency, and **says in its notes what to declare to
get a verdict**. Without that last part the record reads as a dead end rather
than as a missing declaration the operator can supply.

A `quality` block is evidence that a stated comparison ran and what it
returned. It is never evidence that retrieval is good.

### Fields added for the 3b-4 comparison

These are additive and optional within `re-embed-record.v1`. A record written
before them still parses; a record written with them needs
**ai-infrastructure-suite 0.1.0b3 or later**, because an older reader refuses
any field it does not know. The version stays `v1` because nothing that was
valid became invalid.

| Field | Says |
|---|---|
| `mode` | `record_anchored`: a retained record's own stored vector was the query in each store, so neither model was needed at query time. `text`: free-text queries embedded by each store's own model. |
| `metric_basis` | `neighborhood_agreement` or `retrieval_relevance`. Record-anchored is always the former, and the reader refuses a record that claims otherwise. |
| `anchor_selection` | `seeded_random` or `operator`. |
| `anchor_seed` | The seed the draw used. Required for `seeded_random`, refused otherwise. |
| `similarity` | The similarity function the ranking used. |
| `thresholds` | The bar each check was judged against, by name. |
| `candidate_pool_size` | How many records each query was ranked against. `top_k_overlap` is a share of this pool, not of the whole store. |
| `tie_break` | How equal similarities were ordered. |
| `boundary_ties` | Queries whose top-k boundary fell inside a run of equal similarities. |
| `queries_skipped` | Queries declared but not evaluable. |
| `correlation_undefined` | Rank correlation had no defined value for at least one query. |
| `top_k_capped` | A store held fewer records than the requested `top_k`. |

### Neighborhood agreement is not search quality

In `record_anchored` mode the metrics say whether the two stores put the same
records near each other. They say nothing about whether a person searching
either store finds what they wanted, and nothing built on this record may
describe them that way. `Quality.measures_user_search_quality` is false for
every record-anchored result, and is there to be checked rather than assumed.

### Who chose the queries decides whether the score may move

`improves_score` needs five conditions, not four:

1. the caller reproduced the rebuild itself,
2. `quality.status` is `sampled`,
3. the target model is `open_weight_self_hostable`,
4. no retained record failed to rebuild, and
5. the query set was drawn by seed, with that seed in the record.

An operator-chosen set reports with its full label, its metrics and its checks,
and moves nothing. An operator who may pick the queries may pick the result,
and no offline check can tell a fair hand-picked set from a flattering one. A
record written before `anchor_selection` existed carries no claim about how its
queries were chosen, so it moves no score either; silence is read
conservatively.

### Thresholds are attested, and must be shown

A run that "passed" passed the bar its own operator set, which may be any bar
at all. `attested_claims()` therefore lists the declared thresholds alongside
model availability and query-set representativeness, and a report that prints
the verdict must print the bar beside it. `Quality.threshold_summary()` renders
them as one line for that purpose.

The reader refuses a `sampled` or `below_threshold` record whose declared
thresholds do not name exactly the checks that were run, and refuses a
`not_measured` record that carries thresholds at all.
