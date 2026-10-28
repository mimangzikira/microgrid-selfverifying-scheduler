#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""园区微网调度 —— 独立校验器

设计原则：本模块 **不 import mg_model 做可行性判定**。
    V1、V2 全部用自己实现的算术重算，与生成侧不共享任何代码路径。
    V3 是"黑箱差分测试"——把生成侧当黑箱调用，用闭式解卡它的输出。

三组检查：
    V1 约束残差（精确）  自己重读 CSV，重算功率平衡、储荷递推、上下限、端面 SOC、目标值
    V2 上下界夹逼（有效但松）
        下界 = 解析式  min(price) × max(0, Σ(load−pv)·dt)
        上界 = 可行启发式方案（无储能 / 谷充峰放贪心）的成本
        要求 下界 ≤ 求解器成本 ≤ 上界
    V3 退化复现（精确）  三个有闭式解的退化算例，求解器必须复现

用法：
    python tools/mg_verify.py --csv ... --params ... --solution ... [--json]
退出码：0 全部通过；3 存在未通过项
"""
from __future__ import annotations

import argparse
import csv
import copy
import json
import sys
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

TOL_BALANCE = 1e-6
TOL_SOC = 1e-6
TOL_BOUND = 1e-6
TOL_OBJ_REL = 1e-6
TOL_DEGENERATE_REL = 1e-6


class Check:
    def __init__(self, cid: str, name: str, kind: str):
        self.cid = cid
        self.name = name
        self.kind = kind  # exact | sandwich | differential
        self.passed = False
        self.skipped = False
        self.detail: dict = {}
        self.note = ""

    def ok(self, **detail) -> "Check":
        self.passed = True
        self.detail = detail
        return self

    def fail(self, **detail) -> "Check":
        self.passed = False
        self.detail = detail
        return self

    def skip(self, **detail) -> "Check":
        """前提不成立、无需判定的检查。不计入失败，但会如实标注。"""
        self.passed = True
        self.skipped = True
        self.detail = detail
        return self

    def as_dict(self) -> dict:
        return {
            "id": self.cid,
            "name": self.name,
            "kind": self.kind,
            "passed": self.passed,
            "skipped": self.skipped,
            "detail": self.detail,
            "note": self.note,
        }


# --------------------------------------------------------------------------
# 独立解析：不使用 mg_load 的任何代码
# --------------------------------------------------------------------------
def _read_raw(csv_path: Path) -> list[dict]:
    with csv_path.open(encoding="utf-8-sig", newline="") as fh:
        return [r for r in csv.DictReader(fh) if r.get("period")]


def _feasible_upper_bounds(periods, dt, pmax, cap, eta_c, eta_d, soc0, soc_min, soc_end):
    """构造若干个"必定可行"的规则方案，取成本最低者作为上界。

    任何满足储荷递推与上下限的 (c, d, s) 都能补出一个满足功率平衡的调度：
        need = load + c − d − pv
        need ≥ 0 → g = need, x = 0 ；need < 0 → g = 0, x = −need
    所以只要把 (c,d,s) 造得合法，就得到上界。
    """
    T = len(periods)
    ld = [float(p["load_kw"]) for p in periods]
    pvv = [float(p["pv_kw"]) for p in periods]
    prices = [float(p["price_cny_per_kwh"]) for p in periods]
    bounds = {}

    # H1 无储能：全部由电网与光伏承担
    cost_h1 = sum(prices[t] * max(0.0, ld[t] - pvv[t]) * dt for t in range(T))
    bounds["no_storage"] = cost_h1

    # H2 谷充峰放贪心 + 末段修复
    p_lo, p_hi = min(prices), max(prices)
    c = [0.0] * T
    d = [0.0] * T
    s = [0.0] * (T + 1)
    s[0] = soc0
    cur = soc0
    for t in range(T):
        if abs(p_hi - p_lo) < 1e-12:
            target = cur
        elif prices[t] <= p_lo + 1e-12:
            target = cap
        elif prices[t] >= p_hi - 1e-12:
            target = soc_min
        else:
            target = cur
        if target > cur:
            step = min(pmax * dt, (target - cur) / eta_c)
            c[t] = step / dt
            cur += eta_c * step
        elif target < cur:
            step = min(pmax * dt, (cur - target) * eta_d)
            d[t] = step / dt
            cur -= step / eta_d
        s[t + 1] = cur

    # 末段修复：把端面 SOC 拉回 soc_end
    for t in range(T - 1, -1, -1):
        if abs(cur - soc_end) <= 1e-9:
            break
        if cur < soc_end:
            room = min(pmax * dt - c[t] * dt, (soc_end - cur) / eta_c)
            if room > 0:
                c[t] += room / dt
                cur += eta_c * room
        else:
            room = min(pmax * dt - d[t] * dt, (cur - soc_end) * eta_d)
            if room > 0:
                d[t] += room / dt
                cur -= room / eta_d
        s[t + 1] = cur

    if abs(cur - soc_end) <= 1e-6:
        cost_h2 = 0.0
        ok = True
        for t in range(T):
            need = ld[t] + c[t] - d[t] - pvv[t]
            grid = max(0.0, need)
            if grid > 0:
                cost_h2 += prices[t] * grid * dt
            if not (0.0 <= c[t] <= pmax + 1e-9 and 0.0 <= d[t] <= pmax + 1e-9):
                ok = False
        if ok:
            bounds["greedy_tou"] = cost_h2

    return bounds


def verify(csv_path, params_path, solution: dict, *, check_degenerate: bool = True) -> dict:
    periods = _read_raw(Path(csv_path))
    params = json.loads(Path(params_path).read_text(encoding="utf-8"))
    st = params["storage"]
    dt = float(params.get("period_hours", 1.0))
    T = len(periods)

    cap = float(st["capacity_kwh"])
    pmax = float(st["power_kw"])
    eta_c = float(st["eta_charge"])
    eta_d = float(st["eta_discharge"])
    soc0 = float(st["soc_init_kwh"])
    soc_min = float(st["soc_min_kwh"])
    soc_end = float(st["soc_terminal_kwh"])
    grid_max = float(params["grid"]["import_limit_kw"])

    load = [float(r["load_kw"]) for r in periods]
    pv = [float(r["pv_kw"]) for r in periods]
    price = [float(r["price_cny_per_kwh"]) for r in periods]

    disp = solution["dispatch"]
    if len(disp) != T:
        raise ValueError(f"方案时段数 {len(disp)} 与数据 {T} 不一致")

    g = [float(r["grid_import_kw"]) for r in disp]
    c = [float(r["charge_kw"]) for r in disp]
    d = [float(r["discharge_kw"]) for r in disp]
    x = [float(r["curtail_kw"]) for r in disp]
    soc = [float(r["soc_kwh"]) for r in disp]

    checks: list[Check] = []

    # ---------------- V1 约束残差 ----------------
    bal = [abs(g[t] + d[t] + pv[t] - load[t] - c[t] - x[t]) for t in range(T)]
    c1 = Check("V1.1", "功率平衡残差", "exact")
    if max(bal) <= TOL_BALANCE:
        c1.ok(max_abs_residual=max(bal), tolerance=TOL_BALANCE)
    else:
        worst = bal.index(max(bal))
        c1.fail(max_abs_residual=max(bal), tolerance=TOL_BALANCE, worst_period=worst + 1)
    checks.append(c1)

    soc_full = soc + [float(solution["metrics"]["soc_terminal_kwh"])]
    rec = []
    for t in range(T):
        rec.append(abs(soc_full[t + 1] - (soc_full[t] + eta_c * c[t] * dt - d[t] / eta_d * dt)))
    c2 = Check("V1.2", "储荷递推残差", "exact")
    if max(rec) <= TOL_SOC:
        c2.ok(max_abs_residual=max(rec), tolerance=TOL_SOC)
    else:
        worst = rec.index(max(rec))
        c2.fail(max_abs_residual=max(rec), tolerance=TOL_SOC, worst_period=worst + 1)
    checks.append(c2)

    viol = []
    for t in range(T):
        if g[t] < -TOL_BOUND or g[t] > grid_max + TOL_BOUND:
            viol.append(f"t{t+1} 电网购电 {g[t]:.6f} 越界 [0, {grid_max}]")
        if c[t] < -TOL_BOUND or c[t] > pmax + TOL_BOUND:
            viol.append(f"t{t+1} 充电 {c[t]:.6f} 越界 [0, {pmax}]")
        if d[t] < -TOL_BOUND or d[t] > pmax + TOL_BOUND:
            viol.append(f"t{t+1} 放电 {d[t]:.6f} 越界 [0, {pmax}]")
        if x[t] < -TOL_BOUND or x[t] > pv[t] + TOL_BOUND:
            viol.append(f"t{t+1} 弃电 {x[t]:.6f} 越界 [0, {pv[t]}]")
        if soc_full[t] < soc_min - TOL_BOUND or soc_full[t] > cap + TOL_BOUND:
            viol.append(f"t{t+1} 储荷 {soc_full[t]:.6f} 越界 [{soc_min}, {cap}]")
    c3 = Check("V1.3", "变量上下限", "exact")
    (c3.ok(violations=0) if not viol else c3.fail(violations=len(viol), first=viol[:5]))
    checks.append(c3)

    c4 = Check("V1.4", "端面储荷条件", "exact")
    dev = [abs(soc_full[0] - soc0), abs(soc_full[T] - soc_end)]
    if max(dev) <= TOL_SOC:
        c4.ok(soc_init_dev=dev[0], soc_terminal_dev=dev[1], expected=(soc0, soc_end))
    else:
        c4.fail(soc_init_dev=dev[0], soc_terminal_dev=dev[1], expected=(soc0, soc_end))
    checks.append(c4)

    cost_recomputed = sum(price[t] * g[t] * dt for t in range(T))
    reported = float(solution["objective_cny"])
    c5 = Check("V1.5", "目标值独立重算", "exact")
    rel = abs(cost_recomputed - reported) / max(1.0, abs(reported))
    if rel <= TOL_OBJ_REL:
        c5.ok(reported=reported, recomputed=round(cost_recomputed, 6), rel_error=rel)
    else:
        c5.fail(reported=reported, recomputed=round(cost_recomputed, 6), rel_error=rel)
    checks.append(c5)

    # ---------------- V2 上下界夹逼 ----------------
    lb_energy = min(price) * max(0.0, sum(load[t] - pv[t] for t in range(T)) * dt)
    lb_perperiod = sum(price[t] * max(0.0, load[t] - pv[t] - pmax) * dt for t in range(T))
    lower = max(lb_energy, lb_perperiod)
    ubs = _feasible_upper_bounds(periods, dt, pmax, cap, eta_c, eta_d, soc0, soc_min, soc_end)
    upper = min(ubs.values())
    upper_src = min(ubs, key=ubs.get)

    c6 = Check("V2.1", "解析下界 ≤ 方案成本", "sandwich")
    if reported >= lower - 1e-6:
        c6.ok(lower_bound=round(lower, 6), objective=reported, derivation="max(min_price×净负荷总量, Σ price×max(0,load−pv−P_max))")
    else:
        c6.fail(lower_bound=round(lower, 6), objective=reported, note="方案成本低于下界，说明存在算术矛盾")
    c6.note = (
        "下界是松弛结果（假设可以不损失地把能量挪到最低价时段），因此偏松；"
        "它的作用是兜住'成本算得比理论上限还低'这种硬错误。"
    )
    checks.append(c6)

    c7 = Check("V2.2", "方案成本 ≤ 可行上界", "sandwich")
    if reported <= upper + 1e-6:
        c7.ok(upper_bound=round(upper, 6), upper_source=upper_src, candidates={k: round(v, 6) for k, v in ubs.items()}, objective=reported)
    else:
        c7.fail(upper_bound=round(upper, 6), upper_source=upper_src, objective=reported, note="存在更便宜的可行方案，求解结果不最优")
    c7.note = "上界来自规则方案，必定可行；方案成本高于它就说明没解到最优。"
    checks.append(c7)

    c8 = Check("V2.3", "夹逼区间与相对间隙", "sandwich")
    gap = (upper - lower) / abs(reported) if reported else None
    c8.ok(lower_bound=round(lower, 6), upper_bound=round(upper, 6), objective=reported, relative_gap=None if gap is None else round(gap, 6))
    c8.note = "该间隙只用来衡量夹逼有多紧，不构成失败条件；精确性由 V1 与 V3 保证。"
    checks.append(c8)

    # ---------------- V3 退化复现（黑箱差分） ----------------
    degenerate = []
    if check_degenerate:
        import importlib.util

        mod_path = Path(__file__).resolve().parent / "mg_model.py"
        spec = importlib.util.spec_from_file_location("mg_model_blackbox", mod_path)
        mg_model = importlib.util.module_from_spec(spec)
        sys.modules["mg_model_blackbox"] = mg_model
        spec.loader.exec_module(mg_model)

        base_case = {
            "periods": [
                {
                    "period": t + 1,
                    "hour": periods[t].get("hour", ""),
                    "load_kw": load[t],
                    "pv_kw": pv[t],
                    "price_cny_per_kwh": price[t],
                }
                for t in range(T)
            ],
            "params": copy.deepcopy(params),
            "period_hours": dt,
        }

        def run_variant(mutate, closed_form, base_feasible=True):
            case = copy.deepcopy(base_case)
            note = mutate(case)
            try:
                sol = mg_model.solve(case)
            except Exception as exc:  # noqa: BLE001
                return {
                    "passed": False,
                    "skipped": not base_feasible,
                    "error": f"{type(exc).__name__}: {exc}",
                    "note": note,
                }
            got = float(sol["objective_cny"])
            exp = float(closed_form)
            rel = abs(got - exp) / max(1.0, abs(exp))
            return {
                "passed": rel <= TOL_DEGENERATE_REL,
                "skipped": False,
                "solver_objective": round(got, 6),
                "closed_form": round(exp, 6),
                "rel_error": rel,
                "note": note,
            }

        def d1(case):
            case["params"]["storage"]["power_kw"] = 0.0
            return "储能功率置 0：无法移峰，只能逐时买净缺口"

        def d2(case):
            for p in case["periods"]:
                p["price_cny_per_kwh"] = 0.80
            case["params"]["storage"]["soc_terminal_kwh"] = case["params"]["storage"]["soc_init_kwh"]
            return "电价拉平为 0.80：无套利空间，储能只损失电量"

        def d3(case):
            for p in case["periods"]:
                p["pv_kw"] = p["load_kw"] + 800.0
                p["price_cny_per_kwh"] = 0.80
            case["params"]["storage"]["soc_terminal_kwh"] = case["params"]["storage"]["soc_init_kwh"]
            return "光伏全天过剩（负荷+800）且电价拉平：最优是完全不购电、弃掉余量，成本应为 0"

        cf_d1 = sum(price[t] * max(0.0, load[t] - pv[t]) * dt for t in range(T))
        cf_d2 = 0.80 * sum(max(0.0, load[t] - pv[t]) for t in range(T)) * dt
        cf_d3 = 0.0

        base_feasible = c1.passed and c3.passed

        degenerate = [
            {"id": "V3.1", "name": "无储能功率", **run_variant(d1, cf_d1, base_feasible)},
            {"id": "V3.2", "name": "电价拉平（无套利空间）", **run_variant(d2, cf_d2, base_feasible)},
            {"id": "V3.3", "name": "光伏全网过剩且平价", **run_variant(d3, cf_d3, base_feasible)},
        ]

    for item in degenerate:
        ck = Check(item["id"], item["name"], "differential")
        if item.get("skipped"):
            ck.skip(reason="基础算例本身已不可行，退化复现无判定意义", error=item.get("error"))
        elif item.get("passed"):
            ck.ok(solver_objective=item.get("solver_objective"), closed_form=item.get("closed_form"), rel_error=item.get("rel_error"))
        else:
            ck.fail(**{k: v for k, v in item.items() if k not in ("id", "name", "passed")})
        ck.note = item.get("note", "")
        checks.append(ck)

    all_passed = all(c.passed for c in checks) and all(
        d.get("passed") or d.get("skipped") for d in degenerate
    )
    return {
        "passed": all_passed,
        "n_checks": len(checks),
        "n_failed": sum(1 for c in checks if not c.passed),
        "checks": [c.as_dict() for c in checks],
        "bounds": {
            "lower_bound_cny": round(lower, 6),
            "upper_bound_cny": round(upper, 6),
            "upper_source": upper_src,
            "objective_cny": reported,
        },
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="园区微网调度：独立校验器")
    ap.add_argument("--csv", required=True)
    ap.add_argument("--params", required=True)
    ap.add_argument("--solution", required=True)
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--skip-degenerate", action="store_true", help="跳过 V3 退化复现（较慢）")
    args = ap.parse_args(argv)

    solution = json.loads(Path(args.solution).read_text(encoding="utf-8"))
    result = verify(args.csv, args.params, solution, check_degenerate=not args.skip_degenerate)

    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print("=" * 68)
        print("独立校验报告")
        print("=" * 68)
        for c in result["checks"]:
            flag = "跳过" if c.get("skipped") else ("通过" if c["passed"] else "未通过")
            print(f"[{flag}] {c['id']} {c['name']}  ({c['kind']})")
            if c["detail"]:
                print(f"        {json.dumps(c['detail'], ensure_ascii=False)}")
            if c["note"]:
                print(f"        · {c['note']}")
        b = result["bounds"]
        print("-" * 68)
        print(f"夹逼区间: {b['lower_bound_cny']} 元 ≤ {b['objective_cny']} 元 ≤ {b['upper_bound_cny']} 元  （上界来源 {b['upper_source']}）")
        print(f"结论: {'全部通过' if result['passed'] else '存在未通过项'}  （{result['n_checks']} 项检查，{result['n_failed']} 项未通过）")
    return 0 if result["passed"] else 3


if __name__ == "__main__":
    raise SystemExit(main())
