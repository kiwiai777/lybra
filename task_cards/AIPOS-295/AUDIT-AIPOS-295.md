---
task_id: AIPOS-295R
title: '审计 AIPOS-295: watch 健康监护五分钟体检心跳+有界自愈'
project: lybra
task_mode: audit
task_class: simple
priority: high
status: pending
assigned_to: audit.lybra.kiwiai-dev
agent_instance: audit.lybra.kiwiai-dev
reviewed_task_id: AIPOS-295
reviewed_task_path: /home/kiwi/ai-project-os/2_projects/lybra/5_tasks/queue/claimed/aipos-295.md
created_at: '2026-08-02T04:35:00Z'
created_by: exec.lybra.kiwiai-dev
audit: self_generated
---

# AIPOS-295R — 审计 AIPOS-295

## 审计准绳（原执行卡断言 S1-S5）

原卡：`/home/kiwi/ai-project-os/2_projects/lybra/5_tasks/queue/claimed/aipos-295.md`

### S1: 体检心跳

- `watch --health <secs>` (默认300) + 观察面参数（--proc-pattern / --pid-file / --session-dirs / --worktree-path / --run-log）
- 每周期发 `kind:health` 事件 {proc_alive, cpu_delta, new_session_files, worktree_changes, silent_secs}
- 进程树探测量真pi子树，勿量timeout壳

### S2: 死寂判据

- 进程消失 OR (cpu_delta≈0 且 new_session_files=0 且 worktree_changes=0 连续M周期)
- 发 `kind:unhealthy` 事件（判据阈值可配，默认取293实战值）

### S3: 有界自愈

- `lybra agent supervise --spawn-cmd <模板>`（或 watch --respawn 组合）
- unhealthy → 杀真进程树（含壳）→ 按模板重拉 **1次**，发 `kind:respawn` 事件
- 第2次失败 → 绝不再拉：落 ESCALATE 文件（含三派式病史+建议项）+ `kind:escalate` 事件，等待授权

### S4: 红线断言

- 全逻辑观察者/agent侧；gate零新接口
- respawn模板必含timeout包裹
- 探测量的是pi子进程树（测试断言：对timeout壳的0-CPU不误判）

### S5: 测试+文档

- health/unhealthy/respawn/escalate 四事件时序 fixture
- `--stream` 既有语义零回归
- 文档：监护配方一节（顾问/用户抄用）

## 审计检查项（独立取证）

### C1: S1体检心跳实现完整性 ✅预期PASS

**取证项**：
1. 读 `tools/aipos_cli/agent_watch_fs.py`，确认：
   - 添加 `--health` 参数（type=float, 与 --stream 配合检查）
   - 实现健康观察面5指标采集函数（`_find_pi_processes` / `_get_process_cpu_time` / `_count_new_session_files` / `_count_worktree_changes`）
   - 主循环中按 `health_interval` 周期发出 `kind:health` 事件
2. 读 `tools/aipos_cli/aipos_cli.py`，确认：
   - 添加 `--health` / `--pid-file` / `--proc-pattern` / `--session-dirs` / `--worktree-path` / `--unhealthy-cycles` 参数
3. 检查进程树探测逻辑：
   - `_find_pi_processes` 过滤 cmdline 含 'timeout' / 'bash' 的进程
   - 仅返回真实 pi 子进程 PID 列表

**判定标准**：
- PASS: 5指标函数完整，health事件结构符合S1，进程树过滤逻辑正确
- FAIL: 缺指标 / 未过滤timeout壳 / health事件缺字段

### C2: S2死寂判据正确性 ✅预期PASS

**取证项**：
1. 读 `agent_watch_fs.py` 中 unhealthy 检测逻辑：
   - 检查两条触发路径：`not proc_alive` OR `(cpu_delta < 0.01 and new_session_files == 0 and worktree_changes == 0)`
   - 检查连续周期计数（`silent_cycles >= unhealthy_threshold`）
2. 运行 `tests/test_unhealthy_integration.py`：
   - 确认 unhealthy 在连续2周期静默后正确触发
   - 确认 `kind:unhealthy` 事件包含 reason / silent_cycles / 5指标

**判定标准**：
- PASS: 判据逻辑符合S2（进程消失 OR 三维静默），连续周期阈值可配
- FAIL: 判据缺维度 / 单周期即触发 / 事件缺字段

### C3: S3有界自愈实现完整性 ✅预期PASS

**取证项**：
1. 读 `tools/aipos_cli/agent_supervise.py`，确认：
   - `run_supervise` 函数实现 spawn → watch → unhealthy处理 → respawn/escalate 流程
   - `max_attempts = 2`（初始 + 1次重拉）
   - 第1次 unhealthy → `kill_process_tree` + 发 `kind:respawn` + continue
   - 第2次 unhealthy → 发 `kind:escalate` + `write_escalate_file` + return EXIT_ESCALATE (75)
2. 读 ESCALATE 文件生成逻辑（`write_escalate_file`）：
   - 包含 failure_history（两次失败诊断）
   - 包含建议项（换模型/查路由/人工介入）
   - 说明 exit 75 防止 systemd 自动重启
3. 读 `aipos_cli.py`，确认：
   - 添加 `agent supervise` 子命令
   - dispatch 到 `agent_supervise.main`

**判定标准**：
- PASS: 有界重启逻辑正确（1次），ESCALATE文件结构完整，exit 75
- FAIL: 无限重启 / ESCALATE缺病史 / 未exit 75

### C4: S4红线合规性 ✅预期PASS

**取证项**：
1. 检查 gate 零涉入：
   - `git diff` 确认 `confirm_client.py` / `mcp_server/` / gate相关代码零变更
   - 搜索 `agent_watch_fs.py` / `agent_supervise.py` 无 gate client 导入
2. 检查 timeout 包装要求：
   - 读 `agent_supervise.py` 参数说明，确认文档要求 `--spawn-cmd` 必须含 timeout
   - 读 ESCALATE 文件模板，确认提示包含 timeout
3. 检查进程树探测：
   - 读 `_find_pi_processes` 实现，确认过滤 'timeout' / 'bash' cmdline
   - 运行 `tests/test_health_supervise.py` 中 T6 测试（test_find_pi_processes_*）

**判定标准**：
- PASS: gate零变更，timeout包装有文档要求，进程树过滤正确
- FAIL: gate有改动 / 未要求timeout / 未过滤timeout壳

### C5: S5测试+文档完整性 ✅预期PASS

**取证项**：
1. 运行测试套件：
   - `tests/test_health_supervise.py`: 确认9项测试全过（T1-T8覆盖S1-S5）
   - `tests/test_unhealthy_integration.py`: 确认端到端unhealthy检测通过
   - `tests/test_agent_watch_fs.py`: 确认61项既有测试零回归
2. 检查四事件时序覆盖：
   - health: `test_health_event_structure`
   - unhealthy: `test_unhealthy_integration.py`
   - respawn: supervise 逻辑（代码review）
   - escalate: `test_escalate_file_creation`
3. 读 `docs/health-monitoring.md`：
   - 确认包含使用场景（监控/自动重启）
   - 确认包含参数说明表格
   - 确认包含 systemd 集成示例
   - 确认包含故障诊断 Q&A

**判定标准**：
- PASS: 测试全过+零回归，四事件有测试覆盖，文档完整可用
- FAIL: 测试挂 / 零回归失败 / 文档缺场景或参数说明

## 审计方法

按 `skills/audit-independent-evidence` 独立只读取证：
1. 读原执行卡（`aipos-295.md`）获取断言S1-S5
2. 读实现代码（`agent_watch_fs.py` / `agent_supervise.py` / `aipos_cli.py`）
3. 运行测试套件（`test_health_supervise.py` / `test_unhealthy_integration.py` / `test_agent_watch_fs.py`）
4. 检查文档（`docs/health-monitoring.md`）
5. 检查 git diff 确认 gate 零变更
6. 逐项对标 S1-S5，给出 PASS/FAIL + 证据

## 报告出口

唯一化到：`~/projects/lybra/task_cards/AIPOS-295/AUDIT-REPORT-AIPOS-295R.md`

## 裁决格式

每项检查：
```
### C<N>: <检查项名称> [PASS|FAIL]

证据：
- <取证结果1>
- <取证结果2>
- ...

判定：<符合/不符合断言的理由>
```

最终裁决：
```
## 总裁决

- S1: [PASS|FAIL]
- S2: [PASS|FAIL]
- S3: [PASS|FAIL]
- S4: [PASS|FAIL]
- S5: [PASS|FAIL]

整体裁决: [PASS|FAIL]
结论: <一句话总结>
```
