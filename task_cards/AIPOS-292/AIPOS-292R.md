---
task_id: AIPOS-292R
title: '审计 AIPOS-292: lybra auditor loop 动词产品化'
project: lybra
assigned_to: audit.lybra.kiwiai-dev
agent_instance: audit.lybra.kiwiai-dev
context_bundle: audit.lybra.kiwiai-dev
task_mode: audit
task_class: simple
priority: high
status: pending
created_by: exec.lybra.kiwiai-dev
needs_owner: false
reviewed_task_id: AIPOS-292
reviewed_task_path: /home/kiwi/ai-project-os/2_projects/lybra/5_tasks/queue/claimed/aipos-292.md
audit_scope: independent_evidence
output_target: task_cards/AIPOS-292/
artifact_policy: formal_write
---

# AIPOS-292R — 审计 AIPOS-292 执行交付

## 审计准绳 (原执行卡验收断言)

**S1**: 新子命令 `lybra auditor loop --workspace-root <R> [--policy <id>] [--runtime-cmd <模板>] [--interval N]`
- 行为对齐现役脚本契约 (~/bin/lybra-dev-auditor-daemon)
- watch 盯派生审计卡 → PreAuthorized 信封 dry_run 领卡 (一发式) → 按 runtime-cmd 模板拉起审计运行时
- 领卡失败/信封尽 → 写 BLOCK 文件 + exit 75 (systemd RestartPreventExitStatus 配套)

**S2**: systemd 模板 `config/deployment/lybra-auditor.example.service`
- ExecStart=lybra auditor loop ...
- 含防空转注释 (RestartPreventExitStatus=75)

**S3**: 本机 dogfood 迁移
- lybra-dev 的 auditor 单元改指产品命令, 重启服役
- 旧脚本改名 .retired 留档
- 真跑证据 (journal 片段 / 一次真实接单) 入 RETURN

**S4**: 测试
- 领卡决策逻辑单测 (mock gate 读面: 放行/信封外/BLOCK 三径)
- CLI 参数面
- 零回归
- **红线断言**: 循环体无任何 gate 写面调用 (除 claim confirm 动词)

## 审计检查项

### 1. S1 产品命令实现 (auditor_loop.py)
- [ ] 文件存在: `tools/aipos_cli/auditor_loop.py`
- [ ] CLI 集成: `tools/aipos_cli/aipos_cli.py` 有 auditor 子命令
- [ ] 参数完整: --workspace-root (必填), --policy, --runtime-cmd, --interval, --timeout
- [ ] 默认值正确: gate=127.0.0.1:7118, policy=pol_lybra_audit_1, interval=20, timeout=1800
- [ ] 行为对齐: find_pending_audit_cards → claim_preauthorized → launch_auditor_runtime → BLOCK on envelope exhaustion

### 2. S2 systemd 模板
- [ ] 文件存在: `config/deployment/lybra-auditor.example.service`
- [ ] ExecStart 指向产品命令 (lybra auditor loop)
- [ ] RestartPreventExitStatus=75 配置存在
- [ ] 含防空转注释

### 3. S3 本机 dogfood 迁移
- [ ] 旧脚本已备份: ~/bin/lybra-dev-auditor-daemon.retired 存在
- [ ] systemd 单元更新: ~/.config/systemd/user/lybra-dev-auditor.service 指向产品命令
- [ ] 服务运行中: systemctl --user status lybra-dev-auditor.service = active
- [ ] journal 有真跑日志 (auditor-loop 启动日志, watch 等待日志)

### 4. S4 测试覆盖
- [ ] 单测文件存在: `tools/aipos_cli/tests/test_auditor_loop.py`
- [ ] 领卡决策逻辑: mock gate 三径 (auto_released true/false, GateError)
- [ ] pending 扫描: 按 task_mode=audit + status=pending + agent_instance 过滤
- [ ] BLOCK 退出: 未自动放行 → exit 75
- [ ] 红线断言: 无 gate 写面模块导入, 只调用 lybra_queue_claim_dry_run
- [ ] CLI 参数: 必填参数校验, 默认值对齐
- [ ] 所有单测通过 (pytest)

### 5. 红线遵守
- [ ] 循环 = agent 侧泵 (FS watch), gate 零推送
- [ ] auditor_loop.py 源码: 无 queue_mutation / record_writer / draft_writer 等写面导入
- [ ] 车道正确: tools/aipos_cli/ + config/deployment/ (未写治理仓, 未写 kiwiai-pi)

### 6. 零回归
- [ ] pytest tools/aipos_cli/tests/test_auditor_loop.py -v 全过
- [ ] pytest tools/aipos_cli/tests/test_agent_watch_fs.py -v 全过 (FS 泵不受影响)
- [ ] pytest tools/aipos_cli/tests/test_agent_connector.py -v 全过 (gate 候选⑤不受影响)

## 审计输出
按 audit-independent-evidence skill 独立只读取证, 逐项 PASS/FAIL + 证据, 写到:
`/home/kiwi/projects/lybra/task_cards/AIPOS-292/AUDIT-REPORT-AIPOS-292R.md`

审完按 write-return 流程如实记录裁决与自报模型/token。
