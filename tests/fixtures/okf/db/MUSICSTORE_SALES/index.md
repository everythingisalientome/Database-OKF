---
type: index
database: MUSICSTORE_SALES
description: Sales and customer system of record (Teradata).
engine: teradata
build_date: 2026-08-23
completeness: COMPLETE  # reconciliation: visible == cataloged
---

- [inferred:high] Sales and customer system of record (Teradata). Contains the customer master with contact details and assigned support representative, the employee master with reporting hierarchy, and invoicing (headers and line items). Line items reference track identifiers whose master data lives in the catalog SOR - no track attributes are stored here. No declared foreign keys; join intent is carried by Primary Index choices.

## Tables

- `SALES/customer.md` — Customer master with contact data and support rep (59 rows)
- `SALES/employee.md` — Employee master with reporting hierarchy (8 rows)
- `SALES/invoice.md` — Invoice headers: customer, date, billing, total (412 rows)
- `SALES/invoice_line.md` — Invoice line items: track sold, price, quantity (2240 rows)