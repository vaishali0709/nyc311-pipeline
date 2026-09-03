# Dashboard

**[NYC 311 Response Times on Tableau Public](https://public.tableau.com/app/profile/vaishali.singh6053/viz/NYC311ResponseTimeAnalysis/NYC311ResponseTimes)**

Four views, each one a finding from the main README:

- **Two Different Clocks** — median response time by agency, bars sized by request volume. NYPD carries the most work and closes fastest; HPD takes far longer. The sizing is the point — without it the chart reads as a performance ranking, which it isn't.
- **Median vs P90** — both figures side by side per complaint type. Elevator complaints sit mid-table on median and near the top on p90.
- **Weekend Reversal** — weekends look faster in aggregate and are slower inside each complaint type.
- **Borough Comparison** — median, p90 and share still open. Staten Island's median stands out; its p90 doesn't.

## The data behind it

Four small CSVs, one per view, exported from `fct_service_requests`:

| File | Rows | Used by |
|---|---|---|
| [`1_agency.csv`](1_agency.csv) | 9 | Two Different Clocks |
| [`2_complaint_type.csv`](2_complaint_type.csv) | 36 | Median vs P90 |
| [`3_weekend.csv`](3_weekend.csv) | 6 | Weekend Reversal |
| [`4_borough.csv`](4_borough.csv) | 6 | Borough Comparison |

The queries that produced them are in [`../analysis/findings_queries.sql`](../analysis/findings_queries.sql).

## Why four files instead of one

My first attempt was a single export grouped by date, agency, complaint type, borough and channel — one flexible extract that could drive every chart and cross-filter between them.

It gave wrong medians. Elevator came out at 46.9 hours against a real 27.6.

Counts and sums can be added up. Medians cannot. Once a median has been computed for a group, the individual response times behind it are gone, and no weighting scheme brings them back — combining a median of 190 with a median of 1.4 doesn't produce the median of the two groups together. Tableau had no way to get the right answer because the right answer wasn't in the file.

So each percentile is now computed in BigQuery at exactly the grain it's displayed at. The cost is that the views can't cross-filter each other. For a portfolio dashboard, correct numbers matter more than interactivity.
