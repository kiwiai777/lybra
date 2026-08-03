# AIPOS-295 — watch 健康监护使用指南

## 概述

健康监护功能为 `lybra agent watch` 提供周期性心跳检测，并支持通过 `lybra agent supervise` 实现有界自动重启。

**核心能力**：
- 五分钟体检心跳（可配置）：进程活性、CPU增量、会话文件、工作树变化
- 死寂判据：进程消失 OR 持续静默（CPU不爬 + 零会话文件 + 零工作树增量）
- 有界自愈：unhealthy → 杀进程树 → 重拉1次 → 第2次失败ESCALATE请授权

**红线**：
- 全逻辑在观察者/agent侧，gate零涉入
- 探测量的是pi子进程树，不量timeout壳
- 换模型必须经Owner授权或预授权策略引用

## 使用场景

### 场景1：监控执行任务健康（仅观察，不重启）

用于人工监督场景，周期性输出健康报告但不自动干预。

```bash
# 启动监控（每300秒报告一次健康状态）
lybra agent watch \
  --workspace-root ~/ai-project-os \
  --stream \
  --health 300 \
  --proc-pattern "node" \
  --session-dirs "/tmp/pi-sessions,~/.cache/pi" \
  --worktree-path ~/projects/lybra \
  --run-log /tmp/executor.log
```

**输出示例**（JSON事件流）：
```json
{"kind":"health","proc_alive":true,"cpu_delta":12.34,"new_session_files":5,"worktree_changes":2,"silent_secs":300}
{"kind":"health","proc_alive":true,"cpu_delta":0.02,"new_session_files":0,"worktree_changes":0,"silent_secs":300}
{"kind":"unhealthy","reason":"sustained_silence","silent_cycles":2,"proc_alive":true,"cpu_delta":0.0,"new_session_files":0,"worktree_changes":0}
```

**事件类型**：
- `health`: 周期性心跳（正常运行）
- `unhealthy`: 检测到死寂（进程消失 OR 连续2个周期静默）

### 场景2：自动监护+有界重启（生产部署）

用于无人值守场景，自动重启失败任务（限1次），第2次失败升级请授权。

```bash
# 使用supervise包装执行命令
lybra agent supervise \
  --spawn-cmd "timeout 3600 pi --model kiwiai/qwen3.7-plus --prompt 'kickoff message'" \
  --workspace-root ~/ai-project-os \
  --product-repo ~/projects/lybra \
  --card-id AIPOS-295 \
  --health-interval 300 \
  --proc-pattern "node" \
  --session-dirs "/tmp/pi-sessions" \
  --worktree-path ~/projects/lybra \
  --run-log /tmp/executor.log
```

**行为**：
1. 启动目标命令（必须含timeout包装）
2. 并行启动health监控
3. 检测到unhealthy → 杀进程树 → 发出`respawn`事件 → 重拉1次
4. 第2次unhealthy → 发出`escalate`事件 → 写ESCALATE文件 → 退出75（systemd不重启）

**ESCALATE文件示例**：
```
task_cards/AIPOS-295/ESCALATE-1.md
```

内容包含：
- 两次失败的完整诊断（三派式病史）
- 死亡特征（进程消失/静默死）
- 建议措施（换模型/查路由/人工介入）
- 停因说明（防止空转）

## 参数说明

### watch --health 参数

| 参数 | 说明 | 必需 | 默认值 |
|------|------|------|--------|
| `--health SECS` | 健康检查间隔（秒） | 是 | 300 |
| `--stream` | 持续模式（必需，与--health配合） | 是 | - |
| `--pid-file PATH` | PID文件路径（读取父进程PID，监控pi子树） | 否 | - |
| `--proc-pattern STR` | 进程名模式（如'node'），排除timeout/bash | 否 | - |
| `--session-dirs DIRS` | 会话目录列表（逗号分隔） | 否 | - |
| `--worktree-path PATH` | Git工作树路径 | 否 | workspace父目录 |
| `--unhealthy-cycles N` | 连续静默周期数触发unhealthy | 否 | 2 |
| `--run-log PATH` | 运行日志路径（用于stall检测） | 否 | - |

### supervise 参数

| 参数 | 说明 | 必需 | 默认值 |
|------|------|------|--------|
| `--spawn-cmd CMD` | 要启动的命令（必须含timeout包装） | 是 | - |
| `--workspace-root PATH` | Lybra工作空间根目录 | 是 | - |
| `--card-id ID` | 任务卡ID（用于ESCALATE文件名） | 是 | - |
| `--product-repo PATH` | 产品仓库根目录 | 否 | ~/projects/lybra |
| `--health-interval SECS` | 健康检查间隔 | 否 | 300 |
| 其他参数 | 同watch --health参数 | 否 | - |

## 健康判据说明

### 健康指标（每个周期采集）

1. **proc_alive**: 进程树存在（排除timeout壳，只量pi子进程）
2. **cpu_delta**: 本周期CPU时间增量（秒）
3. **new_session_files**: 本周期新增会话文件数
4. **worktree_changes**: 本周期工作树变更文件数
5. **silent_secs**: 距上次检查的时间（秒）

### Unhealthy判据（AIPOS-293实战验证）

触发条件（满足任一）：
- **进程消失**: `proc_alive = false`
- **持续静默**: 连续N个周期（默认2）满足 `cpu_delta < 0.01 AND new_session_files = 0 AND worktree_changes = 0`

**设计理由**：
- 单周期静默可能是正常间歇（模型思考、等待IO）
- 连续2周期静默（默认10分钟）= 实质性挂死
- 避免误杀：CPU/文件/工作树三维度交叉验证

## systemd集成（生产部署）

### 服务单元示例

```ini
[Unit]
Description=Lybra Executor with Health Monitoring
After=network.target

[Service]
Type=simple
ExecStart=/usr/bin/python3 -m tools.aipos_cli.aipos_cli agent supervise \
  --spawn-cmd "timeout 3600 pi --model kiwiai/qwen3.7-plus --prompt '{kickoff}'" \
  --workspace-root /home/lybra/ai-project-os \
  --product-repo /home/lybra/projects/lybra \
  --card-id AIPOS-295 \
  --health-interval 300 \
  --proc-pattern node \
  --session-dirs /tmp/pi-sessions \
  --worktree-path /home/lybra/projects/lybra
Restart=on-failure
RestartSec=60
RestartPreventExitStatus=75
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
```

**关键配置**：
- `Restart=on-failure`: 意外退出时自动重启
- `RestartPreventExitStatus=75`: 遇ESCALATE(75)不重启（防空转）
- `RestartSec=60`: 重启前等待60秒（避免抖动）

### 启动与监控

```bash
# 启动服务
systemctl --user start lybra-executor.service

# 查看状态
systemctl --user status lybra-executor.service

# 查看日志（含health事件）
journalctl --user -u lybra-executor.service -f

# 遇ESCALATE后的续跑（修改模型后）
# 1. 编辑服务单元，改spawn-cmd中的模型
systemctl --user edit lybra-executor.service
# 2. 重启
systemctl --user restart lybra-executor.service
```

## 故障诊断

### Q1: health事件不出现

**检查项**：
1. 是否同时指定了 `--stream` 和 `--health`
2. `--health` 间隔是否过长（测试时建议用小值如30）
3. 是否正确指定了 `--proc-pattern` 或 `--pid-file`

### Q2: 误报unhealthy（正常任务被杀）

**可能原因**：
- `--unhealthy-cycles` 设置过小（默认2，建议保持）
- `--health` 间隔过短（默认300秒，建议不低于120秒）
- 未正确配置 `--session-dirs`（监控不到会话文件活动）

**修正**：
```bash
# 增加容忍度：3个周期才判unhealthy
--unhealthy-cycles 3
```

### Q3: ESCALATE后不知道如何续跑

**查看ESCALATE文件**：
```bash
cat ~/projects/lybra/task_cards/<CARD-ID>/ESCALATE-1.md
```

**决策流程**：
1. **分析失败模式**：进程消失 vs 静默死
2. **进程消失** → 检查provider路由、quota、网络
3. **静默死** → 换模型（qwen → claude / gemini）
4. **换模型需Owner授权** → 提issue或在决策日志记录
5. 修改spawn-cmd，重启服务

### Q4: 如何避免频繁ESCALATE

**预授权策略**（未来扩展）：
- 在ESCALATE文件中引用预授权策略（如 `pol_model_fallback_chain`）
- 策略定义模型顺位（qwen → claude → gemini）
- supervise自动按顺位切换，无需人工介入

**当前版本限制**：
- 必须人工决策换模型（Owner授权）
- 未来版本将支持策略引用

## 最佳实践

### 1. 生产部署三要素

✅ **timeout包装**：spawn-cmd必须含timeout（防止无限挂起）
```bash
--spawn-cmd "timeout 3600 pi ..."  # 1小时超时
```

✅ **会话目录监控**：指定pi会话存储位置
```bash
--session-dirs "/tmp/pi-sessions,~/.cache/pi"
```

✅ **工作树监控**：指向产品仓库（检测代码产出）
```bash
--worktree-path ~/projects/lybra
```

### 2. 日志聚合

health事件是JSON流，适合导入日志系统：

```bash
# 导入到文件
lybra agent watch ... > /var/log/lybra/health.jsonl

# 或通过journald（systemd服务自动）
journalctl -u lybra-executor.service -o json
```

### 3. 告警集成

从health事件流提取unhealthy/escalate事件触发告警：

```bash
# 示例：监听escalate事件并发邮件
lybra agent supervise ... | \
  jq -r 'select(.kind=="escalate") | .timestamp + " ESCALATE: " + .reason' | \
  while read line; do
    echo "$line" | mail -s "Lybra Escalation" admin@example.com
  done
```

## 参考

- 设计输入：`task_cards/AIPOS-284D/FINDING-CANDIDATE-proc-liveness.md`（进程活性观察面）
- 实战判据：AIPOS-293三派死亡诊断（静默死=CPU不爬+零文件+零工作树）
- 同族先例：AIPOS-292 auditor loop（agent侧泵模式）
- 任务卡：`~/ai-project-os/2_projects/lybra/5_tasks/queue/claimed/aipos-295.md`
