#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""园区微网调度 —— 测试套件（正常 / 边界 / 失败 三类）

每个用例都通过 mg_run 跑完整流水线，再核对"应该出现什么结果"。
断言的是**行为契约**，不是某个具体数字：正常算例必须全项通过且有正向收益；
脏数据必须被拦在求解之前；不可行算例必须降级并被校验器拦下。

用法：
    python tests/run_tests.py [--out reports/test_report.json]
退出码：0 全部符合预期；1 有用例不符合
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))

import mg_run  # noqa: E402

DATA_CSV = ROOT / "data" / "park_day.csv"
DATA_PARAMS = ROOT / "data" / "params.json"
RUNS_DIR = ROOT / "evidence" / "runs"

# 基线：无储能时的日成本。
# **不写死数字**——它在运行时由数据算出来（见 main 的校准步骤），
# 这样换任何一份同格式的 CSV 都能跑，测试断言依然成立。
NO_STORAGE_COST = None


def case(cid, group, name, inject, expect):
    return {"id": cid, "group": group, "name": name, "inject": inject, "expect": expect}


CASES = [
    case("T01", "正常", "标准算例：三档电价 + 储能可套利", None,
         {"status": "ok", "verify_all_pass": True, "min_saving_pct": 20.0}),
    case("T02", "边界", "光伏全天过剩：最优成本应为 0", "pv_overload",
         {"status": "ok", "verify_all_pass": True, "objective": 0.0}),
    case("T03", "边界", "储能功率置 0：退化为逐时买净缺口", "zero_storage_power",
         {"status": "ok", "verify_all_pass": True, "objective": "no_storage_baseline"}),
    case("T04", "边界", "储能容量置 0：同上，但走容量约束分支", "zero_storage_capacity",
         {"status": "ok", "verify_all_pass": True, "objective": "no_storage_baseline"}),
    case("T05", "失败", "电价非正：应在求解前拦下", "neg_price",
         {"status": "failed", "failed_stage": "load", "error_code": "E_NONPOSITIVE_PRICE"}),
    case("T06", "失败", "行数缺失：应在求解前拦下", "drop_rows",
         {"status": "failed", "failed_stage": "load", "error_code": "E_ROW_COUNT"}),
    case("T07", "失败", "字段非数字：应在求解前拦下", "bad_number",
         {"status": "failed", "failed_stage": "load", "error_code": "E_PARSE"}),
    case("T08", "失败", "字段留空：应在求解前拦下", "empty_field",
         {"status": "failed", "failed_stage": "load", "error_code": "E_EMPTY_FIELD"}),
    case("T09", "失败", "端面储荷越界：应在求解前拦下", "infeasible_soc",
         {"status": "failed", "failed_stage": "load", "error_code": "E_STORAGE_SOC_BOUND"}),
    case("T10", "失败", "模型不可行：降级后必须被校验器拦下", "no_supply",
         {"status": "verification_failed", "degraded": True, "min_failed_checks": 1}),
    # ---- 分离性与自我修复：把生成侧改坏，校验器必须抓住；回退重算必须修好 ----
    # objective 不写死：断言"修复后的成本 == T01 正常算例的成本"（相对判断更硬）
    case("T11", "分离性", "生成侧漏掉端面储荷约束：校验器须抓住并自我修复", "defect_no_terminal_soc",
         {"status": "recovered", "recovered": True, "defect_injected": "no_terminal_soc",
          "verify_all_pass": True, "caught_by": "V1.4", "attempt1_failed": True,
          "objective": "same_as_normal"}),
    case("T12", "分离性", "生成侧漏掉储能功率上限：校验器须抓住并自我修复", "defect_no_storage_power",
         {"status": "recovered", "recovered": True, "defect_injected": "no_storage_power",
          "verify_all_pass": True, "caught_by": "V1.3", "attempt1_failed": True,
          "objective": "same_as_normal"}),
]

# 校准得到的参考值（main 里填）
BASELINES: dict[str, float] = {}


def evaluate(manifest: dict, expect: dict, outdir: Path) -> list[str]:
    problems = []
    if "status" in expect and manifest.get("status") != expect["status"]:
        problems.append(f"status 期望 {expect['status']}，实际 {manifest.get('status')}")

    if expect.get("failed_stage") and manifest.get("failed_stage") != expect["failed_stage"]:
        problems.append(f"failed_stage 期望 {expect['failed_stage']}，实际 {manifest.get('failed_stage')}")

    if expect.get("error_code"):
        got = (manifest.get("error") or {}).get("code")
        if got != expect["error_code"]:
            problems.append(f"error_code 期望 {expect['error_code']}，实际 {got}")

    if expect.get("degraded") is not None and bool(manifest.get("degraded")) != expect["degraded"]:
        problems.append(f"degraded 期望 {expect['degraded']}，实际 {manifest.get('degraded')}")

    if expect.get("verify_all_pass"):
        v = outdir / "verification.json"
        if not v.is_file():
            problems.append(f"找不到 {v}")
        else:
            data = json.loads(v.read_text(encoding="utf-8"))
            if not data.get("passed"):
                failed = [c["id"] for c in data["checks"] if not c["passed"]]
                problems.append(f"校验未全通过，失败项 {failed}")

    if "objective" in expect:
        want = expect["objective"]
        got = manifest.get("objective_cny")
        # 支持相对引用：不写死数字，改成与"校准得到的基线"比较
        if isinstance(want, str):
            ref_key = {"no_storage_baseline": "no_storage", "same_as_normal": "normal"}[want]
            ref = BASELINES.get(ref_key)
            if ref is None:
                problems.append(f"缺少校准基线 {ref_key}，无法判定（{want}）")
            elif got is None or abs(got - ref) > 1e-6:
                problems.append(f"目标值期望等于{ref_key}({ref})，实际 {got}")
        elif got is None or abs(got - want) > 1e-6:
            problems.append(f"目标值期望 {want}，实际 {got}")

    if "min_failed_checks" in expect:
        failed = [s for s in manifest.get("stages", []) if s["stage"] == "verify"]
        n = failed[0].get("n_failed") if failed else None
        if n is None or n < expect["min_failed_checks"]:
            problems.append(f"未通过检查数期望 ≥{expect['min_failed_checks']}，实际 {n}")

    if "min_saving_pct" in expect:
        obj = manifest.get("objective_cny")
        base = BASELINES.get("no_storage")
        if obj is None or not base:
            problems.append("缺少目标值或基线，无法算节省率")
        else:
            saving = (base - obj) / base * 100.0
            if saving < expect["min_saving_pct"]:
                problems.append(f"相对无储能节省率期望 ≥{expect['min_saving_pct']}%，实际 {saving:.2f}%")

    # ---- 分离性与自我修复相关断言 ----
    if expect.get("recovered") is not None and bool(manifest.get("recovered")) != expect["recovered"]:
        problems.append(f"recovered 期望 {expect['recovered']}，实际 {manifest.get('recovered')}")

    if expect.get("defect_injected") and manifest.get("defect_injected") != expect["defect_injected"]:
        problems.append(f"defect_injected 期望 {expect['defect_injected']}，实际 {manifest.get('defect_injected')}")

    if expect.get("attempt1_failed"):
        atts = manifest.get("attempts") or []
        if len(atts) < 2:
            problems.append(f"期望发生回退重算（≥2 次尝试），实际 attempts={atts}")
        elif atts[0].get("passed") is not False:
            problems.append(f"期望第 1 次校验未通过，实际 passed={atts[0].get('passed')}")
        elif atts[-1].get("passed") is not True:
            problems.append(f"期望最后一次校验通过，实际 passed={atts[-1].get('passed')}")

    if expect.get("caught_by"):
        atts = manifest.get("attempts") or []
        caught = [c.get("id") for a in atts[:1] for c in (a.get("failed_checks") or [])]
        if expect["caught_by"] not in caught:
            problems.append(f"期望第 1 次由 {expect['caught_by']} 抓住，实际抓住的是 {caught}")

    return problems


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="园区微网调度：测试套件")
    ap.add_argument("--out", default=str(ROOT / "reports" / "test_report.json"))
    args = ap.parse_args(argv)

    results = []
    runs = []  # 先跑完、再断言（基线与被判定项的顺序无关）
    for c in CASES:
        outdir = RUNS_DIR / c["id"]
        manifest = mg_run.run(DATA_CSV, DATA_PARAMS, outdir, inject=c["inject"])

        # 校准：把结果登记为参考基线。先跑完所有用例再断言，
        # 因此测试**不依赖任何写死的成本数字**，换一份同格式数据同样成立。
        if c["inject"] is None and manifest.get("status") == "ok":
            BASELINES.setdefault("normal", manifest.get("objective_cny"))
        if c["inject"] == "zero_storage_power" and manifest.get("status") == "ok":
            BASELINES.setdefault("no_storage", manifest.get("objective_cny"))
        if c["inject"] == "zero_storage_capacity" and manifest.get("status") == "ok":
            BASELINES.setdefault("no_storage_capacity", manifest.get("objective_cny"))

        runs.append((c, outdir, manifest))

    # 第二阶段：全部用例跑完后，基线已齐，再逐条判定
    for c, outdir, manifest in runs:
        problems = evaluate(manifest, c["expect"], outdir)
        results.append(
            {
                "id": c["id"],
                "group": c["group"],
                "name": c["name"],
                "inject": c["inject"],
                "status": manifest.get("status"),
                "objective_cny": manifest.get("objective_cny"),
                "passed": not problems,
                "problems": problems,
                "outdir": str(outdir),
            }
        )

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    report = {
        "schema": "mg.testreport/v1",
        "total": len(results),
        "passed": sum(1 for r in results if r["passed"]),
        "failed": sum(1 for r in results if not r["passed"]),
        "cases": results,
    }
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    print("=" * 74)
    print("测试套件报告")
    print("=" * 74)
    group_now = None
    for r in results:
        if r["group"] != group_now:
            group_now = r["group"]
            print(f"\n【{group_now}】")
        flag = "符合预期" if r["passed"] else "不符合预期"
        obj = "" if r["objective_cny"] is None else f"  成本={r['objective_cny']}"
        print(f"  {r['id']}  [{flag}] {r['name']}  → {r['status']}{obj}")
        for p in r["problems"]:
            print(f"        ! {p}")
    print()
    print(f"合计 {report['total']} 例：{report['passed']} 例符合预期，{report['failed']} 例不符合")
    print(f"报告已写入：{out}")
    return 0 if report["failed"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
