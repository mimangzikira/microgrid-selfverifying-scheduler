#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""园区微网调度 —— 报告生成

读 solution.json 与 verification.json，产出：
    report.md   人读的结论
    dispatch.png 调度曲线图（负荷/光伏/电价/购电/储荷）

用法：
    python tools/mg_report.py --run evidence/runs/normal
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

matplotlib.rcParams["font.sans-serif"] = ["Microsoft YaHei", "SimHei", "DejaVu Sans"]
matplotlib.rcParams["axes.unicode_minus"] = False


def build(run_dir: Path) -> dict:
    run_dir = Path(run_dir)
    solution = json.loads((run_dir / "solution.json").read_text(encoding="utf-8"))
    verification = json.loads((run_dir / "verification.json").read_text(encoding="utf-8"))
    manifest_path = run_dir / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8")) if manifest_path.is_file() else {}

    disp = solution["dispatch"]
    m = solution["metrics"]
    summary = solution["case_summary"]
    b = verification["bounds"]

    baseline = b.get("upper_bound_cny")
    obj = solution["objective_cny"]
    # 无储能基准取自 V2.2 的候选集
    no_storage = None
    for c in verification.get("checks", []):
        if c["id"] == "V2.2":
            cand = c["detail"].get("candidates", {})
            no_storage = cand.get("no_storage")
    saving = None
    if no_storage:
        saving = round((no_storage - obj) / no_storage * 100.0, 3)

    lines = []
    lines.append("# 园区微网日调度 —— 运行报告\n")
    lines.append(f"- 算例：`{solution['source']['csv']}`")
    lines.append(f"- 运行结论：**{manifest.get('status', '未知')}**")
    lines.append(f"- 求解器：{solution['solver']['name']} / {solution['solver']['method']}")
    lines.append(f"- 降级：{'是' if solution.get('degraded') else '否'}\n")

    lines.append("## 一、算例概况\n")
    lines.append("| 项目 | 数值 |")
    lines.append("|---|---|")
    lines.append(f"| 时段数 | {summary['n_periods']} 段 × {summary['period_hours']} h |")
    lines.append(f"| 总负荷 | {summary['total_load_kwh']} kWh |")
    lines.append(f"| 总光伏 | {summary['total_pv_kwh']} kWh |")
    lines.append(f"| 净负荷 | {summary['net_load_kwh']} kWh |")
    lines.append(f"| 峰值负荷 | {summary['peak_load_kw']} kW |")
    lines.append(f"| 电价档位 | {', '.join(str(x) for x in summary['price_tiers'])} 元/kWh |")
    lines.append(f"| 储能容量 / 功率 | {solution['case_summary']['soc_init_kwh']} kWh 初值，往返效率见参数 |")

    lines.append("\n## 二、调度结果\n")
    lines.append("| 指标 | 数值 |")
    lines.append("|---|---|")
    lines.append(f"| **日购电成本** | **{obj} 元** |")
    lines.append(f"| 电网购电量 | {m['total_grid_import_kwh']} kWh |")
    lines.append(f"| 储能充电 / 放电 | {m['total_charge_kwh']} / {m['total_discharge_kwh']} kWh |")
    lines.append(f"| 光伏弃电 | {m['total_curtail_kwh']} kWh |")
    lines.append(f"| 平均购电电价 | {m['avg_price_paid']} 元/kWh |")
    lines.append(f"| 期末储荷 | {m['soc_terminal_kwh']} kWh |")
    if no_storage:
        lines.append(f"| 无储能基准成本 | {no_storage} 元 |")
    if saving is not None:
        lines.append(f"| **相对无储能节省** | **{saving}%** |")

    lines.append("\n## 三、独立校验\n")
    n_skip = sum(1 for c in verification["checks"] if c.get("skipped"))
    lines.append(
        f"共 {verification['n_checks']} 项，通过 {verification['n_checks'] - verification['n_failed'] - n_skip} 项，"
        f"跳过 {n_skip} 项，未通过 {verification['n_failed']} 项 → "
        f"**{'全部通过' if verification['passed'] else '存在未通过项'}**\n"
    )
    lines.append("| 检查 | 类型 | 结论 | 关键证据 |")
    lines.append("|---|---|---|---|")
    for c in verification["checks"]:
        flag = "跳过" if c.get("skipped") else ("通过" if c["passed"] else "**未通过**")
        ev = json.dumps(c["detail"], ensure_ascii=False)
        if len(ev) > 150:
            ev = ev[:147] + "..."
        lines.append(f"| {c['id']} {c['name']} | {c['kind']} | {flag} | `{ev}` |")

    lines.append("\n### 夹逼区间\n")
    lines.append(f"```\n{b['lower_bound_cny']} 元（下界） ≤ {b['objective_cny']} 元（方案） ≤ {b['upper_bound_cny']} 元（上界，来自 {b['upper_source']}）\n```")
    lines.append(
        "\n下界由解析式给出（假设可以把能量无损地挪到最低价时段），因此偏松；"
        "上界来自必定可行的规则方案。夹逼的作用是兜住'成本比理论下界还低'这类硬错误，"
        "精确性由 V1（残差）与 V3（退化闭式复现）保证。\n"
    )

    lines.append("\n## 四、逐时调度\n")
    lines.append("| 时段 | 时刻 | 负荷 kW | 光伏 kW | 电价 | 购电 kW | 充电 kW | 放电 kW | 弃电 kW | 储荷 kWh |")
    lines.append("|---|---|---|---|---|---|---|---|---|---|")
    for r in disp:
        lines.append(
            f"| {r['period']} | {r['hour']} | {r['load_kw']:.1f} | {r['pv_kw']:.1f} | {r['price_cny_per_kwh']:.2f} | "
            f"{r['grid_import_kw']:.2f} | {r['charge_kw']:.2f} | {r['discharge_kw']:.2f} | {r['curtail_kw']:.2f} | {r['soc_kwh']:.1f} |"
        )

    (run_dir / "report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")

    # ---------------- 图 ----------------
    t = [r["period"] for r in disp]
    fig, axes = plt.subplots(3, 1, figsize=(12, 9), sharex=True)

    ax = axes[0]
    ax.plot(t, [r["load_kw"] for r in disp], label="负荷", color="#c0392b", lw=2)
    ax.plot(t, [r["pv_kw"] for r in disp], label="光伏", color="#e67e22", lw=2)
    ax.plot(t, [r["grid_import_kw"] for r in disp], label="电网购电", color="#2c3e50", lw=2, ls="--")
    ax.set_ylabel("功率 (kW)")
    ax.set_title("园区微网日调度：功率平衡")
    ax.legend(loc="upper left", ncol=3)
    ax.grid(alpha=0.3)

    ax = axes[1]
    ax.step(t, [r["price_cny_per_kwh"] for r in disp], where="mid", color="#8e44ad", lw=2, label="电价")
    ax.set_ylabel("电价 (元/kWh)")
    ax2 = ax.twinx()
    ax2.bar(t, [r["charge_kw"] for r in disp], color="#27ae60", alpha=0.6, label="充电")
    ax2.bar(t, [-r["discharge_kw"] for r in disp], color="#2980b9", alpha=0.6, label="放电")
    ax2.set_ylabel("储能功率 (kW)")
    ax.legend(loc="upper left")
    ax2.legend(loc="upper right")
    ax.grid(alpha=0.3)

    ax = axes[2]
    ax.plot(t, [r["soc_kwh"] for r in disp], color="#16a085", lw=2, marker="o", ms=3)
    ax.set_ylabel("储荷 (kWh)")
    ax.set_xlabel("时段")
    ax.set_title("储荷轨迹")
    ax.grid(alpha=0.3)
    ax.set_xticks(t)

    fig.tight_layout()
    fig.savefig(run_dir / "dispatch.png", dpi=130)
    plt.close(fig)

    return {"report": str(run_dir / "report.md"), "chart": str(run_dir / "dispatch.png"), "saving_pct": saving}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="园区微网调度：报告生成")
    ap.add_argument("--run", required=True, help="运行产出目录")
    args = ap.parse_args(argv)
    info = build(Path(args.run))
    print("[报告已生成]")
    print(f"  报告 : {info['report']}")
    print(f"  图   : {info['chart']}")
    if info["saving_pct"] is not None:
        print(f"  相对无储能节省: {info['saving_pct']}%")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
