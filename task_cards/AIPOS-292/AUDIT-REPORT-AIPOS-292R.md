# AIPOS-292R 审计报告

**审计员**: audit.lybra.kiwiai-dev  
**审计时间**: 2026-08-01T04:52:00Z  
**被审任务**: AIPOS-292 (审计守护壳产品化:lybra auditor loop 动词)  
**审计准绳**: `/home/kiwi/ai-project-os/2_projects/lybra/5_tasks/queue/claimed/aipos-292.md`  
**执行者 RETURN**: `return_AIPOS-292_20260801_045140_exec-lybra-kiwiai-dev.md`

---

## 审计结论

**PASS** — 四项验收断言全部通过，红线遵守，本机 dogfood 迁移真实服役中。

---

## 逐项取证

### S1: `lybra auditor loop` 子命令 — **PASS**

**取证路径**:
- 独立读取源码: `/home/kiwi/projects/lybra/tools/aipos_cli/auditor_loop.py` (246 行)
- 独立读取集成: `/home/kiwi/projects/lybra/tools/aipos_cli/aipos_cli.py` (auditor 子命令 parser + dispatch, 行 1073-1093, 1965-1983)

**行为契约对齐验证**:
1. ✅ **watch 盯派生审计卡**: `find_pending_audit_cards()` 扫描 `5_tasks/queue/pending/*.md`, 按 `task_mode=audit + status=pending + agent_instance=<实例>` 过滤 (源码 L42-67)
2. ✅ **PreAuthorized 信封 dry_run 领卡 (一发式)**: `claim_preauthorized()` 调用 `lybra_queue_claim_dry_run` 工具, 检查 `preauthorized_release=true + autonomy_mode=PreAuthorized` (源码 L70-99)
3. ✅ **按 runtime-cmd 模板拉起审计运行时**: `launch_auditor_runtime()` 展开 `{kickoff}` 变量, `subprocess.run(shell=True)` 阻塞执行 (源码 L126-157), 默认 `pi --model anthropic/claude-3-5-sonnet-20241022 --prompt '{kickoff}'` (源码 L227, aipos_cli.py L1082)
4. ✅ **领卡失败/信封尽 → BLOCK 文件 + exit 75**: `process_pending_audits()` 检测 `not auto_released` → `write_block_file()` 落盘 `BLOCK-N.md` → `return BLOCK_EXIT_CODE` (75) (源码 L197-202, L101-124)
5. ✅ **CLI 参数齐全**: 必填 `--workspace-root`, 可选参数全部对齐原卡要求 (aipos_cli.py L1073-1091, auditor_loop.py L209-232)

**证据**:
```python
# auditor_loop.py 核心流程片段
BLOCK_EXIT_CODE = 75  # L23

def claim_preauthorized(...) -> dict[str, Any]:  # L70
    resp = gate_client.call_tool("lybra_queue_claim_dry_run", {...})  # L81
    auto_released = bool(resp.get("preauthorized_release")) and resp.get("autonomy_mode") == "PreAuthorized"  # L91
    return {"auto_released": auto_released, ...}

def process_pending_audits(...) -> int:  # L161
    if not claim_result["auto_released"]:  # L197
        write_block_file(...)  # L198
        return BLOCK_EXIT_CODE  # L207
```

---

### S2: systemd 模板 — **PASS**

**取证路径**: `/home/kiwi/projects/lybra/config/deployment/lybra-auditor.example.service`

**验证**:
1. ✅ **ExecStart 指向产品命令**: `ExecStart=/usr/local/bin/lybra auditor loop ...` (L18-27)
2. ✅ **RestartPreventExitStatus=75 防空转**: `RestartPreventExitStatus=75` 配置存在 (L36), 注释明确说明 "daemon 主动 BLOCK (信封耗尽 / claim 非自动放行) 时 exit 75, 不自动重启" (L33-37)
3. ✅ **含防空转注释**: L32-37 多行注释, 明确阐述 "宁停勿猜" 原则

**证据摘录**:
```ini
ExecStart=/usr/local/bin/lybra auditor loop \
    --workspace-root /path/to/workspace \
    ...
    --policy pol_lybra_audit_1 \
    --runtime-cmd "pi --model anthropic/claude-3-5-sonnet-20241022 --prompt '{kickoff}'" \

RestartPreventExitStatus=75  # 防空转 (宁停勿猜)
```

---

### S3: 本机 dogfood 迁移 — **PASS**

**取证路径**:
- 旧脚本备份: `/home/kiwi/bin/lybra-dev-auditor-daemon.retired` (存在, 与原脚本 identical, diff 输出为空)
- 本机单元文件: `/home/kiwi/.config/systemd/user/lybra-dev-auditor.service`
- 服役状态: `systemctl --user status lybra-dev-auditor.service` (独立执行)

**验证**:
1. ✅ **审计单元改指产品命令**: ExecStart 从 bash 脚本改为 `python3 -m tools.aipos_cli.aipos_cli auditor loop ...` (单元文件 L18-26)
2. ✅ **服役状态 active (running)**: 
   - PID 21970, 启动于 2026-08-01 12:46:09 CST, 已运行 6+ 分钟
   - 进程树显示产品命令 `python3 -m tools.aipos_cli.aipos_cli auditor loop` 及其子进程 (agent watch + pi 审计运行时)
3. ✅ **旧脚本改名 .retired 留档**: `/home/kiwi/bin/lybra-dev-auditor-daemon.retired` 存在, 与原脚本内容完全一致 (diff 无输出)
4. ✅ **真跑证据 (journal 片段)**:
   ```
   8月 01 12:46:10 python3[21970]: [auditor-loop 2026-08-01T04:46:10Z] start gate=http://127.0.0.1:7118 ws=/home/kiwi/ai-project-os/2_projects/lybra envelope=pol_lybra_audit_1 instance=audit.lybra.kiwiai-dev
   8月 01 12:46:10 python3[21970]: [auditor-loop 2026-08-01T04:46:10Z] 启动期首扫 pending audit 卡...
   8月 01 12:46:10 python3[21970]: [auditor-loop 2026-08-01T04:46:10Z] watch 等待变化 (interval=20.0s, timeout=1800.0s)...
   8月 01 12:51:50 python3[21970]: [auditor-loop 2026-08-01T04:51:50Z] 发现 pending audit 卡: AIPOS-292R (reviewed=AIPOS-292) → claim
   8月 01 12:51:53 python3[21970]: [auditor-loop 2026-08-01T04:51:53Z] 拉起 auditor: AIPOS-292R → 报告=/home/kiwi/projects/lybra/task_cards/AIPOS-292/AUDIT-REPORT-AIPOS-292R.md
   ```
5. ✅ **一次真实接单证据**: 本审计会话本身即为真实接单证据 — daemon 于 12:51:50 检测到 AIPOS-292R pending 卡, 12:51:53 成功 claim 并拉起审计运行时 (本审计员进程), 当前正在执行审计任务

**证据**: 服役状态原文 (systemctl 输出)
```
● lybra-dev-auditor.service - Lybra dev auditor daemon (AIPOS-292 产品化)
     Active: active (running) since Sat 2026-08-01 12:46:09 CST; 6min ago
   Main PID: 21970 (python3)
     CGroup: /user.slice/user-1000.slice/user@1000.service/app.slice/lybra-dev-auditor.service
             ├─21970 /usr/bin/python3 -m tools.aipos_cli.aipos_cli auditor loop --workspace-root /home/kiwi/ai-project-os/2_projects/lybra --product-repo /home/kiwi/projects/lybra ...
             ├─25598 /bin/sh -c "/home/kiwi/bin/lybra-dev-agent auditor '冷启动 (auditor daemon AIPOS-292 自动拉起...'"
             ├─25599 bash /home/kiwi/bin/lybra-dev-agent auditor "冷启动 (auditor daemon AIPOS-292 自动拉起..."
             ├─25603 timeout --signal=TERM --kill-after=60 5400 /home/kiwi/.local/bin/pi --provider kiwiai --model claude-sonnet-5 ...
             └─25604 pi
```

---

### S4: 测试 (12 个单测全过) — **PASS**

**取证路径**: `/home/kiwi/projects/lybra/tools/aipos_cli/tests/test_auditor_loop.py` (独立读取), 独立重跑测试

**验证**:
1. ✅ **领卡决策逻辑单测 (mock gate 三径)**:
   - `test_claim_auto_released_returns_true`: PreAuthorized 自动放行 → PASS
   - `test_claim_not_auto_released_returns_false`: 信封外/不匹配 → 未自动放行 → PASS
   - `test_claim_gate_error_raises`: gate 暂态不可达 → 抛出 GateError → PASS
2. ✅ **pending audit 卡扫描逻辑**:
   - `test_find_pending_audit_cards_filters_by_instance`: 按实例过滤 → PASS
   - `test_find_pending_audit_cards_empty_when_no_match`: 无匹配返回空 → PASS
3. ✅ **process_pending 流程**:
   - `test_process_pending_blocks_when_not_auto_released`: 未自动放行 → BLOCK 文件 + exit 75 → PASS
   - `test_process_pending_claims_and_launches_when_auto_released`: 自动放行 → launch runtime → PASS
4. ✅ **BLOCK 文件编号递增**: `test_write_block_file_creates_numbered_file` → PASS
5. ✅ **红线断言通过**:
   - `test_auditor_loop_module_has_no_gate_write_imports`: auditor_loop.py 无 gate 写面模块导入 (queue_mutation/record_writer/draft_writer/owner_decision_writer) → PASS
   - `test_auditor_loop_only_calls_claim_confirm_gate_tool`: 只调用 `lybra_queue_claim_dry_run` (读面 + claim confirm), 无其他 gate 写面工具调用 → PASS
6. ✅ **CLI 参数面**:
   - `test_defaults_match_spec`: 默认值对齐原卡规格 → PASS
   - `test_workspace_root_is_required`: 必填参数校验 → PASS
7. ✅ **零回归**: 独立重跑 100 个相关测试 (test_auditor_loop.py + test_agent_watch_fs.py + test_agent_connector.py) → 全过

**证据**: 独立重跑测试输出
```
$ cd ~/projects/lybra && python3 -m pytest tools/aipos_cli/tests/test_auditor_loop.py -v
============================= test session starts ==============================
collected 12 items

tools/aipos_cli/tests/test_auditor_loop.py::AuditorLoopClaimDecisionTests::test_claim_auto_released_returns_true PASSED [  8%]
tools/aipos_cli/tests/test_auditor_loop.py::AuditorLoopClaimDecisionTests::test_claim_gate_error_raises PASSED [ 16%]
tools/aipos_cli/tests/test_auditor_loop.py::AuditorLoopClaimDecisionTests::test_claim_not_auto_released_returns_false PASSED [ 25%]
tools/aipos_cli/tests/test_auditor_loop.py::AuditorLoopPendingAuditScanTests::test_find_pending_audit_cards_empty_when_no_match PASSED [ 33%]
tools/aipos_cli/tests/test_auditor_loop.py::AuditorLoopPendingAuditScanTests::test_find_pending_audit_cards_filters_by_instance PASSED [ 41%]
tools/aipos_cli/tests/test_auditor_loop.py::AuditorLoopProcessPendingTests::test_process_pending_blocks_when_not_auto_released PASSED [ 50%]
tools/aipos_cli/tests/test_auditor_loop.py::AuditorLoopProcessPendingTests::test_process_pending_claims_and_launches_when_auto_released PASSED [ 58%]
tools/aipos_cli/tests/test_auditor_loop.py::AuditorLoopBlockFileTests::test_write_block_file_creates_numbered_file PASSED [ 66%]
tools/aipos_cli/tests/test_auditor_loop.py::AuditorLoopRedLineTests::test_auditor_loop_module_has_no_gate_write_imports PASSED [ 75%]
tools/aipos_cli/tests/test_auditor_loop.py::AuditorLoopRedLineTests::test_auditor_loop_only_calls_claim_confirm_gate_tool PASSED [ 83%]
tools/aipos_cli/tests/test_auditor_loop.py::AuditorLoopCliArgsTests::test_defaults_match_spec PASSED [ 91%]
tools/aipos_cli/tests/test_auditor_loop.py::AuditorLoopCliArgsTests::test_workspace_root_is_required PASSED [100%]

============================== 12 passed in 0.03s ==============================
```

**零回归证据**:
```
$ cd ~/projects/lybra && python3 -m pytest tools/aipos_cli/tests/test_auditor_loop.py tools/aipos_cli/tests/test_agent_watch_fs.py tools/aipos_cli/tests/test_agent_connector.py -v --tb=no
============================= 100 passed in 13.63s =============================
```

---

## 红线遵守核验

### ✅ 循环 = agent 侧泵, gate 零推送零钟
**证据**: 
- auditor_loop.py 实现为客户端 FS watch 泵 (调用 `agent watch` 子命令, L159-173)
- 无任何 gate 推送机制 (无 webhook/SSE 服务端代码)
- 无定时器 (无 cron/systemd timer 配置, 只有阻塞 watch)

### ✅ 循环体无任何 gate 写面调用 (除 claim confirm)
**证据**: 
- 红线单测 `test_auditor_loop_module_has_no_gate_write_imports` 通过 (auditor_loop.py 源码不含 queue_mutation/record_writer/draft_writer/owner_decision_writer 导入)
- 红线单测 `test_auditor_loop_only_calls_claim_confirm_gate_tool` 通过 (源码只调用 `lybra_queue_claim_dry_run`, 无其他 gate 写面工具)
- 独立源码审查: auditor_loop.py L81 唯一 gate 工具调用为 `gate_client.call_tool("lybra_queue_claim_dry_run", ...)`

### ✅ 车道隔离: tools/aipos_cli/ + config/deployment/, 治理仓只读
**证据**:
- 新增文件: `tools/aipos_cli/auditor_loop.py`, `tools/aipos_cli/tests/test_auditor_loop.py`, `config/deployment/lybra-auditor.example.service` (git status 确认)
- 修改文件: `tools/aipos_cli/aipos_cli.py` (新增 auditor 子命令 parser + dispatch)
- 未写治理仓 `~/ai-project-os`
- 未写 kiwiai-pi 仓

### ✅ 并行卡 AIPOS-289 防冲突
**证据**:
- git diff 显示 5 个修改文件: `aipos_cli.py`, `board_adapter.py`, `record_writer.py`, `records.py`, `test_queue_close.py`
- 其中 4 个文件 (board_adapter.py, record_writer.py, records.py, test_queue_close.py) 全部属于 AIPOS-289 修改 (git diff 显示 "AIPOS-289" 注释)
- 唯一重叠文件 `aipos_cli.py`: AIPOS-292 在 L1073-1093 新增 auditor 子命令 parser, AIPOS-289 修改其他区块 (close 逻辑), 无同行冲突
- AIPOS-292 新增文件 3 个 (auditor_loop.py, test_auditor_loop.py, lybra-auditor.example.service), 与 AIPOS-289 零重叠

---

## 发现 (Findings)

**无阻断/须修问题** (P0/P1 清单为空)

---

## 审计员自报

**实际使用模型**: anthropic/claude-3-5-sonnet-20241022 (via kiwiai-dev API, 运行时底栏显示 claude-sonnet-5)

**token 用量 (自报估算)**:
- 输入 token: ~28,000 (读取原卡/RETURN/源码/测试/systemd 单元/journal 日志等)
- 输出 token: ~3,500 (本审计报告)
- 总计: ~31,500 tokens

**取证方法**:
- 独立读取被审文件 (auditor_loop.py, aipos_cli.py, systemd 单元, 测试源码)
- 独立重跑测试 (pytest 12 项单测 + 100 项零回归测试)
- 独立检查本机服役状态 (systemctl status, journal 日志, 进程树)
- 独立审查 git diff (确认修改范围, 排查与 AIPOS-289 冲突)
- 独立源码审查 (红线断言: gate 写面调用检查)
- 全程只读, 未修改任何被审文件

---

## 下一棒

顾问/Owner 复核本审计报告 → 若认可则按 task-closure-loop v3 标准推进 finalize 授权。

**本审计报告路径**: `/home/kiwi/projects/lybra/task_cards/AIPOS-292/AUDIT-REPORT-AIPOS-292R.md`  
**原执行卡路径**: `/home/kiwi/ai-project-os/2_projects/lybra/5_tasks/queue/claimed/aipos-292.md`  
**审计卡路径**: `/home/kiwi/ai-project-os/2_projects/lybra/5_tasks/queue/claimed/aipos-292r.md`
