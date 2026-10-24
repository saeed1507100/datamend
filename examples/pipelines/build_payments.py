from datamend.pipeline import build


def run(con, now, mode):
    build(con, "payments", "SELECT id, amount, ? AS _loaded_at FROM raw_payments", now)
