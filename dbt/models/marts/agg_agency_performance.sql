{{ config(materialized='table') }}

/*
  One row per agency per day.

  Uses countif style conditional aggregation so the whole thing
  is a single pass over the fact table.
*/

select
    created_date_day                                        as report_date,
    agency_code,
    any_value(agency_name)                                  as agency_name,

    count(*)                                                as requests_opened,
    countif(is_closed)                                      as requests_closed,
    countif(not is_closed)                                  as still_open,

    round(avg(response_hours), 1)                           as avg_response_hours,
    round(approx_quantiles(response_hours, 100)[offset(50)], 1) as median_response_hours,
    round(approx_quantiles(response_hours, 100)[offset(90)], 1) as p90_response_hours,

    countif(missed_target)                                  as missed_target_count,
    round(100 * safe_divide(
        countif(missed_target),
        countif(missed_target is not null)
    ), 1)                                                   as missed_target_pct,

    countif(created_on_weekend)                             as opened_on_weekend

from {{ ref('fct_service_requests') }}
group by report_date, agency_code
