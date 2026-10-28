#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""生成项目展示页（单文件自包含 HTML）

把调度曲线图以 base64 内嵌，产出**一个 HTML 文件**——不依赖任何外部资源，
上传到任意静态托管、或者直接双击打开都能看。

用法：
    python tools/build_site.py
    python tools/build_site.py --chart evidence/runs/normal/dispatch.png --out site/index.html
"""
from __future__ import annotations

import argparse
import base64
import sys
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parent.parent

PAGE = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>会自己验算自己的园区微网调度智能体 · 然智核 U070</title>
<style>
  :root {{
    --ink: #1a1d21; --muted: #5c6672; --line: #e3e7ec; --bg: #ffffff;
    --soft: #f6f8fa; --accent: #1a6fd4; --warn: #b4341f; --ok: #1d7a4c;
  }}
  * {{ box-sizing: border-box; }}
  body {{
    margin: 0; background: var(--bg); color: var(--ink);
    font: 16px/1.75 -apple-system, "Segoe UI", "Microsoft YaHei", "PingFang SC", sans-serif;
  }}
  .wrap {{ max-width: 860px; margin: 0 auto; padding: 56px 24px 96px; }}
  header {{ border-bottom: 2px solid var(--ink); padding-bottom: 24px; margin-bottom: 40px; }}
  .tag {{
    display: inline-block; font-size: 13px; color: var(--muted);
    letter-spacing: .04em; margin-bottom: 14px;
  }}
  h1 {{ font-size: 32px; line-height: 1.35; margin: 0 0 12px; letter-spacing: -.01em; }}
  .lede {{ font-size: 17px; color: var(--muted); margin: 0; }}
  h2 {{
    font-size: 21px; margin: 52px 0 16px; padding-top: 22px;
    border-top: 1px solid var(--line);
  }}
  h2:first-of-type {{ border-top: none; padding-top: 0; }}
  h3 {{ font-size: 17px; margin: 28px 0 10px; }}
  p {{ margin: 0 0 14px; }}
  ul, ol {{ margin: 0 0 14px; padding-left: 22px; }}
  li {{ margin-bottom: 6px; }}
  code {{
    background: var(--soft); padding: 2px 6px; border-radius: 4px;
    font: 14px/1.5 "Cascadia Code", Consolas, Monaco, monospace;
  }}
  pre {{
    background: var(--soft); border: 1px solid var(--line); border-radius: 8px;
    padding: 18px 20px; overflow-x: auto; font-size: 13.5px; line-height: 1.65;
    font-family: "Cascadia Code", Consolas, Monaco, monospace;
  }}
  pre code {{ background: none; padding: 0; }}
  table {{ width: 100%; border-collapse: collapse; margin: 18px 0; font-size: 15px; }}
  th, td {{ text-align: left; padding: 10px 12px; border-bottom: 1px solid var(--line); }}
  th {{ background: var(--soft); font-weight: 600; white-space: nowrap; }}
  td.num {{ font-variant-numeric: tabular-nums; white-space: nowrap; }}
  .box {{
    border-left: 3px solid var(--accent); background: var(--soft);
    padding: 16px 20px; border-radius: 0 8px 8px 0; margin: 20px 0;
  }}
  .box.warn {{ border-left-color: var(--warn); }}
  .box.ok {{ border-left-color: var(--ok); }}
  .box p:last-child {{ margin-bottom: 0; }}
  figure {{ margin: 26px 0; }}
  figure img {{ width: 100%; border: 1px solid var(--line); border-radius: 8px; display: block; }}
  figcaption {{ font-size: 13.5px; color: var(--muted); margin-top: 10px; text-align: center; }}
  .kpi {{ display: flex; flex-wrap: wrap; gap: 14px; margin: 22px 0; }}
  .kpi div {{
    flex: 1 1 170px; background: var(--soft); border: 1px solid var(--line);
    border-radius: 8px; padding: 16px 18px;
  }}
  .kpi b {{ display: block; font-size: 25px; line-height: 1.25; font-variant-numeric: tabular-nums; }}
  .kpi span {{ font-size: 13px; color: var(--muted); }}
  footer {{
    margin-top: 64px; padding-top: 24px; border-top: 1px solid var(--line);
    font-size: 14px; color: var(--muted);
  }}
  a {{ color: var(--accent); }}
</style>
</head>
<body>
<div class="wrap">

<header>
  <div class="tag">2026 江苏省高校 AI+科学与工程创新实践黑客松 · 本科生组 · 参赛编号 U070 · 队伍 然智核</div>
  <h1>会自己验算自己的<br>园区微网调度智能体</h1>
  <p class="lede">给它一个园区的负荷、光伏与分时电价曲线，它自己建模、求解、<strong>并独立验算</strong>调度方案；算错了能自己发现、自己回退重算，算不出来就如实说算不出来。</p>
</header>

<h2>为什么做这个</h2>
<p>园区微网调度是典型的"数字看起来都对、但其实全错"的场景。让大模型直接给一个调度方案是不可信的：方案可能违反功率平衡、可能超出储能与电网限值，也可能只是把平凡解包装得很漂亮。</p>
<p>现有科研智能体普遍缺一环：<strong>没有独立的一方去验算它的结果</strong>。模型自己宣称"我检查过了"不构成验证——自己批自己不是验证。</p>
<p>所以本项目把落点放在<strong>验证</strong>上：不是让 Agent 多算一遍，而是用与生成过程<strong>完全不同的一套算术</strong>去卡它的输出，卡不过就自动回退，并把整条链路留档。</p>

<h2>核心设计：生成与校验分离</h2>
<pre><code>                     AGH 底座
        （会话 / 工具调度 / 审批 / 日志 / 失败重试）
                        │
   ┌────────────────────┼────────────────────┐
   │                    │                    │
生成侧                校验侧                编排
mg_model.py         mg_verify.py          mg_run.py
（LP + HiGHS）    （不 import 生成侧）    （读→解→验→报）
                        │
              V1 约束残差   精确
              V2 上下界夹逼 有界但松
              V3 退化复现   精确（黑箱差分）</code></pre>
<p><code>mg_verify.py</code> 判定可行性时<strong>不 import 生成侧的任何代码</strong>，自己重读原始 CSV、自己重算每一条约束。</p>
<table>
  <tr><th>检查组</th><th>方法</th><th>强度</th></tr>
  <tr><td>V1 约束残差</td><td>重算功率平衡、储荷递推、变量上下限、端面 SOC、目标值</td><td>精确（容差 1e-6）</td></tr>
  <tr><td>V2 上下界夹逼</td><td>下界用解析式，上界用必定可行的规则方案</td><td>有界但松</td></tr>
  <tr><td>V3 退化复现</td><td>三个有闭式解的退化算例，把生成侧当黑箱调用</td><td>精确</td></tr>
</table>
<div class="box">
  <p>关于 V2 我们有一个自觉：<strong>它是松的，我们没有假装它紧</strong>。报告里会如实打印相对间隙（标准算例约 0.73），并写明精确性由 V1 与 V3 保证。知道自己哪个检查是松的，比宣称所有检查都严密更有意义。</p>
</div>

<h2>一次真实结果</h2>
<p>标准算例：24 时段，三档电价 0.30 / 0.72 / 1.20 元每千瓦时，储能 2000 kWh / 500 kW。</p>
<div class="kpi">
  <div><b>4651.42</b><span>日购电成本（元）</span></div>
  <div><b>26.90%</b><span>相对无储能节省</span></div>
  <div><b>11 / 11</b><span>独立校验通过</span></div>
</div>
<table>
  <tr><th>指标</th><th>数值</th></tr>
  <tr><td>日购电成本</td><td class="num">4651.42 元</td></tr>
  <tr><td>无储能基准</td><td class="num">6363.46 元</td></tr>
  <tr><td>电网购电量</td><td class="num">9454.43 kWh</td></tr>
  <tr><td>储能充电 / 放电</td><td class="num">2402.33 / 2168.10 kWh</td></tr>
  <tr><td>光伏弃电</td><td class="num">0 kWh</td></tr>
  <tr><td>夹逼区间</td><td class="num">2766.06 ≤ 4651.42 ≤ 6167.13 元</td></tr>
</table>
<figure>
  <img src="data:image/png;base64,__CHART_B64__" alt="园区微网日调度曲线：功率平衡、电价与储能充放电、储荷轨迹">
  <figcaption>调度结果：储能安排在电价低谷充电、高峰放电；光伏全部消纳，无弃电</figcaption>
</figure>

<h2>它出错时什么样</h2>
<p>把电网购电上限与储能功率同时置 0，负荷就没有来源了，模型不可行。</p>
<p>正常做法是随便给个方案——数字看起来都挺正常。但这里：</p>
<ol>
  <li>求解器报 infeasible → 编排器<strong>降级</strong>成规则方案</li>
  <li>校验器接手，逐时段重算 → 发现 <strong>24 个时段的购电全部越界</strong></li>
  <li>运行结论判定为 <strong>校验未通过</strong></li>
</ol>
<div class="box warn">
  <p><strong>它宁可报告失败，也不交一个错的方案。</strong></p>
</div>

<h2>在 AGH 上跑通的四个坑</h2>
<table>
  <tr><th>#</th><th>现象</th><th>根因</th><th>处理</th></tr>
  <tr><td>1</td><td><code>-p</code> 非交互模式下 shell 调用被自动拒绝</td><td>审批策略为 manual，无人可问即拒绝（设计如此）</td><td>工作台点审批卡；批处理临时设 off</td></tr>
  <tr><td>2</td><td><code>approvals.mode: smart</code> 无效</td><td>本版内置审批 seam 未实现 guard，恒返回 guardian unavailable</td><td>放弃 smart</td></tr>
  <tr><td>3</td><td>Agent 执行 python 卡死 148 秒</td><td>PATH 中 Store 应用执行别名排在 Anaconda 之前，一执行就挂起</td><td>启动前把 Anaconda 提到最前</td></tr>
  <tr><td>4</td><td>读 JSON 时中文全成乱码</td><td>用了 PowerShell 的 Get-Content，它按 ANSI(GBK) 解码 UTF-8</td><td>新增专用查看工具，绕开 shell 文本管道</td></tr>
</table>
<p>第 4 条：<strong>文件本身是完全合法的 UTF-8</strong>（已用字节级检查确认），问题出在读取方的解码假设上。我们没有去改文件编码（加 BOM 会破坏 Python 的 <code>json.load</code>），而是给智能体一个不走 shell 文本管道的专用工具，从系统层面绕开。</p>

<h2>快速开始</h2>
<p>先获取 AGH（本项目不含其源码，<a href="https://github.com/AgnesAI-Labs/agnes-harness">官方仓库</a>）：</p>
<pre><code>git clone https://github.com/AgnesAI-Labs/agnes-harness.git vendor/agnes-harness
cd vendor/agnes-harness
echo "registry=https://registry.npmmirror.com" &gt;&gt; .npmrc
corepack pnpm install --frozen-lockfile
corepack pnpm --filter @agnes/cli build:local</code></pre>
<p>然后跑算法本身：</p>
<pre><code>python tools/mg_run.py --csv data/park_day.csv --params data/params.json --outdir evidence/runs/normal
python tools/mg_show.py --run evidence/runs/normal
python tools/mg_report.py --run evidence/runs/normal
python tests/run_tests.py</code></pre>
<p>退出码：<code>0</code> 正常 · <code>2</code> 数据错误 · <code>3</code> 校验未通过 · <code>4</code> 已降级</p>

<h2>目录</h2>
<table>
  <tr><th>路径</th><th>内容</th></tr>
  <tr><td><code>tools/mg_model.py</code></td><td>生成侧：LP + HiGHS</td></tr>
  <tr><td><code>tools/mg_verify.py</code></td><td>校验侧：11 项独立检查</td></tr>
  <tr><td><code>tools/mg_run.py</code></td><td>编排：读→解→验，含降级与异常注入</td></tr>
  <tr><td><code>tools/mg_report.py</code></td><td>报告与调度曲线图</td></tr>
  <tr><td><code>tools/mg_show.py</code></td><td>结果查看器</td></tr>
  <tr><td><code>tools/scan_secrets.py</code></td><td>发布前密钥扫描</td></tr>
  <tr><td><code>tests/run_tests.py</code></td><td>正常 / 边界 / 失败 三类共 10 例</td></tr>
  <tr><td><code>evidence/</code></td><td>AGH 执行记录与每次运行产物</td></tr>
</table>

<footer>
  <p>AGH（Agnes Harness）为本赛事指定执行底座，源码由<a href="https://github.com/AgnesAI-Labs/agnes-harness">官方仓库</a>分发，本项目仅调用、未作修改。<br>
  本项目代码以 MIT 许可发布。生成日期 __BUILD_DATE__。</p>
</footer>

</div>
</body>
</html>
"""


def build(chart: Path, out: Path) -> dict:
    if not chart.is_file():
        raise FileNotFoundError(f"找不到曲线图：{chart}")
    b64 = base64.b64encode(chart.read_bytes()).decode("ascii")
    from datetime import date

    html = PAGE.replace("__CHART_B64__", b64).replace("__BUILD_DATE__", date.today().isoformat())
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(html, encoding="utf-8")
    return {"out": str(out), "bytes": out.stat().st_size, "chart_bytes": chart.stat().st_size}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="生成单文件自包含项目展示页")
    ap.add_argument("--chart", default=str(ROOT / "evidence" / "runs" / "normal" / "dispatch.png"))
    ap.add_argument("--out", default=str(ROOT / "site" / "index.html"))
    args = ap.parse_args(argv)

    info = build(Path(args.chart), Path(args.out))
    print("[展示页已生成]")
    print(f"  文件 : {info['out']}")
    print(f"  体积 : {info['bytes'] / 1024:.1f} KB（其中曲线图 {info['chart_bytes'] / 1024:.1f} KB 已内嵌）")
    print("  特性 : 单文件自包含，不依赖任何外部资源，双击即可打开")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
