# 复现说明与 AGH Trace 索引 · U070

**会自己验算自己的园区微网调度智能体**
2026 江苏省高校 AI+科学与工程创新实践黑客松 · 本科生组 · 队伍 **然智核** · 参赛编号 **U070** · 队长 **马悠然**

> 这份文件是**入口**。评审要的三样东西——说明文档、可复现材料（源码 + AGH trace）、演示视频——在下面第一节直接给出位置。

---

## 一、按评审要求索引（先看这张表）

| 评审要求 | 在压缩包里的位置 | 说明 |
|---|---|---|
| **① 说明文档 / README** | **`README.md`（压缩包根目录）** + **本文件** | `README.md` 是完整技术说明（系统结构、核心设计、快速开始、真实结果、四个坑） |
| **② 可复现：源码** | **`tools/`**（9 个模块）、`data/`、`tests/` | 全部为我们自己编写；`tools/` 内每个模块职责见 `README.md` 的「目录结构」 |
| **② 可复现：AGH 导出的 trace** | **`evidence/agh_diagnostics/agh-diagnostics-d728d0aa-20261014-134215.zip`** ← **这是组委会要的那份，优先看**；另有 6 个 jsonl 在 `evidence/agh/` 与 `evidence/first_run/` | 诊断包自带 `index.html`，解压后双击即看；见本文件第五节 |
| **③ 3–5 分钟演示视频** | **不在压缩包内**，按规程 4.3 以**链接**形式提交 | 视频链接填在提交表单「作品链接」栏 |

---

## 二、环境要求

| 项 | 要求 |
|---|---|
| 操作系统 | Windows 10/11（我们在 Windows 11 上验证） |
| Python | 3.13（我们用 Anaconda `D:\Anaconda3\python.exe`） |
| 依赖 | NumPy、SciPy、Matplotlib（SciPy 自带 HiGHS 求解器） |
| Node.js | ≥ 24（仅复现 AGH 闭环时需要；本项目用 v24.15.0） |

**先决条件：AGH 源码不随包分发。** 它是赛事指定平台、体积大，请从官方仓库获取：

```powershell
git clone https://github.com/AgnesAI-Labs/agnes-harness.git vendor/agnes-harness
cd vendor/agnes-harness
"registry=https://registry.npmmirror.com" | Add-Content .npmrc
$env:COREPACK_NPM_REGISTRY = "https://registry.npmmirror.com"
corepack pnpm install --frozen-lockfile
corepack pnpm --filter @agnes/cli build:local
```

本项目验证所用 AGH 版本：commit **`cde816b8`**（2026-10-06），**未作任何修改**。

### ⚠️ 一个必须注意的坑（否则复现会卡死）

`PATH` 里的 `C:\Users\...\WindowsApps\python.exe` 是 Microsoft Store 的应用执行别名，
**被调用时不会报错，而是直接挂起**——我们实测它把一个 Agent 轮次拖死 **148 秒**。

所以**跑任何命令前**，先把 Anaconda 提到 PATH 最前：

```powershell
$env:PATH = "D:\Anaconda3;D:\Anaconda3\Scripts;D:\Anaconda3\Library\bin;" + $env:PATH
$env:PYTHONUTF8 = "1"; $env:PYTHONIOENCODING = "utf-8"
```

用 `where python` 确认第一位是 Anaconda 即可。包内的 `启动工作台.ps1` / `跑一遍演示.ps1` 已自动做这件事。

---

## 三、可复现步骤（五步，已逐条实测）

### 步骤 1：跑测试套件（12 个用例）

```powershell
cd <解压目录>
python tests\run_tests.py
```

**预期实测结果**：合计 **12 例：12 例符合预期，0 例不符合**，退出码 **0**。

| 分组 | 用例 | 预期结论 | 实测 |
|---|---|---|---|
| 正常 | T01 标准算例：三档电价 + 储能可套利 | 正常 | ok，成本 `4651.417346` |
| 边界 | T02 光伏全天过剩 | 最优成本应为 0 | ok，成本 `0.0` |
| 边界 | T03 储能功率置 0 | 退化为逐时买净缺口 | ok，成本 `6363.462` |
| 边界 | T04 储能容量置 0 | 同上，走容量约束分支 | ok，成本 `6363.462` |
| 失败 | T05 电价非正 | 应在求解前拦下 | 符合预期 |
| 失败 | T06 行数缺失 | 应在求解前拦下 | 符合预期 |
| 失败 | T07 字段非数字 | 应在求解前拦下 | 符合预期 |
| 失败 | T08 字段留空 | 应在求解前拦下 | 符合预期 |
| 失败 | T09 端面储荷越界 | 应在求解前拦下 | 符合预期 |
| 失败 | T10 模型不可行 | 降级后必须被校验器拦下 | 符合预期，`verification_failed` |
| **分离性** | **T11 生成侧漏掉端面储荷约束** | 校验器须抓住 **V1.4** 并自我修复 | `recovered`，第 2 次通过 |
| **分离性** | **T12 生成侧漏掉储能功率上限** | 校验器须抓住 **V1.3** 并自我修复 | `recovered`，第 2 次通过 |

报告落在 `reports\test_report.json`（包内已含一份）。

### 步骤 2：变异覆盖矩阵（**校验器有没有盲区？**）

```powershell
python tools\mutate_verify.py
```

**预期实测结果**：**8 个变异体，8 个被预期检查项抓住，8 个被校验器拦下**，退出码 **0**。

做法：先取基线方案（11 项全过），再按**约束类别**逐个把一条正确方案改坏，
看校验器是否**恰好**由预期的那一项抓住。

| 变异体 | 缺陷类别 | 怎么改坏的 | 预期抓住 | 实际抓住 |
|---|---|---|---|---|
| M1 | 功率平衡 | 第 3 时段购电少记 50 kW | V1.1 | V1.1, V1.5 |
| M2 | 储荷递推 | 储荷曲线整体前移一格（off-by-one） | V1.2 | V1.2 |
| M3 | 储能功率上限 | 第 8 时段充电 1200 kW | V1.3 | V1.1, V1.2, V1.3 |
| M4 | 电网购电上限 | 第 20 时段购电 2000 kW | V1.3 | V1.1, V1.3, V1.5 |
| M5 | 储荷容量上限 | 第 12 时段储荷 2500 kWh | V1.3 | V1.2, V1.3 |
| M6 | 弃电上限 | 第 6 时段弃电 = 光伏 + 30 | V1.3 | V1.1, V1.3 |
| M7 | 端面储荷 | 期末储荷少 120 kWh | V1.4 | V1.2, V1.4 |
| M8 | 目标值 | 成本少报 5% | V1.5 | V1.5 |

两个值得注意的发现：

1. **8 个里有 6 个被多个检查项同时抓住**。这不是冗余，是**纵深防御**：
   一个越界往往会同时破坏功率平衡或储荷递推，于是多个独立算术路径都发现了它。
2. **M8（只把成本少报 5%）只被 V1.5 抓住** —— 这类"方案可行、只是账算错了"的错误，
   只有"独立重算目标值"这一条路径能发现。这说明 V1.5 不是多余的。

> **边界声明（必须如实说）**：本矩阵测的是**校验器的覆盖面**，不是生成器的优劣。
> 它还**只覆盖已枚举的 8 类缺陷**——"未枚举类型的盲区"没有被排除。
> 所以我们说的是"校验器在这 8 类错误上无盲区"，**不是**"校验器绝对无盲区"。
> 报告文件 `reports\mutation_report.json` 的 `caveat` 字段里也写着同一句话。

### 步骤 3：跑标准算例（读 → 解 → 独立校验 → 报告）

```powershell
python tools\mg_run.py --csv data\park_day.csv --params data\params.json --outdir evidence\runs\normal
python tools\mg_report.py --run evidence\runs\normal
```

**预期实测结果**：

```
运行结论：正常
  [       ok] load  (0.003s)
  [       ok] solve  (0.016s)
  [       ok] verify  (0.043s)
  目标成本：4651.417346 元
  夹逼区间：2766.06 ≤ 目标 ≤ 6167.130158 元
  退出码：0  （0 正常 / 2 数据错误 / 3 校验未通过 / 4 已降级）
```

| 指标 | 数值 |
|---|---|
| 日购电成本 | **4651.417346 元** |
| 无储能基准 | 6363.462 元 |
| **相对节省** | **26.904%** |
| 电网购电 | 9454.42687 kWh |
| 储能充电 / 放电 | 2402.32687 / 2168.1 kWh |
| 光伏弃电 | 0 kWh |
| 期末储荷 | 400.0 kWh |

产出：`manifest.json`、`solution.json`、`verification.json`、`report.md`、`dispatch.png`。

### 步骤 4：跑异常算例（**记忆点：它自己发现错误**）

```powershell
python tools\mg_run.py --csv data\park_day.csv --params data\params.json --outdir evidence\runs\demo_bad --inject no_supply
```

`no_supply` 的含义：把电网购电上限与储能功率**同时置 0**，负荷无处可取 → 优化问题**不可行**。

**预期实测结果**：

```
运行结论：校验未通过
  注入异常：no_supply
  [       ok] inject  (0.004s)
  [       ok] load    (0.006s)
  [ degraded] solve   (0.013s)
  [   failed] verify  (0.028s)
  降级原因：E_SOLVER_FAILED 求解器未得到最优解：status=2 The problem is infeasible.
  退出码：3
```

关键点：求解器失败 → **降级**成规则方案 → 校验器**拒绝**该降级方案。
`verification.json` 里 11 项检查中 **V1.3「变量上下限」判死，报出 24 处越界**
（准确口径：**24 个时段的电网购电各越界 1 条，共 24 条**），
例如 `t1 电网购电 412.500000 越界 [0, 0.0]`。

> **系统宁可报告失败，也不交一个错的方案。** 这就是本作品的核心主张。
> 另外 8 种注入方式见 `README.md` 的「异常注入」一节。

#### ⚠️ 一处如实说明：V2 的界在异常算例上会退化（已知限制）

这个异常算例里报出的成本是 **6363.462 元**，但该场景的**购电上限已被置 0**——
也就是说这个成本**在该场景下根本无法达成**（降级方案假设可以买电）。
更值得注意的是：V2.1 的解析下界与 V2.2 的可行上界**都算出 6363.462**，等于**没夹住**。

原因是 **V2 的界没有考虑"注入之后约束被收紧"这件事**。
运行结论仍然正确（V1.3 判死并拒绝交付），但这说明：

> **V2 只在参数正常的算例上有效；在异常算例上它会退化成一个无效的界。**
> 精确性依然由 V1 与 V3 保证——它们不依赖界，只依赖逐条重算。

我们把这个限制写出来，而不是藏起来：**知道自己哪个检查在什么情况下会失效，
比宣称所有检查在所有情况下都成立更有意义。** 这也和"V2 是松的、我们没假装它紧"是同一件事。

### 步骤 5：证明「生成与校验分离」不是口号（**推荐复现，最有说服力**）

前面几步只能说明系统"能跑"。"生成与校验分离"到底是不是真的？——**故意把生成侧改坏，看校验侧能不能抓住。**

```powershell
python tools\mg_run.py --csv data\park_day.csv --params data\params.json `
  --outdir evidence\runs\defect_terminal_soc --inject defect_no_terminal_soc
python tools\mg_run.py --csv data\park_day.csv --params data\params.json `
  --outdir evidence\runs\defect_storage_power --inject defect_no_storage_power
```

这两条注入**不改输入数据**，只在生成侧建模里**故意漏掉一条约束**。
结果：求解器给出一个**看起来更优、实际违规**的方案，校验器当场抓住，系统回退重算后修复。

**预期实测结果**（两条都是 `运行结论：已自我修复`、退出码 `0`）：

| 注入的生成侧缺陷 | 生成侧给出的解 | 被哪项抓住 | 证据 | 修复后 |
|---|---|---|---|---|
| `defect_no_terminal_soc`<br>漏掉 `s[T]=soc_end` | 成本 **4556.68**（比正确解便宜 **94.74**） | **V1.4 端面储荷条件** | `soc_terminal_dev = 300.0` kWh（期望 400.0） | 成本回到 **4651.417346** |
| `defect_no_storage_power`<br>漏掉 `0≤c,d≤power_kw` | 成本 **4528.74**（比正确解便宜 **122.68**） | **V1.3 变量上下限** | **6 处越界**，如 `t8 充电 1083.8 越界 [0, 500.0]` | 成本回到 **4651.417346** |

**为什么这比"跑通"更有意义**：这两个缺陷的共同点是——**解更便宜、看起来更优，但违反真实约束**。
生成侧自己发现不了（它按自己的模型求解，内部完全自洽），**只有独立重算的一方才能抓住**。

审计入口（`evidence\runs\defect_terminal_soc\manifest.json`）：

```json
"defect_injected": "no_terminal_soc",
"recovered": true,
"attempts": [
  {"attempt": 1, "passed": false, "n_failed": 1,
   "failed_checks": [{"id": "V1.4", "name": "端面储荷条件"}]},
  {"attempt": 2, "passed": true, "n_failed": 0, "failed_checks": []}
]
```

第 1 次的完整校验明细另存为同目录下的 `verification_attempt1.json`
（否则会被第 2 次覆写，"是哪一项抓住的"就查不到了）。

> **关键设计**：回退重算的结果**照样要过独立校验**才算数。
> 重试只是"再搜一次"，**不是信任的来源** —— 这正是"生成与校验分离"能守住的原因。
>
> **机制要说准**：第 2 次通过，是**丢弃注入的缺陷标记、用完整模型重新求解**，
> 不是"补回被删掉的约束"——约束从来没有真的被删过，
> 是生成侧在建模时"假装"看不到它。这个区别很重要：
> 它说明缺陷是**生成侧的建模错误**，而不是求解器或数据的问题。

### 查看结果（不要用 PowerShell 的 `Get-Content` 读 JSON）

```powershell
python tools\mg_show.py --run evidence\runs\normal
```

Windows PowerShell 5.1 按 ANSI(GBK) 解码 UTF-8，`Get-Content` 读 JSON 会让中文全成乱码（文件本身是合法 UTF-8，已做字节级确认）。`mg_show.py` 是专门为此提供的可靠入口。

---

## 四、11 项独立校验逐项明细（标准算例，全部通过）

校验侧 `tools/mg_verify.py` 判定可行性时**不 import 生成侧任何代码**，自己重读原始 CSV、自己重算每一条约束。

| 检查 | 名称 | 实测 |
|---|---|---|
| V1.1 | 功率平衡残差 | max_abs_residual `1.14e-13`（容差 1e-6） |
| V1.2 | 储荷递推残差 | max_abs_residual `6.84e-10`（容差 1e-6） |
| V1.3 | 变量上下限 | violations `0` |
| V1.4 | 端面储荷条件 | soc_init_dev `0.0`，soc_terminal_dev `0.0`（期望 400.0） |
| V1.5 | 目标值独立重算 | reported `4651.417346` = recomputed `4651.417346` |
| V2.1 | 解析下界 ≤ 方案成本 | 下界 `2766.06` ≤ `4651.417346` |
| V2.2 | 方案成本 ≤ 可行上界 | `4651.417346` ≤ 上界 `6167.130158`（来源 `greedy_tou`） |
| V2.3 | 夹逼区间与相对间隙 | 报告内如实打印相对间隙（约 0.73） |
| V3.1 | 无储能功率 | solver `6363.462` = 闭式解 `6363.462` |
| V3.2 | 电价拉平（无套利空间） | solver `7376.16` = 闭式解 `7376.16` |
| V3.3 | 光伏全网过剩且平价 | solver `0.0` = 闭式解 `0.0` |

**关于 V2 的自觉**：V2 的夹逼界是**松的**，我们没有假装它紧——报告里如实打印相对间隙，
并写明精确性由 **V1 与 V3** 保证。知道自己哪个检查是松的，比宣称所有检查都严密更有意义。

---

## 五、AGH Trace 索引

### 5.1 主件：AGH 诊断导出包（**组委会要求提交的那份**）

**`evidence/agh_diagnostics/agh-diagnostics-d728d0aa-20261014-134215.zip`**
（47064 字节，SHA256 `15C11920344188038549F97F727B3EDA75FABB1C3ED4B3F8B5DD34FCC6693934`）

这是 AGH 工作台**自带「导出诊断」功能**产出的完整包，**未做任何修改**。它比单独导出的 jsonl 更完整：

| 包内条目 | 大小 | 内容 |
|---|---|---|
| `index.html` | 120290 B | **自包含诊断页**，解压后双击即看，不需要联网/装 AGH |
| `trace.json` | 58114 B | 轨迹主体：40 个节点（**10 个 tool 节点**）、`upto=189`、usage、budget |
| `events.jsonl` | 105220 B | 原始事件流 189 个事件，含 **19 个 `verifier/signal`**、**10 个 `tool/call`**、10 个 `cost/ledger` |
| `system.json` | 2299 B | 运行环境与配置快照 |
| `logs/host.jsonl` | 60166 B | AGH host 日志 |
| `logs/daemon.jsonl` / `logs/browser.json` | 0 / 97 B | 其余日志 |

**这次会话验证了什么**（两个关键 tool 节点，可在 `trace.json` 里核对）：

- ① 任务规划：`name="todo"`，Agent 先把任务拆成四步（正常算例 / 异常算例 / 出报告 / 读结果）
- ② 能力调用：`name="shell"`，执行 `python tools/mg_run.py ... --outdir evidence/runs/demo_normal`，
  返回 `运行结论：正常`、`目标成本：4651.417346 元`、`夹逼区间 2766.06 ≤ 目标 ≤ 6167.130158`、`退出码：0`

> **凭据核查**：诊断包会带出配置快照，我们专门用 `tools/scan_secrets.py` 的模式集扫了全部 7 个条目，
> **API Key / Bearer / JWT / 明文凭据赋值均 0 处命中**；`system.json` 里只有布尔标记
> `credentialConfigured: true`，无任何明文密钥。
> 包内含本机绝对路径与运行环境信息（可复现所必需，AGH 官方导出时也提示 `without redaction`）。
> 详细说明见 `evidence/agh_diagnostics/README.md`。

### 5.2 另有 6 个 jsonl 会话导出

这些是 **AGH 会话事件流的原始导出**，**未做删改**。评审可据此核查执行闭环。

| # | 文件 | 大小 | 内容与用途 |
|---|---|---|---|
| 1 | `evidence/agh/session_web_approval.jsonl` | 93479 B | **主证据**：一轮完整闭环，含 **8 次工具调用**（4 次 shell + 4 次 read），并含审批记录 `allowed-session` |
| 2 | `evidence/agh/session_web_timeout.jsonl` | 36910 B | **审批超时未执行**：审批卡 60 秒无人应答 → 判定超时 → 命令**没有执行** → Agent 如实汇报"没有产出"并请求确认。**它没有编造结果** |
| 3 | `evidence/agh/session_cli_batch.jsonl` | 273947 B | 491 个事件的全程留档：从早期被审批拦下，到修复后跑通 |
| 4 | `evidence/agh/session_web_second.jsonl` | 38315 B | 早期冒烟测试 |
| 5 | `evidence/first_run/session_success.jsonl` | 60364 B | 首次成功运行的会话记录 |
| 6 | `evidence/first_run/session_approval_gate_denied.jsonl` | 149738 B | 首次被审批闸门拒绝的会话记录 |

另有 `evidence/agh/README.md`（5736 B）逐个说明这些导出对应的场景。

### AGH 三条闭环在本作品中的落点

赛事要求经 AGH 完成**至少三个连续步骤**：

| 闭环 | 在本作品中的表现 | 证据位置 |
|---|---|---|
| ① **任务规划** | Agent 收到"生成方案并独立验证"后，先用 todo 工具拆成四步 | `session_web_approval.jsonl` |
| ② **能力调用** | 连续调用 shell 执行 `mg_run.py`（正常/异常算例）、`mg_report.py`，再用 read 读取清单与校验明细 | 同上，8 次工具调用 |
| ③ **反馈处理与结果核验** | 第二个算例返回"校验未通过"，Agent 读取明细后指出是 **V1.3 变量上下限**判死（24 处越界），并说明降级方案为何不能交付 | 同上 |

**模型**：Agnes AI 的 `agnes-3.0-flash`，经 AGH Provider 接入中国官方网关 `https://api.agnes-ai.cn/v1`。

### 如何自己重新导出 trace

```powershell
$env:AGH_HOME      = "<解压目录>\.agh"
$env:AGNES_PROFILE = "local-dev"
$AGH = "<解压目录>\vendor\agnes-harness\packages\cli\dist\local\agnes.mjs"

node $AGH sessions list                                          # 列出会话与事件数
node $AGH export <sessionId> --format agnes --raw -o out.jsonl   # 导出为 jsonl
```

> ⚠️ **`--raw` 会导出未脱敏内容**（AGH 自身会打印 `warning: --raw exports without redaction (secrets, paths, PII)`）。
> 包内这 6 个 trace 就是 `--raw` 导出的，因此**只在本压缩包内提交，不另行公开粘贴到公开平台**。
> 我们已用 `tools/scan_secrets.py` 扫描，未见凭据泄漏。

---

## 六、包内结构与完整性

```
├─ README.md                    ← 完整技术说明（先读这个）
├─ 复现说明与Trace索引.md        ← 本文件（评审要求索引）
├─ LICENSE
├─ tools/                       源码（9 个模块）
├─ data/                        算例数据（24 时段负荷 / 光伏 / 三档电价 + 储能参数）
├─ tests/                       测试套件（10 个用例）
├─ evidence/
│   ├─ agh_diagnostics/         ★★ AGH 诊断导出包（组委会要的 trace）+ 说明 README
│   ├─ agh/                     AGH 会话导出（4 个 jsonl + README）
│   ├─ first_run/               首次运行证据（2 个 jsonl + 脚本 + 结果）
│   ├─ runs/                    21 个运行目录，每次含 manifest / solution / verification / report / 图
│   └─ core/solution.json
├─ reports/test_report.json     测试套件报告
├─ docs/                        技术信息、成员声明、演示脚本等
├─ site/index.html              单文件自包含展示页
├─ 启动工作台.ps1 / 跑一遍演示.ps1 / 停止工作台.ps1
└─ .gitignore / .gitattributes
```

**包内不含**：`.env`（密钥）、`.agh/`（AGH 运行实例，含会话正文）、`vendor/`（AGH 源码与依赖缓存）。
已用 `python tools\scan_secrets.py --fail-on-hit` 扫描，**退出码 0**。

**一个诚实的说明**：运行证据里的 `manifest.json` 含 `elapsed_s` 计时字段。
重新跑测试或算例会让这些计时数值变化（结果字段不变）。包内 21 个 `manifest.json` 与
`reports/test_report.json` 是一致的；若你重跑，计时会变，但 `objective_cny`、`n_checks`、
`n_failed`、各阶段 `status` 都应保持一致。

---

## 七、本文件哪些是已实测、哪些未验证

**已实测（我逐条跑过）**：
- `python tests\run_tests.py` → **12/12、退出码 0**（含 T11/T12 分离性用例）
- `python tools\mutate_verify.py` → **8 个变异体、8 个被预期检查项抓住、全部被拦下、退出码 0**
- `python tools\mg_run.py`（正常算例）→ 4651.417346 元、退出码 0、11 项全过
- `python tools\mg_run.py --inject no_supply` → 退出码 3、V1.3 判死 24 处越界
- `python tools\mg_run.py --inject defect_no_terminal_soc` → **recovered**，V1.4 抓住 `soc_terminal_dev=300.0`
- `python tools\mg_run.py --inject defect_no_storage_power` → **recovered**，V1.3 抓住 6 处越界
- `python tools\mg_report.py` → 节省 26.904%、图已生成
- `python tools\mg_show.py` → 两次运行均正常读取
- `python tools\scan_secrets.py --fail-on-hit` → 退出码 0
- 改造 `mg_run.py` / `mg_model.py` 后**回归核对**：T01/T10/normal/demo_bad 的 `status` 与
  `objective_cny` **完全不变**，调度量**逐时段最大差 0.000e+00**（只新增了 `defect_injected` 字段）
- 诊断导出包逐条目解析：7 个条目、`trace.json` 40 节点 / 10 个 tool、
  `events.jsonl` 189 事件（19 `verifier/signal`、10 `tool/call`）；
  并用凭据模式集扫描全 7 条目 → **0 处命中**；副本 SHA256 与原文件一致

**未验证**：① AGH 源码从官方仓库克隆 + `pnpm install` + `build:local` 的完整流程
（本机 `vendor/agnes-harness` 是既有构建产物，我们未在干净环境重跑这一步）；
② 诊断包内 `index.html` 我们只确认了它能被解压出来且内容自包含，
未在浏览器里实际打开渲染过（这一步请评委或我们自己打开验证）。
若评委按第二节从零构建 AGH，命令取自官方文档，我们不对其可用性负责。
