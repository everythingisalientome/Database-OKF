---
type: table
name: SALES.customer
description: Customer master with contact data and support rep
description_confirmed: false
database: MUSICSTORE_SALES
engine: teradata
row_count: 59
row_count_source: live
crawl_date: 2026-08-23
flags: [pi:customer_id]
---

- [inferred:high] Purpose: Customer master. Contact/address fields plus assigned support representative (support_rep_id).

## Columns

### customer_id
- [observed] type: INT, not null
- [observed] distinct_count: 59; distinct_ratio: 1.0; null_rate: 0.0
- [observed] format: all-digits; range: [1 .. 59]
- [observed] dense_sequence: true  # contiguous surrogate range - value overlap non-distinctive
- [observed] index: PRIMARY INDEX (Teradata PI)
- [observed] fingerprint: sha256-set @ fingerprints/customer.customer_id.json
- [observed] normalization: [none]
- [inferred:high] Unique identifier; candidate key of customer.

### first_name
- [observed] type: VARCHAR(40), not null
- [observed] distinct_count: 57; distinct_ratio: 0.9661; null_rate: 0.0
- [observed] length: min 3, max 9, avg 5.8
- [observed] format: alpha; range: ['Aaron' .. 'Wyatt']
- [observed] fingerprint: sha256-set @ fingerprints/customer.first_name.json
- [observed] normalization: [uppercase]
- [inferred:low] First name attribute.

### last_name
- [observed] type: VARCHAR(20), not null
- [observed] distinct_count: 59; distinct_ratio: 1.0; null_rate: 0.0
- [observed] length: min 4, max 12, avg 6.9
- [observed] format: alpha; range: ['Almeida' .. 'Zimmermann']
- [observed] fingerprint: sha256-set @ fingerprints/customer.last_name.json
- [observed] normalization: [uppercase]
- [inferred:low] Last name attribute.

### company
- [observed] type: VARCHAR(80), nullable
- [observed] distinct_count: 10; distinct_ratio: 1.0; null_rate: 0.8305
- [observed] length: min 5, max 48, avg 16.6
- [observed] format: alpha; range: ['Apple Inc.' .. 'Woodstock Discos']
- [observed] top_values: Embraer - Empresa Brasileira de Aeronáutica S.A.(10%), JetBrains s.r.o.(10%), Woodstock Discos(10%), Banco do Brasil S.A.(10%), Riotur(10%), Telus(10%)
- [observed] fingerprint: sha256-set @ fingerprints/customer.company.json
- [observed] normalization: [uppercase]
- [inferred:medium] Low-cardinality attribute; 10 distinct values.

### address
- [observed] type: VARCHAR(70), nullable
- [observed] distinct_count: 59; distinct_ratio: 1.0; null_rate: 0.0
- [observed] length: min 11, max 40, avg 17.9
- [observed] format: mixed  (sensitive-listed: range suppressed)
- [observed] sensitive-listed: top-N and fingerprint suppressed
- [inferred:low] Address attribute.

### city
- [observed] type: VARCHAR(40), nullable
- [observed] distinct_count: 53; distinct_ratio: 0.8983; null_rate: 0.0
- [observed] length: min 4, max 19, avg 7.8
- [observed] format: alpha; range: ['Amsterdam' .. 'Yellowknife']
- [observed] fingerprint: sha256-set @ fingerprints/customer.city.json
- [observed] normalization: [trim, uppercase]
- [inferred:low] City attribute.

### state
- [observed] type: VARCHAR(40), nullable
- [observed] distinct_count: 25; distinct_ratio: 0.8333; null_rate: 0.4915
- [observed] length: min 2, max 6, avg 2.2
- [observed] format: alpha; range: ['AB' .. 'WI']
- [observed] top_values: SP(10%), CA(10%), ON(7%), QC(3%), RJ(3%), DF(3%)
- [observed] fingerprint: sha256-set @ fingerprints/customer.state.json
- [observed] normalization: [uppercase]
- [inferred:medium] Low-cardinality attribute; 25 distinct values.

### country
- [observed] type: VARCHAR(40), nullable
- [observed] distinct_count: 24; distinct_ratio: 0.4068; null_rate: 0.0
- [observed] length: min 3, max 14, avg 6.4
- [observed] format: alpha; range: ['Argentina' .. 'United Kingdom']
- [observed] top_values: USA(22%), Canada(14%), Brazil(8%), France(8%), Germany(7%), United Kingdom(5%)
- [inferred:medium] Low-cardinality attribute; 24 distinct values.

### postal_code
- [observed] type: VARCHAR(10), nullable
- [observed] distinct_count: 55; distinct_ratio: 1.0; null_rate: 0.0678
- [observed] length: min 4, max 10, avg 6.0
- [observed] format: all-digits  (sensitive-listed: range suppressed)
- [observed] sensitive-listed: top-N and fingerprint suppressed
- [inferred:low] Postal code attribute.

### phone
- [observed] type: VARCHAR(24), nullable
- [observed] distinct_count: 58; distinct_ratio: 1.0; null_rate: 0.0169
- [observed] length: min 14, max 19, avg 16.8
- [observed] format: phone-like  (sensitive-listed: range suppressed)
- [observed] sensitive-listed: top-N and fingerprint suppressed
- [inferred:medium] Phone/fax number, mixed international formats.

### fax
- [observed] type: VARCHAR(24), nullable
- [observed] distinct_count: 12; distinct_ratio: 1.0; null_rate: 0.7966
- [observed] length: min 16, max 18, avg 17.3
- [observed] format: phone-like  (sensitive-listed: range suppressed)
- [observed] sensitive-listed: top-N and fingerprint suppressed
- [inferred:medium] Phone/fax number, mixed international formats.

### email
- [observed] type: VARCHAR(60), not null
- [observed] distinct_count: 59; distinct_ratio: 1.0; null_rate: 0.0
- [observed] length: min 15, max 29, avg 21.0
- [observed] format: email  (sensitive-listed: range suppressed)
- [observed] sensitive-listed: top-N and fingerprint suppressed
- [inferred:high] Email address.

### support_rep_id
- [observed] type: INT, nullable
- [observed] distinct_count: 3; distinct_ratio: 0.0508; null_rate: 0.0
- [observed] format: all-digits; range: [3 .. 5]
- [observed] index: non-unique
- [observed] top_values: 3(36%), 4(34%), 5(31%)
- [observed] fingerprint: sha256-set @ fingerprints/customer.support_rep_id.json
- [observed] normalization: [none]
- [inferred:high] Assigned support employee; joins to employee.
