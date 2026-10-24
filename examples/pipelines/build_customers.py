from datamend.pipeline import build


def run(con, now, mode):
    build(con, "customers", "SELECT id, ? AS _loaded_at FROM raw_customers", now)
