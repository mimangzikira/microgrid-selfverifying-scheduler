# 会自己验算自己的园区微网调度智能体

**生成与校验分离的微网调度智能体 —— 校验侧判定可行性时不 import 生成侧任何代码。**

给它一个园区的负荷、光伏与分时电价曲线，它自己建模、求解、**并独立验算**调度方案；
算错了能自己发现、自己回退重算，算不出来就如实说算不出来。

[English](README.md) | **简体中文**

> 本项目源自 **2026 江苏省高校 AI+科学与工程创新实践黑客松**（本科生组，队伍「然智核」，参赛编号 U070），
> 以 **[Agnes Harness（AGH）](https://github.com/AgnesAI-Labs/agnes-harness)** 为智能体执行底座。

---

## 一、项目介绍（Project Overview）

### 项目背景

园区微网每天要决定一件事：**什么时候从电网买电、什么时候给储能充电、什么时候放电**。
电价是三档分时的，峰谷差 4:1 —— 做对了能省真金白银，做错了也看不出来：数字看起来都对。

这是典型的「**数字都对、但其实全错**」场景。如果让大模型智能体直接给一个调度方案，
你凭什么信它？方案可能违反功率平衡、可能超出储能与电网限值，也可能只是把平凡解包装得很漂亮。

现有科研智能体普遍缺一环：**没有独立的一方去验算它的结果**。模型宣称"我检查过了"不构成验证
—— 自己批自己不是验证。

### 核心功能

| 能力 | 说明 |
|---|---|
| **建模与求解** | 把日调度问题建成线性规划，用 HiGHS 求解 |
| **独立校验** | 三层共 11 项检查，自己重读原始 CSV、用独立算术重算 |
| **自我修复** | 校验未过则丢掉出错原因重新干净求解，**重算结果仍须过校验** |
| **如实失败** | 仍不过就报告失败，**拒绝交付错误方案** |
| **完整留档** | 每次运行留下 `manifest` / `solution` / `verification` 三件套 |

### 创新亮点

1. **「生成与校验分离」被做成可证伪的。**
   `mg_verify.py` 判定可行性时**不 import** `mg_model.py`，两条路不共享任何代码。
   更进一步，我们**证明**了这不是口号：两种注入**只改生成侧、坚持不改输入数据**，
   让生成侧漏掉一条约束。

   | 注入的生成侧缺陷 | 生成侧给出的解 | 被谁抓住 | 修复后 |
   |---|---|---|---|
   | 漏掉端面储荷约束 | **便宜** 94.74 元 | **V1.4 端面储荷条件** | 回到正确成本 |
   | 漏掉储能功率上限 | **便宜** 122.68 元 | **V1.3 变量上下限** | 回到正确成本 |

   两个缺陷的共同点：解**更便宜、看起来更优**，但违反真实约束。
   生成侧自己发现不了（它按自己的模型求解，内部完全自洽），**只有独立重算的一方才能抓住**。

2. **校验器的覆盖面是被测量的，不是被宣称的。**
   `tools/mutate_verify.py` 按约束类别把一条正确方案逐个改坏，看是哪一项抓住：
   **8 类缺陷 → 8 个全部被抓住、全部被拦下**。
   尤其"方案可行、只把成本少报 5%"这类错误**只有 V1.5（独立重算目标值）能发现**，
   证明该检查不是多余的。

3. **自我修复不等于自我认证。**
   重试只是"再搜一次"，**不是信任的来源**：修复后的方案必须通过**同一套独立校验**才被接受。
   状态 `recovered` 精确记录这件事。

4. **三层校验，且如实标注强弱。**
   V1 精确残差（1e-6）· V2 上下界夹逼（**承认是松的**）· V3 闭式解退化复现（黑箱差分）。
   松的那一层在报告里就写着"松" —— 知道自己哪个检查弱，比宣称每个都严密更有意义。

---

## 二、视频介绍（Demo Video）

**▶ [观看 4 分钟演示（B 站）](https://www.bilibili.com/video/BV1iTpm6pERf/)**

视频为无口播版（屏上文字卡），内容：智能体规划任务 → 调用工具 → 抓住人为注入的生成侧缺陷 →
自我修复 → 变异覆盖矩阵。

---

## 三、架构介绍（Architecture）

```
                AGH 底座（会话 / 工具调度 / 审批 / 日志）
                                    │
      ┌─────────────────────────────┼─────────────────────────────┐
      │                             │                             │
    生成侧                       校验侧                        编排
   mg_model.py                 mg_verify.py                  mg_run.py
  （LP + HiGHS）          （不 import 生成侧）        （读→解→验→报）
                                    │
                     V1 约束残差    精确（1e-6）
                     V2 上下界夹逼  有效但松
                     V3 退化复现    精确（黑箱差分）
```

### 核心模块

| 模块 | 职责 | 关键点 |
|---|---|---|
| `tools/mg_load.py` | 数据装载与校验 | 脏数据被拦在**求解之前** |
| `tools/mg_model.py` | 生成侧：LP + HiGHS | 只负责给方案，不做任何校验 |
| `tools/mg_verify.py` | 独立校验器，11 项检查 | **不 import 生成侧** |
| `tools/mg_run.py` | 编排器 | 读→解→验，含回退重算与 11 种注入 |
| `tools/mg_report.py` | 报告与调度曲线图 | 产出 `report.md` 与 `dispatch.png` |
| `tools/mg_show.py` | 结果查看器 | `--evidence` 打印录片所需的关键证据行 |
| `tools/mutate_verify.py` | 变异覆盖矩阵 | 8 种改坏方式，看谁抓住 |
| `tools/make_sample_data.py` | 合成算例生成器 | 让人 clone 即可跑（见「数据来源」） |
| `tools/scan_secrets.py` | 发布前密钥扫描 | 发布前须退出码 0 |

### 实现方式

- **生成侧**：每时段决策变量为电网购电 `g`、充电 `c`、放电 `d`、光伏弃电 `x`、储荷 `s`；
  约束为功率平衡、储荷递推、端面储荷、变量上下限；目标为最小化日购电成本；
  用 `scipy.optimize.linprog(method="highs")` 求解。
- **校验侧**：自己重读原始 CSV，用自己的算术重算每一条约束。V1 容差 `1e-6`；
  V2 构造解析下界与必定可行的上界；V3 用三个闭式解算例，把生成侧当黑箱要求它复现。
- **失败处理**：三层 —— 数据不合法就地失败（退出码 2）；求解器失败降级为规则方案仍须过校验
  （退出码 4）；校验未过则回退重算一次、仍须过校验（退出码 3，或 `recovered` 的 0）。
- **产物**：`manifest.json`（运行记录，含 `attempts` 与 `recovered`）、`solution.json`（完整调度方案）、
  `verification.json`（11 项明细）；发生回退重算时另有 `verification_attempt1.json`
  （否则会被第 2 次覆写，"哪一项抓住的"就查不到了）。

---

## 四、集成标准（Integration Standards）

### 4.1 输入时序 — CSV 契约

`--csv <路径>`，UTF-8（容忍 BOM），必须有表头，每时段一行：

| 列名 | 类型 | 约束 |
|---|---|---|
| `period` | int | 唯一、严格递增、从 1 开始 |
| `hour` | string | 自由文本（如 `08:00`） |
| `load_kw` | float | ≥ 0 且有限 |
| `pv_kw` | float | ≥ 0 且有限 |
| `price_cny_per_kwh` | float | > 0 且有限 |

行数必须等于 `params.periods`。

### 4.2 参数 — JSON 契约

`--params <路径>`：

```json
{
  "periods": 24,
  "period_hours": 1.0,
  "storage": {
    "capacity_kwh": 2000.0,
    "power_kw": 500.0,
    "eta_charge": 0.95,
    "eta_discharge": 0.95,
    "soc_init_kwh": 400.0,
    "soc_min_kwh": 100.0,
    "soc_terminal_kwh": 400.0
  },
  "grid": { "import_limit_kw": 1500.0, "export_allowed": false },
  "pv":   { "curtail_allowed": true }
}
```

校验规则：`0 ≤ soc_min ≤ soc_init, soc_terminal ≤ capacity`；`0 < eta ≤ 1`；`import_limit_kw ≥ 0`。
违反者在进入求解器前抛 `E_*`。

### 4.3 输出契约 — 退出码

| 退出码 | `status` | 含义 |
|---|---|---|
| **0** | `ok` | 求解成功且 11 项校验全过 |
| **0** | `recovered` | 第 1 次校验未过；**回退重算后通过** |
| **2** | `failed` | 数据非法 / 参数不可行 —— 在求解前拦下 |
| **3** | `verification_failed` | 求解给出的方案被校验器**拒绝**，不交付 |
| **4** | `degraded` | 求解器失败，改用规则方案并已通过校验 |

> 下游代码应分支判断 `manifest.json` 的 `status`，而不是只看 shell 退出码
> （某些外壳会把非零码归一化为 1）。

### 4.4 机读结果

`manifest.json` 片段：

```json
{
  "schema": "mg.run/v1",
  "status": "recovered",
  "attempts": [
    {"attempt": 1, "passed": false, "failed_checks": [{"id": "V1.4", "name": "端面储荷条件"}]},
    {"attempt": 2, "passed": true,  "failed_checks": []}
  ],
  "recovered": true,
  "objective_cny": 4651.417346,
  "bounds": {"lower_bound_cny": 2766.06, "upper_bound_cny": 6167.130158}
}
```

`verification.json` 携带 11 项检查，每项含 `id`、`name`、`kind`（`exact` / `sandwich` /
`differential`）、`passed`、`skipped`、`detail`。

### 4.5 错误码

所有校验错误都是结构化的 `{"code": "E_*", "message": ..., "detail": {...}}`。

| 分组 | 错误码 |
|---|---|
| 文件 | `E_MISSING_FILE` `E_ENCODING` `E_EMPTY_FILE` |
| 结构 | `E_SCHEMA`（缺列）`E_ROW_COUNT` `E_PERIOD_SEQUENCE` `E_DUPLICATE_PERIOD` |
| 字段值 | `E_EMPTY_FIELD` `E_PARSE` `E_NOT_FINITE` `E_NEGATIVE_LOAD` `E_NEGATIVE_PV` `E_NONPOSITIVE_PRICE` |
| 参数 | `E_PARAMS_JSON` `E_PARAMS_SHAPE` `E_PARAMS_MISSING` `E_PARAMS_PERIODS` `E_GRID_LIMIT` `E_STORAGE_RANGE` `E_STORAGE_SOC_MIN` `E_STORAGE_SOC_BOUND` `E_STORAGE_EFFICIENCY` |
| 求解器 / 命令行 | `E_SOLVER_FAILED` `E_DEFECT_UNKNOWN` `E_INJECT_UNKNOWN` |

共 25 个错误码；除 `E_SOLVER_FAILED` 外，其余全部在进入求解器之前抛出。

### 4.6 故障注入接口

`--inject <kind>`，共 11 种，分两类：

**输入污染**（改数据，考核数据层）：`drop_rows` `bad_number` `neg_price` `empty_field`
`infeasible_soc` `zero_storage_power` `pv_overload` `no_supply` `zero_storage_capacity`

**生成侧建模缺陷**（不动数据，考核分离性）：`defect_no_terminal_soc` `defect_no_storage_power`

### 4.7 接入自己的模型或校验器

两侧可独立替换：

- **换生成侧** —— 保留 `mg_verify.py` 做审计方。它只需要方案里含 `dispatch` 列表
  （`grid_import_kw`、`charge_kw`、`discharge_kw`、`curtail_kw`、`soc_kwh`）、
  `metrics.soc_terminal_kwh` 与 `objective_cny`。
- **换校验侧** —— 保留 `mg_model.py` / `mg_run.py`，替换校验阶段。
  判定依据**只能是原始 CSV + 参数**，绝不是生成侧的内部实现。
- **程序化调用** —— `mg_run.run(csv_path, params_path, outdir, inject=None)` 返回 manifest 字典；
  `mg_verify.verify(csv_path, params_path, solution)` 返回 11 项检查字典。

---

## 快速开始

**环境要求**：Python ≥ 3.10（开发用 3.13），NumPy / SciPy / Matplotlib。无 GPU、无商业软件。

```bash
git clone https://github.com/你的用户名/mgsched.git
cd mgsched
pip install -r requirements.txt

# 生成一份合成算例（原始赛事数据不随仓库分发）
python tools/make_sample_data.py

# 完整流水线：读数据 → 求解 → 独立校验
python tools/mg_run.py --csv data/park_day.csv --params data/params.json --outdir evidence/runs/normal
python tools/mg_show.py --run evidence/runs/normal

# 12 个用例的测试套件（正常 / 边界 / 失败 / 分离性）
python tests/run_tests.py

# 变异覆盖矩阵：8 类缺陷，8/8 被抓住
python tools/mutate_verify.py

# 看它抓住故意改坏的生成侧，然后自我修复
python tools/mg_run.py --csv data/park_day.csv --params data/params.json \
  --outdir evidence/runs/demo_defect --inject defect_no_terminal_soc
python tools/mg_show.py --run evidence/runs/demo_defect --evidence
```

> 说明：以上**全部不需要 AGH**。只有智能体执行闭环（证据在 `evidence/agh_diagnostics/`）
> 需要另行安装 AGH。

---

## 数据来源

**本仓库不分发原始算例数据。**

本项目的原始算例是在 **2026 年高教社杯全国大学生数学建模竞赛 C 题
「微网与外部电网电力调控策略的优化建模与求解」**（全国大学生数学建模竞赛组委会发布）
**官方附件数据**的基础上，把更高时间粒度的负荷、光伏与分时电价**聚合为 1 小时粒度的
24 时段算例**整理而成。该数据的**版权属于赛事组委会**，因此不随本仓库分发。

`tools/make_sample_data.py` 生成**同格式的合成算例**，让任何人 clone 下来即可跑通。
数值不同，因此**跑出的成本不同**，但约束结构、校验逻辑、自我修复机制完全一致。

**方法的正确性不依赖具体数值**：校验器逐条重算的是*给定数据*下的约束与目标，
换成任何同格式的 CSV + 参数 JSON 都能跑。本项目结论不构成对任何真实园区运行状况的论断。

> 原始算例的参考结果（仅供对照，本仓库无法复现该数值）：
> 日购电成本 4651.42 元，无储能基准 6363.46 元，相对节省 26.90%，11 项校验全过。

---

## 已知限制

- 24 时段**确定性**线性规划：未考虑随机场景、需求响应、电池循环寿命衰减、电网互动结算规则。
- **V2 的上下界在异常算例上会退化**（例如购电上限被置 0 时，上下界塌缩成同一个数）。
  精确性仍由 V1/V3 保证，它们不依赖界。
- 变异矩阵只覆盖已枚举的 8 类缺陷 —— **不声称零盲区**。
- 自我修复是单次回退，不是对方案空间的通用搜索。

---

## 许可与致谢

- 代码以 **MIT License** 发布，见 [LICENSE](LICENSE)。
- **[Agnes Harness（AGH）](https://github.com/AgnesAI-Labs/agnes-harness)** —— 官方开源智能体执行底座，本项目**未修改其源码**。
- **[HiGHS](https://highs.dev/)** —— 经 `scipy.optimize.linprog` 调用的线性规划求解器。
- **NumPy**（BSD-3-Clause）、**SciPy**（BSD-3-Clause）、**Matplotlib**（PSF 系列许可）—— 作为库使用。
- 算例条件参考 **2026 年国赛 C 题**（数据不分发，见「数据来源」）。

**参赛信息**：2026 江苏省高校 AI+科学与工程创新实践黑客松 · 本科生组 · 队伍「然智核」· 参赛编号 **U070**。
