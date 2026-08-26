# Retail Plus data

Retail Plus extends the original Retail domain with ABCD-inspired scenarios and executable policy boundaries. Runtime files are `db.json`, `policy.md`, `tasks.json`, and `split_tasks.json`. Research, source, and deterministic build materials are documented in `work_dir/README.md`.

## Task splits

`split_tasks.json` is strict JSON consumed directly by Tau2, so it must not contain comments. The five splits are intentionally retained:

| Split | Tasks | Meaning |
|---|---:|---|
| `train` | 74 | Original Retail training partition. |
| `test` | 40 | Original Retail held-out partition. It is disjoint from `train`. |
| `base` | 114 | Original Retail benchmark: `train + test`. This is Tau2 CLI's default split and preserves direct comparability with Retail. |
| `abcd_phase1` | 16 | Only the first ABCD-inspired Retail Plus tasks. Use this for focused development and regression runs. |
| `base_plus` | 130 | Complete Retail Plus benchmark: `base + abcd_phase1`. |

There are no duplicate IDs within a split. `base` and `abcd_phase1` are disjoint.

Examples:

```powershell
# Original Retail-compatible baseline
.\.venv\Scripts\tau2.exe run --domain retail_plus --task-set-name retail_plus --task-split-name base

# Only the 16 new tasks
.\.venv\Scripts\tau2.exe run --domain retail_plus --task-set-name retail_plus --task-split-name abcd_phase1

# All 130 Retail Plus tasks
.\.venv\Scripts\tau2.exe run --domain retail_plus --task-set-name retail_plus --task-split-name base_plus
```
