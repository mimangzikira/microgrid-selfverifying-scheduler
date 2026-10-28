#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""园区微网调度 —— 编排器（AGH 调用的主入口）

把"读数据 → 求解 → 独立校验 → 出报告"串成一条可审计的流水线，并处理三类异常：
    1. 数据不合法      → 就地失败，输出结构化错误，不进入求解
    2. 求解器失败      → 降级为规则方案（明确标注 degraded），仍然过校验
    3. 校验未通过      → **回退重算一次**（去掉出错原因后重新干净求解），
                        重算结果仍须通过独立校验；仍不过就如实报告失败

`--inject` 可在跑之前故意污染输入，用来演示异常处理能力。
其中 `defect_*` 两类注入**不改输入数据**，而是故意在**生成侧建模**里漏掉一条约束——
用来证明独立校验器能抓住"生成侧自己发现不了"的错误。

用法：
    python tools/mg_run.py --csv data/park_day.csv --params data/params.json --outdir evidence/runs/normal
    python tools/mg_run.py ... --inject neg_price
    python tools/mg_run.py ... --inject defect_no_terminal_soc --outdir evidence/runs/defect_recover
退出码：0 正常（含 recovered 自我修复）；2 数据错误；3 校验未通过；4 已降级（可用但非最优）
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

import numpy as np

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

sys.path.insert(0, str(Path(__file__).resolve().parent))
import mg_model  # noqa: E402
import mg_verify  # noqa: E402
from mg_load import DataError, load_case  # noqa: E402

INJECTIONS = (
    "drop_rows",
    "bad_number",
    "neg_price",
    "empty_field",
    "infeasible_soc",
    "zero_storage_power",
    "pv_overload",
    "no_supply",
    "zero_storage_capacity",
    # ---- 生成侧建模缺陷（不改输入数据，只改生成侧建模）----
    # 用途：证明独立校验器能抓住"生成侧自己发现不了"的错误。
    # 这些缺陷让解看起来更便宜，但违反真实约束。
    "defect_no_terminal_soc",
    "defect_no_storage_power",
)

# 注入名 → mg_model 的缺陷名
DEFECT_INJECTIONS = {
    "defect_no_terminal_soc": "no_terminal_soc",
    "defect_no_storage_power": "no_storage_power",
}


class StageError(Exception):
    def __init__(self, code, message, detail=None):
        super().__init__(message)
        self.code, self.message, self.detail = code, message, detail or {}

    def as_dict(self):
        return {"code": self.code, "message": self.message, "detail": self.detail}


# --------------------------------------------------------------------------
# 降级方案：规则式移峰填谷。必定可行，因此必定能通过可行性校验。
# --------------------------------------------------------------------------
def build_fallback_plan(case: dict) -> dict:
    periods = case["periods"]
    st = case["params"]["storage"]
    dt = case["period_hours"]
    T = len(periods)

    load = [p["load_kw"] for p in periods]
    pv = [p["pv_kw"] for p in periods]
    price = [p["price_cny_per_kwh"] for p in periods]
    cap = float(st["capacity_kwh"])
    pmax = float(st["power_kw"])
    eta_c = float(st["eta_charge"])
    eta_d = float(st["eta_discharge"])
    soc_min = float(st["soc_min_kwh"])
    soc_end = float(st["soc_terminal_kwh"])
    soc = float(st["soc_init_kwh"])

    p_lo, p_hi = min(price), max(price)
    c = [0.0] * T
    d = [0.0] * T
    s = [soc]
    for t in range(T):
        if abs(p_hi - p_lo) < 1e-12:
            target = soc
        elif price[t] <= p_lo + 1e-12:
            target = cap
        elif price[t] >= p_hi - 1e-12:
            target = soc_min
        else:
            target = soc
        if target > soc:
            step = min(pmax * dt, (target - soc) / eta_c)
            c[t] = step / dt
            soc += eta_c * step
        elif target < soc:
            step = min(pmax * dt, (soc - target) * eta_d)
            d[t] = step / dt
            soc -= step / eta_d
        s.append(soc)

    for t in range(T - 1, -1, -1):
        if abs(soc - soc_end) <= 1e-9:
            break
        if soc < soc_end:
            room = min(pmax * dt - c[t] * dt, (soc_end - soc) / eta_c)
            if room > 0:
                c[t] += room / dt
                soc += eta_c * room
        else:
            room = min(pmax * dt - d[t] * dt, (soc - soc_end) * eta_d)
            if room > 0:
                d[t] += room / dt
                soc -= room / eta_d
        s[t + 1] = soc

    dispatch = []
    total_grid = 0.0
    total_charge = 0.0
    total_discharge = 0.0
    total_curtail = 0.0
    cost = 0.0
    for t in range(T):
        need = load[t] + c[t] - d[t] - pv[t]
        g = max(0.0, need)
        x = max(0.0, -need)
        cost += price[t] * g * dt
        total_grid += g * dt
        total_charge += c[t] * dt
        total_discharge += d[t] * dt
        total_curtail += x * dt
        dispatch.append(
            {
                "period": periods[t]["period"],
                "hour": periods[t].get("hour", ""),
                "load_kw": round(load[t], 9),
                "pv_kw": round(pv[t], 9),
                "price_cny_per_kwh": round(price[t], 9),
                "grid_import_kw": round(g, 9),
                "charge_kw": round(c[t], 9),
                "discharge_kw": round(d[t], 9),
                "curtail_kw": round(x, 9),
                "soc_kwh": round(s[t], 9),
            }
        )

    return {
        "solver": {"name": "rule_based_greedy_tou", "method": "tou_threshold", "status": 0, "success": True},
        "objective_cny": round(cost, 9),
        "dispatch": dispatch,
        "metrics": {
            "total_grid_import_kwh": round(total_grid, 9),
            "total_charge_kwh": round(total_charge, 9),
            "total_discharge_kwh": round(total_discharge, 9),
            "total_curtail_kwh": round(total_curtail, 9),
            "total_load_kwh": round(sum(load) * dt, 9),
            "total_pv_kwh": round(sum(pv) * dt, 9),
            "soc_terminal_kwh": round(s[T], 9),
            "avg_price_paid": round(cost / total_grid, 9) if total_grid > 0 else None,
        },
    }


# --------------------------------------------------------------------------
# 异常注入
# --------------------------------------------------------------------------
def apply_injection(csv_path: Path, params_path: Path, kind: str, dest: Path) -> dict:
    import csv as _csv

    dest.mkdir(parents=True, exist_ok=True)

    # 生成侧建模缺陷：不碰输入数据，只告诉调用方"求解时要带上缺陷"。
    # 这类注入的关键在于——输入数据完全正常，错的是**生成侧自己**，
    # 因此只有独立的一方去重算才能发现。
    if kind in DEFECT_INJECTIONS:
        return {
            "kind": kind,
            "description": mg_model.DEFECT_DESCRIPTIONS[DEFECT_INJECTIONS[kind]],
            "target": "generator",
            "defect": DEFECT_INJECTIONS[kind],
            "csv": str(csv_path),
            "params": str(params_path),
        }

    text = csv_path.read_text(encoding="utf-8-sig")
    with csv_path.open(encoding="utf-8-sig", newline="") as fh:
        rows = list(_csv.DictReader(fh))
    header = list(rows[0].keys()) if rows else []
    params = json.loads(params_path.read_text(encoding="utf-8"))
    desc = ""

    if kind == "drop_rows":
        rows = rows[:-3]
        desc = "删掉最后 3 行数据"
    elif kind == "bad_number":
        rows[7]["load_kw"] = "N/A"
        desc = "把第 8 行负荷改成文本 N/A"
    elif kind == "neg_price":
        rows[12]["price_cny_per_kwh"] = "-0.15"
        desc = "把第 13 行电价改成 -0.15"
    elif kind == "empty_field":
        rows[4]["pv_kw"] = ""
        desc = "把第 5 行光伏留空"
    elif kind == "infeasible_soc":
        params["storage"]["soc_terminal_kwh"] = params["storage"]["capacity_kwh"] + 500.0
        desc = "把端面储荷目标设成超过容量 500 kWh"
    elif kind == "zero_storage_power":
        params["storage"]["power_kw"] = 0.0
        desc = "把储能功率置 0（合法但退化）"
    elif kind == "pv_overload":
        for r in rows:
            r["pv_kw"] = str(float(r["load_kw"]) + 800.0)
        desc = "让光伏全天过剩（负荷+800）"
    elif kind == "no_supply":
        params["grid"]["import_limit_kw"] = 0.0
        params["storage"]["power_kw"] = 0.0
        desc = "电网购电上限与储能功率同时置 0：负荷无处可取，模型不可行"
    elif kind == "zero_storage_capacity":
        st = params["storage"]
        st["capacity_kwh"] = 0.0
        st["soc_init_kwh"] = 0.0
        st["soc_min_kwh"] = 0.0
        st["soc_terminal_kwh"] = 0.0
        desc = "储能容量置 0（合法但退化）：只该逐时买净缺口"
    else:
        raise StageError("E_INJECT_UNKNOWN", f"未知的注入类型 {kind}", {"allowed": list(INJECTIONS)})

    out_csv = dest / "injected.csv"
    with out_csv.open("w", encoding="utf-8", newline="") as fh:
        w = _csv.DictWriter(fh, fieldnames=header)
        w.writeheader()
        w.writerows(rows)
    out_params = dest / "injected_params.json"
    out_params.write_text(json.dumps(params, ensure_ascii=False, indent=2), encoding="utf-8")
    return {"kind": kind, "description": desc, "csv": str(out_csv), "params": str(out_params)}


# --------------------------------------------------------------------------
# 主流程
# --------------------------------------------------------------------------
def run(csv_path: Path, params_path: Path, outdir: Path, *, inject: str | None = None,
        max_attempts: int = 2) -> dict:
    t0 = time.time()
    outdir.mkdir(parents=True, exist_ok=True)
    stages: list[dict] = []

    def stage(name: str, status: str, **detail):
        rec = {"stage": name, "status": status, "elapsed_s": round(time.time() - t0, 3)}
        rec.update(detail)
        stages.append(rec)

    injection = None
    if inject:
        injection = apply_injection(Path(csv_path), Path(params_path), inject, outdir / "inputs")
        csv_path, params_path = Path(injection["csv"]), Path(injection["params"])
        stage("inject", "ok", kind=inject, description=injection["description"])

    # 阶段 1：数据校验
    try:
        case = load_case(csv_path, params_path)
    except DataError as exc:
        stage("load", "failed", error=exc.as_dict())
        manifest = {
            "schema": "mg.run/v1",
            "status": "failed",
            "failed_stage": "load",
            "error": exc.as_dict(),
            "injection": injection,
            "stages": stages,
            "elapsed_s": round(time.time() - t0, 3),
        }
        (outdir / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
        return manifest
    stage("load", "ok", summary=case["summary"])

    # 阶段 2：求解
    # defect 来自生成侧缺陷注入；正常运行时为 None。
    defect = injection.get("defect") if injection else None
    degrade_reason = None
    solution = None
    try:
        solution = mg_model.solve(case, defect=defect)
        stage("solve", "ok", objective_cny=solution["objective_cny"], solver=solution["solver"],
              **({"defect_injected": defect} if defect else {}))
    except Exception as exc:  # noqa: BLE001
        degrade_reason = {"code": getattr(exc, "code", type(exc).__name__), "message": str(exc)}
        solution = build_fallback_plan(case)
        stage("solve", "degraded", reason=degrade_reason, fallback_objective_cny=solution["objective_cny"])

    solution_payload = {
        "schema": "mg.solution/v1",
        "source": case["source"],
        "case_summary": case["summary"],
        "degraded": degrade_reason is not None,
        "defect_injected": defect,
        **solution,
    }
    (outdir / "solution.json").write_text(
        json.dumps(solution_payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    # 阶段 3：独立校验（不合格则回退重算一次：丢掉出错的原因，重新干净求解）
    verification = mg_verify.verify(csv_path, params_path, solution)

    def _failed_checks(v: dict) -> list[dict]:
        """把未通过的检查项摘成可审计的小结（id + 名称）。"""
        out = []
        for c in v.get("checks", []):
            if not c.get("passed"):
                out.append({"id": c.get("id"), "name": c.get("name")})
        return out

    attempts = [{"attempt": 1, "passed": verification["passed"],
                 "n_failed": verification["n_failed"], "failed_checks": _failed_checks(verification)}]
    repaired = False
    if not verification["passed"] and not solution_payload["degraded"] and max_attempts > 1:
        # 回退重算：**去掉导致出错的那个原因**再解一次。
        #   若第 1 次带着建模缺陷（defect），第 2 次就不带缺陷重新求解；
        #   若第 1 次本身是干净的（说明是求解器数值问题），保持原样再解一次。
        # 关键：重算结果**仍然必须通过独立校验**才算数——
        #       重试只是"再搜一次"，不是"信任的来源"。
        # 把第 1 次的完整校验结果留档，否则它会被第 2 次覆写，
        # "是哪一项抓住的" 就查不到了。
        (outdir / "verification_attempt1.json").write_text(
            json.dumps(verification, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        stage("verify", "retrying", n_failed=verification["n_failed"],
              caught_by=[c["id"] for c in _failed_checks(verification)],
              dropped_defect=defect, attempt=2)
        try:
            solution = mg_model.solve(case, defect=None)
        except Exception as exc:  # noqa: BLE001
            degrade_reason = {"code": getattr(exc, "code", type(exc).__name__), "message": str(exc)}
            solution = build_fallback_plan(case)
        solution_payload = {
            "schema": "mg.solution/v1",
            "source": case["source"],
            "case_summary": case["summary"],
            "degraded": degrade_reason is not None,
            "defect_injected": None,
            **solution,
        }
        (outdir / "solution.json").write_text(
            json.dumps(solution_payload, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        verification = mg_verify.verify(csv_path, params_path, solution)
        attempts.append({"attempt": 2, "passed": verification["passed"],
                         "n_failed": verification["n_failed"], "failed_checks": _failed_checks(verification)})
        repaired = bool(verification["passed"])

    (outdir / "verification.json").write_text(
        json.dumps(verification, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    stage("verify", "ok" if verification["passed"] else "failed",
          n_checks=verification["n_checks"], n_failed=verification["n_failed"], attempts=attempts)

    # 阶段 4：结论
    if not verification["passed"]:
        status = "verification_failed"
    elif repaired:
        # 第 1 次没过、回退重算后通过了 —— 这是"算错能自己发现、自己回退重算"的实证
        status = "recovered"
    elif solution_payload["degraded"]:
        status = "degraded"
    else:
        status = "ok"

    manifest = {
        "schema": "mg.run/v1",
        "status": status,
        "injection": injection,
        "stages": stages,
        "attempts": attempts,
        "recovered": repaired,
        "defect_injected": defect,
        "objective_cny": solution["objective_cny"],
        "bounds": verification["bounds"],
        "degraded": solution_payload["degraded"],
        "degrade_reason": degrade_reason,
        "outputs": {
            "solution": str(outdir / "solution.json"),
            "verification": str(outdir / "verification.json"),
        },
        "elapsed_s": round(time.time() - t0, 3),
    }
    (outdir / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    return manifest


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="园区微网调度：读数据→求解→独立校验→出报告")
    ap.add_argument("--csv", required=True)
    ap.add_argument("--params", required=True)
    ap.add_argument("--outdir", required=True)
    ap.add_argument("--inject", choices=list(INJECTIONS), help="跑之前故意污染输入，用于演示异常处理")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)

    try:
        manifest = run(Path(args.csv), Path(args.params), Path(args.outdir), inject=args.inject)
    except StageError as exc:
        print(json.dumps({"ok": False, "error": exc.as_dict()}, ensure_ascii=False, indent=2))
        return 2

    if args.json:
        print(json.dumps(manifest, ensure_ascii=False, indent=2))
    else:
        icon = {"ok": "正常", "recovered": "已自我修复", "degraded": "已降级",
                "failed": "失败", "verification_failed": "校验未通过"}
        print("=" * 68)
        print(f"运行结论：{icon.get(manifest['status'], manifest['status'])}")
        if manifest.get("injection"):
            print(f"  注入异常：{manifest['injection']['kind']} —— {manifest['injection']['description']}")
        for s in manifest["stages"]:
            print(f"  [{s['status']:>9}] {s['stage']}  ({s['elapsed_s']}s)")
        if manifest["status"] == "failed":
            print(f"  错误：{manifest['error']['code']} {manifest['error']['message']}")
        else:
            b = manifest["bounds"]
            print(f"  目标成本：{manifest['objective_cny']} 元")
            print(f"  夹逼区间：{b['lower_bound_cny']} ≤ 目标 ≤ {b['upper_bound_cny']} 元")
            if manifest.get("recovered"):
                att = manifest.get("attempts") or []
                print(f"  自我修复：第 1 次校验未过（{att[0]['n_failed']} 项不过）"
                      f" → 回退重算 → 第 2 次通过")
            if manifest.get("degrade_reason"):
                print(f"  降级原因：{manifest['degrade_reason']['code']} {manifest['degrade_reason']['message']}")
        print(f"  产出目录：{args.outdir}")

    code = {"ok": 0, "recovered": 0, "degraded": 4, "verification_failed": 3, "failed": 2}[manifest["status"]]
    if not args.json:
        # 显式打出退出码：AGH/PowerShell 等外壳可能把非零码归一化为 1，
        # 只看 $LASTEXITCODE 会丢失粒度，所以把约定值写在输出里。
        print(f"  退出码：{code}  （0 正常（含自我修复）/ 2 数据错误 / 3 校验未通过 / 4 已降级）")
    return code


if __name__ == "__main__":
    raise SystemExit(main())
