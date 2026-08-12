---
name: finalize-slice
description: 交付收口(finalize)标准程序:基线校验(含断点续跑)→工作树对账→测试前置→精确 pathspec commit→push→汇报。仅当任务卡明确授权 finalize/commit/push 时使用。
---

# finalize-slice — 交付收口标准程序

**前提**:任务卡**明确授权** commit+push,并给出:基线 hash(或"以当前 HEAD 为基线")、
scope 依据(实现记录/micro-plan 章节指针)、commit message(或其要素)。缺任一 → 走
block-and-report,不开工。

## 步骤

1. **基线校验(含断点续跑)**:核对仓库 HEAD == 卡内基线 hash。若卡含环境恢复步骤
   (如 .git 移植)且现场已具备目标状态 → **校验通过即跳过恢复步**,不重做;校验不符 →
   停,block-and-report。
2. **对账(核心纪律)**:`git status --short` **逐文件**三分类:
   - 能在卡指向的实现记录/scope 里找到出处 → 进 commit pathspec;
   - 卡内明确排除(后续片草稿、垃圾文件、环境残留)→ 不 commit,列入汇报;
   - 来历不明 / 拿不准 → **停,block-and-report,不猜**。
   若 diff 中出现大量与本片无关的历史内容 → 基线不对,停。
3. **测试前置**:按卡/仓惯例跑测试。卡标注的必跑 lane **红即停、不 commit**;环境缺依赖
   跑不了的 lane → **如实记录哪跑了哪没跑,绝不伪造**。
4. **commit**:仅步骤 2 对账通过的文件,**逐个 `git add <path>`,禁 `add -A` / `add .`**;
   author 归因按卡指定(执行者身份,非人类);message 按卡。
5. **push**:仅推卡指定的远端与分支;不建分支、不改历史、不 force。
6. **汇报**:按 write-return 结构,必含:已 commit 清单+hash、排除清单+理由、各测试 lane
   结果原文。

## 红线

- 对账不过 → 不 commit;必跑测试红 → 不 commit;任何不可判定 → block-and-report。
- pathspec 之外的文件一个都不许带上车。
