---
name: lybra-onboarding
description: "从 0 接新项目全流程指南。Use when the user asks about onboarding a new project, setting up a new project from scratch, or 'how do I start a new Lybra project'. Provides step-by-step guidance for zero-manual-editing onboarding."
---

# lybra-onboarding — 从 0 接新项目全流程(advisor 侧)

你是负责接入新项目的 advisor。本 skill 指导你从项目注册到首卡结案的完整流程,
**全程零手工编辑文件**(不 mkdir 治理树、不 cat > 写配置、不 python 补键)。

> **示例中的尖括号为占位, 以本工位 .lybra/role 与 project.json 为准**(新项目尚无声明时, 以 `lybra onboarding guide` 的实际输出为准)。本 skill 项目无关。

## 触发场景

- 用户说"接入新项目"、"onboard a new project"、"从 0 开始一个项目"
- 用户问"怎么注册新项目"、"怎么拿第一张凭据"、"怎么签信封"

## 核心原则

1. **命令从产品生成, 禁硬编码**: 先跑 `lybra onboarding guide <项目名>`, 把输出的每一步命令逐条原样执行; 本 skill 只讲顺序与判据。
2. **单门 home 根约定**: 项目建在 home 根下(`--home-root` > `LYBRA_HOME_ROOT` > `~/.lybra/config.json` home_root > 缺省 `~/.lybra/projects`), 门只扫描 home 根; guide 会打印解析结果与来源。要换位置只能改 home 根本身, 不登记 home 根外的治理根。
3. **Owner 只动手两条**: guide 第 3 步(签发顾问注册码)与第 4 步(一条命令签三张信封)标为【Owner 动作】, 由 Owner 亲自敲; 其余全部由你执行。
4. **会话目录可与治理根不同**: 你的 Claude Code 会话目录(顾问技能交付落点)与治理根分开; guide 每条命令都显式带治理根, 不依赖 cwd。

## 工作流(九步, 零手工)

### Step 0: 生成项目专属指南

```bash
lybra onboarding guide <项目名> --home-root <home根> --advisor-dir <会话目录> --repo <仓名>=<产品仓> --workspace-dir <工位目录> --auditor-dir <审计工位目录>
```
参数均可选(缺省按 home 根梯、主机名、`~/<项目名>` 等推导, guide 打印每项来源)。

### Step 1–2(你): 建项目 + 声明产品仓

```bash
lybra project new <项目名> --home-root <home根> --actor <你的顾问实例>
lybra governance-commit --governance-root <项目根> --actor <你的顾问实例> --paths project.json --paths governance/decision_log.md --paths stage_archive
lybra project set-repos <项目名> --home-root <home根> --repo <仓名>=<产品仓>
lybra governance-commit --governance-root <项目根> --actor <你的顾问实例> --paths project.json
```
`project new` 同时写首份阶段快照「项目创建」(首次 finalize 不被阶段门拦); `set-repos` 经 project.json repos 声明校验, 多仓须 `--default`。
**每步之后落账**(AIPOS-F94): 照 guide 原样跑该步的 `lybra governance-commit --paths <本步产物>`, 只提交本步产物并推送治理仓——卡与声明只在盘上 = 未成为可追溯真相。治理根须在治理仓(git, 带 origin)内; 不在 = 先 `lybra home git-init --home-root <home根>`, 由 Owner 按其输出配远端并首推。

### Step 3–4(Owner): 顾问注册码 + 三张信封

把 guide 第 3、4 步原样交给 Owner(你不代敲)。形如:
```bash
lybra roles --workspace-root <Owner工作区> --connection-json <Owner凭据> enroll-code --role advisor --instance <你的顾问实例> --governance-root <项目名> --token-role owner --owner-authorization-ref onboarding-<项目名>
lybra envelope mint --confirm --workspace-root <项目根> --connection-json <Owner凭据> --policy-id <信封ID> --agent-or-role <你的顾问实例> --max-tasks 50 --task-mode code --expires-at <到期时间> --decision-summary "onboarding" --actor owner
```
第 4 步实际是一条命令签三张(驱动方 / 执行实例 / 审计实例, `--policy-id`/`--agent-or-role` 成对重复)。输出以门生记录为准(`signed ... wrote 5_tasks/policies/...`)。
**可选 `--launch-harness pi`**(AIPOS-F95): 授权 `lybra loop` 在工位自动拉起 pi(执行体/审计体等待前按声明模板拉起一次, 过程汇总到顾问界面); **缺省 = 手工模式**(Owner 在工位敲 `/go`)。要不要加由 Owner 决定——先问 Owner, 同意才在交给 Owner 的命令里带上这个参数(两种模式见 advisor-commands「两种开工模式」)。

### Step 5(你): 凭码 enroll 到治理根 + 技能交付

```bash
lybra roles enroll --code <注册码> --workspace <项目根> --harness claude-code --harness-dir <会话目录> --verify
lybra sync --harness-root <项目根> --workspace-root <项目根> --dry-run
```
凭据落治理根 `.lybra/`(`lybra loop` 按治理根取驱动方凭据); 顾问技能按 distribution 声明交付到 `<会话目录>/.claude/skills/`, 在会话目录重启 Claude Code 即加载。第二条 plan 为空 = 稳态。

### Step 6–7(你): 工位注册码 → 工位 enroll + sync

```bash
lybra roles --workspace-root <项目根> enroll-code --role executor --instance <执行体实例> --governance-root <项目名> --owner-authorization-ref <信封ID>
lybra roles enroll --code <注册码> --workspace <工位目录> --verify
lybra sync --harness-root <工位目录> --workspace-root <项目根>
lybra sync --harness-root <工位目录> --workspace-root <项目根> --dry-run
```
审计工位同法(`--role auditor`)。enroll 按 Step 4 信封推导 owner_policy_ref; sync 落齐技能 / 扩展 / 章程并补挂缺失的 `.pi` 挂载。

**失败出口**(首个外部项目接入实录的缺口已修, 再遇报 bug):
- `401 Unauthorized` → 码过期或已用, 重跑 Step 6 发新码
- `推导 owner_policy_ref 失败` → Step 4 信封未落或实例不符
- `.pi/ 接线缺失` / `lybra_bin` 缺失 / `workspace_root` 写错 → 重跑 enroll 校正; 仍在 = 报 bug

### Step 8(工位): 自检 → 起 pi → /go

```bash
lybra onboarding check <项目名> --step 8 --home-root <home根> --workspace-dir <工位目录>
```
然后在工位目录起 `pi`, pi 内无参 `/go`(go 扩展由分发声明注册, 只查本实例已认领的卡)。每张卡 Owner 只在工位敲 `/go`, 不贴卡号、不报裁决。 若 Step 4 信封带了 `--launch-harness pi`, 则 `lybra loop` 会在本工位自动拉起 pi(工位位置读 enroll 落地登记的 host/目录), Owner 无需敲 `/go`; 手工 `/go` 始终可用。

### Step 9(你): 首卡

```bash
lybra --workspace-root <项目根> draft create --from-json <卡稿JSON>
lybra --workspace-root <项目根> draft publish --path <草稿路径>
lybra loop --task-id <卡ID> --workspace-root <项目根> --envelope <信封ID>
```
`lybra loop` 一段式认领(建工作树)→ 等交回 → 派审 → 等审计报告 → 裁决 → finalize → 结案 → N6 落账(自动 `lybra governance-commit --task-id <卡ID>`, 本卡与审计卡的队列文件 / 记录等精确提交并推送); exit 3 = 工位尚未交产物, `/go` 后重跑同一条; exit 2 落账拒 = 按原文处理(如他人暂存)后重跑。查漏: `lybra state lint --workspace-root <项目根>` 的 GOVERNANCE_UNCOMMITTED。

## 诊断工具

```bash
lybra onboarding check <项目名> --step <步骤号> --home-root <home根>
```
步骤号与 guide 同一编号(1–9), 输出缺什么、怎么修。

## 与执行体侧的边界

- **lybra-onboarding**(本 skill): 从 0 接新项目, advisor 侧用, 一次性流程
- **执行体侧**: 开工由工位章程 + 无参 `/go` 承担; 认领 / 交回 / 派审由驱动方经产品完成(`lybra loop`, 见 advisor-commands), 执行体零门

## 记住

1. **命令从产品拉**(先跑 `lybra onboarding guide`, 不要自己拼)
2. **零手工编辑**(全靠命令; 治理树、凭据、信封、技能都由产品写)
3. **Owner 两条**(第 3、4 步交 Owner 敲, 其余你做)
4. **失败即停报错带路**(每步输出自带下一步, 不猜不绕)

---

**本 skill 随 distribution.schema `advisor-skills` 条目 copy_tree 原样下发(不渲染; AIPOS-F92 起落顾问会话目录 .claude/skills/);工位本地改动会被 sync 覆盖, 改动须回流母本。**
