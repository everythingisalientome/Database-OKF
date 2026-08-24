---
type: table
name: SALES.invoice
description: Invoice headers: customer, date, billing, total
description_confirmed: false
database: MUSICSTORE_SALES
engine: teradata
row_count: 412
row_count_source: live
crawl_date: 2026-08-23
flags: [pi:invoice_id]
---

- [inferred:high] Purpose: Invoice header per purchase. Customer reference, invoice date, billing address snapshot, total amount.

## Columns

### invoice_id
- [observed] type: INT, not null
- [observed] distinct_count: 412; distinct_ratio: 1.0; null_rate: 0.0
- [observed] format: all-digits; range: [1 .. 412]
- [observed] dense_sequence: true  # contiguous surrogate range - value overlap non-distinctive
- [observed] index: PRIMARY INDEX (Teradata PI)
- [observed] fingerprint: sha256/8B @ fingerprints/SALES.invoice.invoice_id.json
- [observed] normalization: [none]
- [inferred:high] Unique identifier; candidate key of invoice.

### customer_id
- [observed] type: INT, not null
- [observed] distinct_count: 59; distinct_ratio: 0.1432; null_rate: 0.0
- [observed] format: all-digits; range: [1 .. 59]
- [observed] dense_sequence: true  # contiguous surrogate range - value overlap non-distinctive
- [observed] index: non-unique
- [observed] fingerprint: sha256/8B @ fingerprints/SALES.invoice.customer_id.json
- [observed] normalization: [none]
- [inferred:medium] Reference identifier (customer).

### invoice_date
- [observed] type: TIMESTAMP, not null
- [observed] distinct_count: 354; distinct_ratio: 0.8592; null_rate: 0.0
- [observed] length: min 8, max 10, avg 8.9
- [observed] format: mixed; range: ['2021/1/1' .. '2025/9/7']
- [observed] fingerprint: sha256/8B @ fingerprints/SALES.invoice.invoice_date.json
- [observed] normalization: [none]
- [inferred:high] Date attribute; ISO datetime format observed.

### billing_address
- [observed] type: VARCHAR(70), nullable
- [observed] distinct_count: 59; distinct_ratio: 0.1432; null_rate: 0.0
- [observed] length: min 11, max 40, avg 17.9
- [observed] format: mixed; range: ['1 Infinite Loop' .. 'Via Degli Scipioni, 43']
- [inferred:low] Billing address attribute.

### billing_city
- [observed] type: VARCHAR(40), nullable
- [observed] distinct_count: 53; distinct_ratio: 0.1286; null_rate: 0.0
- [observed] length: min 4, max 19, avg 7.8
- [observed] format: alpha; range: ['Amsterdam' .. 'Yellowknife']
- [inferred:low] Billing city attribute.

### billing_state
- [observed] type: VARCHAR(40), nullable
- [observed] distinct_count: 25; distinct_ratio: 0.119; null_rate: 0.4903
- [observed] length: min 2, max 6, avg 2.2
- [observed] format: alpha; range: ['AB' .. 'WI']
- [observed] top_values: CA(10%), SP(10%), ON(7%), AB(3%), MA(3%), Dublin(3%)
- [inferred:medium] Low-cardinality attribute; 25 distinct values.

### billing_country
- [observed] type: VARCHAR(40), nullable
- [observed] distinct_count: 24; distinct_ratio: 0.0583; null_rate: 0.0
- [observed] length: min 3, max 14, avg 6.4
- [observed] format: alpha; range: ['Argentina' .. 'United Kingdom']
- [observed] top_values: USA(22%), Canada(14%), France(8%), Brazil(8%), Germany(7%), United Kingdom(5%)
- [inferred:medium] Low-cardinality attribute; 24 distinct values.

### billing_postal_code
- [observed] type: VARCHAR(10), nullable
- [observed] distinct_count: 55; distinct_ratio: 0.1432; null_rate: 0.068
- [observed] length: min 4, max 10, avg 6.0
- [observed] format: all-digits; range: ['00-358' .. 'X1A 1N6']
- [inferred:low] Billing postal code attribute.

### total
- [observed] type: NUMERIC(10,2), not null
- [observed] distinct_count: 23; distinct_ratio: 0.0558; null_rate: 0.0
- [observed] format: mixed; range: [0.99 .. 25.86]
- [observed] top_values: 1.98(27%), 3.96(14%), 5.94(14%), 0.99(13%), 8.91(13%), 13.86(12%)
- [inferred:high] Invoice total amount (sum of line extensions).
