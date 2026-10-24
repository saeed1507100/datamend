"""Compare a shadow table with its production counterpart: schema, volume, keys, nulls, sums."""

from datamend.remote.config import Compare


def _columns(con, schema: str, table: str) -> list[tuple[str, str]]:
    return con.execute(
        "SELECT column_name, data_type FROM information_schema.columns "
        "WHERE table_schema = ? AND table_name = ? ORDER BY ordinal_position",
        [schema, table]).fetchall()


def compare_table(con, prod_schema: str, shadow_schema: str, spec: Compare,
                  max_row_delta: float) -> dict:
    """Metrics and checks for one table. Hard checks fail validation; soft ones are reported."""
    t = spec.table
    prod_cols, shadow_cols = _columns(con, prod_schema, t), _columns(con, shadow_schema, t)
    checks, metrics = [], []

    def check(name, passed, detail, hard=True):
        checks.append({"check": f"{t}.{name}", "passed": bool(passed), "detail": detail, "hard": hard})

    if not shadow_cols:
        check("exists", False, f"{shadow_schema}.{t} was not written")
        return {"table": t, "checks": checks, "metrics": metrics}
    check("schema", prod_cols == shadow_cols or not prod_cols,
          "same columns and types" if prod_cols == shadow_cols
          else f"prod {prod_cols} vs shadow {shadow_cols}")

    def stat(schema, sql):
        return con.execute(sql.format(t=f'"{schema}"."{t}"')).fetchone()[0] if \
            _columns(con, schema, t) else None

    p_rows = stat(prod_schema, "SELECT count(*) FROM {t}")
    s_rows = stat(shadow_schema, "SELECT count(*) FROM {t}")
    metrics.append({"metric": "rows", "prod": p_rows, "shadow": s_rows})
    if p_rows:
        delta = (s_rows - p_rows) / p_rows
        check("volume", abs(delta) <= max_row_delta, f"{p_rows} → {s_rows} rows ({delta:+.0%})",
              hard=False)
    if spec.key:
        dupes = stat(shadow_schema, f'SELECT count(*) - count(DISTINCT "{spec.key}") FROM {{t}}')
        check("unique_key", dupes == 0, f"{dupes} duplicate {spec.key} values")
    for col, typ in shadow_cols:
        p_null = stat(prod_schema, f'SELECT count(*) FILTER (WHERE "{col}" IS NULL) FROM {{t}}')
        s_null = stat(shadow_schema, f'SELECT count(*) FILTER (WHERE "{col}" IS NULL) FROM {{t}}')
        metrics.append({"metric": f"nulls({col})", "prod": p_null, "shadow": s_null})
        if s_null and not p_null:
            check(f"nulls_{col}", False, f"{s_null} new nulls in {col}", hard=False)
        if col != spec.key and any(k in typ.upper() for k in ("INT", "DECIMAL", "NUMERIC", "DOUBLE", "FLOAT", "REAL")):
            p_sum = stat(prod_schema, f'SELECT sum("{col}") FROM {{t}}')
            s_sum = stat(shadow_schema, f'SELECT sum("{col}") FROM {{t}}')
            metrics.append({"metric": f"sum({col})", "prod": _num(p_sum), "shadow": _num(s_sum)})
    return {"table": t, "checks": checks, "metrics": metrics}


def _num(v):
    return None if v is None else float(v)
