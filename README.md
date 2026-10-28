# Self-Verifying Microgrid Dispatch Agent

**A microgrid dispatch agent whose verifier never imports the generator.**

Give it a park's load, PV, and time-of-use tariff curves. It models the day-ahead dispatch
problem, solves it, **and independently verifies its own answer**. When the answer is wrong it
catches itself, backtracks, and re-solves. When it genuinely cannot solve, it says so.

**English** | [简体中文](README.zh-CN.md)

> Origin: **2026 Jiangsu Province University AI+Science & Engineering Hackathon**
> (Undergraduate track, team 然智核, entry U070), built on
> **[Agnes Harness (AGH)](https://github.com/AgnesAI-Labs/agnes-harness)** as the agent execution substrate.

---

## 1. Project Overview

### Background

Park-level microgrids decide every day *when to buy from the grid, when to charge storage, and
when to discharge*. Time-of-use tariffs with a 4:1 peak-to-valley ratio mean a correct schedule
saves real money — and a wrong one is invisible: the numbers look plausible.

This is a classic "**the digits all check out, but it's actually wrong**" problem. If you ask a
language model agent for a dispatch plan, why should you believe it? The plan may violate power
balance, exceed storage or grid limits, or simply be a trivial solution dressed up nicely.

Research agents share one missing piece: **nobody independently audits the result.** A model
claiming *"I checked it"* is not verification — self-review is not verification.

### Core functionality

| Capability | What it does |
|---|---|
| **Model & solve** | Builds a linear program (LP) for the day-ahead dispatch and solves it with HiGHS |
| **Independent verification** | 11 checks in three layers, recomputed from the raw CSV by separate arithmetic |
| **Self-repair** | If verification fails, drops the cause and re-solves cleanly; the result must verify again |
| **Honest failure** | If it still fails, it reports failure and **refuses to hand over a wrong plan** |
| **Full audit trail** | Every run leaves `manifest` / `solution` / `verification` artifacts |

### Innovation highlights

1. **Generation–verification separation, made falsifiable.**
   `mg_verify.py` decides feasibility *without importing* `mg_model.py` — no shared code path.
   We then *prove* this is real: two injections deliberately delete one constraint from the
   **generator only** (input data untouched). The verifier catches them every time.

   | Injected generator defect | Generator's plan | Caught by | After repair |
   |---|---|---|---|
   | Terminal SOC constraint omitted | **cheaper** by 94.74 CNY | **V1.4 terminal SOC** | back to correct cost |
   | Storage power limit omitted | **cheaper** by 122.68 CNY | **V1.3 variable bounds** | back to correct cost |

   Both defects produce plans that are *cheaper and look better* — yet violate real constraints.
   The generator cannot detect this on its own; only an independent recomputation can.

2. **Verifier coverage is measured, not asserted.**
   `tools/mutate_verify.py` corrupts a correct plan category by category and checks which
   verifier catches it: **8 defect classes → 8/8 caught and blocked**.
   Notably, "feasible plan, cost under-reported by 5%" is caught **only** by V1.5
   (independent objective recomputation) — proof that check is not redundant.

3. **Self-repair that is not self-certification.**
   A retry is just *another search*, never a source of trust: the repaired plan must pass the
   **same independent verification** to be accepted. Status `recovered` records exactly this.

4. **Three-layer verification with an honest strength label.**
   V1 exact residuals (1e-6) · V2 sandwich bounds (**admittedly loose**) · V3 closed-form
   degenerate cases (black-box differential). The loose layer is labelled loose in the report —
   knowing which check is weak beats claiming every check is tight.

---

## 2. Demo Video

**▶ [Watch the demo on Bilibili](https://www.bilibili.com/video/BV1iTpm6pERf/)** · about 4 min 40 s

| | |
|---|---|
| **Platform** | Bilibili — public, no login required, no access code |
| **Length** | about 4 min 40 s (contest requirement: 3–5 min) |
| **Format** | screen recording, **no narration** — explained entirely by on-screen captions |
| **Language** | **Chinese captions** (produced for the Chinese-language contest) |

**What it covers** (in roughly this order):

1. **The problem** — a microgrid dispatch plan you have no reason to trust
2. **The design** — generation/verification separation; the verifier does not import the generator
3. **A live run** — the agent plans the task, invokes tools, solves, and runs the 11 independent checks
4. **An infeasible case** — the plan is refused and reported as a failure rather than handed over
5. **A deliberately broken generator** — the verifier catches it, then the system backtracks and repairs itself
6. **The mutation coverage matrix** — 8 defect classes, 8/8 caught
7. **Wrap-up** — what this is worth and where it generalises

> The video is Chinese-language with captions only. The English README and the source code are
> the technical reference. The verification and self-repair behaviour shown is reproducible from
> source **without AGH** (see [Quick Start](#quick-start)); only the agent execution loop was
> run inside AGH.

---

## 3. Architecture

```
                AGH substrate (sessions / tool dispatch / approvals / logs)
                                    │
      ┌─────────────────────────────┼─────────────────────────────┐
      │                             │                             │
 GENERATOR                     VERIFIER                    ORCHESTRATOR
 mg_model.py                  mg_verify.py                  mg_run.py
 (LP + HiGHS)          (never imports generator)      (load → solve → verify → report)
                                    │
                     V1 constraint residuals   exact (1e-6)
                     V2 sandwich bounds        valid but LOOSE
                     V3 degenerate replays     exact (black-box differential)
```

### Core modules

| Module | Role | Key point |
|---|---|---|
| `tools/mg_load.py` | Load & validate input | Dirty data is stopped **before** the solver runs |
| `tools/mg_model.py` | Generator: LP + HiGHS | Only produces a plan; verifies nothing |
| `tools/mg_verify.py` | Independent verifier, 11 checks | **Does not import the generator** |
| `tools/mg_run.py` | Orchestrator | Load → solve → verify, with backtracking and 11 injection modes |
| `tools/mg_report.py` | Report + dispatch curve | Produces `report.md` and `dispatch.png` |
| `tools/mg_show.py` | Result viewer | `--evidence` prints exactly the lines needed for a demo |
| `tools/mutate_verify.py` | Mutation coverage matrix | Corrupts a plan 8 ways, checks who catches it |
| `tools/make_sample_data.py` | Synthetic sample generator | Lets anyone clone and run (see Data Provenance) |
| `tools/scan_secrets.py` | Pre-publish secret scan | Exit code 0 required before publishing |

### The 11 checks

| ID | Check | Kind | What it verifies |
|---|---|---|---|
| V1.1 | Power balance residual | exact | `g + d + pv = load + c + x` every period, tol `1e-6` |
| V1.2 | SoC recursion residual | exact | `s[t+1] = s[t] + ηc·c − d/ηd` every period, tol `1e-6` |
| V1.3 | Variable bounds | exact | `0 ≤ g ≤ import_limit`, `0 ≤ c,d ≤ power_kw`, `0 ≤ x ≤ pv`, `soc_min ≤ s ≤ capacity` |
| V1.4 | Terminal SoC condition | exact | `s[0] = soc_init`, `s[T] = soc_terminal` |
| V1.5 | Independent objective recomputation | exact | Recomputes `Σ price·g·dt` from the dispatch and compares with the reported cost |
| V2.1 | Analytic lower bound ≤ cost | sandwich | Cost cannot be below a closed-form relaxation bound |
| V2.2 | Cost ≤ feasible upper bound | sandwich | Cost cannot exceed a known-feasible rule-based plan |
| V2.3 | Gap reporting | sandwich | Prints the relative gap of the sandwich honestly |
| V3.1 | No-storage degenerate case | differential | Generator (as a black box) must reproduce the closed form |
| V3.2 | Flat-price degenerate case | differential | Same — no arbitrage opportunity left |
| V3.3 | PV-surplus flat-price case | differential | Same — optimal cost is identically 0 |

### Implementation notes

- **Generator.** Decision variables per period: grid import `g`, charge `c`, discharge `d`,
  PV curtailment `x`, state of charge `s`. Constraints: power balance, SoC recursion,
  terminal SoC, variable bounds. Objective: minimize daily purchase cost. Solved via
  `scipy.optimize.linprog(method="highs")`.
- **Verifier.** Re-reads the raw CSV, recomputes every constraint with its own arithmetic.
  V1 uses `1e-6` tolerances; V2 builds analytic lower / feasible upper bounds; V3 solves three
  degenerate cases in closed form and requires the generator (treated as a black box) to match.
- **Failure handling.** Three layers: invalid data → fail in place (exit 2); solver failure →
  degrade to a rule-based plan, still verified (exit 4); verification failure → backtrack and
  re-solve once, still verified (exit 3, or 0 with `recovered`).
- **Artifacts.** `manifest.json` (run record, incl. `attempts` and `recovered`),
  `solution.json` (full dispatch), `verification.json` (11 checks), and on backtracking also
  `verification_attempt1.json` (otherwise attempt 2 would overwrite which check caught it).

---

## 4. Integration Standards

### 4.1 Input time series — CSV contract

`--csv <path>`, UTF-8 (BOM tolerated), header required, one row per period:

| Column | Type | Constraint |
|---|---|---|
| `period` | int | unique, strictly increasing, 1-based |
| `hour` | string | free-form label (e.g. `08:00`) |
| `load_kw` | float | ≥ 0, finite |
| `pv_kw` | float | ≥ 0, finite |
| `price_cny_per_kwh` | float | > 0, finite |

Row count must equal `params.periods`.

### 4.2 Parameters — JSON contract

`--params <path>`:

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

Validation: `0 ≤ soc_min ≤ soc_init, soc_terminal ≤ capacity`; `0 < eta ≤ 1`;
`import_limit_kw ≥ 0`. Violations raise `E_*` before the solver runs.

### 4.3 Output contract — exit codes

| Exit | `status` | Meaning |
|---|---|---|
| **0** | `ok` | Solved and all 11 checks passed |
| **0** | `recovered` | First attempt failed verification; **backtracked and re-solved; passes now** |
| **2** | `failed` | Invalid data / infeasible parametrisation — stopped before the solver |
| **3** | `verification_failed` | Solver produced a plan that the verifier **refused**. No plan is handed over |
| **4** | `degraded` | Solver failed; a rule-based plan was used and verified |

> Downstream code should branch on `manifest.json` → `status`, not only on the shell exit code
> (some shells normalise non-zero codes to 1).

### 4.4 Machine-readable results

`manifest.json` (excerpt):

```json
{
  "schema": "mg.run/v1",
  "status": "recovered",
  "attempts": [
    {"attempt": 1, "passed": false, "failed_checks": [{"id": "V1.4", "name": "terminal SOC"}]},
    {"attempt": 2, "passed": true,  "failed_checks": []}
  ],
  "recovered": true,
  "objective_cny": 4651.417346,
  "bounds": {"lower_bound_cny": 2766.06, "upper_bound_cny": 6167.130158}
}
```

`verification.json` carries the 11 checks with `id`, `name`, `kind` (`exact` / `sandwich` /
`differential`), `passed`, `skipped`, `detail`.

### 4.5 Error codes

All validation errors are structured `{"code": "E_*", "message": ..., "detail": {...}}`.

| Group | Codes |
|---|---|
| Files | `E_MISSING_FILE` `E_ENCODING` `E_EMPTY_FILE` |
| Schema | `E_SCHEMA` (missing columns) `E_ROW_COUNT` `E_PERIOD_SEQUENCE` `E_DUPLICATE_PERIOD` |
| Field values | `E_EMPTY_FIELD` `E_PARSE` `E_NOT_FINITE` `E_NEGATIVE_LOAD` `E_NEGATIVE_PV` `E_NONPOSITIVE_PRICE` |
| Parameters | `E_PARAMS_JSON` `E_PARAMS_SHAPE` `E_PARAMS_MISSING` `E_PARAMS_PERIODS` `E_GRID_LIMIT` `E_STORAGE_RANGE` `E_STORAGE_SOC_MIN` `E_STORAGE_SOC_BOUND` `E_STORAGE_EFFICIENCY` |
| Solver / CLI | `E_SOLVER_FAILED` `E_DEFECT_UNKNOWN` `E_INJECT_UNKNOWN` |

25 codes in total, all raised before the solver runs (except `E_SOLVER_FAILED`).

### 4.6 Fault-injection interface

`--inject <kind>` (11 kinds). Two families:

**Input corruption** (mutates data, exercises the data layer):
`drop_rows` `bad_number` `neg_price` `empty_field` `infeasible_soc`
`zero_storage_power` `pv_overload` `no_supply` `zero_storage_capacity`

**Generator-side modelling defects** (data untouched — exercises separation):
`defect_no_terminal_soc` `defect_no_storage_power`

### 4.7 Integrating your own model or verifier

Both sides are replaceable independently:

- **Bring your own generator** — keep `mg_verify.py` as the auditor. It only needs
  `verification.json`-compatible input: a `dispatch` list with `grid_import_kw`, `charge_kw`,
  `discharge_kw`, `curtail_kw`, `soc_kwh`, plus `metrics.soc_terminal_kwh` and `objective_cny`.
- **Bring your own verifier** — keep `mg_model.py`/`mg_run.py`; replace the verification
  stage. The oracle is **the raw CSV + params**, never the generator's internals.
- **Programmatic use** — `mg_run.run(csv_path, params_path, outdir, inject=None)` returns the
  manifest dict; `mg_verify.verify(csv_path, params_path, solution)` returns the 11-check dict.

---

## Quick Start

**Requirements:** Python ≥ 3.10 (developed on 3.13), NumPy / SciPy / Matplotlib.
No GPU, no commercial software.

```bash
git clone https://github.com/<your-name>/mgsched.git
cd mgsched
pip install -r requirements.txt

# Generate a synthetic sample case (original competition data is NOT redistributed)
python tools/make_sample_data.py

# Full pipeline: load → solve → independent verify
python tools/mg_run.py --csv data/park_day.csv --params data/params.json --outdir evidence/runs/normal
python tools/mg_show.py --run evidence/runs/normal

# 12-case test suite (normal / boundary / failure / separation)
python tests/run_tests.py

# Mutation coverage matrix: 8 defect classes, 8/8 caught
python tools/mutate_verify.py

# Watch it catch a deliberately broken generator, then repair itself
python tools/mg_run.py --csv data/park_day.csv --params data/params.json \
  --outdir evidence/runs/demo_defect --inject defect_no_terminal_soc
python tools/mg_show.py --run evidence/runs/demo_defect --evidence
```

> Note: AGH is **not** required for any of the above. Only the agent execution loop
> (evidenced in `evidence/agh_diagnostics/`) needs AGH installed separately.

---

## Data Provenance

**The original case data is not redistributed in this repository.**

The original case was compiled from the official attachment of the **2026 CUMCM (China
Undergraduate Mathematical Contest in Modeling) Problem C, "Optimization Modeling and Solution
of Power Regulation Strategies between a Microgrid and the External Grid"**, published by the
CUMCM Organizing Committee — by aggregating higher-resolution load / PV / tariff series into a
24-period hourly case. **Copyright of that data belongs to the contest organizer**, hence it is
excluded here.

`tools/make_sample_data.py` generates a **synthetic case of the same shape** so that anyone can
clone and run immediately. Its numbers differ, so the resulting costs differ — but the constraint
structure, verification logic, and self-repair mechanism are identical.

**The method does not depend on the specific numbers**: the verifier recomputes constraints and
the objective *for whatever data it is given*. Any CSV + params JSON of the same schema works.
Nothing here is a claim about any real park's operation.

> Reference results on the original case (for comparison only; not reproducible from this repo):
> daily cost 4651.42 CNY, no-storage baseline 6363.46 CNY, 26.90% saving, 11/11 checks passed.

---

## Known Limitations

- 24-period **deterministic** LP: no stochastic scenarios, demand response, battery cycle-life
  degradation, or grid settlement rules.
- **V2 bounds degrade on corrupted cases** (e.g. when the import limit is forced to 0, the lower
  and upper bounds collapse to the same number). Precision is still guaranteed by V1/V3, which do
  not rely on bounds.
- The mutation matrix covers only the 8 enumerated defect classes — **no claim of zero blind spots**.
- The self-repair is a single backtrack; it is not a general search over plan space.

---

## License & Attribution

- Code released under the **MIT License** — see [LICENSE](LICENSE).
- **This repository does not include Agnes Harness (AGH) source code.** AGH is distributed by its
  own official repository under its own license; this project only *calls* it and **does not modify it**.
- **[Agnes Harness (AGH)](https://github.com/AgnesAI-Labs/agnes-harness)** — official open-source
  agent substrate; used unmodified as the execution base.
- **[HiGHS](https://highs.dev/)** via `scipy.optimize.linprog` — LP solver.
- **NumPy** (BSD-3-Clause), **SciPy** (BSD-3-Clause), **Matplotlib** — used as libraries,
  each under its own license.
- Case conditions referenced from **2026 CUMCM Problem C** (data not redistributed; see above).

**Team:** 然智核 (Ranzhihe) · Undergraduate track · Entry **U070** ·
2026 Jiangsu Province University AI+Science & Engineering Hackathon.
