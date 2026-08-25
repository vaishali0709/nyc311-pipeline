{{ config(materialized='view') }}

/*
  Cleans up the raw feed and works out how long each request took.

  Three things happen here:

  1. The raw table is append-only, so re-running the loader can bring in a
     request twice. ROW_NUMBER keeps the most recently loaded copy of each
     unique_key.

  2. Requests closed before they were opened are impossible. There are a
     handful in the source. They're flagged rather than deleted, so the
     count stays visible instead of quietly shrinking.

  3. Response time is calculated in minutes and divided, not in hours.
     TIMESTAMP_DIFF with HOUR truncates and would make near-misses look
     like hits.
*/

with source as (

    select * from {{ source('nyc311_raw', 'service_requests') }}

),

deduplicated as (

    select
        *,
        row_number() over (
            partition by unique_key
            order by _loaded_at desc
        ) as _row_num
    from source

),

cleaned as (

    select
        unique_key,
        created_date,
        closed_date,

        upper(trim(agency))                                  as agency_code,
        trim(agency_name)                                    as agency_name,
        trim(complaint_type)                                 as complaint_type,
        trim(descriptor)                                     as descriptor,
        lower(trim(status))                                  as status,

        -- the source uses 'Unspecified' and blanks interchangeably
        case
            when borough is null or trim(borough) = '' then 'UNSPECIFIED'
            when upper(trim(borough)) = 'UNSPECIFIED'  then 'UNSPECIFIED'
            else upper(trim(borough))
        end                                                  as borough,

        incident_zip,
        open_data_channel_type                               as channel,
        latitude,
        longitude,

        -- calendar parts, so downstream models don't repeat the extraction
        date(created_date)                                   as created_date_day,
        extract(hour      from created_date)                 as created_hour,
        format_timestamp('%A', created_date)                 as created_day_name,
        extract(dayofweek from created_date) in (1, 7)       as created_on_weekend,

        closed_date is not null                              as is_closed,

        case
            when closed_date is null then null
            when closed_date < created_date then null        -- impossible, see below
            else {{ response_hours('created_date', 'closed_date') }}
        end                                                  as response_hours,

        closed_date is not null
            and closed_date < created_date                   as has_invalid_close_date,

        _loaded_at

    from deduplicated
    where _row_num = 1

)

select * from cleaned
