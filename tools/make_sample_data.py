#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""生成一份可用于公开仓库的示例算例（synth_day.csv）。

为什么不直接放原始算例数据：
    本项目的原始算例是在「2026 年高教社杯全国大学生数学建模竞赛 C 题」
    官方附件数据基础上聚合整理而成的，其版权属于赛事组委会，
    因此**不随本仓库分发**。

    为了让任何人 clone 下来就能立刻跑通，这里提供一个**同格式的合成算例**：
    24 时段、负荷呈日间高夜间的双峰形态、光伏午间出力、三档分时电价。
    数值与原始算例不同，因此**跑出来的成本数字会不一样**，但约束结构、
    校验逻辑、自我修复机制完全一致——本仓库的结论不依赖具体数值。

用法：
    python tools/make_sample_data.py            # 生成 data/synth_day.csv
    python tools/make_sample_data.py --out x.csv
"""
from __future__ import annotations

import argparse
import csv
import math
import sys
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parent.parent

# 24 时段典型日形态（合成，非实测）
LOAD_SHAPE = [
    0.70, 0.66, 0.63, 0.62, 0.64, 0.70,   # 00-05 夜间低谷
    0.80, 0.92, 1.00, 0.97, 0.90, 0.88,   # 06-11 早高峰
    0.86, 0.84, 0.86, 0.90, 0.96, 0.99,   # 12-17
    0.97, 0.92, 0.86, 0.80, 0.76, 0.72,   # 18-23 晚高峰后回落
]
# 光伏：只在 06-18 之间出力，正午最强
PV_SHAPE = [
    0.0, 0.0, 0.0, 0.0, 0.0, 0.0,
    0.04, 0.18, 0.42, 0.66, 0.85, 0.96,
    1.00, 0.95, 0.82, 0.62, 0.38, 0.16,
    0.04, 0.0, 0.0, 0.0, 0.0, 0.0,
]
# 三档电价：谷 / 平 / 峰
PRICE = [
    0.30, 0.30, 0.30, 0.30, 0.30, 0.30,   # 00-05 谷
    0.30, 0.72, 0.72, 0.72, 0.72, 0.72,   # 06-11 平/峰
    0.72, 0.72, 0.72, 0.72, 1.20, 1.20,   # 12-17 峰
    1.20, 1.20, 0.72, 0.72, 0.30, 0.30,   # 18-23
]


def build(load_peak_kw: float = 620.0, pv_peak_kw: float = 480.0) -> list[dict]:
    rows = []
    for t in range(24):
        rows.append(
            {
                "period": t + 1,
                "hour": f"{t:02d}:00",
                "load_kw": round(load_peak_kw * LOAD_SHAPE[t], 1),
                "pv_kw": round(pv_peak_kw * PV_SHAPE[t], 1),
                "price_cny_per_kwh": PRICE[t],
            }
        )
    return rows


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="生成公开仓库用的合成示例算例")
    # 默认落成 park_day.csv：代码与测试都按这个文件名读取。
    # 它是**合成**算例，不是原始赛事数据（原始数据版权属组委会，不随仓库分发）。
    ap.add_argument("--out", default=str(ROOT / "data" / "park_day.csv"))
    ap.add_argument("--load-peak", type=float, default=620.0)
    ap.add_argument("--pv-peak", type=float, default=480.0)
    args = ap.parse_args(argv)

    rows = build(args.load_peak, args.pv_peak)
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=["period", "hour", "load_kw", "pv_kw", "price_cny_per_kwh"])
        w.writeheader()
        w.writerows(rows)

    print(f"[已生成] {out}")
    print(f"  24 时段；负荷峰值 {args.load_peak} kW，光伏峰值 {args.pv_peak} kW")
    print("  电价三档 0.30 / 0.72 / 1.20 元每千瓦时")
    print("")
    print("  注意：这是**合成算例**，用于让仓库 clone 即可跑通；它不是原始赛事数据。")
    print("       因此跑出的成本与 README「数据来源」里记录的原始结果不同。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
