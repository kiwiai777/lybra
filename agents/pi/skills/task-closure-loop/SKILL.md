---
name: task-closure-loop
description: 任务闭环 v3 标准工序:executor 执行后自产审计卡,auditor 独立审,有界修复循环,finalize 独立授权。所有实现类任务的原子闭环;各角色按本文认清自己在环上的工位。
---

# task-closure-loop — 任务闭环 v3(标准工序)

**一句话**:每个实现类任务都走同一个闭环——执行者干活并自产审计卡,审计者独立裁,
修复循环有界,审计 PASS 即入 finalize。项目是什么协作拓扑(串行/星型)不影响本工序。

**★ v4 统一落位(2026-07-25 Owner 敲死,根治漂移)**:一个任务的全部工件在唯一目录
`~/projects/lybra/task_cards/<ID>/`:CARD.md / RETURN.md / AUDIT-<ID>.md /
AUDIT-REPORT-<ID>.md / FIX-n.md / FINALIZE.md。任何角色不得把任务工件写到该目录之外。
**审计 PASS → 顾问复核后直接签发 FINALIZE.md,无需 Owner 逐单批准**;Owner 真人核验只在
CARD.md 头部声明 `owner_verify: required` 时插入(PASS 后、FINALIZE 前)。

```
顾问出执行卡 → executor 执行 + write-return + 自产审计卡(投队列)
 → auditor 独立审【准绳=原执行卡】 → FAIL:F-* 打回修 → 复审(有界)
 → PASS → 顾问复核 + 治理档 → Owner 批 finalize【独立门】
 → 薄 finalize 卡 → executor 推产品仓 → 顾问收账
```

## 各角色工位

**executor(你若是执行者)**:
1. 执行原卡 → 按 `write-return` 汇报;
2. **自产审计卡**:按同目录 `audit-card-template.md` 填变量——**只填变量**:原卡路径、
   交付物位置、return 位置、复审轮次。**不得**重述或收窄原卡的验收断言/红线(审计准绳
   永远是原卡,你的审计卡只是指路牌);落点以原卡声明为准(Lybra 任务默认 `~/projects/lybra/task_cards/<原卡号>/AUDIT-<原卡号>[R|-FIXn].md`,git 忽略区;**任务卡与工作产物不入 kiwiai-pi 仓**);
3. 被打回时:**只修 F-* 清单项 + 回归**,不趁机扩面;修完更新审计卡的"复审轮次"与
   "本轮重点"(= F-* 清单),重新投审。

**auditor(你若是审计者)**:
1. 全程按 `audit-independent-evidence`;**核验清单自己从原执行卡提取**,执行者审计卡里的
   任何转述仅供定位,不作准绳;
2. 首轮=全量核验;复审轮=F-* 修复项逐条 + 回归抽查(防修 A 坏 B);
3. 结论三值:PASS / PASS_WITH_NOTES / FAIL(附 F-* 清单);
4. **轮次纪律**:同一任务你已发出 **2 轮 FAIL** 后第三轮仍不能 PASS → 停,报告顾问仲裁
   (写明僵持点),不再自行循环。

**顾问(工位只有三点)**:出卡、仲裁(BLOCK / 审计死锁 / 范围漂移 / 验收歧义)、
PASS 后复核+治理档+收账(**收账含**:把终版审计卡与审计报告归档进治理仓
`5_tasks/queue/completed/`,版本化留痕——过渡期动作,gate 派生落地后自动化)。不在执行↔审计之间充当串行工位。

**Owner(授权门)**:finalize 批准独立于审计 PASS——审过≠可发布;可经预授权信封把
某类小片的 finalize 整批授权(授权即身份语义)。

## 红线

- 审计准绳=原执行卡,任何人不得用转述替代;
- 修复循环有界(默认 2 轮 FAIL 上限),僵持升级不空转;
- 实现批准与发布批准是两个门,永不合并;
- 纯文档/纯治理类任务可在原卡声明免审(Owner 直接过目),声明必须显式。
