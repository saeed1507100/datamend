from datamend.pipeline import load_synthetic


def run(con, now, mode):
    load_synthetic(con, "raw_orders", now, mode)
