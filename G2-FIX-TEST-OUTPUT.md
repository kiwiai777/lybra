# G2 红线修复测试输出

## 问题描述
queue_withdraw 的 dry_run 在 WARN(非阻塞)下不发 dry_run_token
→ legacy 卡永远清不掉，违反 G2 "WARN 永不吞 token" 原则

## 修复内容

### 1. 统一两阶段动词语义
- withdraw/amend 对齐 verbs.schema 契约 (phases: ["dry_run", "confirm"])
- 非阻塞 WARN 必发 token (execute_allowed=True)

### 2. 修改文件
- tools/aipos_cli/board_adapter.py (3处修改)
  * withdraw_task/amend_task 调用 _attach_controlled_execute_metadata
  * _attach_controlled_execute_metadata 白名单增 queue_withdraw/queue_amend
  * execute_dry_run 增 withdraw/amend revalidation+confirm 分支
- tools/aipos_cli/controlled_execute.py
  * SUPPORTED_OPERATIONS 增 queue_withdraw/queue_amend

### 3. 活测证明 (AIPOS-263FZ)

```
=== Step 1: Dry-Run ===
verdict: WARN
dry_run_token: dryrun_93d049d8caf942918e708cf07fe18017
warnings count: 6

=== Step 2: Confirm (Execute) ===
ok: True
verdict: PASS
moved: True
target_path: 5_tasks/queue/withdrawn/aipos-263fz.md

✅ SUCCESS: G2 red-line fixed!
   WARN verdict → dry_run_token emitted → confirm succeeded
   Legacy card AIPOS-263FZ moved to withdrawn/
```

### 4. 文件验证

```bash
$ ls -la ~/ai-project-os/2_projects/lybra/5_tasks/queue/withdrawn/aipos-263fz.md
-rw-rw-r-- 1 kiwi kiwi 1001 Aug 13 14:06 .../withdrawn/aipos-263fz.md
```

## Commit 信息

```
commit 74c5932
fix(G2): withdraw/amend WARN必发token-对齐verbs.schema两阶段契约

G2 红线现行犯修复: queue_withdraw 的 dry_run 在 WARN(非阻塞)下不发 dry_run_token
→ legacy 卡永远清不掉, 违反 G2 "WARN 永不吞 token" 原则
```

## 横扫所有两阶段动词

已修复的两阶段动词:
- ✅ queue_claim (已有)
- ✅ queue_return (已有)
- ✅ queue_withdraw (本次修复)
- ✅ queue_amend (本次修复)

verbs.schema 声明的两阶段动词全部对齐完成。

## 下一步

顾问可批量清理 8 张陈账卡，然后 /lybra on 1 重跑。

---
测试完成时间: 2026-08-13 14:06 UTC
修复者: lybra-executor
