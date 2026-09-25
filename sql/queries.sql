-- =============================================================================
-- Retail Analytics: hand-written analysis queries (SQLite dialect)
--
-- Conventions
--   * Revenue = SUM(order_items.line_revenue) on COMPLETED orders only.
--     Refunded and cancelled orders are excluded from revenue metrics.
--   * Each query starts with a "-- name: <id>" marker so src/analysis.py can
--     split this file and run the queries one at a time.
-- =============================================================================


-- name: monthly_revenue
-- 1. Monthly revenue trend (completed orders only)
SELECT
    strftime('%Y-%m', o.order_date)                         AS month,
    COUNT(DISTINCT o.order_id)                              AS orders,
    ROUND(SUM(oi.line_revenue), 2)                          AS revenue,
    ROUND(SUM(oi.line_revenue) / COUNT(DISTINCT o.order_id), 2) AS avg_order_value
FROM orders o
JOIN order_items oi ON oi.order_id = o.order_id
WHERE o.status = 'completed'
GROUP BY month
ORDER BY month;


-- name: top_products
-- 2. Top 10 products by revenue
SELECT
    p.product_name,
    p.category,
    SUM(oi.quantity)                    AS units_sold,
    ROUND(SUM(oi.line_revenue), 2)      AS revenue,
    ROUND(100.0 * SUM(oi.line_revenue)
          / (SELECT SUM(oi2.line_revenue)
             FROM order_items oi2
             JOIN orders o2 ON o2.order_id = oi2.order_id
             WHERE o2.status = 'completed'), 2) AS pct_of_revenue
FROM order_items oi
JOIN orders   o ON o.order_id   = oi.order_id
JOIN products p ON p.product_id = oi.product_id
WHERE o.status = 'completed'
GROUP BY p.product_id, p.product_name, p.category
ORDER BY revenue DESC
LIMIT 10;


-- name: regional_performance
-- 3. Regional performance: revenue, order count and average order value
--    (region of the store where the order was placed)
WITH order_totals AS (
    SELECT
        o.order_id,
        s.region,
        SUM(oi.line_revenue) AS order_total
    FROM orders o
    JOIN stores      s  ON s.store_id  = o.store_id
    JOIN order_items oi ON oi.order_id = o.order_id
    WHERE o.status = 'completed'
    GROUP BY o.order_id, s.region
)
SELECT
    region,
    COUNT(*)                                                AS orders,
    ROUND(SUM(order_total), 2)                              AS revenue,
    ROUND(AVG(order_total), 2)                              AS avg_order_value,
    ROUND(100.0 * SUM(order_total) / SUM(SUM(order_total)) OVER (), 2) AS pct_of_revenue
FROM order_totals
GROUP BY region
ORDER BY revenue DESC;


-- name: customer_segments
-- 4. RFM-style customer value segmentation.
--    Customers are ranked by total completed spend and split into equal
--    thirds: High, Mid and Low value. Recency is measured in days from the
--    last day in the dataset.
WITH customer_rfm AS (
    SELECT
        c.customer_id,
        MAX(o.order_date)                                       AS last_order_date,
        COUNT(DISTINCT o.order_id)                              AS frequency,
        COALESCE(SUM(oi.line_revenue), 0)                       AS monetary
    FROM customers c
    LEFT JOIN orders o
           ON o.customer_id = c.customer_id AND o.status = 'completed'
    LEFT JOIN order_items oi ON oi.order_id = o.order_id
    GROUP BY c.customer_id
),
segmented AS (
    SELECT
        *,
        CAST(julianday((SELECT MAX(order_date) FROM orders))
             - julianday(last_order_date) AS INTEGER)           AS recency_days,
        CASE NTILE(3) OVER (ORDER BY monetary DESC)
            WHEN 1 THEN 'High'
            WHEN 2 THEN 'Mid'
            ELSE 'Low'
        END                                                     AS segment
    FROM customer_rfm
)
SELECT
    segment,
    COUNT(*)                                                    AS customers,
    ROUND(MIN(monetary), 2)                                     AS min_spend,
    ROUND(AVG(monetary), 2)                                     AS avg_spend,
    ROUND(SUM(monetary), 2)                                     AS total_spend,
    ROUND(100.0 * SUM(monetary) / SUM(SUM(monetary)) OVER (), 1) AS pct_of_revenue,
    ROUND(AVG(frequency), 1)                                    AS avg_orders,
    ROUND(AVG(recency_days), 0)                                 AS avg_recency_days
FROM segmented
GROUP BY segment
ORDER BY avg_spend DESC;


-- name: anomaly_daily_store_orders
-- 5a. Anomaly detection: daily order counts per store for February 2025.
--     A calendar spine is cross joined with stores so days with zero orders
--     still appear (a plain GROUP BY would silently skip them). Every order
--     is counted regardless of status, since we are measuring system activity.
WITH RECURSIVE calendar(day) AS (
    SELECT '2025-02-01'
    UNION ALL
    SELECT date(day, '+1 day') FROM calendar WHERE day < '2025-02-28'
),
daily AS (
    SELECT
        s.store_id,
        s.store_name,
        s.region,
        cal.day,
        COUNT(o.order_id) AS order_count
    FROM calendar cal
    CROSS JOIN stores s
    LEFT JOIN orders o
           ON o.store_id = s.store_id AND o.order_date = cal.day
    GROUP BY s.store_id, s.store_name, s.region, cal.day
)
SELECT
    store_id,
    store_name,
    region,
    day,
    order_count,
    ROUND(AVG(order_count) OVER (PARTITION BY store_id), 2) AS store_feb_avg,
    CASE WHEN order_count <= 1 THEN 1 ELSE 0 END             AS low_activity_flag
FROM daily
ORDER BY store_id, day;


-- name: anomaly_outage_windows
-- 5b. Rolling 7-day order totals per store, compared with that store's
--     normal weekly volume (average daily orders in Q1 2025 x 7). A single
--     slow day is normal noise for one store; a full week running below 15%
--     of normal points to a system or operational outage. Only complete
--     7-day windows ending in February 2025 are evaluated.
WITH RECURSIVE calendar(day) AS (
    SELECT '2025-01-26'
    UNION ALL
    SELECT date(day, '+1 day') FROM calendar WHERE day < '2025-02-28'
),
daily AS (
    SELECT s.store_id, s.store_name, s.region, cal.day, COUNT(o.order_id) AS order_count
    FROM calendar cal
    CROSS JOIN stores s
    LEFT JOIN orders o ON o.store_id = s.store_id AND o.order_date = cal.day
    GROUP BY s.store_id, s.store_name, s.region, cal.day
),
baseline AS (
    SELECT store_id, 7.0 * COUNT(*) / (julianday('2025-03-31') - julianday('2025-01-01') + 1) AS expected_weekly
    FROM orders
    WHERE order_date BETWEEN '2025-01-01' AND '2025-03-31'
    GROUP BY store_id
),
rolling AS (
    SELECT
        d.store_id,
        d.store_name,
        d.region,
        date(d.day, '-6 days') AS window_start,
        d.day                  AS window_end,
        SUM(d.order_count) OVER w AS orders_7d,
        COUNT(*)           OVER w AS days_in_window
    FROM daily d
    WINDOW w AS (PARTITION BY d.store_id ORDER BY d.day ROWS BETWEEN 6 PRECEDING AND CURRENT ROW)
)
SELECT
    r.store_id,
    r.store_name,
    r.region,
    r.window_start,
    r.window_end,
    r.orders_7d,
    ROUND(b.expected_weekly, 1)                   AS expected_7d,
    ROUND(100.0 * r.orders_7d / b.expected_weekly, 1) AS pct_of_expected
FROM rolling r
JOIN baseline b ON b.store_id = r.store_id
WHERE r.window_end >= '2025-02-07'
  AND r.days_in_window = 7
  AND r.orders_7d < 0.15 * b.expected_weekly
ORDER BY pct_of_expected, r.window_end;


-- name: order_status_breakdown
-- 6. Order status breakdown with percentages
WITH order_totals AS (
    SELECT o.order_id, o.status, SUM(oi.line_revenue) AS order_value
    FROM orders o
    LEFT JOIN order_items oi ON oi.order_id = o.order_id
    GROUP BY o.order_id, o.status
)
SELECT
    status,
    COUNT(*)                                                AS orders,
    ROUND(100.0 * COUNT(*) / SUM(COUNT(*)) OVER (), 2)      AS pct_of_orders,
    ROUND(SUM(order_value), 2)                              AS order_value,
    ROUND(100.0 * SUM(order_value) / SUM(SUM(order_value)) OVER (), 2) AS pct_of_value
FROM order_totals
GROUP BY status
ORDER BY orders DESC;
