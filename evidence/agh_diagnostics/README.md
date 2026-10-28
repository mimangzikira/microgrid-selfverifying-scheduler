# AGH 诊断导出包（组委会要求提交的「agh 导出的 trace」）

## 一、这是什么

`agh-diagnostics-d728d0aa-20261014-134215.zip` 是 **AGH 工作台自带的「导出诊断」功能**产出的完整诊断包，
**未做任何修改**（SHA256 见第四节）。

组委会反馈「缺少可复现部分，包括源码、agh 导出的 trace」——**这个 zip 就是那份 trace**。
相比单独导出的 jsonl，诊断包更完整：它不只是对话记录，还包含**工具调用链、验证信号、成本台账、
预算状态、上下文用量**。

## 二、怎么打开（最快路径）

1. 解压这个 zip（它本身是个压缩包，共 **7 个条目**）
2. **双击 `index.html`** —— 这是 AGH 生成的自包含诊断页面，**图片与数据都已内嵌**，
   不需要联网、不需要装 AGH、不需要配环境，用浏览器直接打开即可看到这次运行的完整轨迹

> 如果只想看结构化数据，直接看第三节列出的文件。

## 三、包内 7 个条目说明

| 条目 | 大小 | 内容 |
|---|---|---|
| `index.html` | 120290 B | **自包含诊断页**，双击即看（推荐入口） |
| `trace.json` | 58114 B | **轨迹主体**：`sessionId`、`upto=189`、`nodes` 40 个、`turns` 1 个，含 `usage` 与 `budget` |
| `events.jsonl` | 105220 B | **原始事件流**，189 个事件 |
| `system.json` | 2299 B | 运行环境与配置快照 |
| `logs/host.jsonl` | 60166 B | AGH host 日志 |
| `logs/daemon.jsonl` | 0 B | 本次为空（正常，无 daemon 侧日志） |
| `logs/browser.json` | 97 B | 浏览器侧信息 |

### 事件类型分布（`events.jsonl`，189 个）

| 数量 | 事件类型 | 说明 |
|---|---|---|
| 19 | `verifier/signal` | **验证信号** ← 组委会明确要求的项 |
| 19 | `effect/intent` / `effect/settled` | 副作用意图与落定（各 19） |
| **10** | `tool/call` | **工具调用** ← 「≥1 条工具调用链」 |
| 10 | `tool/result` | 工具返回 |
| 10 | `cost/ledger` | 成本台账 |
| 9 | `step/start` / `budget.state` / `request/sent` 等 | 步进与预算状态 |

## 四、这次会话验证了什么（可核对）

`trace.json` 的 40 个节点中，**10 个是 `tool` 节点**。两个关键节点：

**① 任务规划**（`name = "todo"`，`status = "completed"`）—— Agent 先把任务拆成四步：

```json
{"items":[
  {"content":"Step 1: normal run (demo_normal)","status":"in_progress"},
  {"content":"Step 2: abnormal run with no_supply (demo_bad)","status":"pending"},
  {"content":"Step 3: generate report for demo_normal","status":"pending"},
  {"content":"Step 4: show results of both runs","status":"pending"}]}
```

**② 能力调用**（`name = "shell"`，`status = "completed"`）—— 实际执行并拿到结果：

```
command: python tools/mg_run.py --csv data/park_day.csv --params data/params.json --outdir evidence/runs/demo_normal
cwd    : D:\AI黑客松_2026_U070
返回   : 运行结论：正常 / 目标成本：4651.417346 元 / 夹逼区间：2766.06 ≤ 目标 ≤ 6167.130158 元 / 退出码：0
```

这两个数字与包内 `evidence/runs/demo_normal/` 的产物一致，可交叉核对。

另外 `trace.json` 还记录了本次会话的用量与预算：

- `usage.totals`：input `8433`、output `1615`、cacheRead `89088`
- `budget`：`creditsCap 4000`、`creditsUsed 0`、`escalate false`

## 五、凭据核查（我们已自检）

诊断包会带出配置快照，所以**我们专门查了凭据**：

- 用 `tools/scan_secrets.py` 的模式集扫描全部 7 个条目：**API Key / Bearer / JWT / 明文凭据赋值 —— 均 0 处命中**
- `system.json` 的配置区只有布尔标记 `credentialConfigured: true`、`authType: "api-key"`，
  **没有任何明文密钥**
- 配置中出现的是非密文标识：`accountId`、`label: "hks1"`、`baseUrl: "https://api.agnes-ai.cn/v1"`、
  模型 `agnes-3.0-flash`

> 注意：诊断包内含我们本机的绝对路径（如 `D:\AI黑客松_2026_U070`）与运行环境信息
> （Node 24.15.0、Windows 10.0.26200、屏幕 2560×1440）。这些是**可复现环境所必需的信息**，
> 不涉及隐私或凭据。AGH 官方在导出时也会提示 `--raw exports without redaction (secrets, paths, PII)`。

## 六、文件指纹

| 项 | 值 |
|---|---|
| 文件名 | `agh-diagnostics-d728d0aa-20261014-134215.zip` |
| 大小 | 47064 字节 |
| SHA256 | `15C11920344188038549F97F727B3EDA75FABB1C3ED4B3F8B5DD34FCC6693934` |
| 会话 ID | `d728d0aa-e4a6-4894-b005-7e1cbbfe5136`（189 事件） |
| 导出时间 | 2026-10-14 13:42:15（文件名内嵌） |

## 七、如何自己重新导出一份

1. 启动工作台：`.\启动工作台.ps1` → 浏览器打开 <http://127.0.0.1:4177>
2. 进入目标会话
3. 点工作台里的「**导出诊断**」，保存得到的 zip

或走 CLI（本项目的 trace 导出方式）：

```powershell
$env:AGH_HOME      = "<解压目录>\.agh"
$env:AGNES_PROFILE = "local-dev"
$AGH = "<解压目录>\vendor\agnes-harness\packages\cli\dist\local\agnes.mjs"

node $AGH sessions list
node $AGH export <sessionId> --format agnes --raw -o out.jsonl
```
