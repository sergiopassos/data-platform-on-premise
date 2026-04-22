{{ config(materialized='table') }}

SELECT
    c.customer_id,
    c.email,
    COUNT(o.order_id)                    AS total_orders,
    SUM(CAST(o.amount AS DOUBLE))        AS lifetime_value,
    MAX(o._ingested_at)                  AS last_order_at
FROM {{ source('silver', 'customers') }} c
LEFT JOIN {{ source('silver', 'orders') }} o
    ON o.customer_id = c.customer_id
    AND o._cdc_op != 'D'
WHERE c._cdc_op != 'D'
GROUP BY 1, 2
