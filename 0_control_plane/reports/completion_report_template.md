# Completion Report Template

任务完成后，本地（non_24h）Agent **必须**填写 Resume 章节。

---

# Completion Report

Task ID:
Executor:
Role Instance:
Environment:
Availability:

## Summary
任务执行情况综述。

## Completed Items
- [ ] 事项 1
- [ ] 事项 2

## Changed Files
- `path/to/file`

## Tests / Verification
验证通过的具体证据。

## Risks / Open Issues
遗留风险或未解决的问题。

## Suggested Memory Capture
（可选）附带的 Memory Capture 引用。

## Next Step Recommendation
建议的下一步操作。

## Resume / Continue

是否需要继续：
下次继续命令：
下次继续点：
---

## 使用规则
1. **Resume 强制性**：对于本地非 24h Agent，此章节是实现任务接力的关键。
2. **追溯价值**：即使任务已完成，Resume 命令也可用于快速找回历史执行上下文。
3. **归档路径**：完成回报应提交至 `4_inbox/<agent_id>/`。

## Resume Policy
Local non_24h agents must fill Resume / Continue. Cloud 24h agents may fill Resume / Continue, but it is not mandatory. For long-running cloud tasks, a next checkpoint is still recommended.
