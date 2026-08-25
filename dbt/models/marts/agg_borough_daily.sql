{{ config(materialized='table') }}

/*
  One row per borough per day. Used for the "is anywhere getting worse"
  question and for anything mapped.
*/

select
    created_date_day                              as report_date,
    borough,

    count(*)                                      as requests_opened,
    countif(is_closed)                            as requests_closed,
    round(avg(response_hours), 1)                 as avg_response_hours,

    countif(missed_target)                        as missed_target_count,
    round(100 * safe_divide(
        countif(missed_target),
        countif(missed_target is not null)
    ), 1)                                         as missed_target_pct,

    -- 7-day rolling average smooths out the weekday/weekend sawtooth
    round(avg(avg(response_hours)) over (
        partition by borough
        order by created_date_day
        rows between 6 preceding and current row
    ), 1)                                         as avg_response_hours_7d

from {{ ref('fct_service_requests') }}
group by report_date, borough
