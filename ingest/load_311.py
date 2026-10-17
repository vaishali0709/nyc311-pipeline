"""
Pull NYC 311 service requests from the Socrata API and load them into BigQuery.

Runs incrementally. Each run looks up the most recent created_date already in
BigQuery and asks the API only for records newer than that. The first run has
nothing to look up, so it falls back to LOOKBACK_DAYS.

Paging uses a timestamp cursor, not an offset. Offset paging asks the API to
count and discard everything before the page you want, so page 10 is ten times
slower than page 1 and eventually times out. A cursor says "everything from
this timestamp onward", which the API answers from an index at the same speed
however deep you are.

The cursor is inclusive - >= rather than >. Several requests can share the
same timestamp, and a page boundary can fall in the middle of them. Asking for
> would skip the rest of that second; asking for >= re-fetches the whole second
and hands the overlap to the staging model, which keeps one row per unique_key.
Duplicates are cheap to remove. Missing rows are not recoverable.

Each page is loaded into BigQuery as it arrives. If a run dies halfway, the
pages that already landed stay, and the next run resumes from them - because
the watermark is read back out of BigQuery, not held in memory.

No API key needed.
"""

import os
import sys
import time
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime

import pandas as pd
import requests
from google.cloud import bigquery

API = "https://data.cityofnewyork.us/resource/erm2-nwe9.json"
PAGE_SIZE = 10_000          # Socrata allows 50k, but small pages survive a slow line
LOOKBACK_DAYS = 90          # only used on the very first run
MAX_PAGES = 500             # safety stop so a bad filter can't loop forever
TIMEOUT = 180               # seconds per request
MAX_RETRIES = 5             # total attempts per page, including the first request
MAX_RETRY_DELAY = 120       # cap each pause so an outage cannot stall the job
RETRYABLE_HTTP_STATUSES = {408, 429, 500, 502, 503, 504}

PROJECT = os.environ["GCP_PROJECT"]
DATASET = os.environ.get("BQ_DATASET", "nyc311_raw")
TABLE = "service_requests"
TABLE_ID = f"{PROJECT}.{DATASET}.{TABLE}"

COLUMNS = [
    "unique_key", "created_date", "closed_date", "agency", "agency_name",
    "complaint_type", "descriptor", "status", "borough",
    "incident_zip", "open_data_channel_type", "latitude", "longitude",
]

TIMESTAMP_COLS = ["created_date", "closed_date"]
NUMERIC_COLS = ["latitude", "longitude"]

SCHEMA = [
    bigquery.SchemaField("unique_key", "STRING"),
    bigquery.SchemaField("created_date", "TIMESTAMP"),
    bigquery.SchemaField("closed_date", "TIMESTAMP"),
    bigquery.SchemaField("agency", "STRING"),
    bigquery.SchemaField("agency_name", "STRING"),
    bigquery.SchemaField("complaint_type", "STRING"),
    bigquery.SchemaField("descriptor", "STRING"),
    bigquery.SchemaField("status", "STRING"),
    bigquery.SchemaField("borough", "STRING"),
    bigquery.SchemaField("incident_zip", "STRING"),
    bigquery.SchemaField("open_data_channel_type", "STRING"),
    bigquery.SchemaField("latitude", "FLOAT"),
    bigquery.SchemaField("longitude", "FLOAT"),
    bigquery.SchemaField("_loaded_at", "TIMESTAMP"),
]


def get_watermark(client):
    """Latest created_date already loaded. Falls back to LOOKBACK_DAYS."""
    default = datetime.now(timezone.utc) - timedelta(days=LOOKBACK_DAYS)
    try:
        rows = list(client.query(f"SELECT MAX(created_date) AS m FROM `{TABLE_ID}`").result())
        if rows and rows[0].m:
            print(f"watermark from BigQuery: {rows[0].m}")
            return rows[0].m
    except Exception as e:
        print(f"no existing table ({type(e).__name__}), using {LOOKBACK_DAYS}-day lookback")
    print(f"watermark: {default}")
    return default


def retry_delay(attempt, retry_after=None):
    """Bounded exponential backoff, including Retry-After seconds or HTTP dates."""
    delay = min(10 * 2 ** (attempt - 1), MAX_RETRY_DELAY)
    if retry_after is not None:
        try:
            seconds = int(retry_after)
        except (TypeError, ValueError):
            try:
                retry_at = parsedate_to_datetime(retry_after)
                if retry_at.tzinfo is None:
                    retry_at = retry_at.replace(tzinfo=timezone.utc)
                seconds = (retry_at - datetime.now(timezone.utc)).total_seconds()
            except (TypeError, ValueError, OverflowError):
                return delay
        delay = min(MAX_RETRY_DELAY, max(delay, seconds))
    return delay


def fetch_page(cursor):
    """Retry temporary HTTP/network failures without advancing the page cursor.

    Other HTTP errors fail immediately. Exhausted retries still fail the run,
    leaving already-loaded pages available for the next run to resume from.
    """
    params = {
        "$select": ",".join(COLUMNS),
        "$where": f"created_date >= '{cursor.strftime('%Y-%m-%dT%H:%M:%S')}'",
        "$order": "created_date ASC",
        "$limit": PAGE_SIZE,
    }

    for attempt in range(1, MAX_RETRIES + 1):
        retry_after = None
        try:
            with requests.get(API, params=params, timeout=TIMEOUT) as response:
                response.raise_for_status()
                return response.json()
        except requests.HTTPError as e:
            # An error response is falsy in requests, so check against None.
            if (e.response is None
                    or e.response.status_code not in RETRYABLE_HTTP_STATUSES
                    or attempt == MAX_RETRIES):
                raise
            reason = f"HTTP {e.response.status_code}"
            retry_after = e.response.headers.get("Retry-After")
        except (requests.RequestException, OSError) as e:
            if attempt == MAX_RETRIES:
                raise
            reason = type(e).__name__
        wait = retry_delay(attempt, retry_after)
        print(f"    {reason} - retrying in {wait:g}s "
              f"({attempt}/{MAX_RETRIES - 1})")
        time.sleep(wait)


def clean(df):
    """Make sure every expected column exists and has the right type."""
    for col in COLUMNS:
        if col not in df.columns:
            df[col] = None

    for col in TIMESTAMP_COLS:
        df[col] = pd.to_datetime(df[col], errors="coerce", utc=True)

    for col in NUMERIC_COLS:
        df[col] = pd.to_numeric(df[col], errors="coerce")

    for col in COLUMNS:
        if col not in TIMESTAMP_COLS and col not in NUMERIC_COLS:
            df[col] = df[col].astype("string")

    df["_loaded_at"] = datetime.now(timezone.utc)

    before = len(df)
    df = df[df["created_date"].notna()]
    if before != len(df):
        print(f"    dropped {before - len(df):,} rows with no created_date")

    return df[COLUMNS + ["_loaded_at"]]


def load(client, df):
    """Append one page to BigQuery, creating the table if needed."""
    job_config = bigquery.LoadJobConfig(
        write_disposition="WRITE_APPEND",
        time_partitioning=bigquery.TimePartitioning(field="created_date"),
        clustering_fields=["agency", "complaint_type"],
        schema=SCHEMA,
    )
    client.load_table_from_dataframe(df, TABLE_ID, job_config=job_config).result()


def main():
    client = bigquery.Client(project=PROJECT)
    client.create_dataset(bigquery.Dataset(f"{PROJECT}.{DATASET}"), exists_ok=True)

    cursor = get_watermark(client)
    print(f"fetching requests created from {cursor} onward")

    total = 0
    for page in range(1, MAX_PAGES + 1):
        print(f"  page {page}: requesting from {cursor} ...")
        batch = fetch_page(cursor)
        if not batch:
            break

        # Take the next cursor from the raw response, before cleaning. Cleaning
        # can drop the last row, and losing the cursor with it would re-request
        # the same page forever.
        next_cursor = pd.to_datetime(batch[-1]["created_date"], utc=True)

        df = clean(pd.DataFrame(batch))
        if not df.empty:
            load(client, df)
            total += len(df)

        print(f"    {len(batch):,} fetched, {total:,} loaded so far (through {next_cursor})")

        if len(batch) < PAGE_SIZE:
            break

        # A full page that never gets past one timestamp would ask for the same
        # rows again on the next loop. Step over that second and carry on; the
        # tail of it is lost, but a hang is worse than a handful of rows.
        if next_cursor <= cursor:
            print(f"    {PAGE_SIZE:,} rows share one timestamp - stepping past {cursor}")
            next_cursor = cursor + timedelta(seconds=1)

        cursor = next_cursor
        time.sleep(1)
    else:
        print(f"stopped at MAX_PAGES ({MAX_PAGES}) - run again to pick up the rest")

    if total == 0:
        print("nothing new - exiting cleanly")
    else:
        print(f"loaded {total:,} rows into {TABLE_ID}")
        print("overlapping rows are expected - stg_311_requests keeps one per unique_key")
    return 0


if __name__ == "__main__":
    sys.exit(main())
