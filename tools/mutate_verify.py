#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""生成与校验分离 —— 变异覆盖矩阵（mutation coverage）

目的：回答一个比"能跑通"更硬的问题——
      **独立校验器有没有盲区？**

做法：按**约束类别**枚举生成侧可能犯的错，逐个把一条正确方案改坏，
      看校验器是否**恰好**由预期的那一项抓住。
      每类缺陷都对应一条真实世界里会犯的错（漏约束 / 差一时段 / 系数写错）。

边界声明（重要）：
    本脚本测的是**校验器的覆盖面**，不是生成器。
    它不 import 生成侧的求解逻辑；基线方案由 mg_model.solve 给出，
    各变异体是对该方案的**针对性破坏**。
    因此结论是"校验器在测试的这几类错误上无盲区"，不是"校验器绝对无盲区"。

用法：
    python tools/mutate_verify.py [--out reports/mutation_report.json]
退出码：0 全部符合预期；1 有变异未被预期检查项抓住
"""
from __future__ import annotations

import argparse
import copy
import json
import sys
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))

import mg_model  # noqa: E402
import mg_verify  # noqa: E402
from mg_load import load_case  # noqa: E402

DATA_CSV = ROOT / "data" / "park_day.csv"
DATA_PARAMS = ROOT / "data" / "params.json"


# --------------------------------------------------------------------------
# 变异体定义
#   每个变异体：名字、类别、说明、以及"预期应由哪一项错误抓住"
#   expect_caught = 预期未通过的检查项 id 集合（下界要求：这些必须未通过）
# --------------------------------------------------------------------------
def _mutate_skip_balance(sol: dict) -> None:
    """漏掉功率平衡：把第 3 时段的购电少记 50 kW（负荷缺口凭空消失）。"""
    sol["dispatch"][2]["grid_import_kw"] = round(sol["dispatch"][2]["grid_import_kw"] - 50.0, 9)


def _mutate_off_by_one_recurrence(sol: dict) -> None:
    """储荷递推差一时段：把储荷曲线整体前移一格（经典的 off-by-one）。"""
    socs = [d["soc_kwh"] for d in sol["dispatch"]]
    for i, d in enumerate(sol["dispatch"]):
        d["soc_kwh"] = socs[i - 1] if i > 0 else socs[0]


def _mutate_power_over_limit(sol: dict) -> None:
    """漏掉储能功率上限：把第 8 时段充电放大到超过额定功率。"""
    sol["dispatch"][7]["charge_kw"] = 1200.0


def _mutate_grid_over_limit(sol: dict) -> None:
    """漏掉电网购电上限：把第 20 时段购电抬到超过并网容量。"""
    sol["dispatch"][19]["grid_import_kw"] = 2000.0


def _mutate_soc_over_capacity(sol: dict) -> None:
    """漏掉储荷上下限：把某时段储荷抬到超过储能容量。"""
    sol["dispatch"][11]["soc_kwh"] = 2500.0


def _mutate_curtail_over_pv(sol: dict) -> None:
    """弃电上限写错：让弃电量超过该时段光伏出力。"""
    t = 5
    sol["dispatch"][t]["curtail_kw"] = round(sol["dispatch"][t]["pv_kw"] + 30.0, 9)


def _mutate_terminal_soc(sol: dict) -> None:
    """漏掉端面储荷条件：期末储荷不回到目标值。"""
    sol["metrics"]["soc_terminal_kwh"] = round(sol["metrics"]["soc_terminal_kwh"] - 120.0, 9)


def _mutate_objective_coeff(sol: dict) -> None:
    """目标系数写错：报出的成本与实际不符（少报约 5%）。"""
    sol["objective_cny"] = round(sol["objective_cny"] * 0.95, 6)


MUTANTS = [
    ("M1", "功率平衡", "漏掉功率平衡：第 3 时段购电少记 50 kW", _mutate_skip_balance, {"V1.1"}),
    ("M2", "储荷递推", "差一时段：储荷曲线整体前移一格", _mutate_off_by_one_recurrence, {"V1.2"}),
    ("M3", "储能功率上限", "漏掉功率上限：第 8 时段充电 1200 kW", _mutate_power_over_limit, {"V1.3"}),
    ("M4", "电网购电上限", "漏掉购电上限：第 20 时段购电 2000 kW", _mutate_grid_over_limit, {"V1.3"}),
    ("M5", "储荷容量上限", "漏掉储荷上限：第 12 时段储荷 2500 kWh", _mutate_soc_over_capacity, {"V1.3"}),
    ("M6", "弃电上限", "弃电超过光伏：第 6 时段弃电 = 光伏 + 30", _mutate_curtail_over_pv, {"V1.3"}),
    ("M7", "端面储荷", "漏掉端面条件：期末储荷少 120 kWh", _mutate_terminal_soc, {"V1.4"}),
    ("M8", "目标值", "目标系数写错：成本少报 5%", _mutate_objective_coeff, {"V1.5"}),
]


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="校验器变异覆盖矩阵")
    ap.add_argument("--out", default=str(ROOT / "reports" / "mutation_report.json"))
    args = ap.parse_args(argv)

    case = load_case(DATA_CSV, DATA_PARAMS)
    base = mg_model.solve(case)

    # 基线：未被变异的方案必须全项通过，否则整个矩阵没有意义
    base_v = mg_verify.verify(DATA_CSV, DATA_PARAMS, base)
    if not base_v["passed"]:
        print("[中止] 基线方案本身就没通过校验，矩阵无意义")
        return 2
    print(f"基线：{base_v['n_checks']} 项全通过，成本 {base['objective_cny']} 元")
    print(f"校验器声明覆盖的检查项：{', '.join(c['id'] for c in base_v['checks'])}")
    print()

    results = []
    for mid, klass, desc, mutate, expect in MUTANTS:
        sol = copy.deepcopy(base)
        mutate(sol)
        v = mg_verify.verify(DATA_CSV, DATA_PARAMS, sol)
        caught = {c["id"] for c in v["checks"] if not c["passed"]}
        # 预期项必须全部被抓住
        ok = expect.issubset(caught)
        extra = caught - expect
        results.append({
            "id": mid, "class": klass, "desc": desc,
            "expected_caught_by": sorted(expect),
            "actually_caught_by": sorted(caught),
            "verifier_blocked": not v["passed"],
            "as_expected": ok,
            "also_caught_by": sorted(extra),
        })
        flag = "符合预期" if ok else "不符合预期"
        extra_note = f"（另由 {', '.join(sorted(extra))} 一并抓住）" if extra else ""
        print(f"  {mid} [{flag}] {klass}：{desc}")
        print(f"        预期 {', '.join(sorted(expect))} → 实际 {', '.join(sorted(caught)) or '无'}{extra_note}")

    print()
    n_ok = sum(1 for r in results if r["as_expected"])
    blocked = sum(1 for r in results if r["verifier_blocked"])
    classes = sorted({r["class"] for r in results})
    print(f"合计 {len(results)} 个变异体：{n_ok} 个被预期检查项抓住，{blocked} 个被校验器拦下")
    print(f"覆盖的缺陷类别（{len(classes)} 类）：{'、'.join(classes)}")
    print()
    print("声明：本矩阵说明校验器在上述类别上无盲区；")
    print('      不等于「校验器绝对无盲区」——未枚举到的错误类型仍可能有盲区。')

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    report = {
        "schema": "mg.mutationreport/v1",
        "baseline_objective_cny": base["objective_cny"],
        "baseline_checks_passed": base_v["passed"],
        "n_checks": base_v["n_checks"],
        "total": len(results),
        "as_expected": n_ok,
        "blocked_by_verifier": blocked,
        "covered_classes": classes,
        "caveat": "本矩阵只覆盖已枚举的缺陷类别；未枚举类型的盲区未被排除。",
        "mutants": results,
    }
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n报告已写入：{out}")
    return 0 if n_ok == len(results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
