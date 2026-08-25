{{ config(
    materialized = 'table',
    partition_by = {'field': 'created_date_day', 'data_type': 'date'},
    cluster_by   = ['agency_code', 'complaint_type']
) }}

/*
  The fact table everything else reads from. One row per request.

  Adds a target response time per complaint type and a flag for whether it
  was met. The city doesn't publish official targets, so these are set here
  and documented as an assumption - see the seed comment below.

  Partitioned by day and clustered by agency so that a query filtering on
  a date range and one agency only scans the blocks it needs. On the free
  tier this is the difference between staying inside the quota and not.
*/

with requests as (

    select * from {{ ref('stg_311_requests') }}

),

with_targets as (

    select
        *,

        /*
          Assumed targets, in hours. These are my own, not the city's.
          Grouped by how urgent the complaint is rather than by agency,
          because the same agency handles both urgent and routine work.
        */
        case
            when upper(complaint_type) in (
                'WATER SYSTEM', 'SEWER', 'GAS LEAK', 'STRUCTURAL',
                'ELEVATOR', 'BUILDING/USE', 'TRAFFIC SIGNAL CONDITION'
            ) then 24
            when upper(complaint_type) in (
                'HEAT/HOT WATER', 'HEATING', 'PLUMBING', 'PAINT/PLASTER',
                'UNSANITARY CONDITION', 'ELECTRIC', 'WATER QUALITY'
            ) then 72
            when upper(complaint_type) in (
                'NOISE', 'NOISE - RESIDENTIAL', 'NOISE - STREET/SIDEWALK',
                'NOISE - COMMERCIAL', 'ILLEGAL PARKING',
                'BLOCKED DRIVEWAY', 'DERELICT VEHICLE'
            ) then 12
            -- anything not named above. Not a real target, just the widest
            -- assumption I'd defend - most types land here, so treat their
            -- missed_target with more suspicion than the named ones.
            else 96
        end as target_hours

    from requests

)

select
    unique_key,
    created_date,
    closed_date,
    created_date_day,
    created_hour,
    created_day_name,
    created_on_weekend,

    agency_code,
    agency_name,
    complaint_type,
    descriptor,
    borough,
    channel,
    status,

    is_closed,
    response_hours,
    target_hours,

    -- only closed requests can be judged; open ones stay null
    case
        when not is_closed        then null
        when response_hours is null then null
        when response_hours > target_hours then true
        else false
    end as missed_target,

    has_invalid_close_date,
    _loaded_at

from with_targets
