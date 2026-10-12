# AIPOS-R2 enroll 命令使用指南

## 概述

`lybra roles enroll-deliver`(在治理根所在机签码, 并在进程内交 `lybra roles enroll` 兑换)实现 gate 分发器 v1 的凭据注册功能,一条命令完成:
1. 签发一次性 enrollment code 并从 gate 兑换 role credential(码不出进程)
2. 落 `.lybra/` 自发现配置(connection/role/actor/policy)
3. 使后续 loop 操作零手工配置(不需要 source 脚本或手动设置 token)

## 使用流程

> AIPOS-F151 / F159(gap #92 / #143): 注册码**不出签发进程**——签码与兑换由 `lybra roles enroll-deliver` 在治理根所在机一条命令完成,
> 码经进程内(远端工位经 ssh stdin)送达兑换, 永不出现在命令行、聊天或环境变量里。旧的「签码 → 把码贴给远端 → 在命令行带码兑换」做法已退役。
> 下文 `<…>` 均为占位, 按你的项目、实例与机器填写(路径与地址读项目声明, 不假设主机)。

### 1. 签码并送达兑换(Owner / 顾问, 在治理根所在机)

先预演(缺省 `--dry-run`: 列出将签发的角色 / 实例 / 治理根 / 到期与凭据引用, 不签码、不写入), 确认无误后把 `--dry-run` 换成 `--confirm`:

```bash
# 本机工位(工位目录在治理根所在机上)
lybra roles --workspace-root <治理根> enroll-deliver \
  --role executor --instance <执行体实例> \
  --governance-root <治理根> --workspace <工位目录> \
  --actor <签发实例> --owner-authorization-ref <覆盖该工位的信封ID> --dry-run

# 远端工位(码经 ssh stdin 送达远端兑换, 不进远端命令行)
lybra roles --workspace-root <治理根> enroll-deliver \
  --role executor --instance <执行体实例> \
  --governance-root <治理根> --workspace <远端工位目录> --ssh <ssh目标> \
  --gate-url <工位可达的门地址> \
  --actor <签发实例> --owner-authorization-ref <覆盖该工位的信封ID> --dry-run
```

- `--actor` = 签发实例: 门凭据按该实例在 connection.json 的条目取(AIPOS-F134/F154 按实例选凭据唯一口); 同角色凭据绑定了 ≥2 个实例时必填。
  Owner 凭据签码时改 `--connection-json <Owner凭据> --token-role owner`, 去掉 `--actor`。
- 输出只含 `code_id` 与指纹(非敏感), 码与凭据明文永不回显。

### 2. 兑换结果

`--confirm` 后, 目标工位的 `.lybra/` 自发现配置(connection / role / actor / policy)已就位, 后续 loop 操作零手工配置:

**FIX-1: 新机零手工上线**
- 工位目录不需要预先存在, enroll 自动创建工位目录和 .lybra/ 目录
- 只需要 .lybra/ 配置, 不需要队列结构(队列在 gate 侧)

### 3. 验证自发现配置

Enroll 完成后,检查 `.lybra/` 目录:

```bash
ls -la <工位目录>/.lybra/
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

workspace = Path("<工位目录>").expanduser()

# 自动发现 token(不需要 env 变量; AIPOS-F159: 给了角色 = 经按实例选凭据唯一口——给实例只取该实例本角色条目, 无即拒)
token = ConnectionResolver.resolve_token(
    workspace_root=workspace,
    role="executor",
    agent_instance="<执行体实例>",
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
- 会轮换(替换)现有 token, 旧条目标为 retired
- 旧 token 立即失效(如果 gate 重载了配置)

轮换 = 对同一实例原样再跑一次第 1 步的 `enroll-deliver`(先 `--dry-run` 再 `--confirm`), 输出会显示 `rotated: true`。

## 安全特性

1. **Token 明文不跨机传输**
   - 注册码不出签发进程(enroll-deliver 进程内兑换; 远端工位经 ssh stdin 送达), 不进命令行 / 聊天 / 环境变量
   - Token 只在 gate→agent 的响应中传输一次
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
lybra --workspace-root <治理根> \
  roles enroll-list
```

### 吊销 enrollment code

```bash
lybra --workspace-root <治理根> \
  roles enroll-revoke <code_id> \
  --owner-authorization-ref "security-revoke" \
  --reason "Compromised or no longer needed"
```

## 故障排查

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
cd <产品仓>
python3 tools/test_aipos_r2_enroll.py
```

测试覆盖:
- Enrollment code 签发与兑换
- `.lybra/` 配置落位(connection/role/actor)
- ConnectionResolver 自发现
- Token 明文不泄露
- 幂等性(重复 enroll 轮换 token)
- 现有角色零回归
