#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""园区微网调度 —— 运行结果查看器

给智能体一个可靠的结果查看入口。

**为什么需要它**：在 Windows 上用 PowerShell 的 `Get-Content` 读 UTF-8 文件时，
Windows PowerShell 5.1 会按 ANSI（GBK）解码，中文全成乱码；
即使文件本身是完全合法的 UTF-8（已实测：`功率平衡残差` → `鍔熺巼骞宠　娈嬪樊`）。
本脚本自己以 UTF-8 打开文件并把输出重编码为 UTF-8，绕开这个坑。

用法：
    python tools/mg_show.py --run evidence/runs/agh_demo
    python tools/mg_show.py --run evidence/runs/agh_demo --json
    python tools/mg_show.py --run evidence/runs/agh_demo --section verify
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

STATUS_CN = {
    "ok": "正常",
    "recovered": "已自我修复",
    "degraded": "已降级",
    "verification_failed": "校验未通过",
    "failed": "失败",
}
EXIT_CODE = {"ok": 0, "recovered": 0, "degraded": 4, "verification_failed": 3, "failed": 2}


def _load(path: Path):
    if not path.is_file():
        return None
    # 显式用 utf-8-sig 读：既吃 UTF-8，也容忍别人加了 BOM
    return json.loads(path.read_text(encoding="utf-8-sig"))


def collect(run_dir: Path) -> dict:
    run_dir = Path(run_dir)
    manifest = _load(run_dir / "manifest.json")
    verification = _load(run_dir / "verification.json")
    solution = _load(run_dir / "solution.json")

    out = {
        "run_dir": str(run_dir),
        "has_manifest": manifest is not None,
        "has_verification": verification is not None,
        "has_solution": solution is not None,
    }
    if manifest:
        out["status"] = manifest.get("status")
        out["status_cn"] = STATUS_CN.get(manifest.get("status"), manifest.get("status"))
        out["exit_code"] = EXIT_CODE.get(manifest.get("status"))
        out["degraded"] = manifest.get("degraded")
        out["degrade_reason"] = manifest.get("degrade_reason")
        out["objective_cny"] = manifest.get("objective_cny")
        out["bounds"] = manifest.get("bounds")
        out["injection"] = manifest.get("injection")
        out["stages"] = manifest.get("stages")
        out["error"] = manifest.get("error")
        out["attempts"] = manifest.get("attempts")
        out["recovered"] = manifest.get("recovered")
        out["defect_injected"] = manifest.get("defect_injected")
    # 第 1 次的校验明细（回退重算时会另存，否则会被第 2 次覆写）
    attempt1 = _load(run_dir / "verification_attempt1.json")
    if attempt1:
        out["verify_attempt1_checks"] = attempt1.get("checks")
    if verification:
        out["verify_passed"] = verification.get("passed")
        out["verify_n_checks"] = verification.get("n_checks")
        out["verify_n_failed"] = verification.get("n_failed")
        out["verify_checks"] = verification.get("checks")
    if solution:
        out["solver"] = solution.get("solver")
        out["metrics"] = solution.get("metrics")
    return out


def render(info: dict) -> str:
    L = []
    icon = info.get("status_cn", "未知")
    L.append("=" * 70)
    L.append(f"运行目录：{info['run_dir']}")
    L.append(f"运行结论：{icon}（status={info.get('status')}，脚本退出码 {info.get('exit_code')}）")
    L.append("=" * 70)

    if info.get("injection"):
        inj = info["injection"]
        L.append(f"注入异常：{inj.get('kind')} —— {inj.get('description')}")
        L.append("")

    if info.get("error"):
        e = info["error"]
        L.append(f"错误：{e.get('code')} {e.get('message')}")
        L.append(f"明细：{json.dumps(e.get('detail'), ensure_ascii=False)}")
        L.append("")
        return "\n".join(L)

    L.append("【流程】")
    for s in info.get("stages") or []:
        extra = ""
        if s.get("stage") == "verify" and "n_failed" in s:
            # 回退重算那一行只有 n_failed、没有 n_checks，不能硬打 n_checks（否则显示 None）
            if s.get("n_checks") is None:
                caught = "、".join(s.get("caught_by") or [])
                extra = f"  未通过 {s.get('n_failed')} 项"
                if caught:
                    extra += f"（{caught}）"
                if s.get("dropped_defect"):
                    extra += f" → 丢弃缺陷 {s['dropped_defect']} 后重解"
            else:
                extra = f"  检查 {s.get('n_checks')} 项，未通过 {s.get('n_failed')} 项"
        if s.get("stage") == "solve" and s.get("reason"):
            extra = f"  降级：{s['reason'].get('code')}"
        if s.get("stage") == "inject":
            extra = f"  {s.get('kind')}"
        L.append(f"  [{s.get('status')}] {s.get('stage')}{extra}")
    L.append("")

    if info.get("degrade_reason"):
        L.append(f"降级原因：{info['degrade_reason'].get('code')} {info['degrade_reason'].get('message')}")
        L.append("")

    b = info.get("bounds")
    if b:
        L.append("【成本】")
        L.append(f"  方案日成本   {b.get('objective_cny')} 元")
        L.append(f"  夹逼区间     {b.get('lower_bound_cny')} ≤ 方案 ≤ {b.get('upper_bound_cny')} 元（上界来源 {b.get('upper_source')}）")
        L.append("")

    m = info.get("metrics")
    if m:
        L.append("【指标】")
        for k, label in (
            ("total_grid_import_kwh", "电网购电"),
            ("total_charge_kwh", "储能充电"),
            ("total_discharge_kwh", "储能放电"),
            ("total_curtail_kwh", "光伏弃电"),
            ("avg_price_paid", "平均购电价"),
            ("soc_terminal_kwh", "期末储荷"),
        ):
            if m.get(k) is not None:
                L.append(f"  {label:<10} {m.get(k)}")
        L.append("")

    if info.get("has_verification"):
        L.append(f"【独立校验】{info.get('verify_n_checks')} 项，未通过 {info.get('verify_n_failed')} 项 —— "
                 f"{'全部通过' if info.get('verify_passed') else '存在未通过项'}")
        for c in info.get("verify_checks") or []:
            flag = "跳过" if c.get("skipped") else ("通过" if c.get("passed") else "未通过")
            L.append(f"  [{flag}] {c.get('id')} {c.get('name')}（{c.get('kind')}）")
            if not c.get("passed"):
                L.append(f"         明细：{json.dumps(c.get('detail'), ensure_ascii=False)}")
    return "\n".join(L)


def render_evidence(info: dict) -> str:
    """面试/录片专用：只打印"这个运行为什么可信"的关键几行。

    用途：录演示视频或答辩时，直接把这个窗口录进画面 / 截图当素材。
    只输出能被一句话讲清的证据行，不输出表格化的细节。
    """
    L = []
    st = info.get("status")
    cn = info.get("status_cn") or st
    code = info.get("exit_code")
    L.append("=" * 62)
    L.append("运行结论")
    L.append("=" * 62)
    L.append(f"  {cn}   （退出码 {code}）")
    L.append("")

    inj = info.get("injection")
    if inj:
        L.append(f"  注入：{inj.get('kind')}")
        L.append(f"        {inj.get('description')}")
        L.append("")

    # 自我修复：这是最关键的一段
    atts = info.get("attempts") or []
    if info.get("recovered") and len(atts) >= 2:
        L.append("=" * 62)
        L.append("自我修复（第 1 次被抓住 → 回退重算 → 第 2 次通过）")
        L.append("=" * 62)
        for a in atts:
            fc = "、".join(f"{c.get('id')} {c.get('name')}" for c in (a.get("failed_checks") or []))
            mark = "通过" if a.get("passed") else "未通过"
            tail = f"   ← 由 {fc} 抓住" if fc else ""
            L.append(f"  第 {a.get('attempt')} 次：{mark}{tail}")
        if info.get("defect_injected"):
            L.append(f"  本轮注入的建模缺陷：{info['defect_injected']}")
        L.append("")

    # 校验器抓住缺陷时的原文（最能说明问题的一行）
    v1 = info.get("verify_attempt1_checks")
    if v1:
        bad = [c for c in v1 if not c.get("passed") and not c.get("skipped")]
        if bad:
            L.append("=" * 62)
            L.append("校验器抓到的那一项（原文）")
            L.append("=" * 62)
            for c in bad:
                L.append(f"  {c.get('id')}  {c.get('name')}   passed = false")
                det = c.get("detail") or {}
                for k, v in det.items():
                    if k == "first":
                        for line in v[:3]:
                            L.append(f"      {line}")
                    elif k == "expected":
                        L.append(f"      expected = {v}")
                    else:
                        L.append(f"      {k} = {v}")
            L.append("")

    b = info.get("bounds")
    if b:
        L.append("=" * 62)
        L.append("最终交付的方案")
        L.append("=" * 62)
        L.append(f"  日成本 {b.get('objective_cny')} 元")
        L.append(f"  夹逼区间 {b.get('lower_bound_cny')} ≤ 方案 ≤ {b.get('upper_bound_cny')} 元")
    return "\n".join(L)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="查看某次运行的结论、校验与指标")
    ap.add_argument("--run", required=True, help="运行产出目录")
    ap.add_argument("--json", action="store_true", help="输出结构化 JSON")
    ap.add_argument("--evidence", action="store_true",
                    help="录片/答辩专用：只打印关键证据行，方便直接录进画面或截图")
    ap.add_argument("--section", choices=["summary", "verify", "metrics"], default="summary")
    args = ap.parse_args(argv)

    run_dir = Path(args.run)
    if not run_dir.is_dir():
        print(f"[错误] 目录不存在：{run_dir}", file=sys.stderr)
        return 2

    info = collect(run_dir)
    if not (info["has_manifest"] or info["has_verification"]):
        print(f"[错误] 该目录下没有 manifest.json / verification.json：{run_dir}", file=sys.stderr)
        return 2

    if args.evidence:
        print(render_evidence(info))
    elif args.json:
        payload = info
        if args.section == "verify":
            payload = {"run_dir": info["run_dir"], "passed": info.get("verify_passed"),
                       "n_checks": info.get("verify_n_checks"), "n_failed": info.get("verify_n_failed"),
                       "checks": info.get("verify_checks")}
        elif args.section == "metrics":
            payload = {"run_dir": info["run_dir"], "metrics": info.get("metrics"), "bounds": info.get("bounds")}
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        print(render(info))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
