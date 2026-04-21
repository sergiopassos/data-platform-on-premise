{{ config(materialized='table') }}

SELECT
    DATE_TRUNC('day', o._ingested_at) AS order_date,
    o.status,
    COUNT(*) AS order_count,
    SUM(CAST(o.amount AS DOUBLE)) AS total_amount
FROM {{ source('silver', 'orders') }} o
WHERE o._cdc_op != 'D'
GROUP BY 1, 2
