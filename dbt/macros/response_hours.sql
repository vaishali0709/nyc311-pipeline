{#
  Hours between two timestamps, as a decimal.

  TIMESTAMP_DIFF with HOUR truncates - a request closed in 3 hours 55 minutes
  comes back as 3, which makes a 4-hour target look met when it was missed.
  Working in minutes and dividing keeps the fraction.
#}

{% macro response_hours(start_col, end_col) %}
    ROUND(TIMESTAMP_DIFF({{ end_col }}, {{ start_col }}, MINUTE) / 60.0, 2)
{% endmacro %}
