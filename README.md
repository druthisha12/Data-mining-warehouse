# Annapurna Stores - exam solution

## Important
Only the `data/` folder from the supplied ZIP is used. The loader reads:
- `data/sales/`
- `data/masters.sql`
- `data/finance_monthly.csv`
- `data/billing_notes.md`

## 1. Start the platform

Open PowerShell in this folder:

```powershell
docker compose up -d postgres minio
docker compose run --rm minio-init
```

Check:

```powershell
docker compose ps
```

PostgreSQL is on host port `5433`; MinIO API is `9000`; MinIO console is `9001`.

## 2. Read the vendor notes first

The key rules are:
1. Business date = date in filename, not timestamp.
2. Re-sends are not necessarily complete; deduplicate by `(bill_no,line_no)`.
3. Revenue line types are SALE, RETURN, DISCOUNT and VOID.
4. TAX and TENDER are not revenue.
5. Keep VOID rows so cancelled bills net to zero.
6. Product code is not unique; use `product_sk` and product validity dates.
7. Use price revisions for historical price questions.
8. S07 has three missing July days.

## 3. Load the daily files into MinIO

```powershell
docker compose build loader
docker compose run --rm loader
```

The curated layout is:

```text
s3://annapurna/sales/
  store_id=S01/year=2024/month=01/data.parquet
  store_id=S01/year=2024/month=02/data.parquet
  ...
  store_id=S12/year=2024/month=12/data.parquet
```

One compact Parquet file is produced per store-month. Therefore a query for S01 in October can be reduced to one partition file rather than scanning every store/month.

## 4. Prove idempotency

Run the loader three times:

```powershell
docker compose run --rm loader
docker compose run --rm loader
docker compose run --rm loader
```

Each run prints `load_audit.json`. The expected values from this supplied dataset are:

```text
source_rows          1137585
curated_rows         1120924
duplicate_rows_removed 29661
partition_files      144
revenue_total_inr    522865735.75
canonical_sha256     20ec1062f7e31b24325ca3b3910355bbce6fe4fc605c7095d1a9f224003cd5e8
```

All three runs must show the same values.

## 5. Run DuckDB

```powershell
docker compose run --rm duckdb
```

Inside DuckDB:

```sql
.read /work/queries.sql
```

For a simpler manual test:

```sql
INSTALL httpfs;
LOAD httpfs;
CREATE OR REPLACE SECRET minio_secret (
  TYPE S3, KEY_ID 'minioadmin', SECRET 'minioadmin123',
  REGION 'us-east-1', ENDPOINT 'minio:9000',
  URL_STYLE 'path', USE_SSL false
);

SELECT store_id, SUM(revenue_amount)
FROM read_parquet(
  's3://annapurna/sales/**/*.parquet',
  hive_partitioning=true
)
WHERE store_id='S01'
  AND business_date >= DATE '2024-10-01'
  AND business_date < DATE '2024-11-01'
GROUP BY store_id;
```

Expected S01 October revenue: `6,435,443.95`.

## 6. Dashboard table design

Use a star schema:
- `fact_sales_line`: only keys + measures
- `stores`: store attributes
- `products`: product attributes + SCD validity
- `product_categories`: category attributes
- `dim_date`: day/week/month attributes

This avoids repeating store name/address on millions of fact rows.

For dashboard speed, aggregate the fact by:
`store_id, product_sk/category_id, business_date`
or maintain a daily store-category aggregate derived from the fact.

## 7. Historical March prices

Do not use the current price and do not join product on code alone.

Use temporal predicates:

```sql
p.valid_from <= :as_of_date
AND :as_of_date < p.valid_to
AND pr.effective_from <= :as_of_date
AND :as_of_date < pr.effective_to
```

The same query works for March 2024 and another month by changing only the date parameter.

## 8. Cross-system query and evidence

DuckDB can query:
- MinIO through `read_parquet(...)`
- PostgreSQL through `ATTACH ... (TYPE POSTGRES)`

Run:

```sql
EXPLAIN ANALYZE
SELECT ...
```

Use the actual plan in the report. The evidence to point out is the presence of a Parquet/S3 scan for the sales side and PostgreSQL scan operators for the master side. Do not claim a part ran in PostgreSQL merely because the SQL references PostgreSQL; quote the operator shown by `EXPLAIN ANALYZE`.

## 9. Reconciliation

The supplied data produces:

```text
Month    Pipeline          Finance            Difference
2024-01  38,446,071.33     38,446,071.33          0.00
2024-02  34,887,085.55     34,887,085.55          0.00
2024-03  41,971,649.09     42,457,899.09    -486,250.00
2024-04  37,958,457.37     37,958,457.37          0.00
2024-05  41,764,716.40     41,764,716.40          0.00
2024-06  38,987,082.82     38,987,082.82          0.00
2024-07  40,295,160.11     40,527,291.81    -232,131.70
2024-08  45,252,181.75     45,252,181.75          0.00
2024-09  44,615,037.46     44,615,037.46          0.00
2024-10  56,359,195.92     56,359,195.92          0.00
2024-11  51,583,838.47     51,583,838.47          0.00
2024-12  50,725,259.48     50,745,209.00         50.48
```

Interpretation:
- July: source-data gap. Vendor notes explicitly say S07 is missing 2024-07-09 through 2024-07-11. Finance has manually phoned-in numbers, so the source files cannot reproduce finance for those days.
- March: investigate as a finance/source reconciliation difference; the pipeline's line-type rule and deduplication are consistent with the months that match. Do not label it a pipeline bug without additional evidence.
- December: tiny `50.48` difference; investigate as a source/finance reconciliation difference before changing the pipeline. Do not hide it with rounding.

For the finance meeting, take the pipeline number for months that reconcile exactly; for non-matching months, report the difference and its evidence rather than silently altering the pipeline.

## 10. The October trap

Never do:

```sql
SELECT SUM(qty * unit_price)
FROM all_lines;
```

because TENDER is already the bill total and TAX is not revenue.

Also never do:

```sql
JOIN products USING (product_code)
```

because 24 product codes are reused after retirement. Use `product_sk`/validity.

Finally, do not remove VOID rows while keeping the original SALE rows. A cancelled bill must net to zero.
