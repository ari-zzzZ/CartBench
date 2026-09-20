# Retail Plus 风险控制（实验功能）

## 架构

风险控制通过可选 middleware 接入标准 `Orchestrator`，不再维护第二套对话循环：

```text
UserGymEnv / tau2 run experiment
    -> Orchestrator
       -> RetailRiskController
          -> first-request classifier
          -> tool permission and ActionGate
          -> audit events
       -> Agent / Environment / User
```

默认 `tau2 run` 和普通 `tau2 play` 不启用 middleware，原有行为保持不变。

## 风险等级

- `L0`：查询、公开政策、退换规则、商品/订单/物流状态和标准信息回复，只开放读取类操作。
- `L1`：退款、补偿、费用减免及任何状态修改。写操作必须通过原业务前置条件并获得单独的 yes/no 确认；自动给客户的单次及会话累计金额均不得超过 50 美元；地址每次会话最多自动修改一次。
- `L2`：投诉、大额赔偿、明显情绪升级、辱骂、诉讼、监管、人身安全等，主 Agent 不再被调用，由 middleware 直接创建可创建的工单并转人工。

客户第一句话会使用 Agent 相同的模型进行一次独立分类。输出必须能唯一解析为 `L0`、`L1` 或 `L2`。最多调用三次；三次均失败时记录 `risk.classification_failed`，按 `L2` 安全转人工。后续用户和 Agent 可见文本还会接受确定性敏感词检查，风险只升不降。

## 动作控制

`ActionGate` 在工具真正执行前检查：

- 工具及严格参数 Schema；
- 客户认证、数据归属与当前请求范围；
- L0/L1/L2 权限；
- 订单和商品等原领域前置条件；
- 50 美元自动 credit 上限；
- 单会话一次地址修改；
- 与具体参数、客户、请求、状态快照和有效期绑定的二次确认；
- 幂等 receipt，避免重复执行。

首期允许在规则和确认下自动执行 Retail Plus 已有的标准写工具。无法通过规则、超过额度或属于 L2 的请求进入人工处理。

确认协议由 middleware 而不是 Agent 自由文本控制：Agent 首次提出具体写操作后，控制器保存完整调用并向客户显示精确操作；模糊回复会保留 pending action 并再次要求 `yes/no`；明确 `yes` 后，控制器通过标准 Orchestrator 执行已保存的调用，不再让模型重新生成参数。明确 `no`、超时、客户身份/请求/DB 快照变化都会阻止执行。

未通过 Gate、因而没有到达 Environment 的工具提案会带有可信的 `risk_control.executed=false` 元数据。DB、Action 和 Policy evaluator 在回放时统一排除这些提案，所以被拦截的动作既不会修改回放状态，也不会错误满足 Golden Action。

当前可在 L1 自动执行（仍需前置条件和确认）的业务写操作为：取消待处理订单、退货、换货、修改待处理订单商品/支付/地址、修改用户地址/电话、减免费用、提交缺件 claim 和给待处理订单加购。`open_support_case` 与 `transfer_to_human_agents` 属于受控交接流程，不作为普通业务写操作开放。控制器只有在已认证且能把当前请求中的 reference 绑定到该客户时才先建工单；否则直接转人工，避免凭空创建或泄露他人资源。

## 回复边界

旧版本“丢弃 Agent 草稿并用最近工具证据生成固定模板”的逻辑已删除。当前 middleware 不声称实现通用自然语言事实核验：普通 Agent 回复保持原样；只有回复本身触发 L2 敏感信号时才阻止发送并转人工。真正的 claim/evidence verifier 留待后续单独设计。

旧版独立“审核员”入口也已删除：它没有独立身份、权限或真实审批通道，保留只会形成一个无法兑现审批语义的旁路。当前人工介入统一落为审计事件、可选工单和转人工工具调用；真正的审核队列留待接入外部系统时实现。

## `tau2 play`

```powershell
.\.venv\Scripts\tau2.exe play
```

选择 `Play as User`、`retail_plus` 后，可以选择是否启用 L0/L1/L2 风险控制。`play` 会显示 Agent 工具调用、结果和供应商实际返回的 reasoning；它不会主动开启 thinking。

每次 `play` 结束（包括手动退出）都会保存：

```text
data/simulations/play_<domain>_<task>_<timestamp>.json
```

文件包含完整消息轨迹、完成时的 `SimulationRun`（若有）和风险审计状态。风险调试信息可能包含客户数据和模型原始输出，不应提交到公共仓库。

## 离线测试与模型实验

```powershell
.\.venv\Scripts\python.exe scripts/run_risk_eval.py --offline
```

真实模型实验仍使用标准 `Orchestrator`：

```powershell
.\.venv\Scripts\python.exe scripts/run_risk_eval.py `
  --agent-llm MODEL `
  --user-llm MODEL `
  --task-split-name new `
  --num-trials 1 `
  --output data/risk_control/experiment_01
```

输出 `guarded.json` 和 `risk-audits.json`。分类器成本计入 Agent cost，但分类器原始消息不进入面向客户的 benchmark trajectory。

## 生产边界

这是本地评测原型，不是生产审批系统。真实落地仍需：审核员身份和 RBAC、真实工单队列、外部 API 幂等键与事务、数据加密和留存策略、经过标注数据校准的分类器，以及后续独立的回复证据核验器。
