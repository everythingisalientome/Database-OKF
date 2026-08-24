---
type: table
name: SALES.invoice_line
description: Invoice line items: track sold, price, quantity
description_confirmed: false
database: MUSICSTORE_SALES
engine: teradata
row_count: 2240
row_count_source: live
crawl_date: 2026-08-23
flags: [pi:invoice_id]
---

- [inferred:high] Purpose: Invoice detail lines. Each line references the sold track (track_id) and its price/quantity. NOTE: track data lives in the CORE database - cross-SOR reference.

## Columns

### invoice_line_id
- [observed] type: INT, not null
- [observed] distinct_count: 2240; distinct_ratio: 1.0; null_rate: 0.0
- [observed] format: all-digits; range: [1 .. 2240]
- [observed] dense_sequence: true  # contiguous surrogate range - value overlap non-distinctive
- [observed] fingerprint: sha256-set @ fingerprints/invoice_line.invoice_line_id.json
- [observed] normalization: [none]
- [inferred:high] Unique identifier; candidate key of invoice_line.

### invoice_id
- [observed] type: INT, not null
- [observed] distinct_count: 412; distinct_ratio: 0.1839; null_rate: 0.0
- [observed] format: all-digits; range: [1 .. 412]
- [observed] dense_sequence: true  # contiguous surrogate range - value overlap non-distinctive
- [observed] index: non-unique
- [observed] index: PRIMARY INDEX (Teradata PI)
- [observed] fingerprint: sha256-set @ fingerprints/invoice_line.invoice_id.json
- [observed] normalization: [none]
- [inferred:medium] Reference identifier (invoice).

### track_id
- [observed] type: INT, not null
- [observed] distinct_count: 1984; distinct_ratio: 0.8857; null_rate: 0.0
- [observed] format: all-digits; range: [1 .. 3500]
- [observed] index: non-unique
- [observed] fingerprint: sha256-set @ fingerprints/invoice_line.track_id.json
- [observed] normalization: [none]
- [inferred:medium] Track sold on this line. Values match CORE track catalog identifiers.

### unit_price
- [observed] type: NUMERIC(10,2), not null
- [observed] distinct_count: 2; distinct_ratio: 0.0009; null_rate: 0.0
- [observed] format: mixed; range: [0.99 .. 1.99]
- [observed] top_values: 0.99(95%), 1.99(5%)
- [inferred:medium] Low-cardinality attribute; 2 distinct values.

### quantity
- [observed] type: INT, not null
- [observed] distinct_count: 1; distinct_ratio: 0.0004; null_rate: 0.0
- [observed] format: all-digits; range: [1 .. 1]
- [observed] top_values: 1(100%)
- [inferred:medium] Low-cardinality attribute; 1 distinct values.
