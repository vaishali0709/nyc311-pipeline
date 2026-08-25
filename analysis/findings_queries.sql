/*
  Queries behind the Findings section of the README.

  Run against the marts after `dbt build`. Replace the project id if you've
  built this somewhere else. Written to be run one at a time in the BigQuery
  console rather than as a script.
*/


-- 1. Row counts and date range, raw against fct. This is the one that caught
--    the partition-expiration problem - raw held far fewer rows than the
--    loader had reported writing.
select 'raw' as layer,
       count(*)                           as total_rows,
       count(distinct unique_key)         as distinct_keys,
       count(distinct date(created_date)) as distinct_days,
       min(created_date)                  as earliest,
       max(created_date)                  as latest
from `nyc311-pipeline-506418.nyc311_raw.service_requests`

union all

select 'fct',
       count(*),
       count(distinct unique_key),
       count(distinct created_date_day),
       min(created_date),
       max(created_date)
from `nyc311-pipeline-506418.nyc311_marts.fct_service_requests`;


-- 2. Where the partitions start. A table fed continuously ramps up at the
--    beginning; a hard cliff on the oldest partition meant something was
--    deleting from the back.
select partition_id, total_rows
from `nyc311-pipeline-506418.nyc311_raw.INFORMATION_SCHEMA.PARTITIONS`
where table_name = 'service_requests'
order by partition_id
limit 10;


-- 3. Partition expiration setting. Sandbox mode forces 60 days silently.
select option_name, option_value
from `nyc311-pipeline-506418.nyc311_raw.INFORMATION_SCHEMA.TABLE_OPTIONS`
where table_name = 'service_requests';


-- 4. Agency picture. HAVING because a percentage off a few hundred requests
--    swings on a handful of slow cases.
--
--    avg_of_daily_medians is the average of each day's median, not the median
--    of everything - this table is one row per agency per day, so the true
--    overall median only comes from the fact table.
select agency_code,
       any_value(agency_name)                                          as agency_name,
       sum(requests_opened)                                            as total_requests,
       round(100 * sum(missed_target_count) / sum(requests_closed), 1) as missed_pct,
       round(avg(median_response_hours), 1)                            as avg_of_daily_medians
from `nyc311-pipeline-506418.nyc311_marts.agg_agency_performance`
group by agency_code
having total_requests >= 5000
order by total_requests desc;


-- 5. Complaint types ranked by missed volume rather than miss rate. A 100%
--    miss rate on 60 requests is smaller than 80% on 25,000.
select complaint_type,
       total_requests,
       median_response_hours,
       p90_response_hours,
       missed_target_pct
from `nyc311-pipeline-506418.nyc311_marts.agg_complaint_type_sla`
order by missed_target_count desc
limit 15;


-- 6. Weekend vs weekday across all requests. Weekend comes out faster here.
select created_on_weekend,
       count(*)                                                    as requests,
       round(approx_quantiles(response_hours, 100)[offset(50)], 1) as median_hours,
       round(100 * countif(missed_target)
             / countif(missed_target is not null), 1)              as missed_pct
from `nyc311-pipeline-506418.nyc311_marts.fct_service_requests`
group by created_on_weekend;


-- 7. Same split inside one complaint type at a time, and it reverses - both
--    types are slower at the weekend. Query 6 said otherwise because weekends
--    are disproportionately illegal parking, which is fast regardless.
select complaint_type,
       created_on_weekend,
       count(*)                                                    as requests,
       round(approx_quantiles(response_hours, 100)[offset(50)], 1) as median_hours
from `nyc311-pipeline-506418.nyc311_marts.fct_service_requests`
where complaint_type in ('Illegal Parking', 'UNSANITARY CONDITION')
group by complaint_type, created_on_weekend
order by complaint_type, created_on_weekend;


-- 8. Channel mix. Phone comes out eight times slower than the mobile app.
--    UNKNOWN is ~9% of everything.
select channel,
       count(*)                                                    as requests,
       round(100 * count(*) / sum(count(*)) over (), 1)            as pct,
       round(approx_quantiles(response_hours, 100)[offset(50)], 1) as median_hours
from `nyc311-pipeline-506418.nyc311_marts.fct_service_requests`
group by channel
order by requests desc;


-- 9. Channel inside one complaint type at a time - the same decomposition as
--    query 7, applied to query 8. NOT YET RUN. I expect the gap to mostly
--    close, which is why the README treats the channel result as a suspicion.
select complaint_type,
       channel,
       count(*)                                                    as requests,
       round(approx_quantiles(response_hours, 100)[offset(50)], 1) as median_hours
from `nyc311-pipeline-506418.nyc311_marts.fct_service_requests`
where complaint_type in ('Illegal Parking', 'UNSANITARY CONDITION')
  and channel in ('PHONE', 'MOBILE', 'ONLINE')
group by complaint_type, channel
order by complaint_type, requests desc;


-- 10. Borough. Staten Island's median is nearly triple the others while its
--     p90 sits mid-pack, so the slow tail isn't slower there - only the fast
--     half is less fast.
--
--     UNSPECIFIED is 0.1% of volume with 39% still open against 12-16%
--     everywhere else.
select borough,
       count(*)                                                    as requests,
       round(100 * countif(not is_closed) / count(*), 1)           as still_open_pct,
       round(approx_quantiles(response_hours, 100)[offset(50)], 1) as median_hours,
       round(approx_quantiles(response_hours, 100)[offset(90)], 1) as p90_hours
from `nyc311-pipeline-506418.nyc311_marts.fct_service_requests`
group by borough
order by requests desc;


-- 11. How much volume sits in the 96-hour catch-all band. Most complaint
--     types land there, and 96 is a default rather than a target.
select target_hours,
       count(distinct complaint_type) as complaint_types,
       count(*)                       as requests
from `nyc311-pipeline-506418.nyc311_marts.fct_service_requests`
group by target_hours
order by target_hours;
