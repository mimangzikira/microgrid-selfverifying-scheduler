#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""园区微网调度 —— 数据装载与校验

职责：把原始 CSV 与参数 JSON 读成结构化算例，并在进入建模前把脏数据拦住。
本模块只做校验，不做任何优化决策。

用法：
    python tools/mg_load.py --csv data/park_day.csv --params data/params.json
    python tools/mg_load.py --csv ... --params ... --json

退出码：0 通过；2 数据不合法（E_* 见下）
"""
from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path

# Windows 上 Python 往管道写会退回 cp936，而 AGH 的 shell 按 UTF-8 解码，
# 结果中文全成乱码。这里强制 UTF-8，不依赖环境变量，换机器也一样。
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

REQUIRED_COLUMNS = ("period", "hour", "load_kw", "pv_kw", "price_cny_per_kwh")


class DataError(Exception):
    """数据不合法。code 供上游按类型分流处置。"""

    def __init__(self, code: str, message: str, detail: dict | None = None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.detail = detail or {}

    def as_dict(self) -> dict:
        return {"code": self.code, "message": self.message, "detail": self.detail}


def _finite_num(raw, field: str, row_no: int) -> float:
    if raw is None or str(raw).strip() == "":
        raise DataError(
            "E_EMPTY_FIELD",
            f"第 {row_no} 行的字段 {field} 是空的",
            {"row": row_no, "field": field},
        )
    try:
        value = float(str(raw).strip())
    except ValueError:
        raise DataError(
            "E_PARSE",
            f"第 {row_no} 行的字段 {field} 不是数字：{raw!r}",
            {"row": row_no, "field": field, "value": str(raw)},
        )
    if value != value or value in (float("inf"), float("-inf")):
        raise DataError(
            "E_NOT_FINITE",
            f"第 {row_no} 行的字段 {field} 不是有限数：{raw!r}",
            {"row": row_no, "field": field},
        )
    return value


def _read_csv(csv_path: Path) -> list[dict]:
    if not csv_path.is_file():
        raise DataError("E_MISSING_FILE", f"找不到数据文件：{csv_path}", {"path": str(csv_path)})
    try:
        text = csv_path.read_text(encoding="utf-8-sig")
    except UnicodeDecodeError as exc:
        raise DataError(
            "E_ENCODING",
            f"数据文件不是 UTF-8：{exc}",
            {"path": str(csv_path)},
        )
    reader = csv.DictReader(text.splitlines())
    header = reader.fieldnames or []
    missing = [c for c in REQUIRED_COLUMNS if c not in header]
    if missing:
        raise DataError(
            "E_SCHEMA",
            f"数据文件缺少必需列：{missing}",
            {"path": str(csv_path), "header": header, "missing": missing},
        )
    rows = [r for r in reader if any((v or "").strip() for v in r.values())]
    return rows


def _check_storage(st: dict) -> None:
    cap = _finite_num(st.get("capacity_kwh"), "storage.capacity_kwh", 0)
    power = _finite_num(st.get("power_kw"), "storage.power_kw", 0)
    eta_c = _finite_num(st.get("eta_charge"), "storage.eta_charge", 0)
    eta_d = _finite_num(st.get("eta_discharge"), "storage.eta_discharge", 0)
    soc_init = _finite_num(st.get("soc_init_kwh"), "storage.soc_init_kwh", 0)
    soc_min = _finite_num(st.get("soc_min_kwh"), "storage.soc_min_kwh", 0)
    soc_term = _finite_num(st.get("soc_terminal_kwh"), "storage.soc_terminal_kwh", 0)

    if cap < 0 or power < 0:
        raise DataError("E_STORAGE_RANGE", "储能容量与功率不能为负", {"capacity_kwh": cap, "power_kw": power})
    if not (0 < eta_c <= 1) or not (0 < eta_d <= 1):
        raise DataError(
            "E_STORAGE_EFFICIENCY",
            "充/放电效率必须落在 (0, 1] 区间",
            {"eta_charge": eta_c, "eta_discharge": eta_d},
        )
    if not (0 <= soc_min <= cap):
        raise DataError("E_STORAGE_SOC_MIN", "soc_min 必须满足 0 ≤ soc_min ≤ capacity", {"soc_min_kwh": soc_min, "capacity_kwh": cap})
    for name, val in (("soc_init_kwh", soc_init), ("soc_terminal_kwh", soc_term)):
        if not (soc_min <= val <= cap):
            raise DataError(
                "E_STORAGE_SOC_BOUND",
                f"{name} 必须落在 [soc_min, capacity] 区间内",
                {name: val, "soc_min_kwh": soc_min, "capacity_kwh": cap},
            )


def load_case(csv_path: str | Path, params_path: str | Path) -> dict:
    """读入并校验算例，返回 {"params": {...}, "periods": [...], "summary": {...}}。"""
    csv_path = Path(csv_path)
    params_path = Path(params_path)

    if not params_path.is_file():
        raise DataError("E_MISSING_FILE", f"找不到参数文件：{params_path}", {"path": str(params_path)})
    try:
        params = json.loads(params_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise DataError(
            "E_PARAMS_JSON",
            f"参数文件不是合法 JSON：{exc}",
            {"path": str(params_path), "line": exc.lineno, "col": exc.colno},
        )

    if not isinstance(params, dict):
        raise DataError("E_PARAMS_SHAPE", "参数文件的顶层必须是对象", {"path": str(params_path)})
    for key in ("periods", "storage", "grid"):
        if key not in params:
            raise DataError("E_PARAMS_MISSING", f"参数文件缺少必需字段 {key}", {"path": str(params_path), "missing": key})

    n_expected = params["periods"]
    if not isinstance(n_expected, int) or n_expected <= 0:
        raise DataError("E_PARAMS_PERIODS", "params.periods 必须是正整数", {"periods": n_expected})

    _check_storage(params["storage"])
    grid = params["grid"]
    if not isinstance(grid, dict) or "import_limit_kw" not in grid:
        raise DataError("E_PARAMS_MISSING", "params.grid 缺少 import_limit_kw", {})
    import_limit = _finite_num(grid.get("import_limit_kw"), "grid.import_limit_kw", 0)
    if import_limit < 0:
        raise DataError("E_GRID_LIMIT", "电网购电上限不能为负", {"import_limit_kw": import_limit})

    raw_rows = _read_csv(csv_path)
    if len(raw_rows) == 0:
        raise DataError("E_EMPTY_FILE", "数据文件没有任何数据行", {"path": str(csv_path)})
    if len(raw_rows) != n_expected:
        raise DataError(
            "E_ROW_COUNT",
            f"数据行数与参数不符：期望 {n_expected} 行，实际 {len(raw_rows)} 行",
            {"expected": n_expected, "actual": len(raw_rows)},
        )

    periods = []
    seen = set()
    for idx, row in enumerate(raw_rows, start=2):  # +1 表头，+1 从 1 计数
        period = int(_finite_num(row.get("period"), "period", idx))
        if period in seen:
            raise DataError("E_DUPLICATE_PERIOD", f"period 重复：{period}", {"row": idx, "period": period})
        seen.add(period)
        load = _finite_num(row.get("load_kw"), "load_kw", idx)
        pv = _finite_num(row.get("pv_kw"), "pv_kw", idx)
        price = _finite_num(row.get("price_cny_per_kwh"), "price_cny_per_kwh", idx)
        if load < 0:
            raise DataError("E_NEGATIVE_LOAD", f"第 {idx} 行负荷为负：{load}", {"row": idx, "load_kw": load})
        if pv < 0:
            raise DataError("E_NEGATIVE_PV", f"第 {idx} 行光伏为负：{pv}", {"row": idx, "pv_kw": pv})
        if price <= 0:
            raise DataError(
                "E_NONPOSITIVE_PRICE",
                f"第 {idx} 行电价非正：{price}。本算例假定园区只能购电、不参与反向售电，非正电价会让目标函数失去意义",
                {"row": idx, "price": price},
            )
        periods.append(
            {
                "period": period,
                "hour": (row.get("hour") or "").strip(),
                "load_kw": load,
                "pv_kw": pv,
                "price_cny_per_kwh": price,
            }
        )

    periods.sort(key=lambda p: p["period"])
    if [p["period"] for p in periods] != list(range(1, n_expected + 1)):
        raise DataError(
            "E_PERIOD_SEQUENCE",
            "period 必须是从 1 开始的连续整数",
            {"got": [p["period"] for p in periods][:10]},
        )

    period_hours = float(params.get("period_hours", 1.0))
    total_load = sum(p["load_kw"] for p in periods) * period_hours
    total_pv = sum(p["pv_kw"] for p in periods) * period_hours
    prices = [p["price_cny_per_kwh"] for p in periods]

    return {
        "source": {"csv": str(csv_path), "params": str(params_path)},
        "params": params,
        "period_hours": period_hours,
        "periods": periods,
        "summary": {
            "n_periods": len(periods),
            "period_hours": period_hours,
            "total_load_kwh": round(total_load, 6),
            "total_pv_kwh": round(total_pv, 6),
            "net_load_kwh": round(total_load - total_pv, 6),
            "peak_load_kw": max(p["load_kw"] for p in periods),
            "price_min": min(prices),
            "price_max": max(prices),
            "price_tiers": sorted(set(prices)),
            "soc_init_kwh": params["storage"]["soc_init_kwh"],
            "soc_terminal_kwh": params["storage"]["soc_terminal_kwh"],
        },
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="园区微网调度：数据装载与校验")
    ap.add_argument("--csv", required=True, help="时序数据 CSV")
    ap.add_argument("--params", required=True, help="参数 JSON")
    ap.add_argument("--json", action="store_true", help="以 JSON 输出（供程序消费）")
    args = ap.parse_args(argv)

    try:
        case = load_case(args.csv, args.params)
    except DataError as exc:
        payload = {"ok": False, "error": exc.as_dict()}
        if args.json:
            print(json.dumps(payload, ensure_ascii=False, indent=2))
        else:
            print(f"[数据校验失败] {exc.code}: {exc.message}", file=sys.stderr)
            if exc.detail:
                print(f"  明细: {json.dumps(exc.detail, ensure_ascii=False)}", file=sys.stderr)
        return 2

    if args.json:
        print(json.dumps({"ok": True, "summary": case["summary"]}, ensure_ascii=False, indent=2))
    else:
        s = case["summary"]
        print("[数据校验通过]")
        print(f"  时段数      : {s['n_periods']} 段 × {s['period_hours']} h")
        print(f"  总负荷      : {s['total_load_kwh']} kWh")
        print(f"  总光伏      : {s['total_pv_kwh']} kWh")
        print(f"  净负荷      : {s['net_load_kwh']} kWh")
        print(f"  峰值负荷    : {s['peak_load_kw']} kW")
        print(f"  电价区间    : {s['price_min']} ~ {s['price_max']} 元/kWh（{len(s['price_tiers'])} 档）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
