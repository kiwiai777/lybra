---
name: lybra-onboarding
description: "从 0 接新项目全流程指南。Use when the user asks about onboarding a new project, setting up a new project from scratch, or 'how do I start a new Lybra project'. Provides step-by-step guidance for zero-manual-editing onboarding."
---

# lybra-onboarding — 从 0 接新项目全流程(advisor 侧)

你是负责接入新项目的 advisor。本 skill 指导你从项目注册到首卡开跑的完整流程,
**全程零手工编辑文件**(不 mkdir、不 cat > 写配置、不 python 补键)。

## 触发场景

- 用户说"接入新项目"、"onboard a new project"、"从 0 开始一个项目"
- 用户问"怎么注册新项目"、"怎么生成 enrollment code"
- chris 实录的 6 个缺口(工位目录/凭据 401/.pi 接线/owner_policy_ref/lybra_bin/workspace_root)都已修复

## 核心原则

**命令从产品生成,禁硬编码**:本 skill 不写死任何命令文本——你用 `lybra onboarding guide <项目名>`
**从产品拉取最新命令模板**,确保 skill 与产品命令演进同步。

## 工作流(六步,零手工)

### Step 0: 生成项目专属指南

```bash
lybra onboarding guide <项目名> [--home-root <治理根>] [--gate-url <门地址>] [--code-repo <代码仓>]
```

这条命令会为指定项目生成完整的六步指南,所有命令参数化(项目名/路径/URL 都自动填充)。
**把输出的每一步命令逐条复制执行**,不要手工改任何参数。

### Step 1: 项目注册(治理根 + project.json)

从 guide 输出的 Step 1 命令复制执行:
```bash
lybra project new <项目名> --home-root <治理根> --actor <你>
```

**验证**: `lybra project list` 应显示新项目名。

**失败出口**:
- `PROJECT_EXISTS` → 项目已存在,跳到 Step 2
- gate 连接失败 → 确认 `lybra serve` 已启动

### Step 2: 信封铸造(执行 + 审计各一)

从 guide 输出的 Step 2 命令逐条复制执行(两条,executor 和 auditor):
```bash
lybra envelope mint --policy-id <pol_项目名_1> --agent-or-role executor ...
lybra envelope mint --policy-id <pol_项目名_audit_1> --agent-or-role auditor ...
```

**验证**: 每条命令输出 JSON 含 `ok: true`。

**失败出口**:
- `policy_id` 冲突 → 换后缀(如 `_v2`)
- 缺 owner 授权 → 加 `--actor owner`

### Step 3: 三角色发码(executor / auditor / advisor)

从 guide 输出的 Step 3 命令逐条复制执行(三条,三角色):
```bash
lybra roles enroll-code --role executor --ttl 86400 --gate-url <门> --governance-root <项目根> --reason 'Onboarding ...' --json
lybra roles enroll-code --role auditor ...
lybra roles enroll-code --role advisor ...
```

**验证**: 每条输出 JSON 含 `enrollment_code` 字段。**保存这三个码**供 Step 4 使用。

**失败出口**:
- `no advisor token` → 检查 connection.json 是否有 advisor token
- `gate 拒绝` → 确认当前角色有 `enroll-code` scope(advisor/owner 才可)

### Step 4: 一条 enroll 配齐(三角色各跑一次)

**在各自工位目录**用 Step 3 的码兑换凭据(三角色各跑一次,换不同 `--code`):
```bash
cd <工位目录>
lybra roles enroll --code <ENROLLMENT_CODE> --workspace <工位目录> --verify
```

**验证**: 命令输出 enroll 成功 + verify 通过;检查 `.lybra/connection.json` 存在且含 `lybra_bin`。

**失败出口**(chris 实录的 6 个缺口已由 F54/F54-fix1 修复,如再遇报 bug):
- `401 Unauthorized` → 码过期或已用,重新跑 Step 3
- `.pi/ 接线缺失` → F54 应自动落,如缺失报 bug
- `lybra_bin` 缺失 → F54-fix1 应自动补,如缺失报 bug
- `workspace_root` 写错 → F54-fix1 应校正,如仍有问题报 bug
- `owner_policy_ref` 缺失 → Step 2 信封可能未生效,检查 `status=active`

### Step 5: 起 pi 三步(进工位 → sync → lybra on)

在工位目录:
```bash
cd <工位目录>
ls -la .pi/  # 确认接线完整(settings.json, extensions/, skills/)
pi           # 起 pi
/lybra sync  # 拉取最新分发(含本 skill)
lybra on     # 进入接活模式
```

**验证**: pi 启动 + sync 无报错 + lybra on 显示任务列表或"暂无可认领"。

**失败出口**:
- `pi 找不到` → `npm i -g @earendil-works/pi-coding-agent`
- `/lybra sync 失败` → 检查 `.lybra/connection.json` 中 `lybra_bin` 指向的文件存在

### Step 6: 首卡开跑自检

```bash
lybra agent launch-check --gate-url <门> --workspace-root <工位目录>
```

**验证**: 自检全绿(ok=true);缺项会逐项点名。

**失败出口**:
- 缺项报错 → 按输出的缺项名逐项修复
- token 无效 → 重跑 Step 4 的 enroll --verify

## 诊断工具

如果怀疑某步的前置条件不满足,用:
```bash
lybra onboarding check <项目名> --step <1-6>
```

会告诉你该步需要什么、缺什么、怎么修。

## 与 lybra-executor skill 的边界

- **lybra-onboarding**(本 skill):从 0 接新项目,advisor 侧用,一次性流程(注册→铸信封→发码→enroll→起 pi→自检)
- **lybra-executor**(已有):executor 接活循环(`lybra on`/`lybra off`),executor 侧用,常驻模式

本 skill 覆盖"从无到有",executor skill 覆盖"从有到跑"。二者不重复。

## 记住

1. **命令从产品拉**(先跑 `lybra onboarding guide` 拿命令,不要自己拼)
2. **零手工编辑**(不 mkdir、不写文件、不补键;全靠命令)
3. **失败即停报错带路**(每步输出自带下一步引导,不猜不绕)
4. **项目无关**(probe-xyz 也好,lybra 也好,流程一致)

---

**本 skill 与 `lybra-fallback`(链条卡住时兜底,如有)走同一分发通道,seed_only 不覆盖定制。**
