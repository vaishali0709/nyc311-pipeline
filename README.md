# CryptoPunks NFT Sales Analysis

A SQL analysis of 19,920 CryptoPunk transactions from June 2017 to January 2022, covering roughly $1.9B in trading volume across 6,542 unique NFTs and 4,242 buyer wallets.

The analysis answers 13 business questions using window functions, views, unions, ranking, and temporary tables. It also documents three data quality problems found in the source table and the decisions taken in response — those decisions are the substance of this project, not the queries.

---

## Dataset

| | |
|---|---|
| Source | CryptoPunks secondary market sales |
| Rows | 19,920 |
| Date range | 23 June 2017 – 14 January 2022 |
| Unique NFTs | 6,542 |
| Unique buyer wallets | 4,242 |
| Total volume (priced sales) | $1,909,252,010 |

**Columns:** `buyer_address`, `seller_address`, `eth_price`, `usd_price`, `event_date`, `token_id`, `transaction_hash`, `name`

---

## Repository structure

```
.
├── README.md
├── cryptopunk_sales_analysis.sql    # the analysis — 13 questions
└── data/
    ├── cryptopunk_pricedata.sql     # MySQL dump (schema + data)
    └── cryptopunkdata.csv           # same data as CSV
```

---

## Running this yourself

Requires MySQL 8.0 or later (the analysis uses window functions).

```bash
# 1. create the database
mysql -u root -p -e "CREATE DATABASE IF NOT EXISTS cryptopunk;"

# 2. load the data
mysql -u root -p cryptopunk < data/cryptopunk_pricedata.sql

# 3. run the analysis
mysql -u root -p cryptopunk < cryptopunk_sales_analysis.sql
```

The script is idempotent — running it twice produces the same result and no errors. Views use `CREATE OR REPLACE` and temporary tables are dropped before creation.

---

## Data quality findings

Three problems in the source table affect the answers. Each is handled explicitly in the SQL, with the reasoning in comments beside the query.

### 1. 969 rows are not sales

946 rows have `usd_price` of exactly 0, and 23 more hold dust values such as `1e-18` ETH — a billionth of a billionth of a coin. These are transfers, mints, and wrap/unwrap events sitting in a table named `pricedata`.

They are excluded from every query that computes an average or a price trend. They are **not** hidden: Q.1 reports all three figures so the reader can apply their own definition of "sale."

The effect is not marginal. CryptoPunk #7804 has two rows:

| Date | ETH | USD |
|---|---|---|
| 2021-03-11 | 4,200 | $7,541,310 |
| 2018-01-10 | 0 | $0 |

Averaged together, its "average sale price" becomes $3,770,655 — the midpoint of a real sale and a non-event, describing nothing that ever happened. This is the **top row** of the Q.4 results, so the filter changes the headline answer, not just the tail.

### 2. The same NFT appears under two names

The `name` column mixes two conventions: `CryptoPunk #8970` and `W#8970`, the wrapped version. Wrapping repackaged the original pre-ERC-721 punks so they could trade on modern marketplaces — the asset is identical, only the container changed.

**727 tokens are affected across 1,599 wrapped sales**, producing 7,269 distinct names for only 6,542 real NFTs — 727 NFTs that do not exist.

Grouping by `name` splits a single punk into two rows, and neither is right. Token 8970 reports $126,230 across 14 sales and $969 across 7, when its true average across all 21 is $84,476.

Queries measuring NFT identity (Q.4, Q.9, Q.10) group by `token_id`. Q.6, which describes individual sales, keeps `name` — the wrapped label is accurate for that specific transaction.

### 3. `event_date` is stored as `varchar(255)`

Not a `DATE`. MySQL casts it implicitly, so date functions work, but the column offers no guarantee that its contents are valid dates. It also carries no time component, and some days hold up to 351 sales — so any query ordering by date alone is non-deterministic. Q.3 adds `token_id` as a tiebreaker for this reason.

---

## Key findings

**The market arc is extreme.** Monthly volume runs in the thousands of dollars through 2017–2020, peaks at **$686.8M in August 2021**, then falls to $32.0M by January 2022 — a 95% decline from peak in five months.

**Prices are severely skewed.** 15,544 of 18,951 sales — 78% — fall in the lowest 100-ETH bucket, while the highest bucket holds two sales at 4,200 ETH. The Q.8 histogram uses a log scale because a linear bar renders everything above 200 ETH as a single character.

**Two of the largest sales are identical.** CryptoPunk #7804 and #3100 both sold for 4,200 ETH in March 2021 — $7,541,310 each, matching to the cent. Q.10 uses `RANK()` rather than `ROW_NUMBER()` so both appear; `ROW_NUMBER()` would silently drop one. Three months contain ties, so Q.10 returns 59 rows across 56 months.

**Wallet 0x1919…e685 is a net seller** — 234 purchases against 257 sales across 491 transactions. The two sides sum exactly to the total, which rules out self-dealing; had the wallet ever bought from itself, they would exceed it.

---

## Notes on approach

**Assumptions are stated, not buried.** Q.10 asks which NFT "sold the most" each month. That could mean highest total value or most frequently traded. This analysis uses total USD value and says so in the query comments.

**Averages are reported with their sample size.** An average built on 2 sales is not comparable to one built on 20, and a reader cannot tell the difference without being shown. Q.4 returns `sale_count` alongside `average_price`.

**Filters are documented so they can be reversed.** Excluding zero-price rows is a judgement, not a fact — a reasonable analyst could argue those transfers belong in a count of ownership changes. The comments record what was excluded and why, so anyone who disagrees can change one line and see the alternative.

---

## Known limitations

- **Q.13 trims only one tail.** The estimated-average calculator excludes sales below 10% of the daily average but leaves high outliers intact, so a single large sale still pulls the estimate up. Trimming both tails, or using a median, would be more robust.
- **No time component in the data.** Intraday ordering and true transaction sequence cannot be recovered.
- **USD prices are as-recorded** and are not adjusted for inflation or ETH/USD volatility, both significant across a five-year window.
- **Wrapped and unwrapped sales are merged by `token_id`.** Whether wrapped punks trade at a discount is a real question this analysis does not answer.

---

## Tech

MySQL 8.0 — window functions (`AVG() OVER`, `RANK()`), CTEs and derived tables, views, temporary tables, `UNION ALL`, conditional aggregation, date formatting, string functions.
