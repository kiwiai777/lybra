---
name: audit-independent-evidence
description: 只读独立审计程序:独立取证、逐项 PASS/FAIL、F-* 登记分级、绝不热修。审计类任务卡使用。
---

# audit-independent-evidence — 独立取证审计程序

**身份前提**:审计者与被审执行者是**不同的 canonical 身份**;不复用其 session、token、
工作目录状态。

## 纪律

1. **只读**:不 edit/write 被审系统任何文件、不 commit/push、不改配置。唯一允许的写 =
   卡指定的审计报告出口。
2. **独立取证**:被审方的自述(return/报告)只是**线索,不是证据**——
   - 声称"测试全绿" → 自己重跑,看原文;
   - 声称"只改了 X" → 自己看 diff/git log;
   - 声称"状态为 Y" → 自己查盘上真相。
3. **逐项核验**:对被审卡的每条验收断言 → PASS/FAIL + 证据(file:line / 命令输出原文)。
4. **发现问题只登记,绝不动手修**:编号 `F-<片号>-<序号>`,分级 P0(阻断)/ P1(须修)/
   P2(改进),各附证据。**审计员热修 = 审计真空,是回路头号禁忌。**
5. **结论**:PASS / PASS_WITH_NOTES / FAIL + 一段理由(引用 F-* 清单)。
6. **汇报**:按 write-return 结构(含实际模型/token 自报)。

## 红线

- 被拦截/证据取不到 → block-and-report,不绕过、不推断补齐。
- 报告中不得出现任何明文凭据。
