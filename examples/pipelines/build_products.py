from datamend.pipeline import build


def run(con, now, mode):
    build(con, "products", "SELECT id, ? AS _loaded_at FROM raw_products", now)
