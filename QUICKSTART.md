# Lybra Quick Start — 从零到第一张卡闭环

本指南带你从安装到完成第一张任务卡的完整闭环，体验 Lybra 的 **gate-based accountability loop**。

---

## 前置条件

- **Node.js 18+** 和 **Python 3** 在 PATH 上
- **一个文本编辑器** 或 IDE
- **终端访问权限**

---

## 第一步：安装 Lybra

```bash
# 安装 gate core（npm 发布，Node.js + Python 3 零依赖运行正确）
npm install -g lybra

# 安装 TUI 依赖（Textual 在 PyPI 上，lybra 本身仅在 npm）
pip install "textual>=4.0"

# 验证安装
lybra --version
```

---

## 第二步：初始化工作区

```bash
# 初始化工作区（使用 blank 模板）
# 默认创建在 ~/.lybra/workspaces/<project-id>/
lybra init --project-id my_project --template blank

# 或者显式指定路径：
# lybra init ~/my-lybra-workspace --project-id my_project --template blank

# 你会看到：
# ✅ 生成 governance/advisor-charter.md（顾问接入包）
# ✅ 生成 governance/AGENTS.md（executor/auditor 角色说明）
# ✅ 生成 5_tasks/drafts/example-task.md（示例任务卡）
# ✅ 打印三步指引
```

初始化后，工作区结构如下：

```
~/.lybra/workspaces/my_project/
├── .lybra/
│   └── config.json                  # 工作区配置
├── governance/
│   ├── advisor-charter.md           # 顾问接入包（置顶铁律 + 六查 + governance_refs）
│   └── AGENTS.md                    # Executor/Auditor 角色说明
├── 2_projects/my_project/
│   ├── README.md                    # 项目概述
│   ├── roadmap.md                   # 路线图
│   ├── decision_log.md              # 决策日志
│   └── project_status.md            # 项目状态
├── 5_tasks/
│   ├── drafts/
│   │   └── example-task.md          # 示例任务卡
│   ├── queue/
│   │   ├── pending/                 # 待认领
│   │   ├── claimed/                 # 已认领
│   │   ├── blocked/                 # 受阻
│   │   └── completed/               # 已完成
│   └── records/                     # 执行记录（gate 写入）
└── README.md
```

---

## 第三步：启动 Gate

Gate 是 Lybra 的核心服务，负责：
- 任务队列的 I/O 控制
- Claim/Return/Audit 流程管理
- Controlled execute 的 owner 确认闸门
- Agent 身份认证（opaque agent_instance ID）

在**终端 1**中启动 gate：

```bash
cd ~/.lybra/workspaces/my_project
lybra serve --workspace-root .

# 你会看到：
# 🔒 Gate started at http://127.0.0.1:7118
# 🔑 Owner token: owner_abc123...
# 🔑 Advisor token: advisor_xyz456...
# 🔑 Executor token: exec_def789...
# 🔑 Auditor token: audit_ghi012...
```

**保持 gate 运行**，不要关闭此终端。记下 **advisor token**（下一步需要）。

---

## 第四步：打开看板

在**终端 2**中打开看板：

```bash
lybra board open --workspace-root ~/.lybra/workspaces/my_project

# 浏览器自动打开 http://127.0.0.1:7117
# 如需手动打开，运行：lybra board serve --workspace-root ~/.lybra/workspaces/my_project
```

**首次访问空工作区**，看板会显示 **"三步开始"向导**：

- ① **连接你的顾问**：一键复制定制接入提示词
- ② **发布第一张卡**：示例卡 + 发布命令一键复制
- ③ **看它流经门**：链路示意（发布 → 认领 → 交付 → 审计 → 收编）

发布任务后，向导自动让位给任务中心。

---

## 第五步：连接顾问 Agent

你可以使用任何支持 MCP 或文件系统操作的 agent 作为顾问（Claude Desktop、Codex、Cursor、或自建 agent）。

### 方法 A：使用看板的一键接入提示词（推荐）

1. 在看板的"三步开始"向导中，点击 **"① 连接你的顾问"** 下的 **"📋 一键复制接入提示词"**
2. 粘贴到你的 agent（例如 Claude Desktop 的对话框）
3. Agent 读取提示词后，它会：
   - 知道工作区路径、gate URL、charter 位置
   - 理解自己的角色边界（只读治理仓、起草不发布、凭据只按名引用）
   - 可以开始查看状态、起草任务卡

### 方法 B：手动配置（适用于支持 MCP 的 agent）

如果你的 agent 支持 MCP，可以直接配置连接：

```json
{
  "mcpServers": {
    "lybra-advisor": {
      "url": "http://127.0.0.1:7118",
      "headers": {
        "Authorization": "Bearer advisor_xyz456..."
      }
    }
  }
}
```

然后告诉 agent：

```
你是 my_project 工作区的顾问。
工作区路径：~/.lybra/workspaces/my_project
Charter：~/.lybra/workspaces/my_project/governance/advisor-charter.md
请阅读 charter 了解你的职责和红线。
```

---

## 第六步：发布示例任务卡

现在你的顾问已连接，可以发布第一张任务卡。

### 方法 A：使用看板的一键命令（推荐）

1. 在看板的"三步开始"向导中，点击 **"② 发布第一张卡"** 下的 **"📋 一键复制发布命令"**
2. 在**终端 1**（gate 所在终端）或**终端 3**中运行复制的命令：

```bash
lybra draft publish ~/.lybra/workspaces/my_project/5_tasks/drafts/example-task.md
```

### 方法 B：通过顾问 agent 发布

告诉你的顾问 agent：

```
请帮我发布示例任务卡：5_tasks/drafts/example-task.md
```

Agent 会读取草稿，验证格式，然后建议你运行发布命令（由 Owner 确认）。

### 发布后的状态

发布成功后，任务卡会：
- 从 `5_tasks/drafts/` 移动到 `5_tasks/queue/pending/`
- 分配一个 `task_id`（例如 `EXAMPLE-001`）
- 状态变为 `pending`，等待 executor 认领

刷新看板，你会看到：
- **"三步开始"向导消失**
- **任务中心显示**，展示 `EXAMPLE-001` 卡片，状态为"已发布"

---

## 第七步：配置 Executor

Executor 负责认领任务、在卡声明的车道内独立执行、如实返回。

### 方法 A：使用 Lybra 的 agent watch（简单模式）

如果你有支持文件系统操作的 agent，可以用 `agent watch` 让它自动监听任务队列：

在**终端 3**中：

```bash
# 无 gate 模式：agent 自行监听文件系统变化
lybra agent watch --workspace-root ~/.lybra/workspaces/my_project --timeout 30

# 有 gate 模式：通过 gate 拉取可认领任务（需 executor token）
# lybra agent watch --gate-url http://127.0.0.1:7118 --token exec_def789... --timeout 30
```

Agent watch 会：
- 每秒轮询 `5_tasks/queue/**` 和 `5_tasks/records/**`
- 检测到变化时打印 JSON 并退出（exit 0）
- 超时无变化时静默退出（exit 2）

### 方法 B：手动认领（Owner 亲自执行，用于演示）

如果你想亲自完成示例任务（验证工作区基础设施），直接在**终端 3**中：

```bash
cd ~/.lybra/workspaces/my_project

# 认领任务
lybra claim EXAMPLE-001

# 查看任务详情
cat 5_tasks/queue/claimed/EXAMPLE-001.md

# 执行任务（按卡内要求：检查目录结构，输出验证报告）
# 示例：创建验证报告
mkdir -p docs
cat > docs/workspace-verification-report.md << 'EOF'
# Workspace Verification Report

**Generated:** $(date)

## Directory Structure

- ✅ `2_projects/my_project/` — exists
- ✅ `5_tasks/queue/` — exists (pending/claimed/blocked/completed)
- ✅ `5_tasks/drafts/` — exists
- ✅ `5_tasks/records/` — exists
- ✅ `governance/` — exists (advisor-charter.md, AGENTS.md)

## Configuration

- ✅ `.lybra/config.json` — exists and valid JSON

## Summary

All core directories and configuration files are present and accessible.
Workspace is ready for use.
EOF

# 返回任务（如实汇报）
lybra return EXAMPLE-001 \
  --outcome completed \
  --summary "验证报告已生成：docs/workspace-verification-report.md" \
  --details "检查了所有核心目录和配置文件，均存在且可访问。报告包含清单和状态。"
```

返回后，任务状态变为 `delivered`，等待 auditor 审计。

---

## 第八步：审计（Auditor）

Auditor 负责审计 executor 的交付产出，确保符合任务卡的验收断言。

### 方法 A：Owner 亲自审计（演示模式）

在**终端 3**中：

```bash
# 查看返回的任务
lybra records

# 审计任务（验证报告是否符合要求）
cat docs/workspace-verification-report.md

# 提交审计结论（通过）
lybra audit EXAMPLE-001 \
  --verdict pass \
  --summary "验证报告符合验收断言：所有核心目录存在，报告清晰。" \
  --details "S1-S4 全部满足。零回归。"
```

审计通过后，任务状态变为 `closed`，任务卡移动到 `5_tasks/queue/completed/`。

### 方法 B：独立 Auditor Agent

配置一个独立的 auditor agent（与 executor 不同），使用 auditor token 连接 gate，领取审计任务并提交结论。

---

## 第九步：查看真相视图

刷新看板，你会看到：

- **任务中心**展示 `EXAMPLE-001` 的完整生命周期：
  - 阶段：已发布 → 执行中 → 已交付 → 审计中 → 已闭环
  - 每轮摘要时间线（claim/return/audit 的时间戳和参与者）
  - 动态流（按记录时间倒序）

在**终端**中查看 Owner 真相视图（record-derived）：

```bash
lybra owner-truth

# 输出：
# - 真实阶段统计（已发布/执行中/已闭环）
# - 每任务的每轮摘要
# - 动态流（claim/return/audit 事件）
```

---

## 第十步：继续前行

🎉 恭喜！你已完成第一张任务卡的闭环。

### 下一步

1. **起草真实任务**：让顾问 agent 按六查流程起草实际需求的任务卡
2. **配置产品仓**：任务卡的车道指向外部产品代码仓库（`~/my-product-repo`）
3. **添加里程碑**：在 `2_projects/my_project/project-map.md` 定义项目里程碑
4. **设置 CI/CD**：集成 `lybra validate` 到你的 CI pipeline
5. **多工作区**：在看板的 `.board_config.json` 添加多个工作区

### 核心概念回顾

- **Gate = 真相源**：所有状态改变通过 gate 记录到文件，不依赖内存
- **Drafter ≠ Confirmer ≠ Executor**：角色分离是可问责的前提
- **Files are truth**：任务卡、records、审计结论都在文件中，可审计、可复现
- **Owner 掌握闸门**：controlled_execute 需 owner_verify 的操作必须 Owner 亲自确认

### 阅读更多

- **governance/advisor-charter.md** — 顾问工作方式和红线
- **governance/AGENTS.md** — Executor/Auditor 角色说明
- **README.md** — Lybra 的设计原则和架构
- **docs/v1_release_macos_runbook.md** — v1.0 发布流程（参考）

---

## 跨机接入：顾问在另一台机器

前面的示例假设顾问 agent、gate、工作区都在同一台机器上。实际使用中，顾问 agent 常跑在开发机，gate 和工作区在服务器或远程主机。跨机接入有两种方式：

### 方式 A：零安装 MCP 直连（推荐）

顾问 agent 无需安装 Lybra CLI，直接通过 HTTP MCP 连接远程 gate。

#### 1) 服务端：启动 gate 并绑定网络接口

在**服务器**（工作区所在机器）上：

```bash
cd ~/.lybra/workspaces/my_project

# 绑定到所有接口，允许远程访问
lybra serve --workspace-root . --mcp-host 0.0.0.0

# 如果需要指定广播地址（例如服务器公网 IP 或内网 IP）：
# lybra serve --workspace-root . --mcp-host 0.0.0.0 --mcp-advertise http://192.168.1.100:7118

# Gate 会输出：
# 🔒 Gate started at http://0.0.0.0:7118
# 🌐 Advertised as: http://192.168.1.100:7118
# 🔑 Advisor token: advisor_xyz456...
```

**安全提示**：
- 确保 token 不泄露（通过安全渠道传给顾问 agent）
- 考虑使用 VPN 或 SSH 隧道，避免直接暴露 gate 到公网
- 如需防火墙，开放 MCP 端口（默认 7118）

#### 2) 客户端：配置顾问 agent 连接远程 gate

在**开发机**（顾问 agent 所在机器）上，配置 agent 的 MCP 服务器：

**Claude Desktop/Cline**（编辑 `claude_desktop_config.json` 或 Cline 设置）：

```json
{
  "mcpServers": {
    "lybra-advisor": {
      "url": "http://192.168.1.100:7118/mcp",
      "headers": {
        "Authorization": "Bearer advisor_xyz456..."
      }
    }
  }
}
```

**Claude Code 命令行**：

```bash
claude mcp add lybra --transport http http://192.168.1.100:7118/mcp --header "Authorization: Bearer advisor_xyz456..."
```

**Pi/Codex/其他 HTTP MCP harness**：

```json
{
  "url": "http://192.168.1.100:7118/mcp",
  "headers": {
    "Authorization": "Bearer advisor_xyz456..."
  }
}
```

配置后，顾问 agent 可通过 MCP 工具调用 gate（查看队列、起草任务卡、建议发布）。

#### 3) 验证连接

告诉顾问 agent：

```
你是 my_project 工作区的顾问。
Gate URL：http://192.168.1.100:7118
Charter：~/.lybra/workspaces/my_project/governance/advisor-charter.md
请使用 lybra MCP 工具查看当前队列状态。
```

Agent 应能调用 `lybra_queue` 工具并返回队列摘要。

---

### 方式 B：CLI 自举（完整功能）

如果顾问 agent 需要使用 `lybra agent watch` 监听队列变化（唤醒泵），或需要在本地执行 CLI 命令，可通过 gate 自举安装 CLI。

#### 1) 假设条件

- Gate 所在机器已暴露 git/npm/pip 源（或 agent 可访问公网）
- Agent 有权限在本地机器执行 `npm install -g` 和 `pip install`

#### 2) Agent 自举安装

告诉顾问 agent：

```
请安装 Lybra CLI（从 npm）：

npm install -g lybra
pip install "textual>=4.0"
lybra --version

安装后，使用跨机模式监听队列变化：
lybra agent watch --gate-url http://192.168.1.100:7118 --token advisor_xyz456... --timeout 30
```

Agent 会：
1. 执行安装命令（如有权限）
2. 运行 `agent watch` 跨机模式，通过 gate API 拉取队列和记录变化
3. 检测到变化时返回 JSON 摘要，适合作为唤醒泵

---

### 安全注意事项

**Token 管理**：
- Advisor token 只给顾问 agent
- Executor/Auditor token 只给对应角色的 agent
- Owner token 永远不给 agent（Owner 亲自持有）

**网络隔离**：
- 同机：`--mcp-host 127.0.0.1`（默认，仅本机访问）
- 跨机（内网）：`--mcp-host 0.0.0.0 --mcp-advertise http://<内网IP>:7118`
- 跨机（公网）：强烈建议使用 VPN 或 SSH 隧道，避免直接暴露

**防火墙**：
- 如需开放端口，仅允许可信 IP 访问 MCP 端口（7118）和看板端口（7117）

---

### 同机 vs 跨机对比

| 维度             | 同机（本地）                              | 跨机（远程）                                  |
|------------------|-------------------------------------------|-----------------------------------------------|
| **Gate 启动**    | `lybra serve --workspace-root .`          | `lybra serve --workspace-root . --mcp-host 0.0.0.0 --mcp-advertise http://<IP>:7118` |
| **Agent 接入**   | 文件系统直接访问 或 MCP `127.0.0.1:7118`  | MCP `http://<服务器IP>:7118/mcp` + token      |
| **CLI 需求**     | 可选（MCP 直连即可基础功能）              | 可选（零安装 MCP 直连）或 CLI 自举（完整功能）|
| **agent watch**  | `--workspace-root <本地路径>`             | `--gate-url <远程URL> --token <TOKEN>`        |
| **安全考量**     | 本机隔离，无网络暴露                      | 需 token 管理 + 网络隔离（VPN/防火墙）        |

---

## 常见问题

### Q1: Gate 启动失败，提示端口被占用？

A: 修改默认端口：

```bash
lybra serve --workspace-root . --mcp-port 7119 --board-port 7118
```

### Q2: 看板显示"Workspace error: does not contain 5_tasks/queue"？

A: 确保在 `lybra init` 初始化的工作区目录中运行 `lybra serve`。

### Q3: Agent 无法读取 charter？

A: 检查 agent 是否有文件系统访问权限，或手动将 `governance/advisor-charter.md` 的内容复制给 agent。

### Q4: 示例任务卡的车道是什么？

A: 示例任务只读工作区（治理仓），只写 `docs/workspace-verification-report.md`。真实任务的车道通常是外部产品仓路径。

### Q5: 如何切换语言？

A: 看板右上角有语言切换器（中文/English）。CLI 默认使用系统语言。

### Q6: 如何备份工作区？

A: 工作区就是普通目录，直接 `git init` 并 commit 即可（`.lybra/config.json` 和 `5_tasks/` 都应纳入版本控制）。

---

**开始你的 Lybra 之旅吧！** 🚀

如有问题或反馈，欢迎提交 issue 或查阅文档。
