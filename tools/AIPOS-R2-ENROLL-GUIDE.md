# AIPOS-R2 enroll 命令使用指南

## 概述

`lybra roles enroll` 命令实现 gate 分发器 v1 的凭据注册功能,一条命令完成:
1. 从 gate 兑换 enrollment code 获取 role credential
2. 落 `.lybra/` 自发现配置(connection/role/actor/policy)
3. 使后续 loop 操作零手工配置(不需要 source 脚本或手动设置 token)

## 使用流程

### 1. Owner/Advisor 签发 enrollment code

在 gate 所在机器(dev)上:

```bash
lybra --workspace-root /home/kiwi/ai-project-os/2_projects/lybra \
  roles enroll-code \
  --role executor \
  --instance exec.lybra.mac1 \
  --ttl 86400 \
  --owner-authorization-ref "AIPOS-R2-mac-enroll" \
  --reason "Enroll Mac executor for lybra project" \
  --json
```

输出包含(FIX-2: 顶层字段):
- `code`: enrollment code(明文,可以通过聊天/邮件传输)
- `code_id`: 用于后续管理(revoke 等)
- `fingerprint`: code 的指纹(非敏感)
- `role`, `instance`, `expires_at`: 元数据

### 2. Remote agent 使用 enrollment code 进行 enroll

在目标机器(Mac)上:

```bash
# 方式 1: 直接使用 CLI
lybra --workspace-root ~/ai-projects/lybra \
  roles enroll \
  --code "<enrollment_code>" \
  --gate-url "http://kiwiai-dev.tail6b5218.ts.net:7118" \
  --bootstrap-token "<any_valid_token>"

# 方式 2: 使用环境变量提供 bootstrap token
export LYBRA_BOOTSTRAP_TOKEN="<any_valid_token>"
lybra --workspace-root ~/ai-projects/lybra \
  roles enroll \
  --code "<enrollment_code>" \
  --gate-url "http://kiwiai-dev.tail6b5218.ts.net:7118"

# 方式 3: 独立脚本(无需完整 lybra 安装)
python3 /path/to/tools/aipos_cli/enroll_client.py \
  --code "<enrollment_code>" \
  --gate-url "http://kiwiai-dev.tail6b5218.ts.net:7118" \
  --workspace ~/ai-projects/lybra \
  --bootstrap-token "<any_valid_token>"
```

**关于 bootstrap token:**
- Enrollment exchange 在工具层面是"公开"的(不需要特定 scope)
- 但 HTTP 传输层仍需要一个有效的 bearer token 通过认证
- 可以使用任何有效的 token(executor/owner/auditor 等)
- 只用于 HTTP 传输认证,不影响最终铸造的 token 权限

**FIX-1: 新机零手工上线**
- workspace-root 不需要预先存在
- enroll 自动创建 workspace-root 和 .lybra/ 目录
- 只需要 .lybra/ 配置,不需要队列结构(队列在 gate 侧)

### 3. 验证自发现配置

Enroll 完成后,检查 `.lybra/` 目录:

```bash
ls -la ~/ai-projects/lybra/.lybra/
# 应包含:
# - connection.json (0600, 包含 token 和 gate URL)
# - role (纯文本,角色名)
# - actor (纯文本,agent_instance)
# - policy (可选,policy reference)
```

验证 ConnectionResolver 自发现:

```python
from loop_context import ConnectionResolver
from pathlib import Path

workspace = Path("~/ai-projects/lybra").expanduser()

# 自动发现 token(不需要 env 变量)
token = ConnectionResolver.resolve_token(
    workspace_root=workspace,
    role="executor",
    agent_instance="exec.lybra.mac1",
)

# 自动发现 gate URL
gate_url = ConnectionResolver.resolve_gate_url(
    workspace_root=workspace,
)

print(f"Token fingerprint: {token[:10]}...")
print(f"Gate URL: {gate_url}")
```

## 幂等性与轮换

重复 enroll 同一个 (project, role, instance):
- 不会创建重复 token 条目
- 会轮换(替换)现有 token
- 旧 token 立即失效(如果 gate 重载了配置)

```bash
# 第二次 enroll 同一 instance → 轮换 token
lybra --workspace-root ~/ai-projects/lybra \
  roles enroll \
  --code "<new_enrollment_code>" \
  --gate-url "http://kiwiai-dev.tail6b5218.ts.net:7118" \
  --bootstrap-token "$LYBRA_BOOTSTRAP_TOKEN"
```

输出会显示 `rotated: true`,表示替换了现有凭据。

## 安全特性

1. **Token 明文不跨机传输**
   - Enrollment code 可以明文传输(它不是凭据,只是兑换凭据的临时通行证)
   - Token 只在 gate→agent 的 HTTPS 响应中传输一次
   - Token 落地后立即以 0600 权限存储

2. **Enrollment code 一次性**
   - 每个 code 只能使用一次
   - 使用后自动标记为 `used`,无法重复兑换
   - 支持 TTL 和手动吊销

3. **Token 不出现在日志/输出**
   - CLI 输出只显示 fingerprint(sha256 前 12 位)
   - 完整 token 只在 connection.json 中

## 管理命令

### 列出所有 enrollment codes

```bash
lybra --workspace-root /home/kiwi/ai-project-os/2_projects/lybra \
  roles enroll-list
```

### 吊销 enrollment code

```bash
lybra --workspace-root /home/kiwi/ai-project-os/2_projects/lybra \
  roles enroll-revoke <code_id> \
  --owner-authorization-ref "security-revoke" \
  --reason "Compromised or no longer needed"
```

## 故障排查

### 错误: "Bootstrap token required"

需要提供 `--bootstrap-token` 或设置 `LYBRA_BOOTSTRAP_TOKEN` 环境变量。

### 错误: "Enrollment code is expired/used"

Enrollment code 已使用或过期,需要重新签发。

### 错误: "HTTP 502: Bad Gateway"

- 检查 gate URL 是否正确
- 如果使用 tailscale 地址,尝试改用 `127.0.0.1`(本地测试)
- 确认 gate 服务正在运行:`ps aux | grep mcp_server`

### ConnectionResolver 未发现配置

检查:
1. `.lybra/connection.json` 是否存在且权限正确(0600)
2. 环境变量 `LYBRA_GATE_URL` / `LYBRA_TOKEN` 是否覆盖了自发现
3. workspace_root 路径是否正确

## 设计权威

- LOOP-REDESIGN v2 §4 (gate 分发器)
- LOOP-REDESIGN v2 §7 R2
- AIPOS-362 (enrollment code 机制)
- AIPOS-R1 (ConnectionResolver 自发现)

## 测试

运行集成测试:

```bash
cd ~/projects/lybra
python3 tools/test_aipos_r2_enroll.py
```

测试覆盖:
- Enrollment code 签发与兑换
- `.lybra/` 配置落位(connection/role/actor)
- ConnectionResolver 自发现
- Token 明文不泄露
- 幂等性(重复 enroll 轮换 token)
- 现有角色零回归
