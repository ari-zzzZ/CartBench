# Retail Plus data

Retail Plus extends the original Retail domain with ABCD-inspired scenarios and executable policy boundaries. Runtime files are `db.json`, `policy.md`, `tasks.json`, and `split_tasks.json`. Research, source, and deterministic build materials are documented in `work_dir/README.md`.

## Task splits

`split_tasks.json` is strict JSON consumed directly by Tau2, so it must not contain comments. The available splits are:

| Split | Tasks | Meaning |
|---|---:|---|
| `train` | 74 | Original Retail training partition. |
| `test` | 40 | Original Retail held-out partition. It is disjoint from `train`. |
| `base` | 114 | Original Retail benchmark: `train + test`. This is Tau2 CLI's default split and preserves direct comparability with Retail. |
| `abcd_phase1` | 16 | Only the first ABCD-inspired Retail Plus tasks. Use this for focused development and regression runs. |
| `policy_phase1` | 7 | Focused deterministic policy-boundary tasks. |
| `mixecom_phase1` | 18 | Mix-ECom-inspired after-sales and under-covered-tool tasks. |
| `new` | 41 | Every Retail Plus addition: `abcd_phase1 + policy_phase1 + mixecom_phase1`. |
| `base_plus` | 130 | Complete Retail Plus benchmark: `base + abcd_phase1`. |
| `all_plus` | 155 | Every original and added task: `base + new`. |

There are no duplicate IDs within a split. `base` and `new` are disjoint.

Examples:

```powershell
# Original Retail-compatible baseline
.\.venv\Scripts\tau2.exe run --domain retail_plus --task-set-name retail_plus --task-split-name base

# Only the 16 new tasks
.\.venv\Scripts\tau2.exe run --domain retail_plus --task-set-name retail_plus --task-split-name abcd_phase1

# All 41 added tasks
.\.venv\Scripts\tau2.exe run --domain retail_plus --task-set-name retail_plus --task-split-name new

# Legacy baseline + ABCD expansion
.\.venv\Scripts\tau2.exe run --domain retail_plus --task-set-name retail_plus --task-split-name base_plus

# All 155 tasks
.\.venv\Scripts\tau2.exe run --domain retail_plus --task-set-name retail_plus --task-split-name all_plus
```
