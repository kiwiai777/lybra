# AIPOS-356 — Gate 重连 Runbook

> 部署后各类客户端的标准恢复动作、判活方式、常见误判。
> 适用于 `lybra-dev-gate.service` (MCP port 7118 / Board port 7117)。

---

## 背景

Lybra gate 由 systemd 管理 (`lybra-dev-gate.service`)。每次部署 (`lybra-deploy deploy`) 会：
1. 创建新快照 → 切换 symlink → 重启 gate 服务

**AIPOS-356 起**：部署使用 SO_REUSEPORT 优雅切换——新进程先起并完成自检，旧进程 drain 后退出。
目标：部署期间**新建连接零拒绝**，存量 SSE 连接断开后客户端重连**立即成功**。

但客户端侧行为不变：SSE 长连接在进程切换时**会断开**（旧进程退出），客户端需要重连。

---

## 判活纪律（部署后先确认 gate 状态）

### ✅ 正确做法

```bash
# 1. 检查 systemd 服务状态
systemctl --user status lybra-dev-gate.service

# 2. 检查端口是否有进程监听
ss -tlnp | grep -E '7117|7118'

# 3. 查看部署版本
lybra-deploy status
```

### ❌ 禁止做法

```bash
# 禁止！curl /mcp 会发送无效请求，污染日志
curl http://kiwiai-dev.tail6b5218.ts.net:7118/mcp

# 禁止！curl /sse 会挂起（SSE 长连接）
curl http://kiwiai-dev.tail6b5218.ts.net:7118/sse
```

**原因**：`/mcp` 需要 POST + Bearer token + 合法 JSON-RPC body；`/sse` 是长连接会 hang。
两者都不适合作为健康检查。用 `ss` 和 `systemctl` 就够了。

---

## 各客户端重连动作

### 1. Pi (本仓 executor agent)

Pi 通过 MCP 连接 gate。部署后如果工具调用失败：

```bash
# 重启 pi 会话即可（pi 会重新初始化 MCP 连接）
# 通常不需要手动操作——pi 的下一次工具调用会自动重连
```

**判活**：在 pi 中执行任意 MCP 工具调用（如 `lybra_queue_claim_dry_run`），成功即表示连接恢复。

### 2. Claude Code (MCP 配置)

Claude Code 使用 MCP 工具表。部署后工具表可能显示断开：

```
# 在 Claude Code 中执行：
/mcp reconnect
```

这会重新初始化 MCP 连接，刷新工具表。

**判活**：`/mcp` 后确认工具列表正常显示。

### 3. Codex (Streamable-HTTP)

Codex 使用 Streamable-HTTP 传输。部署后：

```
# Codex 通常会自动重连（SSE retry: 1000ms 指令已发送）
# 如果工具调用失败，重启 codex 会话
```

### 4. 其他 MCP 客户端

任何标准 MCP 客户端：
1. 关闭现有连接
2. 重新初始化（发送 `initialize` JSON-RPC）
3. 确认 `tools/list` 返回正常

---

## 部署后快速验证清单

```bash
# 1. gate 在跑？
systemctl --user is-active lybra-dev-gate.service
# 期望输出: active

# 2. 端口在监听？
ss -tlnp | grep 7118
# 期望输出: 有 python3 进程在 LISTEN

# 3. MCP 能响应？（用 python 探测，不用 curl）
python3 -c "
import socket
s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
s.settimeout(2)
s.connect(('127.0.0.1', 7118))
s.close()
print('✓ MCP port is accepting connections')
"

# 4. 版本正确？
lybra-deploy status
```

---

## 故障排查

### gate 不在跑

```bash
# 查看日志
journalctl --user -u lybra-dev-gate.service -n 50 --no-pager

# 常见原因:
# - 端口被占用: ss -tlnp | grep 7118 → kill 残留进程
# - 导入失败: 检查 .deploy/current 快照完整性
# - 权限问题: 检查 .lybra/connection.json 权限 (应为 0600)

# 手动重启
systemctl --user restart lybra-dev-gate.service
```

### 端口被残留进程占用

```bash
# 找到残留进程
ss -tlnp | grep 7118
# 输出中会有 pid=XXXXX

# 杀掉残留
kill <PID>
sleep 2

# 重启
systemctl --user restart lybra-dev-gate.service
```

### 部署后旧 token 失效

如果部署过程中执行了 `serve rotate`（token 轮换），客户端需要更新 connection.json：

```bash
# 查看当前 token 状态
lybra serve status

# 远程机器需要更新 connection.json 中的 token
# 具体操作见 AIPOS-346 config_update_locations
```

---

## 与 AIPOS-341 的衔接

AIPOS-341 (deploy 重启注册服务) 落地后，部署动作以 341 形态为准。
当前（341 未落地）：部署动作 = `lybra-deploy deploy`（本 runbook 描述的流程）。

---

## 技术细节：优雅切换原理

```
时间线:
─────────────────────────────────────────────────────────
t0: 旧 gate 在跑 (PID 100, 监听 7118)
t1: deploy 创建新快照, 切换 symlink
t2: 临时 MCP 进程启动 (--reuse-port, 在独立 systemd scope)
t3: 临时进程就绪 (也监听 7118, SO_REUSEPORT 共享端口)
t4: systemctl restart → 旧进程被 SIGTERM → 新进程启动 (也有 --reuse-port)
t5: 新进程就绪 → 临时进程退出
t6: 新 gate 独立服务
─────────────────────────────────────────────────────────

端口 7118 的监听状态:
t0-t3: [旧]
t3-t4: [旧, 临时]     ← 两个进程共享端口
t4-t5: [临时, 新]     ← 旧退出, 新加入
t5-t6: [新]           ← 临时退出

结论: 端口始终有至少一个监听者 → 新建连接零拒绝
```

**注意**：存量 SSE 长连接在旧进程退出时会断开。但客户端收到 SSE `retry: 1000` 指令后会
立即重连（1 秒后），此时新进程已在监听 → 重连一次成功。
