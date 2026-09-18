# Annapurna Stores - exam report

## A. Platform and landing

Platform:
- MinIO = object store
- PostgreSQL = operational/master database
- DuckDB = analytical query engine

Landing layout:
`store_id=Sxx/year=YYYY/month=MM/data.parquet`

This gives one compact file per store-month. A query for S01/October can touch one partition file. The original source folder contains 4,457 CSV files and 68,706,877 bytes. S01 October alone has 31 source files and 846,899 bytes.

## B. Idempotency

Source rows: 1,137,585.

After line-level deduplication on `(bill_no,line_no)`: 1,120,924 rows.

There are no conflicting duplicate line keys in the supplied files. Therefore re-sends add no duplicate business lines.

Canonical SHA-256:
`20ec1062f7e31b24325ca3b3910355bbce6fe4fc605c7095d1a9f224003cd5e8`

Expected after run 1, 2 and 3:
- rows = 1,120,924
- SHA-256 = `20ec1062f7e31b24325ca3b3910355bbce6fe4fc605c7095d1a9f224003cd5e8`

## C. Dashboard model

Fact:
`fact_sales_line(store_id, product_sk, business_date, bill_no, line_no, qty, unit_price, line_type, revenue_amount)`

Dimensions:
- stores
- products (SCD/validity)
- product_categories
- dim_date

The fact stores keys, not repeated store names/addresses.

Revenue:
`SALE + RETURN + DISCOUNT + VOID`
and excludes `TAX + TENDER`.

## D. Historical prices

Product identity is temporal:
`product_code + valid_from/valid_to`, or preferably `product_sk`.

Price is temporal:
`product_sk + effective_from/effective_to`.

For a report date, use the same predicates against both histories. The SQL text does not need to change between March 2024 and another month; only the date parameter changes.

## E. Cross-system query

DuckDB reads Parquet from MinIO and PostgreSQL master tables in the same SQL statement. Run `EXPLAIN ANALYZE` and include its actual output in the submitted report. Cite the Parquet scan and PostgreSQL scan operators shown by the engine as the evidence for execution location.

## F. Reconciliation

| Month | Pipeline | Finance | Difference |
|---|---:|---:|---:|
| 2024-01 | 38,446,071.33 | 38,446,071.33 | 0.00 |
| 2024-02 | 34,887,085.55 | 34,887,085.55 | 0.00 |
| 2024-03 | 41,971,649.09 | 42,457,899.09 | -486,250.00 |
| 2024-04 | 37,958,457.37 | 37,958,457.37 | 0.00 |
| 2024-05 | 41,764,716.40 | 41,764,716.40 | 0.00 |
| 2024-06 | 38,987,082.82 | 38,987,082.82 | 0.00 |
| 2024-07 | 40,295,160.11 | 40,527,291.81 | -232,131.70 |
| 2024-08 | 45,252,181.75 | 45,252,181.75 | 0.00 |
| 2024-09 | 44,615,037.46 | 44,615,037.46 | 0.00 |
| 2024-10 | 56,359,195.92 | 56,359,195.92 | 0.00 |
| 2024-11 | 51,583,838.47 | 51,583,838.47 | 0.00 |
| 2024-12 | 50,725,259.48 | 50,745,209.00 | 50.48 |

July is directly explained by the documented S07 three-day source gap. March and December need reconciliation evidence from Finance/source before changing the pipeline. October matches exactly.

## Warning

The two common inflation bugs are:
1. counting TENDER/TAX as revenue;
2. joining products on `product_code` alone after code reissue.

Both are explicitly prevented in this design.
