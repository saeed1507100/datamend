from datamend.pipeline import build


def run(con, now, mode):
    build(con, "orders", "SELECT id, ? AS _loaded_at FROM raw_orders", now)
