-- Run inside DuckDB.
INSTALL httpfs;
LOAD httpfs;
INSTALL postgres;
LOAD postgres;

-- MinIO/S3 connection
CREATE OR REPLACE SECRET minio_secret (
  TYPE S3,
  KEY_ID 'minioadmin',
  SECRET 'minioadmin123',
  REGION 'us-east-1',
  ENDPOINT 'minio:9000',
  URL_STYLE 'path',
  USE_SSL false
);

-- A) One-store/one-month query. Hive partition pruning selects only S01/2024/10.
SELECT
  store_id,
  date_trunc('month', business_date) AS month,
  SUM(revenue_amount) AS revenue_inr
FROM read_parquet(
  's3://annapurna/sales/**/*.parquet',
  hive_partitioning = true
)
WHERE store_id = 'S01'
  AND business_date >= DATE '2024-10-01'
  AND business_date < DATE '2024-11-01'
GROUP BY 1,2;

-- C) Dashboard star-schema query.
-- The fact has only keys; names/addresses/categories live in PostgreSQL.
ATTACH 'host=postgres port=5432 dbname=annapurna user=annapurna password=annapurna'
  AS pg (TYPE POSTGRES, READ_ONLY);

SELECT
  s.store_id,
  s.store_name,
  pc.category_name,
  EXTRACT(isodow FROM f.business_date) AS day_of_week,
  date_trunc('month', f.business_date) AS month,
  SUM(f.revenue_amount) AS revenue_inr
FROM read_parquet('s3://annapurna/sales/**/*.parquet', hive_partitioning=true) f
JOIN pg.stores s ON s.store_id = f.store_id
JOIN pg.products p ON p.product_sk = f.product_sk
JOIN pg.product_categories pc ON pc.category_id = p.category_id
GROUP BY 1,2,3,4,5
ORDER BY month, store_id, category_name, day_of_week;

-- D) Same SQL shape works for March 2024 and another month by changing only :as_of_date.
-- Product identity is temporal, so product_code alone is NOT sufficient.
WITH params AS (SELECT DATE '2024-03-31' AS as_of_date)
SELECT
  p.product_sk, p.product_code, p.product_name,
  pc.category_name, pr.selling_price, pr.effective_from, pr.effective_to
FROM pg.products p
JOIN pg.product_categories pc ON pc.category_id = p.category_id
JOIN pg.price_revisions pr ON pr.product_sk = p.product_sk
CROSS JOIN params x
WHERE p.product_name ILIKE '%Biscuit%'
  AND p.valid_from <= x.as_of_date
  AND x.as_of_date < p.valid_to
  AND pr.effective_from <= x.as_of_date
  AND x.as_of_date < pr.effective_to
ORDER BY p.product_name;

-- E) Cross-system query: S3 Parquet + PostgreSQL in one statement.
SELECT
  f.store_id,
  s.store_name,
  pc.category_name,
  date_trunc('month', f.business_date) AS month,
  SUM(f.revenue_amount) AS revenue_inr
FROM read_parquet('s3://annapurna/sales/**/*.parquet', hive_partitioning=true) f
JOIN pg.stores s ON s.store_id = f.store_id
JOIN pg.products p
  ON p.product_sk = f.product_sk
  AND f.business_date >= p.valid_from
  AND f.business_date < p.valid_to
JOIN pg.product_categories pc ON pc.category_id = p.category_id
WHERE f.business_date >= DATE '2024-10-01'
  AND f.business_date < DATE '2024-11-01'
GROUP BY 1,2,3,4;

-- Evidence:
EXPLAIN
SELECT
  f.store_id, s.store_name, pc.category_name,
  date_trunc('month', f.business_date) AS month,
  SUM(f.revenue_amount) AS revenue_inr
FROM read_parquet('s3://annapurna/sales/**/*.parquet', hive_partitioning=true) f
JOIN pg.stores s ON s.store_id=f.store_id
JOIN pg.products p ON p.product_sk=f.product_sk
JOIN pg.product_categories pc ON pc.category_id=p.category_id
WHERE f.business_date >= DATE '2024-10-01'
  AND f.business_date < DATE '2024-11-01'
GROUP BY 1,2,3,4;

-- EXPLAIN ANALYZE is the evidence to quote in the report.
EXPLAIN ANALYZE
SELECT
  f.store_id, s.store_name, pc.category_name,
  date_trunc('month', f.business_date) AS month,
  SUM(f.revenue_amount) AS revenue_inr
FROM read_parquet('s3://annapurna/sales/**/*.parquet', hive_partitioning=true) f
JOIN pg.stores s ON s.store_id=f.store_id
JOIN pg.products p ON p.product_sk=f.product_sk
JOIN pg.product_categories pc ON pc.category_id=p.category_id
WHERE f.business_date >= DATE '2024-10-01'
  AND f.business_date < DATE '2024-11-01'
GROUP BY 1,2,3,4;
