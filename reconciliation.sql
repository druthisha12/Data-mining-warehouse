INSTALL httpfs;
LOAD httpfs;

CREATE OR REPLACE SECRET minio_secret (
    TYPE S3,
    KEY_ID 'minioadmin',
    SECRET 'minioadmin123',
    ENDPOINT 'minio:9000',
    URL_STYLE 'path',
    USE_SSL false
);

WITH pipeline AS (
    SELECT
        year || '-' || LPAD(month::VARCHAR, 2, '0') AS month,
        ROUND(SUM(revenue_amount), 2) AS pipeline_revenue
    FROM read_parquet(
        's3://annapurna/sales/store_id=*/year=*/month=*/data.parquet',
        hive_partitioning = true
    )
    GROUP BY 1
),
finance AS (
    SELECT *
    FROM read_csv_auto('/data/finance_monthly.csv')
)
SELECT
    p.month,
    p.pipeline_revenue,
    f.revenue_inr AS finance_revenue,
    ROUND(p.pipeline_revenue - f.revenue_inr, 2) AS difference
FROM pipeline p
JOIN finance f
    ON p.month = f.month
ORDER BY p.month;