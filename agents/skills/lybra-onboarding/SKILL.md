---
name: lybra-onboarding
description: "从 0 接新项目全流程指南。Use when the user asks about onboarding a new project, setting up a new project from scratch, or 'how do I start a new Lybra project'. Provides step-by-step guidance for zero-manual-editing onboarding."
---

# lybra-onboarding — 从 0 接新项目全流程(advisor 侧)

你是负责接入新项目的 advisor。本 skill 指导你从项目注册到首卡开跑的完整流程,
**全程零手工编辑文件**(不 mkdir、不 cat > 写配置、不 python 补键)。

> **示例中的尖括号为占位, 以本工位 .lybra/role 与 project.json 为准**(新项目尚无声明时, 以 `lybra onboarding guide` 的实际输出为准)。本 skill 项目无关。

## 触发场景

- 用户说"接入新项目"、"onboard a new project"、"从 0 开始一个项目"
- 用户问"怎么注册新项目"、"怎么生成 enrollment code"
- 首个外部项目接入实录的 6 个缺口(工位目录/凭据 401/.pi 接线/owner_policy_ref/lybra_bin/workspace_root)都已修复(F54/F54-fix1)

## 核心原则

**命令从产品生成,禁硬编码**:本 skill 不写死任何命令文本——你用 `lybra onboarding guide <项目名>`
**从产品拉取最新命令模板**,确保 skill 与产品命令演进同步。

## 工作流(六步,零手工)

### Step 0: 生成项目专属指南

```bash
lybra onboarding guide <项目名> --home-root <治理根> --gate-url <门地址> --code-repo <代码仓>
```
`--home-root`/`--gate-url`/`--code-repo` 均可选(缺省读环境与本机配置)。

这条命令会为指定项目生成完整的六步指南,所有命令参数化(项目名/路径/URL 都自动填充)。
**把输出的每一步命令逐条复制执行**,不要手工改任何参数。

### Step 1: 项目注册(治理根 + project.json)

从 guide 输出的 Step 1 命令复制执行:
```bash
lybra project new <项目名> --home-root <治理根> --actor <你的顾问实例> --code-repo <代码仓>
```

**验证**: `lybra project list` 应显示新项目名。

**失败出口**:
- `PROJECT_EXISTS` → 项目已存在,跳到 Step 2
- gate 连接失败 → 确认 `lybra serve` 已启动

### Step 2: 信封铸造(执行 + 审计各一)

从 guide 输出的 Step 2 命令逐条复制执行(两条,executor 和 auditor):
```bash
lybra envelope mint --policy-id pol_<项目名>_1 --agent-or-role executor --max-tasks 50 --task-mode code \
  --expires-at <到期时间> --decision-summary "New project <项目名>: executor autonomy envelope" --actor owner --json
lybra envelope mint --policy-id pol_<项目名>_audit_1 --agent-or-role auditor --max-tasks 50 --task-mode code \
  --expires-at <到期时间> --decision-summary "New project <项目名>: auditor autonomy envelope" --actor owner --json
```

**验证**: 每条命令输出 JSON 含 `ok: true`。

**失败出口**:
- `policy_id` 冲突 → 换后缀(如 `_v2`)
- 缺 owner 授权 → 加 `--actor owner`

### Step 3: 三角色发码(executor / auditor / advisor)

从 guide 输出的 Step 3 命令逐条复制执行(三条,三角色):
```bash
lybra roles enroll-code --role executor --ttl 86400 --gate-url <门地址> --governance-root <项目根> --reason 'Onboarding <项目名> executor' --json
lybra roles enroll-code --role auditor --ttl 86400 --gate-url <门地址> --governance-root <项目根> --reason 'Onboarding <项目名> auditor' --json
lybra roles enroll-code --role advisor --ttl 86400 --gate-url <门地址> --governance-root <项目根> --reason 'Onboarding <项目名> advisor' --json
```

**验证**: 每条输出 JSON 含 `enrollment_code` 字段。**保存这三个码**供 Step 4 使用(码一次性、有时效, 不贴进聊天/文档)。

**失败出口**:
- `no advisor token` → 检查 connection.json 是否有 advisor token
- `gate 拒绝` → 确认当前角色有 `enroll-code` scope(advisor/owner 才可)

### Step 4: 一条 enroll 配齐(三角色各跑一次)

**在各自工位目录**用 Step 3 的码兑换凭据(三角色各跑一次,换不同 `--code`):
```bash
cd <工位目录>
lybra roles enroll --code <注册码> --workspace <工位目录> --verify
```

**验证**: 命令输出 enroll 成功 + verify 通过;检查 `.lybra/connection.json` 存在且含 `lybra_bin`。

**失败出口**(首个外部项目接入实录的 6 个缺口已由 F54/F54-fix1 修复,如再遇报 bug):
- `401 Unauthorized` → 码过期或已用,重新跑 Step 3
- `.pi/ 接线缺失` → F54 应自动落,如缺失报 bug
- `lybra_bin` 缺失 → F54-fix1 应自动补,如缺失报 bug
- `workspace_root` 写错 → F54-fix1 应校正,如仍有问题报 bug
- `owner_policy_ref` 缺失 → Step 2 信封可能未生效,检查 `status=active`

### Step 5: 起 pi 三步(sync 分发 → --dry-run 稳态 → 起 pi)

在工位目录照 guide 输出的 Step 5 执行。工位件(技能/扩展/章程/schema)唯一来源 = distribution 声明, 由 `lybra sync` 落齐;
`--harness-root` 给工位目录, `--workspace-root` 给本项目治理根(project.json 所在):
```bash
lybra sync --harness-root <工位目录> --workspace-root <项目根>
lybra sync --harness-root <工位目录> --workspace-root <项目根> --dry-run
```
第二条零写入复核稳态: plan 与 prune 皆空(`up-to-date: 0 file(s) to fetch/render`、无 would-prune 行)。然后在工位目录起 `pi`。
工位不敲任何门动词(认领/交回/派审由驱动方经产品完成)。

**失败出口**:
- `pi 找不到` → `npm i -g @earendil-works/pi-coding-agent`
- sync 把工位记为 skipped → `--workspace-root` 须为本项目治理根, 不是工位目录
- `--dry-run` 的 plan/prune 非空 → 再跑一次 sync 后复核; 仍非空 = 报 bug(附 `--dry-run --json` 输出)

### Step 6: 首卡开跑自检(工位自检 → pi 内 /go)

照 guide 输出的 Step 6 执行: 先用产品自检(只读, 缺项逐项点名), 再在 pi 内用无参 `/go` 开工
(`/go` 由分发声明中的 go 扩展注册, 只查询本实例已认领的卡并发开工提示; 首卡由顾问发卡、驱动方经产品认领, 工位不自领):
```bash
lybra onboarding check <项目名> --step 6 --home-root <治理根> --workspace-dir <工位目录>
```

**失败出口**:
- 缺项报错 → 按输出的缺项名逐项修复
- `/go` 提示无已认领卡 → 驱动方尚未认领(正常), 认领后再 `/go`
- `/go` 不是已知命令 → go 扩展未落到工位, 回 Step 5 重跑 sync 并复核稳态

## 诊断工具

如果怀疑某步的前置条件不满足,用:
```bash
lybra onboarding check <项目名> --step <步骤号>   # 步骤号 1-6
```

会告诉你该步需要什么、缺什么、怎么修。

## 与执行体侧的边界

- **lybra-onboarding**(本 skill):从 0 接新项目,advisor 侧用,一次性流程(注册→铸信封→发码→enroll→起 harness→自检)
- **执行体侧**:开工由工位章程 + 无参 `/go` 承担;认领/交回/派审由驱动方经产品完成(`lybra loop`, 见 advisor-commands),执行体零门

本 skill 覆盖"从无到有",执行体侧覆盖"从有到跑"。二者不重复。

## 记住

1. **命令从产品拉**(先跑 `lybra onboarding guide` 拿命令,不要自己拼)
2. **零手工编辑**(不 mkdir、不写文件、不补键;全靠命令)
3. **失败即停报错带路**(每步输出自带下一步引导,不猜不绕)
4. **项目无关**(任何项目名,流程一致)

---

**本 skill 随 distribution.schema `advisor-skills` 条目 copy_tree 原样下发(不渲染);工位本地改动会被 sync 覆盖, 改动须回流母本。**
