# AIPOS-295 快速开始

## 监控模式（只观察不干预）

```bash
lybra agent watch \
  --workspace-root ~/ai-project-os \
  --stream \
  --health 300 \
  --proc-pattern "node" \
  --session-dirs "/tmp/pi-sessions" \
  --worktree-path ~/projects/lybra
```

输出 JSON 事件流：
- `kind:health` - 每5分钟心跳（进程活性、CPU、文件、工作树）
- `kind:unhealthy` - 检测到静默死

## 自动重启模式（生产部署）

```bash
lybra agent supervise \
  --spawn-cmd "timeout 3600 pi --model kiwiai/qwen3.7-plus --prompt 'kickoff'" \
  --workspace-root ~/ai-project-os \
  --card-id AIPOS-XXX \
  --health-interval 300 \
  --proc-pattern "node" \
  --session-dirs "/tmp/pi-sessions"
```

行为：
1. 启动命令并监控健康
2. 第1次失败 → 自动重启（发 `respawn` 事件）
3. 第2次失败 → 写 ESCALATE 文件，exit 75，等待授权

## 检查 ESCALATE 文件

```bash
cat ~/projects/lybra/task_cards/<CARD-ID>/ESCALATE-1.md
```

包含完整病史、死亡特征、建议措施（换模型/查路由/人工介入）

## 验证安装

```bash
# 测试健康监护
python3 tools/aipos_cli/tests/test_health_supervise.py

# 测试 unhealthy 检测
python3 tools/aipos_cli/tests/test_unhealthy_integration.py

# 零回归测试
python3 tools/aipos_cli/tests/test_agent_watch_fs.py
```

## 完整文档

详见：`tools/aipos_cli/docs/health-monitoring.md`
