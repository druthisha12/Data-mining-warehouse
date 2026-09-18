-- Run after masters.sql has loaded.

CREATE TABLE IF NOT EXISTS dim_date AS
SELECT
  d::date AS date_id,
  EXTRACT(isodow FROM d)::int AS day_of_week,
  TO_CHAR(d, 'FMDay') AS day_name,
  date_trunc('month', d)::date AS month_id
FROM generate_series(DATE '2024-01-01', DATE '2024-12-31', INTERVAL '1 day') AS g(d);

-- Curated fact table is physically in S3 Parquet. This view documents the star.
-- fact_sales_line:
--   store_id, product_sk, business_date, bill_no, line_no, qty,
--   unit_price, line_type, revenue_amount
--
-- Dimensions:
--   stores(store_id PK, store_name, address_line, ...)
--   products(product_sk PK, product_code, category_id, valid_from, valid_to, ...)
--   product_categories(category_id PK, category_name, ...)
--   dim_date(date_id PK, day_of_week, day_name, month_id)
