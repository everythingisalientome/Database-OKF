---
type: table
name: SALES.employee
description: Employee master with reporting hierarchy
description_confirmed: false
database: MUSICSTORE_SALES
engine: teradata
row_count: 8
row_count_source: live
crawl_date: 2026-08-23
flags: [pi:employee_id]
---

- [inferred:high] Purpose: Employee master including self-referencing reports_to hierarchy.

## Columns

### employee_id
- [observed] type: INT, not null
- [observed] distinct_count: 8; distinct_ratio: 1.0; null_rate: 0.0
- [observed] format: all-digits; range: [1 .. 8]
- [observed] dense_sequence: true  # contiguous surrogate range - value overlap non-distinctive
- [observed] index: PRIMARY INDEX (Teradata PI)
- [observed] top_values: 1(12%), 2(12%), 3(12%), 4(12%), 5(12%), 6(12%)
- [observed] fingerprint: sha256/8B @ fingerprints/SALES.employee.employee_id.json
- [observed] normalization: [none]
- [inferred:high] Unique identifier; candidate key of employee.

### last_name
- [observed] type: VARCHAR(20), not null
- [observed] distinct_count: 8; distinct_ratio: 1.0; null_rate: 0.0
- [observed] length: min 4, max 8, avg 6.2
- [observed] format: alpha; range: ['Adams' .. 'Peacock']
- [observed] top_values: Adams(12%), Edwards(12%), Peacock(12%), Park(12%), Johnson(12%), Mitchell(12%)
- [observed] fingerprint: sha256/8B @ fingerprints/SALES.employee.last_name.json
- [observed] normalization: [uppercase]
- [inferred:medium] Low-cardinality attribute; 8 distinct values.

### first_name
- [observed] type: VARCHAR(20), not null
- [observed] distinct_count: 8; distinct_ratio: 1.0; null_rate: 0.0
- [observed] length: min 4, max 8, avg 5.8
- [observed] format: alpha; range: ['Andrew' .. 'Steve']
- [observed] top_values: Andrew(12%), Nancy(12%), Jane(12%), Margaret(12%), Steve(12%), Michael(12%)
- [observed] fingerprint: sha256/8B @ fingerprints/SALES.employee.first_name.json
- [observed] normalization: [uppercase]
- [inferred:medium] Low-cardinality attribute; 8 distinct values.

### title
- [observed] type: VARCHAR(30), nullable
- [observed] distinct_count: 5; distinct_ratio: 0.625; null_rate: 0.0
- [observed] length: min 8, max 19, avg 13.9
- [observed] format: alpha; range: ['General Manager' .. 'Sales Support Agent']
- [observed] top_values: Sales Support Agent(38%), IT Staff(25%), General Manager(12%), Sales Manager(12%), IT Manager(12%)
- [observed] fingerprint: sha256/8B @ fingerprints/SALES.employee.title.json
- [observed] normalization: [uppercase]
- [inferred:high] Display name of the employee record.

### reports_to
- [observed] type: INT, nullable
- [observed] distinct_count: 3; distinct_ratio: 0.4286; null_rate: 0.125
- [observed] format: all-digits; range: [1 .. 6]
- [observed] index: non-unique
- [observed] top_values: 2(43%), 1(29%), 6(29%)
- [observed] fingerprint: sha256/8B @ fingerprints/SALES.employee.reports_to.json
- [observed] normalization: [none]
- [inferred:high] Manager employee_id (self-reference); null for top of hierarchy.

### birth_date
- [observed] type: TIMESTAMP, nullable
- [observed] distinct_count: 8; distinct_ratio: 1.0; null_rate: 0.0
- [observed] length: min 8, max 9, avg 8.6
- [observed] format: mixed  (sensitive-listed: range suppressed)
- [observed] sensitive-listed: top-N and fingerprint suppressed
- [inferred:high] Date attribute; ISO datetime format observed.

### hire_date
- [observed] type: TIMESTAMP, nullable
- [observed] distinct_count: 7; distinct_ratio: 0.875; null_rate: 0.0
- [observed] length: min 8, max 10, avg 8.6
- [observed] format: mixed; range: ['2002/4/1' .. '2004/3/4']
- [observed] top_values: 2003/10/17(25%), 2002/8/14(12%), 2002/5/1(12%), 2002/4/1(12%), 2003/5/3(12%), 2004/1/2(12%)
- [observed] fingerprint: sha256/8B @ fingerprints/SALES.employee.hire_date.json
- [observed] normalization: [none]
- [inferred:high] Date attribute; ISO datetime format observed.

### address
- [observed] type: VARCHAR(70), nullable
- [observed] distinct_count: 8; distinct_ratio: 1.0; null_rate: 0.0
- [observed] length: min 11, max 27, avg 16.2
- [observed] format: mixed  (sensitive-listed: range suppressed)
- [observed] sensitive-listed: top-N and fingerprint suppressed
- [inferred:medium] Low-cardinality attribute; 8 distinct values.

### city
- [observed] type: VARCHAR(40), nullable
- [observed] distinct_count: 3; distinct_ratio: 0.375; null_rate: 0.0
- [observed] length: min 7, max 10, avg 7.9
- [observed] format: alpha; range: ['Calgary' .. 'Lethbridge']
- [observed] top_values: Calgary(62%), Lethbridge(25%), Edmonton(12%)
- [inferred:medium] Low-cardinality attribute; 3 distinct values.

### state
- [observed] type: VARCHAR(40), nullable
- [observed] distinct_count: 1; distinct_ratio: 0.125; null_rate: 0.0
- [observed] length: min 2, max 2, avg 2.0
- [observed] format: alpha; range: ['AB' .. 'AB']
- [observed] top_values: AB(100%)
- [inferred:medium] Low-cardinality attribute; 1 distinct values.

### country
- [observed] type: VARCHAR(40), nullable
- [observed] distinct_count: 1; distinct_ratio: 0.125; null_rate: 0.0
- [observed] length: min 6, max 6, avg 6.0
- [observed] format: alpha; range: ['Canada' .. 'Canada']
- [observed] top_values: Canada(100%)
- [inferred:medium] Low-cardinality attribute; 1 distinct values.

### postal_code
- [observed] type: VARCHAR(10), nullable
- [observed] distinct_count: 8; distinct_ratio: 1.0; null_rate: 0.0
- [observed] length: min 7, max 7, avg 7.0
- [observed] format: mixed  (sensitive-listed: range suppressed)
- [observed] sensitive-listed: top-N and fingerprint suppressed
- [inferred:medium] Low-cardinality attribute; 8 distinct values.

### phone
- [observed] type: VARCHAR(24), nullable
- [observed] distinct_count: 7; distinct_ratio: 0.875; null_rate: 0.0
- [observed] length: min 16, max 17, avg 16.9
- [observed] format: phone-like  (sensitive-listed: range suppressed)
- [observed] sensitive-listed: top-N and fingerprint suppressed
- [inferred:medium] Phone/fax number, mixed international formats.

### fax
- [observed] type: VARCHAR(24), nullable
- [observed] distinct_count: 8; distinct_ratio: 1.0; null_rate: 0.0
- [observed] length: min 16, max 17, avg 16.9
- [observed] format: phone-like  (sensitive-listed: range suppressed)
- [observed] sensitive-listed: top-N and fingerprint suppressed
- [inferred:medium] Phone/fax number, mixed international formats.

### email
- [observed] type: VARCHAR(60), nullable
- [observed] distinct_count: 8; distinct_ratio: 1.0; null_rate: 0.0
- [observed] length: min 20, max 24, avg 21.8
- [observed] format: email  (sensitive-listed: range suppressed)
- [observed] sensitive-listed: top-N and fingerprint suppressed
- [inferred:high] Email address.
