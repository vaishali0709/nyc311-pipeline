# NYC 311 Service Request Pipeline

New York City publishes every 311 service request as open data — noise complaints, heating failures, potholes, blocked driveways. Around 3 million a year, updated continuously.

I built a pipeline that pulls them daily, models them, tests them, and answers one question:

> **Which kinds of request are we slowest on, and where is it getting worse?**

It runs on its own every morning. The green ticks in the Actions tab are real runs against live data.

**Stack:** Python · BigQuery · dbt · GitHub Actions

Currently holding **~632,000 requests**, 26 June 2026 to 23 August 2026. The start date isn't arbitrary — see *A month of data disappeared*.

---

## How it works

```
NYC Open Data (Socrata API)
        │
        │  ingest/load_311.py — incremental, cursor-paged
        ▼
BigQuery: nyc311_raw.service_requests          ← append-only, untouched
        │
        │  dbt
        ▼
nyc311_staging.stg_311_requests                ← deduplicated, typed, response time
        │
        ▼
nyc311_marts.fct_service_requests              ← one row per request, with targets
        │
        ├── agg_agency_performance             ← per agency per day
        ├── agg_complaint_type_sla             ← per complaint type
        └── agg_borough_daily                  ← per borough per day, 7-day rolling
```

Every model has tests attached. `dbt build` runs a model then immediately tests it and stops if a test fails, so a broken model never feeds the one after it.

---

## Decisions I made along the way

**The raw table is never modified.** The loader appends and nothing else. If a transformation turns out to be wrong, I can rebuild from raw.

**Loading is incremental.** Each run asks BigQuery for the most recent `created_date` it already holds, then requests only what's newer.

**Paging uses a cursor, not an offset.** My first version asked for `$offset=0`, then `50000`, then `100000`. Socrata answers an offset by counting and discarding everything before it, so each page is slower than the last — page 1 came back in seconds, page 11 timed out at two minutes and killed the run. The cursor version asks "everything from this timestamp onward", which the API answers from an index at constant speed. 98 pages, no retries.

**The cursor is inclusive.** Several requests can share a timestamp and a page boundary can land in the middle of them, so `> last_seen` would skip the rest of that second permanently. `>=` re-fetches the whole second and passes the overlap to staging. Measured cost: 92 duplicate rows out of 632,000. I'd rather delete duplicates than lose rows.

**Each page loads as it arrives.** My first version accumulated everything in memory and wrote once at the end, so the page-11 timeout threw away ten pages of work. Now a dead run keeps whatever landed and the next run resumes from it, because the watermark is read back out of BigQuery rather than held in a variable.

**Duplicates are deduplicated in staging.** The staging model keeps the most recently loaded copy of each `unique_key`, with a `unique` test on it — so if deduplication breaks, the build fails instead of double-counting.

**Response time is calculated in minutes, then divided.** `TIMESTAMP_DIFF(..., HOUR)` truncates: a request closed in 3 hours 55 minutes returns 3, turning a missed 4-hour target into a met one. Minutes ÷ 60 keeps the fraction.

**Impossible rows are flagged, not deleted.** A few requests close before they open. They carry `has_invalid_close_date` and are excluded from response-time maths, so the count stays visible.

**Open requests get `NULL`, not `false`.** A request still open hasn't missed its target yet.

**The fact table is partitioned and clustered** — by day, and by agency and complaint type. A full `dbt build` scans about 130 MB.

---

## The targets are mine, not the city's

New York doesn't publish official response-time targets per complaint type, so `fct_service_requests` sets its own, grouped by urgency:

| Band | Hours | Examples |
|---|---|---|
| Urgent | 12 | Noise complaints, illegal parking, blocked driveway |
| Same day | 24 | Gas leak, water system, elevator, structural |
| Standard | 72 | Heat/hot water, plumbing, electric, unsanitary condition |
| Routine | 96 | Everything else |

**Every "missed target" number below depends on these.** They live in one place in the model, so changing them is a one-line edit and a rebuild.

Most of the hundred-plus complaint types land in "everything else" at 96 hours. That band is a default, not a target, and I've treated its numbers as weaker evidence throughout.

Queries behind everything below: [`analysis/findings_queries.sql`](analysis/findings_queries.sql).

---

## Findings

**Interactive dashboard:** [Tableau Public](https://public.tableau.com/app/profile/vaishali.singh6053/viz/NYC311ResponseTimeAnalysis/NYC311ResponseTimes)

The dashboard runs on the current data, so its numbers have moved on a little from the tables below — the pipeline keeps loading every morning. 

The agency figures also differ because the table below averages each day's median, while the dashboard takes the median across everything.

### The city runs on at least two different clocks

| Agency | Requests | Avg of daily medians | Missed target |
|---|---|---|---|
| NYPD | 288,910 | 1.3 h | 3.3% |
| HPD | 102,110 | 155.1 h | 73.1% |
| DSNY | 56,167 | 41.8 h | 19.1% |
| DEP | 49,297 | 17.7 h | 26.2% |
| DOT | 44,644 | 43.8 h | 35.7% |
| DPR | 33,873 | 94.7 h | 42.4% |
| DOB | 22,557 | 31.6 h | 33.9% |
| DOHMH | 16,002 | 121.1 h | 54.4% |
| DHS | 7,328 | 14.1 h | 3.4% |
| TLC | 5,832 | 61.9 h | 15.6% |

NYPD handles 46% of everything at a 1.3-hour median. HPD takes 155. I don't read that as NYPD performing better.

Most of NYPD's volume is illegal parking — 94,689 requests, median 1.5 hours, the largest single complaint type in the data. A car gets towed, or ticketed, or has already gone by the time anyone arrives; either way the case closes. HPD does housing repair: notice to the landlord, an inspection appointment, access to an occupied apartment, a re-inspection.

DHS is the check on that reading — 14.1 hours, 3.4% missed, and not a police agency. DOHMH sits with HPD at 121 hours and 54%, and its work is inspection-driven too. The split tracks the kind of work rather than the agency.

Which means these are different jobs on a single clock, and my own 12/24/72/96 bands do the same thing at smaller scale. HPD's 73.1% is partly a statement about the 72 I chose.

### Three patterns that dissolved when I split them

**Weekend requests looked faster.** Filed on a weekend: median 2.3 hours, 15.0% missed. Filed on a weekday: 4.7 hours, 22.9% missed.

Split by complaint type and it reverses:

| Complaint type | Weekday median | Weekend median |
|---|---|---|
| Illegal Parking | 1.4 h | 1.7 h |
| Unsanitary Condition | 212.9 h | 226.8 h |

Both are slower at the weekend. The aggregate said otherwise because weekends skew toward illegal parking, which is fast whenever it's filed — every subgroup moving one way while the total moves the other. Simpson's paradox, in live municipal data.

**Channel looked like it mattered.** Online is half of all requests at a 3.1-hour median, mobile 19% at 1.7 hours, phone 22% at 24 hours — phone eight times slower than the app.

I don't think that's a channel effect. People pick the channel to fit the problem: you photograph a badly parked car from your phone, you sit down and call about your heating. That's complaint mix again. I haven't decomposed this one the way I did weekends, so it stays a suspicion rather than a result; the query that would settle it is in `analysis/`, unrun.

Separately, 8.9% of requests (56,552) have no channel recorded, so any channel conclusion is silent about an eleventh of the data.

**Staten Island looked slow.** Median 8.6 hours against 3.2–4.3 elsewhere.

| Borough | Requests | Still open | Median | p90 |
|---|---|---|---|---|
| Brooklyn | 194,270 | 15.5% | 3.2 h | 196.8 h |
| Queens | 162,233 | 12.6% | 3.3 h | 151.9 h |
| Bronx | 131,273 | 13.1% | 4.3 h | 194.3 h |
| Manhattan | 117,553 | 15.7% | 3.4 h | 213.3 h |
| Staten Island | 25,805 | 15.1% | 8.6 h | 194.8 h |
| Unspecified | 778 | 39.2% | 26.8 h | 241.0 h |

The p90 column is flat — every borough between 152 and 213 hours, Staten Island mid-pack at 194.8. If work were slower there, the slow tail would be slower too. Staten Island just has proportionally fewer instant-close street complaints (less kerbside parking, more driveways), which lifts the median and leaves the tail alone.

### Medians hide the requests that hurt

| Complaint type | Requests | Median | p90 | Missed |
|---|---|---|---|---|
| Illegal Parking | 94,689 | 1.5 h | 7.5 h | 4.0% |
| Unsanitary Condition | 25,548 | 215.1 h | 548.0 h | 88.8% |
| Street Condition | 18,564 | 43.5 h | 188.2 h | 29.4% |
| Plumbing | 13,741 | 123.2 h | 484.2 h | 67.6% |
| Dirty Condition | 12,947 | 49.0 h | 142.0 h | 21.7% |
| Damaged Tree | 12,243 | 53.7 h | 354.0 h | 33.4% |
| Paint/Plaster | 11,096 | 157.1 h | 482.1 h | 79.7% |
| Water Leak | 8,841 | 197.0 h | 530.9 h | 78.1% |
| General | 8,319 | 228.3 h | 606.1 h | 78.8% |
| Door/Window | 8,200 | 213.8 h | 568.2 h | 80.5% |
| Noise | 8,027 | 65.6 h | 235.7 h | 90.3% |
| Maintenance or Facility | 6,460 | 217.9 h | 601.5 h | 71.2% |
| Electric | 6,090 | 126.2 h | 474.8 h | 71.5% |
| Rodent | 5,562 | 190.8 h | 502.5 h | 76.8% |
| Elevator | 5,423 | 27.6 h | 399.1 h | 51.2% |

Elevator is the sharpest case: median 27.6 hours, p90 399. Over half handled inside a day, the worst tenth taking more than two weeks. Illegal parking is the opposite shape — 1.5 and 7.5, tight and predictable. Both columns are in every aggregate for that reason.

### 778 requests nobody can route

The Unspecified borough row is 0.1% of volume, with **39.2% still open** against 12–16% everywhere else and a 26.8-hour median against 3–4.

These are requests where the location never resolved to a borough — incomplete address, or a point that didn't map — and they're also the ones sitting unresolved. A request that lost the information needed to route it doesn't reach anyone properly.

---

## Two things that were quietly wrong

Both produced clean, green, entirely wrong output. Neither was caught by a test.

### A capital letter moved 14,000 requests into the wrong band

The target logic matched complaint types against string lists. The data has `ELECTRIC`. My list had `Electric`. SQL string comparison is case-sensitive, so it never matched and 6,090 electrical complaints fell through to the 96-hour default. Plain `Noise` — 8,027 requests — wasn't in any list at all and did the same.

The `accepted_values` test on `target_hours` passed throughout, because 96 is an accepted value. The test checks that the answer sits in the allowed set, not that it's the right member of it.

I fixed it by wrapping the comparison in `upper()` and uppercasing the lists, so the match survives whatever casing the city sends next.

| | Before | After |
|---|---|---|
| Noise missed target | 37.6% | 90.3% |
| Electric missed target | 61.9% | 71.5% |
| Illegal Parking missed target | 4.0% | 4.0% |

Illegal Parking holding still was my check that the fix hadn't broken something already correct.

### A month of data disappeared

The loader reported 970,572 rows loaded from 26 May onward. The table held 642,959, starting 25 June. Around 328,000 rows — the entire oldest month — were gone, and nothing had errored.

BigQuery's sandbox mode, the free tier before you attach a billing account, forces a **60-day partition expiration** on every partitioned table. My fact table is partitioned by `created_date`, so partitions aged past 60 days were being deleted about as fast as they were written.

I found it by accident. An aggregate came out at 360 rows when 6 boroughs × 90 days should have been 540. Chasing that number led to `INFORMATION_SCHEMA.PARTITIONS`, which showed the table starting at a hard cliff with no ramp — data doesn't arrive that way.

Fixed by enabling billing and setting `partition_expiration_days = NULL`. History accumulates properly now. The scar is that this repo's data starts 26 June instead of 26 May, and I've left it rather than backfilling over it.

I'd written "the raw table is never modified" in this README before I noticed it was being modified — by a platform default I hadn't read.

---

## Running it yourself

Full walkthrough in [SETUP.md](SETUP.md). Short version:

```bash
pip install -r requirements.txt

export GCP_PROJECT=your-project-id
export GOOGLE_APPLICATION_CREDENTIALS=/path/to/key.json

python ingest/load_311.py

cd dbt
dbt deps
dbt source freshness
dbt build
```

**On cost:** BigQuery's free tier is 10 GB of storage and 1 TB of query scanning per month, and this uses a rounding error of both — a full `dbt build` scans about 130 MB. But sandbox mode caps partition retention at 60 days, so keeping history means attaching a billing account. I've set `maximum_bytes_billed` in the dbt profile as a hard per-query ceiling; budget alerts only email you after the money's spent.

```
.
├── README.md
├── SETUP.md
├── requirements.txt
├── analysis/
│   └── findings_queries.sql      # queries behind the Findings section
├── ingest/
│   └── load_311.py
├── dbt/
│   ├── dbt_project.yml
│   ├── packages.yml
│   ├── package-lock.yml          # pins dbt_utils version
│   ├── profiles.yml              # reads env vars, for CI
│   ├── profiles.yml.example      # template for local use
│   ├── macros/
│   │   └── response_hours.sql
│   └── models/
│       ├── staging/
│       │   ├── _sources.yml      # source definitions + freshness checks
│       │   ├── _staging.yml      # tests
│       │   └── stg_311_requests.sql
│       └── marts/
│           ├── _marts.yml
│           ├── fct_service_requests.sql
│           ├── agg_agency_performance.sql
│           ├── agg_complaint_type_sla.sql
│           └── agg_borough_daily.sql
└── .github/workflows/
    └── daily_pipeline.yml
```

---

## Tests

| Test | What it catches |
|---|---|
| `unique` on `unique_key` | Deduplication broke and requests are being double-counted |
| `not_null` on `created_date`, `agency_code` | Source feed changed shape |
| `accepted_values` on `borough` | City introduced a value the models don't handle |
| `accepted_values` on `target_hours` | Someone edited the target logic and left a gap |
| `accepted_range` on `missed_target_pct` | A percentage came out above 100 or below 0 — usually a division bug |
| `unique_combination_of_columns` | An aggregate produced more than one row per group |
| `source freshness` | The daily load stopped running and nobody noticed |

The freshness check is the one I care most about, because a pipeline that silently stops keeps showing yesterday's numbers as if they were today's. It also has to be asked for — `dbt build` doesn't run freshness checks, so the workflow calls `dbt source freshness` as its own step.

The case-sensitivity bug above is what the rest of these can't catch. 27 passing tests told me nothing was malformed.

---

## What this doesn't do

**No official targets.** Everything about missed targets rests on values I chose.

**Response time isn't resolution quality.** A request closed in an hour might have been closed without being fixed. The data records when the ticket shut, not whether the problem went away.

**Business hours aren't accounted for.** A complaint filed at 11pm Friday has the weekend counted against it. Splitting elapsed time from working time needs a calendar table, and given that within-type weekend response is measurably slower, it's the gap I'd close first.

**Timestamps are stored as the city sends them.** The feed publishes naive local timestamps and they load as-is. Hour-of-day and day-of-week analysis is correct; comparing against a live UTC clock would be off by New York's offset.

**Boroughs vary enormously in size.** Raw counts across them say more about population than performance, so `agg_borough_daily` reports rates alongside counts.

**Reopened requests aren't tracked.** The feed doesn't expose them, so a request closed and reopened looks like two separate requests.

**Sixty days is a short window.** Enough for the distributional findings above, not enough for seasonality. Heating complaints in February would look nothing like August.

---

## Stack notes

**Python** — ingestion only. Watermark lookup, cursor paging with retry and backoff, type coercion, per-page load.

**BigQuery** — partitioned and clustered to keep the daily build cheap, with a per-query byte ceiling set in the dbt profile.

**dbt** — staging and marts, with tests and generated documentation. `dbt docs generate` produces a lineage graph tracing every column back to source.

**GitHub Actions** — daily cron plus manual trigger. Credentials live in repository secrets, reach the shell through `env` rather than being interpolated into the command, and the key file is deleted after every run whether it succeeded or not.
