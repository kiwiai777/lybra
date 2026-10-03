# AIPOS-295 — watch 健康监护使用指南

## 概述

健康监护功能为 `lybra agent watch` 提供周期性心跳检测（只观察不干预；原 `lybra agent supervise` 有界自动重启随 AIPOS-F91 退役删除）。

**核心能力**：
- 五分钟体检心跳（可配置）：进程活性、CPU增量、会话文件、工作树变化
- 死寂判据：进程消失 OR 持续静默（CPU不爬 + 零会话文件 + 零工作树增量）

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

## 最佳实践

### 1. 监控两要素

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

## 参考

- 设计输入：`task_cards/AIPOS-284D/FINDING-CANDIDATE-proc-liveness.md`（进程活性观察面）
- 实战判据：AIPOS-293三派死亡诊断（静默死=CPU不爬+零文件+零工作树）
- 任务卡：`~/ai-project-os/2_projects/lybra/5_tasks/queue/claimed/aipos-295.md`
