#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""园区微网调度 —— 优化模型（生成侧）

决策变量（每时段 t = 0..T-1）：
    g[t]  电网购电        kW，0 ≤ g ≤ grid.import_limit_kw
    c[t]  储能充电        kW，0 ≤ c ≤ storage.power_kw
    d[t]  储能放电        kW，0 ≤ d ≤ storage.power_kw
    x[t]  光伏弃电        kW，0 ≤ x ≤ pv[t]
    s[t]  储荷电量        kWh，t = 0..T，soc_min ≤ s ≤ capacity

约束：
    功率平衡  g[t] + d[t] + pv[t] = load[t] + c[t] + x[t]
    储荷递推  s[t+1] = s[t] + eta_c·c[t] − d[t]/eta_d
    端面条件  s[0] = soc_init，s[T] = soc_terminal

目标：min Σ price[t]·g[t]·period_hours

本模块只负责"给出一个方案"，不做任何校验。校验由 mg_verify.py 独立完成。

用法：
    python tools/mg_model.py --csv data/park_day.csv --params data/params.json --out out/solution.json
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np
from scipy.optimize import linprog

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

sys.path.insert(0, str(Path(__file__).resolve().parent))
from mg_load import DataError, load_case  # noqa: E402


class ModelError(Exception):
    def __init__(self, code: str, message: str, detail: dict | None = None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.detail = detail or {}

    def as_dict(self) -> dict:
        return {"code": self.code, "message": self.message, "detail": self.detail}


# --------------------------------------------------------------------------
# 故意的建模缺陷（只在 --defect 下启用）
#
# 用途：证明"生成与校验分离"不是一句设计口号——把生成侧改坏，
#       校验侧无需任何改动就能当场抓住。
# 这些缺陷都是**真实常见**的建模错误，而且都会让解"看起来更便宜"：
#   no_terminal_soc —— 忘了写端面储荷约束，储能可以期末不放空
#   no_storage_power —— 忘了写储能功率上限，充放电可以任意快
# --------------------------------------------------------------------------
DEFECTS = ("no_terminal_soc", "no_storage_power")

DEFECT_DESCRIPTIONS = {
    "no_terminal_soc": "漏掉端面储荷约束 s[T]=soc_end：储能期末不必回到目标值，凭空多用存量电量",
    "no_storage_power": "漏掉储能功率上限 0≤c,d≤power_kw：充放电功率可以超过物理限值",
}


def solve(case: dict, defect: str | None = None) -> dict:
    """对算例求解，返回含调度方案与关键指标的字典。

    defect: 仅供"分离性验证"使用。**故意在生成侧注入一个建模缺陷**，
            用来证明独立校验器能抓住生成侧的错误（生成侧自己发现不了）。
            正常调用一律 defect=None。
            可选值见 DEFECTS。
    """
    if defect is not None and defect not in DEFECTS:
        raise ModelError("E_DEFECT_UNKNOWN", f"未知的缺陷类型 {defect}", {"allowed": list(DEFECTS)})

    periods = case["periods"]
    params = case["params"]
    st = params["storage"]
    T = len(periods)
    dt = case["period_hours"]

    load = np.array([p["load_kw"] for p in periods], dtype=float)
    pv = np.array([p["pv_kw"] for p in periods], dtype=float)
    price = np.array([p["price_cny_per_kwh"] for p in periods], dtype=float)

    cap = float(st["capacity_kwh"])
    pmax = float(st["power_kw"])
    eta_c = float(st["eta_charge"])
    eta_d = float(st["eta_discharge"])
    soc0 = float(st["soc_init_kwh"])
    soc_min = float(st["soc_min_kwh"])
    soc_end = float(st["soc_terminal_kwh"])
    grid_max = float(params["grid"]["import_limit_kw"])

    # 变量布局： g[T] | c[T] | d[T] | x[T] | s[T+1]
    n = 5 * T + 1
    i_g, i_c, i_d, i_x, i_s = 0, T, 2 * T, 3 * T, 4 * T

    c_obj = np.zeros(n)
    c_obj[i_g : i_g + T] = price * dt

    a_eq, b_eq = [], []

    # 功率平衡： g + d − c − x = load − pv
    for t in range(T):
        row = np.zeros(n)
        row[i_g + t] = 1.0
        row[i_d + t] = 1.0
        row[i_c + t] = -1.0
        row[i_x + t] = -1.0
        a_eq.append(row)
        b_eq.append(load[t] - pv[t])

    # 储荷递推： s[t+1] − s[t] − eta_c·c + d/eta_d = 0
    for t in range(T):
        row = np.zeros(n)
        row[i_s + t + 1] = 1.0
        row[i_s + t] = -1.0
        row[i_c + t] = -eta_c
        row[i_d + t] = 1.0 / eta_d
        a_eq.append(row)
        b_eq.append(0.0)

    bounds = []
    bounds += [(0.0, max(grid_max, 0.0))] * T          # g
    # 缺陷注入：漏掉储能功率上限
    hi_pwr = None if defect == "no_storage_power" else max(pmax, 0.0)
    bounds += [(0.0, hi_pwr)] * T                      # c
    bounds += [(0.0, hi_pwr)] * T                      # d
    bounds += [(0.0, float(pv[t])) for t in range(T)]  # x ≤ pv
    bounds += [(soc_min, cap)] * (T + 1)               # s
    bounds[i_s] = (soc0, soc0)                         # s[0] 固定
    # 缺陷注入：漏掉端面储荷约束
    if defect != "no_terminal_soc":
        bounds[i_s + T] = (soc_end, soc_end)           # s[T] 固定

    res = linprog(
        c=c_obj,
        A_eq=np.array(a_eq),
        b_eq=np.array(b_eq),
        bounds=bounds,
        method="highs",
    )

    if not res.success:
        raise ModelError(
            "E_SOLVER_FAILED",
            f"求解器未得到最优解：status={res.status} {res.message}",
            {"status": int(res.status), "message": str(res.message)},
        )

    xv = res.x
    dispatch = []
    for t in range(T):
        dispatch.append(
            {
                "period": periods[t]["period"],
                "hour": periods[t]["hour"],
                "load_kw": round(load[t], 6),
                "pv_kw": round(pv[t], 6),
                "price_cny_per_kwh": round(price[t], 6),
                "grid_import_kw": round(float(xv[i_g + t]), 9),
                "charge_kw": round(float(xv[i_c + t]), 9),
                "discharge_kw": round(float(xv[i_d + t]), 9),
                "curtail_kw": round(float(xv[i_x + t]), 9),
                "soc_kwh": round(float(xv[i_s + t]), 9),
            }
        )
    soc_terminal_actual = round(float(xv[i_s + T]), 6)

    total_grid = float(np.sum(xv[i_g : i_g + T]) * dt)
    total_charge = float(np.sum(xv[i_c : i_c + T]) * dt)
    total_discharge = float(np.sum(xv[i_d : i_d + T]) * dt)
    total_curtail = float(np.sum(xv[i_x : i_x + T]) * dt)

    return {
        "solver": {"name": "scipy.linprog", "method": "highs", "status": int(res.status), "success": True},
        "objective_cny": round(float(res.fun), 6),
        "defect_injected": defect,
        "defect_description": DEFECT_DESCRIPTIONS.get(defect) if defect else None,
        "dispatch": dispatch,
        "metrics": {
            "total_grid_import_kwh": round(total_grid, 6),
            "total_charge_kwh": round(total_charge, 6),
            "total_discharge_kwh": round(total_discharge, 6),
            "total_curtail_kwh": round(total_curtail, 6),
            "total_load_kwh": round(float(np.sum(load) * dt), 6),
            "total_pv_kwh": round(float(np.sum(pv) * dt), 6),
            "soc_terminal_kwh": soc_terminal_actual,
            "avg_price_paid": round(float(res.fun) / total_grid, 6) if total_grid > 0 else None,
        },
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="园区微网调度：LP 建模与求解")
    ap.add_argument("--csv", required=True)
    ap.add_argument("--params", required=True)
    ap.add_argument("--out", required=True, help="方案 JSON 输出路径")
    ap.add_argument("--defect", default=None, choices=list(DEFECTS),
                    help="[仅供演示] 故意在生成侧注入建模缺陷，用于验证校验器的独立性")
    args = ap.parse_args(argv)

    try:
        case = load_case(args.csv, args.params)
        solution = solve(case, defect=args.defect)
    except (DataError, ModelError) as exc:
        print(json.dumps({"ok": False, "error": exc.as_dict()}, ensure_ascii=False, indent=2))
        return 2

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "schema": "mg.solution/v1",
        "source": case["source"],
        "case_summary": case["summary"],
        **solution,
    }
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    m = solution["metrics"]
    print("[求解完成]")
    print(f"  最优日成本      : {solution['objective_cny']} 元")
    print(f"  电网购电总量    : {m['total_grid_import_kwh']} kWh")
    print(f"  储能充电/放电   : {m['total_charge_kwh']} / {m['total_discharge_kwh']} kWh")
    print(f"  光伏弃电        : {m['total_curtail_kwh']} kWh")
    print(f"  平均购电电价    : {m['avg_price_paid']} 元/kWh")
    print(f"  方案已写入      : {out}")
    if solution.get("defect_injected"):
        print(f"  ⚠ 已注入建模缺陷 : {solution['defect_injected']} —— {solution['defect_description']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
