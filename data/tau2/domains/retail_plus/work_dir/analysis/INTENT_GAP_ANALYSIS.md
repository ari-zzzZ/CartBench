# Retail Plus 与 ABCD 意图差距分析（第一步）

## 1. 结论

扩展方案可行，但不能采用“发现缺失意图后，只从 ABCD 随机抽取对话并追加到 `tasks.json`”的方式。一个新的可评测意图必须形成完整的垂直切片：

```text
数据库状态 → 业务规则 → 可执行工具 → 用户 Scenario → Golden actions → 评价标准 → 工具与任务测试
```

ABCD 对话可以提供真实的用户表达、信息披露方式、冲突和意图素材，但当前 Retail 的数据、政策和工具仍然是判断一个意图是否已覆盖的依据。

本轮只分析 `data/tau2/domains/retail_plus`，没有修改原始 `retail`。

## 2. 标签口径

对 ABCD 意图采用三个等级：

- `covered`：当前 Retail 能执行并客观评价该意图的核心用户结果。
- `partial`：存在相邻能力，但 ABCD 的关键状态、规则、操作或结果至少缺少一项。
- `missing`：当前环境不能执行并客观评价该意图的核心结果。

只在政策文本里提到、只能口头回答、或者只有相似工具，都不自动算作完整覆盖。

114 个 Retail Task 的标签分为：

- `operation_intents`：由 golden tool actions 确定，可信度最高；
- `information_intents`：由用户 `reason_for_call` 的保守规则识别；
- `interaction_traits`：用户改变主意、隐瞒信息、情绪化等交互难点；
- `requested_but_unsupported_intents`：Scenario 中提出，但当前工具只能拒绝或执行 fallback 的请求。

完整逐 Task 标签在 `analysis/intent_labels.json`，生成逻辑在 `scripts/analyze_retail_intents.py`。

## 3. 现有 114 个 Task 的覆盖分布

### 3.1 可执行操作意图

| Retail 操作意图 | Task 数 | 说明 |
|---|---:|---|
| 修改 pending 订单商品 | 35 | 同一产品下替换 variant |
| delivered 商品退货 | 31 | 发起 return requested |
| delivered 商品换货 | 29 | 同一产品下替换 variant |
| 修改 pending 订单地址 | 20 | 修改订单收货地址 |
| 取消 pending 订单 | 18 | 只能整单取消 |
| 修改用户默认地址 | 10 | 修改账户默认地址 |
| 转人工 | 4 | 当前能力之外的请求 |
| 修改 pending 订单支付方式 | 1 | 覆盖明显偏少 |

同一 Task 可以包含多个操作，所以以上数量之和大于 114。

### 3.2 信息查询意图

| 查询意图 | 识别到的 Task 数 |
|---|---:|
| 价格、退款额或差价计算 | 19 |
| 配送 tracking 或到达时间 | 11 |
| 商品目录、选项或库存 | 10 |
| 订单商品或数量 | 9 |
| 订单状态 | 6 |
| 订单支付方式 | 3 |
| Gift card 余额 | 2 |

这些是保守识别结果，适合用于覆盖统计；在正式生成新数据前仍应人工复核。

### 3.3 已存在但无法完成的用户请求

| 当前不支持的请求 | Task 数 |
|---|---:|
| 只取消订单中的一行商品 | 4 |
| 拆分支付 | 3 |
| 跨产品类型替换 | 2 |
| 向已有订单增加商品 | 2 |
| 撤销已经取消的订单 | 1 |

这说明当前 114 个 Task 已经在对话中触及部分缺口，但通常只评价拒绝、转人工或 fallback，并不代表该业务能力已实现。

### 3.4 交互难度

| 交互特征 | Task 数 |
|---|---:|
| 存在 fallback 偏好 | 32 |
| 隐瞒、忘记或混淆信息 | 24 |
| 改变主意或条件确认 | 20 |
| 情绪化、坚持或施压 | 15 |
| 题外干扰 | 2 |

现有 Retail 的交互复杂度已经较强，主要短板不是对话花样，而是业务意图宽度和可执行后台状态。

## 4. ABCD 55 个意图对比结果

严格按“可以执行并评价核心结果”的口径：

| 状态 | 数量 |
|---|---:|
| 完整覆盖 `covered` | 2 |
| 部分覆盖 `partial` | 18 |
| 缺失 `missing` | 35 |
| 合计 | 55 |

### 4.1 完整覆盖的 2 个意图

- `order_issue.status_payment_method`：能查询支付历史，也能修改 pending 订单支付方式；
- `manage_account.manage_change_address`：能查询并修改用户默认地址。

### 4.2 部分覆盖的 18 个意图

- Product Defect：`refund_initiate`、`return_stain`、`return_color`、`return_size`；
- Order Issue：`status_delivery_time`、`status_quantity`、`manage_cancel`；
- Manage Account：`manage_payment_method`；
- Purchase Dispute：`out_of_stock_general`、`out_of_stock_one_item`；
- Shipping Issue：`status`、`manage`；
- Single-Item Query：`boots`、`shirt`、`jeans`、`jacket`；
- Storewide Query：`pricing`、`policy`。

其中最容易被误判的是退货：当前 Retail 已经有 31 个退货 Task，因此不缺“通用退货”。它缺的是退货原因、不可退品类、资格时间窗、退款生命周期等更细的业务状态和规则。

### 4.3 完全缺失的 35 个意图

#### Product Defect

- `refund_update`
- `refund_status`

#### Order Issue

- `status_mystery_fee`
- `manage_upgrade`
- `manage_downgrade`
- `manage_create`

#### Account Access

- `recover_username`
- `recover_password`
- `reset_2fa`

#### Troubleshoot Site

- `credit_card`
- `shopping_cart`
- `search_results`
- `slow_speed`

#### Manage Account

- `status_service_added`
- `status_service_removed`
- `status_shipping_question`
- `status_credit_missing`
- `manage_change_name`
- `manage_change_phone`

#### Purchase Dispute

- `bad_price_competitor`
- `bad_price_yesterday`
- `promo_code_invalid`
- `promo_code_out_of_date`
- `mistimed_billing_already_returned`
- `mistimed_billing_never_bought`

#### Shipping Issue

- `missing`
- `cost`

#### Subscription Inquiry

- `status_active`
- `status_due_amount`
- `status_due_date`
- `manage_pay_bill`
- `manage_extension`
- `manage_dispute_bill`

#### Storewide Query

- `membership`
- `timing`

每个意图的 ABCD 样本量、Retail 证据 Task、具体缺失状态和建议阶段，见 `abcd_intent_coverage.json`。

## 5. 对原扩展计划的可行性修正

### 5.1 不应随机抽样

ABCD 中同一意图的对话质量和分支差异很大。应使用带固定 seed 的分层筛选，而不是直接随机抽 1～3 条。候选对话至少需要满足：

- 场景字段完整；
- 用户目标单一或组合边界清楚；
- 不依赖无法实现的 ABCD 专属按钮；
- 能绑定到 Retail Plus 的数据库状态；
- 最终结果能通过 DB、Action 或 Communicate 客观评价；
- 不与新 Retail Plus policy 冲突。

抽样过程需要保存 `source_split + convo_id + selection_reason`，确保可复现。

### 5.2 新意图必须先实现后台能力

正确顺序是：

```text
选定意图
→ 定义业务状态和规则
→ 扩展 data_model/db
→ 实现 tools 与防重复约束
→ 编写工具单元测试
→ 从 ABCD 筛选并改写 Scenario
→ 生成 golden actions / assertions
→ 加入 tasks.json
```

如果先写 Scenario，通常会遇到没有字段、没有工具、无法计算最终状态的问题。

### 5.3 你列出的部分规则当前已经存在

- “发货后不能直接取消”：当前 `cancel_pending_order` 已拒绝非 pending 订单；
- “修改地址前必须二次确认”：当前 policy 要求所有数据库写操作先说明详情并取得明确确认；
- “不得透露其他用户订单信息”：当前 policy 要求认证、一次会话只服务一个用户并拒绝其他用户请求。

但这些目前主要依赖 Agent 遵守 policy。后续实现 Policy Violation Rate 时，应把关键规则下沉到可观察事件或工具前置条件，不能只靠自然语言提示。

以下属于真正的新规则/状态：

- 金额阈值转人工；
- 食品、定制品等不可无理由退货；
- Voucher 不可折现；
- 同一订单或退款 case 不得重复退款。

### 5.4 `retail_plus` 尚未成为可运行 Domain

目前只有：

```text
data/tau2/domains/retail_plus/
```

CLI 和 registry 尚未注册 `retail_plus`。以后还需要增加相应源码 Domain 或让新 Domain 显式复用 Retail 实现，并在 registry 中注册，否则：

```text
tau2 run --domain retail_plus
```

不会被识别。第一步标签分析不需要注册，因此本轮没有修改源码和 registry。

## 6. 第一批建议实现的意图

不建议一次实现全部 35 个缺失意图。第一批可选择 8 个，优先复用你计划增加的规则：

| 优先级 | ABCD 意图 | 为什么适合第一批 |
|---:|---|---|
| 1 | `refund_status` | 能建立退款生命周期，也是重复退款规则的基础 |
| 2 | `mistimed_billing_already_returned` | 覆盖退款延迟、重复退款和人工升级 |
| 3 | `promo_code_invalid` | 可建立 Voucher 验证与不可折现规则 |
| 4 | `promo_code_out_of_date` | 与 Voucher 生命周期共用数据和工具 |
| 5 | `status_mystery_fee` | 可建立费用明细、金额阈值和人工升级 |
| 6 | `shipping_issue.missing` | 常见电商场景，适合 case/补发/转人工流程 |
| 7 | `manage_create` | 补齐“向 pending 订单增加商品”的明显能力缺口 |
| 8 | `manage_change_phone` | 数据结构和工具相对简单，适合验证扩展流程 |

建议每个意图先做 2 个 Task，共 16 个：

- 1 个正常成功路径；
- 1 个拒绝、边界或转人工路径。

完成这 8 个垂直切片后，再扩展配送升级/降级、价格争议、账户名称、保存支付方式等意图。

## 7. 关于后续评价指标的提前校正

### Task Success

将 `DB × COMMUNICATE × ACTION` 全部作为硬门槛在工程上可实现，但要允许“多条等价合法路径”。否则 golden actions 只写一种路径，会把正确但不同的工具调用误判为失败。

### Tool Call Accuracy

需要区分：

- 必需写操作；
- 必需认证/查询；
- 可选的合理查询；
- 重复查询；
- 无关查询；
- 非法或产生错误副作用的写调用。

因此不应仅使用工具名集合做准确率，后续需要参数级匹配、调用次数和允许的替代动作集合。

### Pass@k / Pass^k

当前项目输出的是 `Pass^k`，含义是随机选择的 k 次全部成功；常见 `pass@k` 是 k 次中至少成功一次。后续必须明确保留哪一种，或者同时输出两种，不能只改显示名称。

### Cost per Successful Resolution

“总费用 ÷ 全部 Task/Trial 数”实际是平均每次尝试成本：

```text
Average Cost per Attempt = Total Cost / Attempts
```

真正的成功解决成本是：

```text
Cost per Successful Resolution = Total Cost / Successful Resolutions
```

失败任务的费用仍在分子中，但分母只能是成功数量。建议后续两个指标同时输出，避免命名和公式不一致。

## 8. 本轮产物

- `intent_labels.json`：114 个 Retail Plus Task 的逐项标签；
- `abcd_intent_coverage.json`：ABCD 55 意图逐项覆盖/缺口表；
- `scripts/analyze_retail_intents.py`：可重复运行的标签生成脚本；
- 本文档：分析方法、结论和下一阶段建议。



选择缺失意图
→ 定义数据库状态
→ 定义业务规则
→ 实现工具和前置条件
→ 编写工具测试
→ 从 ABCD 筛选 Scenario
→ 绑定数据库实体
→ 生成 golden actions
→ 定义评价标准
→ 加入 tasks.json
