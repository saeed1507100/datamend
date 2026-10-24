"""Compare a shadow table with its production counterpart: schema, volume, keys, nulls, sums.

`expectations()` then checks the shadow table against what `datamend.yaml` says it must be.
"""

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
    return {"table": t, "checks": checks, "metrics": metrics,
            "expectations": expectations(con, prod_schema, shadow_schema, spec)}


def expectations(con, prod_schema: str, shadow_schema: str, spec: Compare) -> list[dict]:
    """Expected vs actual for the patched shadow output. Every result is a hard requirement."""
    exp, t = spec.expectations, spec.table
    if exp is None or not _columns(con, shadow_schema, t):
        return []
    prod, shadow = f'"{prod_schema}"."{t}"', f'"{shadow_schema}"."{t}"'
    has_prod = bool(_columns(con, prod_schema, t))
    out = []

    def one(sql):
        return con.execute(sql).fetchone()[0]

    def expect(name, expected, actual, passed):
        out.append({"check": f"{t}.expect_{name}", "expected": expected, "actual": actual,
                    "passed": bool(passed),
                    "detail": f"expected {expected}, got {actual}"})

    def need_prod(name):
        out.append({"check": f"{t}.expect_{name}", "expected": "production table", "actual": None,
                    "passed": False, "detail": f"{prod_schema}.{t} not found to compare with"})

    rows = one(f"SELECT count(*) FROM {shadow}")
    if exp.row_count is not None:
        if isinstance(exp.row_count, int):
            expect("row_count", exp.row_count, rows, rows == exp.row_count)
        elif not has_prod:
            need_prod("row_count")
        else:
            p_rows = one(f"SELECT count(*) FROM {prod}")
            if exp.row_count == "prod":
                expect("row_count", f"= {p_rows} (prod)", rows, rows == p_rows)
            else:
                expect("row_count", f">= {p_rows} (prod)", rows, rows >= p_rows)
    if exp.keys and spec.key:
        k = f'"{spec.key}"'
        if not has_prod:
            need_prod("keys")
        else:
            missing = one(f"SELECT count(*) FROM (SELECT {k} FROM {prod} EXCEPT SELECT {k} FROM {shadow})")
            extra = one(f"SELECT count(*) FROM (SELECT {k} FROM {shadow} EXCEPT SELECT {k} FROM {prod})")
            if exp.keys == "prod":
                expect("keys", "same key set as prod", f"{missing} missing, {extra} extra",
                       missing == 0 and extra == 0)
            else:
                expect("keys", "every prod key present", f"{missing} missing, {extra} new",
                       missing == 0)
    for col, limit in exp.nulls.items():
        nulls = one(f'SELECT count(*) FILTER (WHERE "{col}" IS NULL) FROM {shadow}')
        if limit == "prod":
            if not has_prod:
                need_prod(f"nulls_{col}")
                continue
            limit = one(f'SELECT count(*) FILTER (WHERE "{col}" IS NULL) FROM {prod}')
        expect(f"nulls_{col}", f"<= {limit}", nulls, nulls <= limit)
    for col, tol in exp.sums_within.items():
        if not has_prod:
            need_prod(f"sum_{col}")
            continue
        if spec.key:
            k = f'"{spec.key}"'
            shared = f"WHERE {k} IN (SELECT {k} FROM {prod}) AND {k} IN (SELECT {k} FROM {shadow})"
            scope = "on shared keys"
        else:
            shared, scope = "", "whole table"
        p_sum = _num(one(f'SELECT sum("{col}") FROM {prod} {shared}')) or 0.0
        s_sum = _num(one(f'SELECT sum("{col}") FROM {shadow} {shared}')) or 0.0
        delta = abs(s_sum - p_sum) / abs(p_sum) if p_sum else abs(s_sum)
        expect(f"sum_{col}", f"{p_sum:.2f} ±{tol:.1%} ({scope})", f"{s_sum:.2f} ({delta:+.2%})",
               delta <= tol)
    return out


def _num(v):
    return None if v is None else float(v)
