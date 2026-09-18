import os
import json
import re
from pathlib import Path

import boto3
import pandas as pd
import psycopg2
from botocore.client import Config


# =========================================================
# CONFIGURATION
# =========================================================

DATA_DIR = Path("/data")
OUTPUT_DIR = Path("/output")

S3_ENDPOINT = os.getenv("S3_ENDPOINT", "http://minio:9000")
S3_ACCESS_KEY = os.getenv("S3_ACCESS_KEY", "minioadmin")
S3_SECRET_KEY = os.getenv("S3_SECRET_KEY", "minioadmin123")
S3_BUCKET = os.getenv("S3_BUCKET", "annapurna")

PG_HOST = os.getenv("PG_HOST", "postgres")
PG_PORT = os.getenv("PG_PORT", "5432")
PG_DB = os.getenv("PG_DB", "annapurna")
PG_USER = os.getenv("PG_USER", "annapurna")
PG_PASSWORD = os.getenv("PG_PASSWORD", "annapurna")


# =========================================================
# S3 / MINIO CONNECTION
# =========================================================

s3 = boto3.client(
    "s3",
    endpoint_url=S3_ENDPOINT,
    aws_access_key_id=S3_ACCESS_KEY,
    aws_secret_access_key=S3_SECRET_KEY,
    config=Config(signature_version="s3v4"),
    region_name="us-east-1",
)


# =========================================================
# POSTGRES PRODUCT MASTER
# =========================================================

def get_product_master():
    conn = psycopg2.connect(
        host=PG_HOST,
        port=PG_PORT,
        dbname=PG_DB,
        user=PG_USER,
        password=PG_PASSWORD,
    )

    query = """
        SELECT
            product_sk,
            product_code,
            valid_from,
            valid_to
        FROM products
    """

    df = pd.read_sql_query(query, conn)

    conn.close()

    df["valid_from"] = pd.to_datetime(
        df["valid_from"],
        errors="coerce"
    )

    df["valid_to"] = pd.to_datetime(
        df["valid_to"],
        errors="coerce"
    )

    return df


# =========================================================
# READ ONE SALES FILE
# =========================================================

def read_sales_file(file_path):

    filename = file_path.name.upper()

    # -----------------------------------------------------
    # S06-S09 use semicolon-separated files
    # -----------------------------------------------------

    if any(store in filename for store in ["S06", "S07", "S08", "S09"]):

        df = pd.read_csv(
            file_path,
            sep=";",
            encoding="utf-8-sig"
        )

        rename_map = {
            "item_code": "product_code",
            "quantity": "qty",
            "rate": "unit_price",
            "type": "line_type",
            "txn_time": "ts",
        }

        df = df.rename(columns=rename_map)

    else:

        df = pd.read_csv(
            file_path,
            encoding="utf-8-sig"
        )

    return df


# =========================================================
# GET BUSINESS DATE FROM FILE NAME
# =========================================================

def get_business_date(file_path):

    filename = file_path.name

    # Look for YYYY-MM-DD
    match = re.search(
        r"(20\d{2})[-_](\d{2})[-_](\d{2})",
        filename
    )

    if match:

        return pd.Timestamp(
            int(match.group(1)),
            int(match.group(2)),
            int(match.group(3))
        ).normalize()

    # Look for YYYYMMDD
    match = re.search(
        r"(20\d{2})(\d{2})(\d{2})",
        filename
    )

    if match:

        return pd.Timestamp(
            int(match.group(1)),
            int(match.group(2)),
            int(match.group(3))
        ).normalize()

    # Last part of filename
    last_part = file_path.stem.split("_")[-1]

    date = pd.to_datetime(
        last_part,
        errors="coerce"
    )

    if pd.isna(date):

        raise ValueError(
            f"Could not determine business date from: {filename}"
        )

    return pd.Timestamp(date).normalize()


# =========================================================
# GET STORE ID FROM FILE NAME
# =========================================================

def get_store_id(file_path):

    match = re.search(
        r"(S\d{2})",
        file_path.name.upper()
    )

    if match:
        return match.group(1)

    raise ValueError(
        f"Could not determine store_id from: {file_path.name}"
    )


# =========================================================
# NORMALIZE ONE FILE
# =========================================================

def normalize_file(file_path):

    df = read_sales_file(file_path)

    # -----------------------------------------------------
    # Normalize column names
    # -----------------------------------------------------

    df.columns = [
        str(column)
        .strip()
        .lower()
        .replace(" ", "_")
        for column in df.columns
    ]

    # -----------------------------------------------------
    # Alternative column names
    # -----------------------------------------------------

    rename_map = {
        "item": "product_code",
        "itemcode": "product_code",
        "item_code": "product_code",
        "quantity": "qty",
        "rate": "unit_price",
        "type": "line_type",
        "transaction_type": "line_type",
        "bill": "bill_no",
        "bill_number": "bill_no",
        "line": "line_no",
    }

    df = df.rename(columns=rename_map)

    # -----------------------------------------------------
    # Required columns
    # -----------------------------------------------------

    required_columns = [
        "bill_no",
        "line_no",
        "product_code",
        "qty",
        "unit_price",
        "line_type",
    ]

    missing = [
        column
        for column in required_columns
        if column not in df.columns
    ]

    if missing:

        raise ValueError(
            f"{file_path.name}: missing columns {missing}. "
            f"Available columns: {list(df.columns)}"
        )

    # -----------------------------------------------------
    # Business date
    # -----------------------------------------------------

    df["business_date"] = get_business_date(file_path)

    # -----------------------------------------------------
    # Store ID
    # -----------------------------------------------------

    df["store_id"] = get_store_id(file_path)

    # -----------------------------------------------------
    # Data types
    # -----------------------------------------------------

    df["bill_no"] = df["bill_no"].astype(str)
    df["line_no"] = df["line_no"].astype(str)
    df["product_code"] = df["product_code"].astype(str)

    df["qty"] = pd.to_numeric(
        df["qty"],
        errors="coerce"
    ).fillna(0)

    df["unit_price"] = pd.to_numeric(
        df["unit_price"],
        errors="coerce"
    ).fillna(0)

    df["line_type"] = (
        df["line_type"]
        .astype(str)
        .str.strip()
        .str.upper()
    )

    # -----------------------------------------------------
    # Revenue calculation
    # -----------------------------------------------------
    #
    # SALE      -> revenue
    # RETURN    -> revenue
    # DISCOUNT  -> revenue
    # VOID      -> revenue
    #
    # TAX       -> excluded
    # TENDER    -> excluded
    # -----------------------------------------------------

    revenue_types = {
        "SALE",
        "RETURN",
        "DISCOUNT",
        "VOID",
    }

    df["revenue_amount"] = 0.0

    revenue_mask = df["line_type"].isin(
        revenue_types
    )

    df.loc[revenue_mask, "revenue_amount"] = (
        df.loc[revenue_mask, "qty"]
        * df.loc[revenue_mask, "unit_price"]
    )

    # -----------------------------------------------------
    # Source file
    # -----------------------------------------------------

    df["source_file"] = file_path.name

    return df


# =========================================================
# FIND ALL SALES FILES
# =========================================================

def find_sales_files():

    sales_dir = DATA_DIR / "sales"

    if not sales_dir.exists():

        raise FileNotFoundError(
            f"Sales directory not found: {sales_dir}"
        )

    files = sorted(
        sales_dir.rglob("*.csv")
    )

    return files


# =========================================================
# REMOVE OLD S3 SALES DATA
# =========================================================

def clean_s3_sales():

    print()
    print("Cleaning previous sales objects...")

    try:

        paginator = s3.get_paginator(
            "list_objects_v2"
        )

        objects_to_delete = []

        for page in paginator.paginate(
            Bucket=S3_BUCKET,
            Prefix="sales/"
        ):

            for obj in page.get(
                "Contents",
                []
            ):

                objects_to_delete.append(
                    {
                        "Key": obj["Key"]
                    }
                )

        # Delete in batches of 1000
        for i in range(
            0,
            len(objects_to_delete),
            1000
        ):

            batch = objects_to_delete[
                i:i + 1000
            ]

            s3.delete_objects(
                Bucket=S3_BUCKET,
                Delete={
                    "Objects": batch,
                    "Quiet": True,
                }
            )

        print(
            f"Old objects removed: "
            f"{len(objects_to_delete):,}"
        )

    except Exception as exc:

        print(
            f"Warning while cleaning S3: {exc}"
        )


# =========================================================
# MAIN
# =========================================================

def main():

    print("=" * 70)
    print("ANNAPURNA STORES DATA LOADER")
    print("=" * 70)

    OUTPUT_DIR.mkdir(
        parents=True,
        exist_ok=True
    )

    # -----------------------------------------------------
    # Test PostgreSQL
    # -----------------------------------------------------

    print()
    print("Loading product master from PostgreSQL...")

    product_master = get_product_master()

    print(
        f"Product master rows: "
        f"{len(product_master):,}"
    )

    # -----------------------------------------------------
    # Find files
    # -----------------------------------------------------

    files = find_sales_files()

    print()
    print(
        f"Sales files found: "
        f"{len(files):,}"
    )

    if not files:

        raise RuntimeError(
            "No sales CSV files found."
        )

    # -----------------------------------------------------
    # Read files
    # -----------------------------------------------------

    frames = []

    for index, file_path in enumerate(
        files,
        start=1
    ):

        if index == 1 or index % 100 == 0:

            print(
                f"Reading file "
                f"{index:,}/{len(files):,}: "
                f"{file_path.name}"
            )

        df = normalize_file(
            file_path
        )

        frames.append(df)

    # -----------------------------------------------------
    # Combine
    # -----------------------------------------------------

    fact = pd.concat(
        frames,
        ignore_index=True
    )

    print()
    print(
        f"Rows before deduplication: "
        f"{len(fact):,}"
    )

    # -----------------------------------------------------
    # Deduplication
    # -----------------------------------------------------

    before = len(fact)

    fact = fact.drop_duplicates(
        subset=[
            "bill_no",
            "line_no"
        ],
        keep="first"
    ).reset_index(
        drop=True
    )

    after = len(fact)

    duplicates_removed = (
        before - after
    )

    print(
        f"Duplicate rows removed: "
        f"{duplicates_removed:,}"
    )

    print(
        f"Rows after deduplication: "
        f"{after:,}"
    )

       # -----------------------------------------------------
    # Product temporal resolution
    # -----------------------------------------------------

    print()
    print("Resolving product versions...")

    # Normalize product codes before matching
    fact["product_code"] = (
        fact["product_code"]
        .astype(str)
        .str.strip()
        .str.upper()
    )

    product_master["product_code"] = (
        product_master["product_code"]
        .astype(str)
        .str.strip()
        .str.upper()
    )

    # Give every sales row a unique temporary ID
    fact["_row_id"] = range(len(fact))

    # Join on product_code
    matched = fact[
        [
            "_row_id",
            "product_code",
            "business_date",
        ]
    ].merge(
        product_master[
            [
                "product_sk",
                "product_code",
                "valid_from",
                "valid_to",
            ]
        ],
        on="product_code",
        how="left",
    )

    # Keep only the product version valid on the
    # business date of the sales transaction.
    matched = matched[
        (matched["business_date"] >= matched["valid_from"])
        &
        (matched["business_date"] <= matched["valid_to"])
    ]

    # There should be one valid product version per
    # sales row. Keep one match if multiple rows exist.
    matched = (
        matched
        .sort_values(
            [
                "_row_id",
                "valid_from",
            ]
        )
        .drop_duplicates(
            subset=["_row_id"],
            keep="last",
        )
    )

    # Start with no product_sk
    fact["product_sk"] = pd.NA

    # Map the valid product version back to the
    # original sales rows.
    product_map = matched.set_index("_row_id")["product_sk"]

    fact["product_sk"] = (
        fact["_row_id"]
        .map(product_map)
    )

    unresolved = int(
        fact["product_sk"]
        .isna()
        .sum()
    )

    print(
        f"Unresolved product rows: "
        f"{unresolved:,}"
    )

    # Remove temporary column
    fact = fact.drop(
        columns=["_row_id"]
    )
    
    # -----------------------------------------------------
    # Select final columns
    # -----------------------------------------------------

    final_columns = [
        "store_id",
        "business_date",
        "bill_no",
        "line_no",
        "product_code",
        "product_sk",
        "qty",
        "unit_price",
        "line_type",
        "revenue_amount",
        "source_file",
    ]

    fact = fact[
        final_columns
    ]

    # -----------------------------------------------------
    # Clean old S3 data
    # -----------------------------------------------------

    clean_s3_sales()

    # -----------------------------------------------------
    # Write Parquet partitions
    # -----------------------------------------------------

    print()
    print(
        "Writing Parquet partitions to MinIO..."
    )

    partition_count = 0

    fact["year"] = (
        fact["business_date"]
        .dt.year
    )

    fact["month"] = (
        fact["business_date"]
        .dt.month
    )

    grouped = fact.groupby(
        [
            "store_id",
            "year",
            "month"
        ],
        dropna=False
    )

    for (
        store_id,
        year,
        month
    ), group in grouped:

        year = int(year)
        month = int(month)

        # -------------------------------------------------
        # Local output directory
        # -------------------------------------------------

        partition_dir = (
            OUTPUT_DIR
            / "sales"
            / f"store_id={store_id}"
            / f"year={year}"
            / f"month={month:02d}"
        )

        partition_dir.mkdir(
            parents=True,
            exist_ok=True
        )

        parquet_path = (
            partition_dir
            / "data.parquet"
        )

        # Remove helper columns
        output_group = group.drop(
            columns=[
                "year",
                "month"
            ]
        )

        output_group.to_parquet(
            parquet_path,
            index=False
        )

        # -------------------------------------------------
        # MinIO object path
        # -------------------------------------------------

        s3_key = (
            f"sales/"
            f"store_id={store_id}/"
            f"year={year}/"
            f"month={month:02d}/"
            f"data.parquet"
        )

        s3.upload_file(
            str(parquet_path),
            S3_BUCKET,
            s3_key
        )

        partition_count += 1

    # -----------------------------------------------------
    # Revenue
    # -----------------------------------------------------

    total_revenue = float(
        fact["revenue_amount"]
        .sum()
    )

    # -----------------------------------------------------
    # Audit information
    # -----------------------------------------------------

    audit = {
        "source_files": int(
            len(files)
        ),
        "rows_before_dedup": int(
            before
        ),
        "rows_after_dedup": int(
            after
        ),
        "duplicates_removed": int(
            duplicates_removed
        ),
        "unresolved_product_rows": int(
            unresolved
        ),
        "partition_files": int(
            partition_count
        ),
        "total_revenue": total_revenue,
    }

    audit_path = (
        OUTPUT_DIR
        / "load_audit.json"
    )

    with open(
        audit_path,
        "w",
        encoding="utf-8"
    ) as file:

        json.dump(
            audit,
            file,
            indent=2
        )

    # -----------------------------------------------------
    # Final output
    # -----------------------------------------------------

    print()
    print("=" * 70)
    print("LOAD COMPLETE")
    print("=" * 70)

    print(
        f"Source files       : "
        f"{len(files):,}"
    )

    print(
        f"Rows before dedup  : "
        f"{before:,}"
    )

    print(
        f"Rows after dedup   : "
        f"{after:,}"
    )

    print(
        f"Duplicates removed : "
        f"{duplicates_removed:,}"
    )

    print(
        f"Partitions         : "
        f"{partition_count:,}"
    )

    print(
        f"Total revenue      : "
        f"{total_revenue:,.2f}"
    )

    print()
    print(
        f"Audit file: "
        f"{audit_path}"
    )


# =========================================================
# PROGRAM START
# =========================================================

if __name__ == "__main__":
    main()