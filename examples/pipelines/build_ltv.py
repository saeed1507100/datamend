from datamend.pipeline import build


def run(con, now, mode):
    build(con, "customer_ltv", """
        SELECT c.id, count(o.id) AS orders, ? AS _loaded_at
        FROM customers c LEFT JOIN orders o ON o.id % 50 = c.id
        GROUP BY c.id
    """, now)
