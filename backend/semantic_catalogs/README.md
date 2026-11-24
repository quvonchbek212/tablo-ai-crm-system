# Semantic catalogs

Each connected database gets its own catalog file:

`<database-name>-<schema-fingerprint>.json`

Use `GET /api/semantic-catalog` to obtain a schema-derived draft. Review the
business meaning, mark only verified tables/metrics/relationships as certified,
set `reviewed` to `true`, then submit it with `PUT /api/semantic-catalog`.

Catalogs contain structured metadata only. They never contain executable SQL
and never write to the connected source database.

Important review rules:

- define the exact row grain for every certified fact or snapshot;
- certify a metric only after its aggregation, column, filters, and unit are verified;
- approve a relationship only when its business meaning and cardinality are known;
- use `join_paths` when a certified metric must traverse reviewed FK dimensions;
- use `trigger_phrases` plus `prompt_filters` for reviewed prompt parameters such
  as region names; values are allow-listed and typo-matched, never copied from
  user text directly into SQL;
- use `group_by_phrases` on a prompt filter for reviewed requests such as
  "har bir viloyat" so the same certified formula is grouped by that dimension;
- regenerate and re-review the catalog when the schema fingerprint changes.

