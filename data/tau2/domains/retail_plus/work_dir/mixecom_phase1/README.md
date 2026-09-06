# Mix-ECom-inspired Retail Plus phase 1

This batch uses the 91 after-sales examples in Mix-ECom's `eval_gt_a.json`
as intent and language-pattern material. Mix-ECom entities, tools, scripted
answers, images, and trajectories are not merged into the Retail Plus runtime.
All executable state and golden actions use Retail Plus entities.

## Scope

The batch contains 18 tasks:

- 3 delivered-order missing-item claims;
- 3 pending-order add-item requests;
- 3 pending-order payment-method corrections;
- 3 eligible small-fee disputes;
- 1 wrong-color exchange;
- 1 size-mismatch exchange;
- 1 transit-damage return;
- 1 quality-defect return;
- 1 not-as-described return;
- 1 pre-fulfillment cancellation.

The batch adds three independent task occurrences for each of these previously
under-covered golden tools. `get_item_details` is required only where the user
knows a saved item ID but not its current catalog details; it is deliberately
not required after another tool has already returned the same item data.

- `get_item_details`
- `add_pending_order_items`
- `file_missing_item_claim`
- `modify_pending_order_payment`
- `waive_order_fee`

## Reproducible build

Run the builders in this order from the repository root:

```powershell
.\.venv\Scripts\python.exe data\tau2\domains\retail_plus\work_dir\scripts\build_phase1.py
.\.venv\Scripts\python.exe data\tau2\domains\retail_plus\work_dir\scripts\build_policy_phase1.py
.\.venv\Scripts\python.exe data\tau2\domains\retail_plus\work_dir\scripts\build_mixecom_phase1.py
```

The final builder is deterministic and safe to rerun. It upserts the three
fee fixtures and replaces tasks whose IDs start with `rp_mix_`. The locally
downloaded raw Mix-ECom directory is intentionally gitignored. On a clean
clone, the builder uses the committed selected-row audit and source manifest
as an offline fallback; downloading the full dataset is not required to run
or rebuild these 18 tasks.

## Audit artifacts

- `selected_mixecom_examples.json` records the source query, selection reason,
  adaptation, removed source assumptions, and golden tools.
- `retail_plus_bindings.json` records the Retail Plus order, user, and item IDs.
- `mixecom_source_manifest.json` records dataset attribution and the local
  source file SHA-256.

## Run only this batch

```powershell
.\.venv\Scripts\tau2.exe run `
  --domain retail_plus `
  --task-set-name retail_plus `
  --task-split-name mixecom_phase1 `
  --agent llm_agent `
  --agent-llm dashscope/qwen3.7-flash `
  --agent-llm-args '{\"temperature\":0,\"enable_thinking\":false}' `
  --user user_simulator `
  --user-llm dashscope/qwen3.7-flash `
  --user-llm-args '{\"temperature\":0,\"enable_thinking\":false}' `
  --num-trials 1 `
  --max-steps 200 `
  --max-errors 10 `
  --max-concurrency 1 `
  --seed 300 `
  --log-level INFO `
  --save-to qwen3_7_flash_retail_plus_mixecom_phase1
```

## Verification

```powershell
.\.venv\Scripts\python.exe -m pytest tests\test_domains\test_retail_plus -q
```
