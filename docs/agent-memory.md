# Reading an `agent-memory.v1`

An agent-memory document is a portable export of an agent's memory: retrieval
documents or a conversation transcript, tombstones for items deleted under
policy, and provenance for items that arrived by import. It is the evidence the
`memory` vector will score; this release only reads and validates it.

```python
from ai_infrastructure_suite.agent_memory import load_agent_memory

export = load_agent_memory(path, expected_sha256=digest)
for store in export.stores:
    print(store.store_id, store.kind, store.active, store.quarantined, store.deleted_by_policy)
```

The JSON Schema ships as `ai_infrastructure_suite/data/agent-memory.schema.json`.
Examples are in `docs/examples/agent-memory/`.

## Shape

- `export` names the export, when it ran, the redaction profile applied (or
  `null`), and the exporting system and backend kind. **Never** a DSN, file
  path, URL or credential: the schema has no field for one.
- Each store is `retrieval` (items of kind `document`) or `conversation`
  (items of kind `message`, numbered 0, 1, 2, ... by `sequence`).
- Items are inline (`single_file`) or in JSONL shards (`sharded_jsonl`), one
  item per line, each shard with its own SHA-256 and `record_count`.
- Every item carries `content_sha256`, the SHA-256 of its text, and
  `created_at`, which is `null` when the source never recorded it.
- `status` is `active` or `quarantined`. A quarantined item came from an
  import and must carry `provenance`: source system, backend and store, the
  source export's ID and digest, the original item ID, the import time and the
  importer.

## Vectors are not stored here

A store with embeddings references an
[`embedding-portability.v1`](exit-report.md#embedding-inventories) document by
path and SHA-256. Each item's `item_id` is a `record_id` there, and the
inventory's recorded source-text digest equals the item's `content_sha256`, so
every vector is bound to the exact memory text it came from. One vector format,
reused. This reader parses the reference; following it is part of scoring.

## Deletion

An item deleted under policy leaves only a **tombstone**: its `item_id` and
`deleted_at`, nothing else. Not its text, metadata, digest or vector: even a
digest would let someone confirm a guessed value. A tombstoned ID never
appears as an item, and the declared `deleted_by_policy` count must equal the
tombstones present. That a deletion really happened in the source system is
the operator's statement; this format cannot prove it.

## What the reader checks itself

- the document's digest, when the manifest declares one;
- every shard's digest over its exact bytes before any line is trusted; **one
  failed shard invalidates the whole store**;
- every item's text against its `content_sha256`;
- unique item IDs, retrieval items sorted by `item_id`, conversation sequences
  contiguous from 0;
- declared counts against the items and tombstones present;
- tombstones carry nothing but an ID and a time, and match no live item;
- quarantined items record provenance;
- the bounds below.

Error messages never repeat a value from the document: memory holds user data.

## Bounds

| Limit | Value |
|---|---|
| Document | 4 MiB |
| Shard / shard line | 16 MiB / 64 KiB |
| Item text | 48 KiB (UTF-8); chunk longer text before export |
| Stores / shards per store / items per store / tombstones per store | 64 / 1024 / 100,000 / 100,000 |
| Metadata | depth 8; 64 keys per object; 256 items per array; 4096 characters per string |
| Tool calls or results per message | 64 |

Duplicate JSON keys, NaN and Infinity, invalid UTF-8, symbolic links,
non-regular files, absolute paths and `..` segments are refused.

## Schema and reader

Every object in the schema is closed. The rules a JSON Schema cannot express
are the reader's alone: digests, ordering, uniqueness, counts, the
tombstone-to-item check, impossible dates, and metadata depth and string
lengths. The test suite pins this division: every negative case is refused by
the reader, and each records whether the schema rejects it too.
