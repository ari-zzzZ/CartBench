# Retail Plus 工作区

这个目录保存 Retail Plus 的研究、来源和可复现构建材料。它不参与 `tau2 run` 的运行时加载；运行时真正读取的是上一级目录中的 `db.json`、`tasks.json`、`split_tasks.json` 和 `policy.md`。

## 目录结构

```text
work_dir/
├─ README.md
├─ scripts/                  # 确定性分析与生成脚本
│  ├─ analyze_retail_intents.py
│  ├─ select_abcd_examples.py
│  ├─ build_phase1.py
│  └─ build_policy_phase1.py
├─ sources/                  # 外部原始来源，不做业务改写
│  └─ abcd_v1_1/
│     ├─ abcd_v1.1.json.gz
│     ├─ guidelines.json
│     ├─ ontology.json
│     ├─ kb.json
│     ├─ README.md
│     └─ LICENSE
├─ analysis/                 # Retail 与 ABCD 的意图覆盖分析
│  ├─ INTENT_GAP_ANALYSIS.md
│  ├─ intent_labels.json
│  └─ abcd_intent_coverage.json
└─ phase1/                   # 第一批 8 意图、16 Task 的构建与审计产物
   ├─ abcd_candidate_shortlist.json
   ├─ selected_abcd_examples.json
   ├─ retail_plus_bindings.json
   └─ abcd_source_manifest.json
```

## 哪些文件是必要的

### `scripts/`

- `analyze_retail_intents.py`：对原始 114 个 Retail Task 进行确定性意图标注，输出 `analysis/intent_labels.json`。
- `select_abcd_examples.py`：使用固定 seed `20260824`，从 ABCD 官方 test split 为 8 个意图各抽取候选对话。
- `build_phase1.py`：以原始 Retail 数据和候选对话为输入，确定性重建 Retail Plus 的 DB、130 个 Task、splits、实体绑定和来源审计。
- `build_policy_phase1.py`：在上述产物上确定性加入 7 个策略回归 Task，并生成 `policy_phase1` 和 `all_plus` split；总任务数变为 137。

这些脚本是可复现性的核心，应该保留。

### `sources/abcd_v1_1/`

这里是固定到 ABCD commit `6b8700ce67c6b37b062dd7a60abc76d7ef832a97` 的官方 v1.1 数据及许可证。`abcd_v1.1.json.gz` 约 35.3 MiB，是本目录绝大部分体积的来源。

如果只想运行现有 Retail Plus，它可以不在本机；但如果需要重新筛选 ABCD 对话、核查原文或继续扩展意图，它就是必要的。为了让仓库中的生成过程可以离线复现，本项目选择保留它。

### `analysis/`

- `INTENT_GAP_ANALYSIS.md`：114 个原始 Retail Task 与 ABCD 55 个意图的差距结论。
- `intent_labels.json`：原 Task 的逐条机器标签及证据。
- `abcd_intent_coverage.json`：ABCD 55 个意图的覆盖状态、样本量和建议阶段。

它们不影响运行，但会作为后续选择第二批意图的依据，因此属于研究资产而不是临时文件。

### `phase1/`

- `abcd_candidate_shortlist.json`：固定 seed 得到的候选池，是 `build_phase1.py` 的直接输入。
- `selected_abcd_examples.json`：16 个 Task 的 ABCD split、convo_id、选择原因、改写内容、删除或替换的 ABCD 规则和绑定实体。
- `retail_plus_bindings.json`：Task 对应的用户、订单、商品、退款、Voucher 等 Retail 实体；工具测试也使用它。
- `abcd_source_manifest.json`：ABCD 来源版本、文件大小和 SHA-256，用于验证来源没有悄悄变化。

这些文件保证新增 Task 可追溯、可审计，应该保留。

## 第一批任务

第一批加入 8 个意图，每个意图包含 1 条正常路径和 1 条拒绝、边界或转人工路径，共 16 个 Task：

| Retail Plus 意图 | ABCD test convo（正常 / 边界） |
|---|---:|
| `refund_status` | 8102 / 8354 |
| `mistimed_billing_already_returned` | 431 / 4761 |
| `promo_code_invalid` | 3631 / 6233 |
| `promo_code_out_of_date` | 6965 / 6395 |
| `status_mystery_fee` | 9816 / 6178 |
| `shipping_issue.missing` | 2343 / 1403 |
| `manage_create` | 8819 / 7317 |
| `manage_change_phone` | 1941 / 405 |

16 个任务的 `reward_basis` 均为 `DB + ACTION + COMMUNICATE`。

## 生成顺序

在项目根目录执行：

```powershell
.\.venv\Scripts\python.exe data\tau2\domains\retail_plus\work_dir\scripts\analyze_retail_intents.py
.\.venv\Scripts\python.exe data\tau2\domains\retail_plus\work_dir\scripts\select_abcd_examples.py
.\.venv\Scripts\python.exe data\tau2\domains\retail_plus\work_dir\scripts\build_phase1.py
.\.venv\Scripts\python.exe data\tau2\domains\retail_plus\work_dir\scripts\build_policy_phase1.py
```

前两个脚本分别更新意图标签和 ABCD 候选池；最后一个脚本会从原始 Retail 基线重新生成 Retail Plus 数据，因此执行前应确保需要保留的手工修改已经进入生成脚本。

## 运行新增 16 个任务

```powershell
.\.venv\Scripts\tau2.exe run `
  --domain retail_plus `
  --task-set-name retail_plus `
  --task-split-name abcd_phase1 `
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
  --save-to qwen3_7_flash_retail_plus_abcd_phase1
```

`base_plus` 包含原始 114 个任务和 16 个 ABCD 扩展任务，共 130 个；只回归 7 条策略规则时使用 `policy_phase1`；运行全部 137 个任务时使用 `all_plus`。详细设计与命令见 `POLICY_EVALUATION.md`。

## 验证

```powershell
.\.venv\Scripts\python.exe -m pytest tests\test_domains\test_retail_plus -q
.\.venv\Scripts\python.exe -m pytest tests\test_domains\test_retail -q
```
