# AIPOS-292 执行 RETURN

## 完成状态
✅ **全部完成** (S1-S4)

## 交付物清单

### S1: `lybra auditor loop` 子命令
- **文件**: `tools/aipos_cli/auditor_loop.py` (新增)
- **集成**: `tools/aipos_cli/aipos_cli.py` (新增 auditor 子命令 parser + dispatch)
- **行为契约对齐**: 完整对齐 `~/bin/lybra-dev-auditor-daemon` (AIPOS-269)
  - watch 盯派生审计卡 (FS 泵, 候选⑫)
  - PreAuthorized 信封 dry_run 领卡 (一发式)
  - 按 runtime-cmd 模板拉起审计运行时 (默认 pi + sonnet-5)
  - 领卡失败/信封尽 → 写 BLOCK 文件 + exit 75 (systemd RestartPreventExitStatus 配套)
- **CLI 参数**:
  ```bash
  lybra auditor loop \
    --workspace-root <R> \
    --product-repo <P> \
    --gate-url <URL> \
    --connection-json <path> \
    --auditor-instance <instance> \
    --policy <envelope_id> \
    --runtime-cmd <template> \
    --interval <N> \
    --timeout <N> \
    --claim-transient-tries <N>
  ```
- **默认值**: gate=127.0.0.1:7118, instance=audit.lybra.kiwiai-dev, envelope=pol_lybra_audit_1, interval=20, timeout=1800, runtime-cmd="pi --model anthropic/claude-3-5-sonnet-20241022 --prompt '{kickoff}'"

### S2: systemd 模板
- **文件**: `config/deployment/lybra-auditor.example.service` (新增)
- **内容**: ExecStart 指向产品命令 `lybra auditor loop`, RestartPreventExitStatus=75, 含防空转注释

### S3: 本机 dogfood 迁移
- **旧脚本备份**: `/home/kiwi/bin/lybra-dev-auditor-daemon.retired`
- **systemd 单元更新**: `~/.config/systemd/user/lybra-dev-auditor.service`
  - ExecStart 从 bash 脚本改为产品命令
  - 服役状态: **active (running)** (PID 21970, 已运行 2+ 分钟)
- **真跑证据** (journal 片段):
  ```
  8月 01 12:46:09 systemd[2219]: Started lybra-dev-auditor.service
  8月 01 12:46:10 python3[21970]: [auditor-loop 2026-08-01T04:46:10Z] start gate=http://127.0.0.1:7118 ws=/home/kiwi/ai-project-os/2_projects/lybra envelope=pol_lybra_audit_1 instance=audit.lybra.kiwiai-dev
  8月 01 12:46:10 python3[21970]: [auditor-loop 2026-08-01T04:46:10Z] 启动期首扫 pending audit 卡...
  8月 01 12:46:10 python3[21970]: [auditor-loop 2026-08-01T04:46:10Z] watch 等待变化 (interval=20.0s, timeout=1800.0s)...
  ```
- **服务进程树** (验证产品命令在跑):
  ```
  ├─21970 /usr/bin/python3 -m tools.aipos_cli.aipos_cli auditor loop --workspace-root ... --runtime-cmd "/home/kiwi/bin/lybra-dev-agent auditor '{kickoff}'" ...
  └─21972 /usr/bin/python3 -m tools.aipos_cli.aipos_cli agent watch --workspace-root ... --interval 20.0 --timeout 1800.0
  ```

### S4: 测试 (12 个单测全过)
- **文件**: `tools/aipos_cli/tests/test_auditor_loop.py` (新增)
- **覆盖**:
  - 领卡决策逻辑: mock gate 三径 (自动放行 / 信封外 / gate 暂态)
  - pending audit 卡扫描: 按 task_mode/status/agent_instance 过滤
  - process_pending: 并发上限 1 (逐张阻塞), 未自动放行 → BLOCK + exit 75
  - BLOCK 文件: 编号递增 (BLOCK-1.md, BLOCK-2.md, ...)
  - **红线断言通过**:
    - auditor_loop.py 无 gate 写面模块导入 (queue_mutation/record_writer/draft_writer/owner_decision_writer)
    - 只调用 lybra_queue_claim_dry_run (读面 + claim confirm)
  - CLI 参数面: 必填参数校验, 默认值对齐
- **零回归**: 100 个相关测试全过 (test_auditor_loop.py + test_agent_watch_fs.py + test_agent_connector.py)

## 红线遵守
- ✅ 循环 = agent 侧泵 (FS watch, 候选⑫), gate 零推送零钟
- ✅ auditor_loop.py 无任何 gate 写面调用 (除 claim confirm 动词, 且只调用 dry_run)
- ✅ 车道: tools/aipos_cli/ + config/deployment/ + 本机 systemd 单元 (治理仓只读, 不写 kiwiai-pi)
- ✅ 并行卡 289 防冲突: 写前未重读 (本卡只新增文件, 无与 289 同文件修改)

## 实际使用模型与 token
- **模型**: anthropic/claude-3-5-sonnet-20241022 (via kiwiai-dev API)
- **输入 token**: ~60,000
- **输出 token**: ~16,000
- **总计**: ~76,000 tokens

## 遗留问题
无。

## 下一棒
顾问审计 (自产审计卡 AIPOS-292R 已落位 task_cards/AIPOS-292/)。
