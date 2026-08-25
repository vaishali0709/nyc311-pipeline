# Setup

About an hour the first time, most of it waiting.

Do the steps in order. Each one ends with something to check before you move on — if the check fails, fix it there rather than carrying the problem forward. I lost most of a day to that.

---

## What you need

- A Google account
- Python 3.9 or later
- Git

---

## 1. Create a Google Cloud project

1. Go to **console.cloud.google.com**
2. Project dropdown at the top → **New Project**
3. Name it `nyc311-pipeline` → **Create**

Copy the **Project ID**. It's not the name you typed — it looks like `nyc311-pipeline-506418`. You'll need it repeatedly.

### About billing

You can run this without attaching a card. BigQuery calls that **sandbox mode**, and it works — but it silently forces a **60-day partition expiration** on every partitioned table. This project's raw table is partitioned by date, so anything older than 60 days gets deleted while you watch. No error, no warning. I lost a month of data to this before I noticed, and it took a while to work out why, because everything reported success.

If you want history to accumulate, attach a billing account. The free tier stays the same — 10 GB storage, 1 TB of queries per month, and this project uses a fraction of both. What changes is that you can turn the expiration off.

Once billing is on:

```sql
ALTER TABLE `your-project.nyc311_raw.service_requests`
SET OPTIONS (partition_expiration_days = NULL);
```

That command fails in sandbox mode, which is how you find out you're in it.

Two guards worth setting while you're here:

- **`maximum_bytes_billed`** in the dbt profile (already in this repo, set to 10 GB per query). This is the one that actually stops a runaway query — it refuses to run rather than warning you afterwards.
- **A $1 budget alert** at Billing → Budgets & alerts. This only emails you; it doesn't stop anything. Useful, but don't mistake it for a limit.

If you'd rather stay in sandbox, that's fine — you'll have a rolling 60-day window instead of growing history. Just know that's what you have.

**Check:** your project name is in the top bar.

---

## 2. Turn on BigQuery

1. Search bar → **BigQuery**
2. If it asks to enable the API, click **Enable**
3. Wait for BigQuery Studio to load

**Check:** your project appears in the Explorer panel on the left.

---

## 3. Create a service account

This is the identity the pipeline logs in as, so GitHub Actions never needs your password.

1. Search **IAM & Admin** → **Service Accounts**
2. **Create Service Account**, name it `nyc311-pipeline`
3. **Create and Continue**
4. Grant two roles:
   - `BigQuery Data Editor`
   - `BigQuery Job User`
5. **Continue** → **Done**

Then make a key:

6. Click the service account → **Keys** tab
7. **Add Key** → **Create new key** → **JSON** → **Create**
8. A `.json` file downloads. Put it somewhere outside this repo and note the full path.

**That file is a password.** Anyone holding it can use your BigQuery. The `.gitignore` here blocks `*.json`, but keeping the key outside the repo folder entirely is the safer habit.

**Check:** you have the JSON file on disk and know its full path.

---

## 4. Get the code running

```bash
git clone https://github.com/YOUR_USERNAME/nyc311-pipeline.git
cd nyc311-pipeline

python -m venv venv
# Windows:
venv\Scripts\activate
# Mac/Linux:
source venv/bin/activate

pip install -r requirements.txt
```

**Check:** `dbt --version` prints a version.

---

## 5. Point dbt at your project

`dbt/profiles.yml` reads everything from environment variables, so there's nothing to edit. Set two:

```bash
# Windows
set GCP_PROJECT=nyc311-pipeline-506418
set GOOGLE_APPLICATION_CREDENTIALS=C:\path\to\your-key.json

# Mac/Linux
export GCP_PROJECT=nyc311-pipeline-506418
export GOOGLE_APPLICATION_CREDENTIALS=/path/to/your-key.json
```

On Windows, don't put spaces around the `=` and don't wrap the value in quotes — both end up inside the variable and nothing works.

These only live in the terminal window you set them in. New window, set them again.

Then:

```bash
cd dbt
dbt debug
```

**Check:** the last line says `All checks passed!`

Don't continue until it does. Everything after this depends on it.

> **If you'd rather use a static file:** `dbt/profiles.yml.example` is a template with values written out instead of read from the environment. But be aware that dbt checks the current directory **before** `~/.dbt/`, so as long as `dbt/profiles.yml` exists and you're running from inside `dbt/`, that's the file it uses — a copy in `~/.dbt/` will be ignored. Delete or rename `dbt/profiles.yml` first if you want the home-directory one to take effect. This caught me out for a while.

---

## 6. Load the first batch

From the project root, with those two variables still set:

```bash
python ingest/load_311.py
```

The first run pulls 90 days — around 900,000 rows in roughly 100 pages. Budget **30 to 45 minutes** depending on your connection. Each page prints as it goes, and the timestamp on each line tells you how far through the window you are, which is more useful than the page number.

You can stop it with Ctrl+C at any point. Every page is written to BigQuery as it arrives, so nothing already loaded is lost, and re-running picks up where it stopped — the loader reads its position back out of BigQuery rather than keeping it in memory.

If it's taking too long, drop `LOOKBACK_DAYS` from 90 to 30 in `ingest/load_311.py`. 30 days is plenty to work with, and the daily schedule builds history from there.

**Check:** the last line says `loaded N rows into ...`, and BigQuery Studio shows a `nyc311_raw` dataset containing `service_requests`.

---

## 7. Build the models

```bash
cd dbt
dbt deps               # downloads dbt_utils, once
dbt source freshness   # dbt build doesn't do this, it has to be asked for
dbt build              # models and tests together
```

`dbt build` runs each model then immediately tests it and stops if a test fails, so a broken model never feeds the one after it.

**Check:** `Completed successfully`, and BigQuery now shows `nyc311_staging` and `nyc311_marts`.

Then look at what came out:

```sql
SELECT complaint_type, total_requests, median_response_hours, missed_target_pct
FROM `your-project.nyc311_marts.agg_complaint_type_sla`
ORDER BY missed_target_count DESC
LIMIT 15;
```

More queries in [`analysis/findings_queries.sql`](analysis/findings_queries.sql), including the ones that check whether the numbers you're looking at are real.

---

## 8. Read the docs dbt generates

```bash
dbt docs generate
dbt docs serve
```

Opens in a browser with a lineage graph showing how the raw table flows into staging and then into each mart. Worth a screenshot for the README.

---

## 9. Automate it

In GitHub: **Settings** → **Secrets and variables** → **Actions** → **New repository secret**.

| Name | Value |
|---|---|
| `GCP_PROJECT` | your Project ID |
| `GCP_SA_KEY` | the **entire contents** of the JSON key file |

For `GCP_SA_KEY`, open the file in a text editor, select all, paste. It must include the opening and closing braces.

Push, then go to **Actions** → **Daily 311 pipeline** → **Run workflow** to test it by hand rather than waiting for the schedule.

**Check:** a green tick in the Actions tab.

After that it runs itself at 06:00 UTC daily.

---

## Troubleshooting

**`dbt debug` can't find the profile, or uses the wrong one**

dbt looks in the current directory before `~/.dbt/`. Read the `Using profiles.yml file at ...` line in the output — that tells you which file it actually picked, which isn't always the one you edited.

**`dbt debug` fails on Connection test**

Usually the keyfile path. If you're using a static profile, use forward slashes even on Windows: `C:/Users/...`. Check the file exists at that exact path.

**`Compilation Error ... expects 1 package(s) ... found only 0`**

`dbt deps` hasn't run. It fetches `dbt_utils`, and it needs internet.

**`'response_hours' is undefined`**

The macro folder is named wrong. `dbt_project.yml` expects `macros/` — plural. A folder called `macro/` is invisible to dbt. Same class of problem if `models/marts/` or `models/staging/` sit somewhere other than under `models/`. `dbt parse` catches all of these without building anything, so it's worth running after any file moving.

**`403 Access Denied`**

The service account is missing a role. IAM & Admin → IAM → confirm it has both `BigQuery Data Editor` and `BigQuery Job User`.

**`404 Not found: Dataset ... in location US`**

Your dataset is in a different region from what the profile says. Click the dataset in BigQuery Studio, check its location, match the `location:` line.

**`Table nyc311_raw.service_requests was not found`**

Step 6 hasn't run, or ran against a different project. Check `GCP_PROJECT`.

**The loader times out partway through**

Retries and backoff are built in, and it resumes from BigQuery on the next run, so just run it again. If it times out on the same page repeatedly, drop `PAGE_SIZE` in `ingest/load_311.py` from 10,000 to 5,000.

**The loader says "nothing new"**

Everything's already loaded. Correct behaviour.

**Rows load but disappear, or the row count doesn't match what the loader reported**

Partition expiration — see the billing section in step 1. Check it with:

```sql
SELECT option_name, option_value
FROM `your-project.nyc311_raw.INFORMATION_SCHEMA.TABLE_OPTIONS`
WHERE table_name = 'service_requests';
```

If `partition_expiration_days` appears there, that's what's eating your data.

**GitHub Actions fails on the key**

`GCP_SA_KEY` must be the whole JSON file. Copy it from a text editor, not from the browser's download preview.

**A dbt test fails**

Read which one. `unique` on `unique_key` means duplicates survived deduplication. `accepted_values` on borough means the city introduced a value the models don't handle. Both are real findings — investigate before loosening the test, and if you do change it, write down why.

Also worth remembering what tests can't tell you. All 27 of mine passed while the target logic was matching `Electric` against data that says `ELECTRIC`, sending 14,000 requests into the wrong band. `accepted_values` confirmed the answer was in the allowed set. It had no way to check it was the right one.

---

## Costs

Free tier: 10 GB storage, 1 TB of queries per month.

This stores well under 1 GB and a full `dbt build` scans about 130 MB, so a daily run uses roughly half a percent of the monthly query allowance. The partitioning and clustering on the fact table exist partly for that reason.

`maximum_bytes_billed` is set in the dbt profile as a hard per-query ceiling. Budget alerts are worth setting too, but they notify after the fact rather than preventing anything.
