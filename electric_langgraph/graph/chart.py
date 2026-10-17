"""图表数据构建：把查询结果行自动转换成前端可渲染的折线图/柱状图配置。"""
import re

_TIME_HINTS = ("ts", "time", "date", "day", "hour", "week", "month", "period")
_DT_RE = re.compile(r"^\d{4}[-/.]\d{1,2}[-/.]\d{1,2}")


def _is_numeric(v) -> bool:
    if v is None:
        return False
    if isinstance(v, (int, float)):
        return True
    if isinstance(v, str):
        s = v.strip().replace(",", "")
        return bool(re.fullmatch(r"-?\d+(\.\d+)?", s))
    return False


def _looks_dt(v) -> bool:
    return isinstance(v, str) and bool(_DT_RE.match(v.strip()))


def _col_values(rows: list[dict], c) -> list:
    return [r[c] for r in rows if r[c] is not None]


def _num_cols(rows: list[dict], cols: list[str], exclude=None) -> list[str]:
    return [c for c in cols if c != exclude and _col_values(rows, c)
            and all(_is_numeric(v) for v in _col_values(rows, c))]


def rows_to_chart(title: str, rows: list[dict]) -> dict | None:
    """把查询结果行转成折线图/柱状图配置。

    自动识别：
    - 折线图：存在时间列（列名含时间暗示，或整列值都像日期时间字符串）+ 数值列；
    - 柱状图：无时间列，但存在分类列 + 数值列（如按电压等级统计设备数）；
    - 其余情况返回 None（不渲染图表）。
    """
    if not rows or not isinstance(rows[0], dict):
        return None
    cols = list(rows[0].keys())

    # 1) 时间列
    xcol = next((c for c in cols if any(h in c.lower() for h in _TIME_HINTS)), None)
    if not xcol:
        for c in cols:
            vals = _col_values(rows, c)
            if vals and all(_looks_dt(v) for v in vals):
                xcol = c
                break

    # 2) 折线图：有时间列 + 数值列
    if xcol:
        num_cols = _num_cols(rows, cols, exclude=xcol)
        if not num_cols:
            return None
        max_pts = 48
        n = min(len(rows), max_pts)
        x = [str(rows[i][xcol])[-11:] for i in range(n)]
        series = [{"name": c, "data": [rows[i][c] for i in range(n)]} for c in num_cols[:2]]
        return {"type": "line", "title": title, "x": x, "series": series}

    # 3) 柱状图：无时间列 → 分类列 + 数值列
    num_cols = _num_cols(rows, cols)
    cat_cols = [c for c in cols if c not in num_cols]
    if not num_cols or not cat_cols:
        return None
    cat_col = cat_cols[0]
    max_pts = 48
    n = min(len(rows), max_pts)
    x = [str(rows[i][cat_col]) for i in range(n)]
    series = [{"name": c, "data": [rows[i][c] for i in range(n)]} for c in num_cols[:2]]
    return {"type": "bar", "title": title, "x": x, "series": series}
