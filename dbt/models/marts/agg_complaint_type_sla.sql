{{ config(materialized='table') }}

/*
  One row per complaint type, across the whole loaded history.

  This is the table that answers the project's main question: which kinds of
  request are we slowest on, and how much volume sits behind each one.

  Complaint types with fewer than 50 closed requests are excluded. On a small
  base one slow request moves the percentage several points, and that reads
  as a pattern when it is noise.
*/

with by_type as (

    select
        complaint_type,
        any_value(target_hours)                                    as target_hours,

        count(*)                                                   as total_requests,
        countif(is_closed)                                         as closed_requests,
        countif(not is_closed)                                     as open_requests,

        round(avg(response_hours), 1)                              as avg_response_hours,
        round(approx_quantiles(response_hours, 100)[offset(50)], 1) as median_response_hours,
        round(approx_quantiles(response_hours, 100)[offset(90)], 1) as p90_response_hours,

        countif(missed_target)                                     as missed_target_count,
        round(100 * safe_divide(
            countif(missed_target),
            countif(missed_target is not null)
        ), 1)                                                      as missed_target_pct

    from {{ ref('fct_service_requests') }}
    group by complaint_type

)

select
    *,
    -- share of the requests we kept, not of every request in the data -
    -- the 50-closed filter below runs first, so rare types are already gone
    round(100 * total_requests / sum(total_requests) over (), 2) as pct_of_all_requests,
    rank() over (order by missed_target_pct desc)                as rank_by_miss_rate,
    rank() over (order by missed_target_count desc)              as rank_by_miss_volume
from by_type
where closed_requests >= 50
