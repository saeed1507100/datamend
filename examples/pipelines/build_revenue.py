from datamend.pipeline import build


def run(con, now, mode):
    build(con, "revenue_daily", """
        SELECT 1 AS id,
               (SELECT count(*) FROM orders) AS orders,
               (SELECT sum(amount) FROM payments) AS revenue,
               ? AS _loaded_at
    """, now)
