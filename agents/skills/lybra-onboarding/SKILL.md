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
- 用户问"怎么注册新项目"、"怎么拿第一张凭据"、"怎么签项目授权"、"执行体 / 审计体怎么分工"

## 核心原则

1. **命令从产品生成, 禁硬编码**: 先跑 `lybra onboarding guide <项目名>`, 把输出的每一步命令逐条原样执行; 本 skill 只讲顺序与判据。
2. **单门 home 根约定**: 项目建在 home 根下(`--home-root` > `LYBRA_HOME_ROOT` > `~/.lybra/config.json` home_root > 缺省 `~/.lybra/projects`), 门只扫描 home 根; guide 会打印解析结果与来源。要换位置只能改 home 根本身, 不登记 home 根外的治理根。
3. **Owner 只动手两条**: guide 第 3 步(签项目常设授权)与第 4 步(签码接入顾问到治理根, enroll-deliver, 码不出进程)标为【Owner 动作】, 由 Owner 亲自敲; 其余全部由你执行。
4. **会话目录可与治理根不同**: 你的 Claude Code 会话目录(顾问技能交付落点)与治理根分开; guide 每条命令都显式带治理根, 不依赖 cwd。
5. **执行分工成步**(AIPOS-F143 / F256 / F262): 代码卡 = 子 agent 执行者(你在会话里派生), 非代码卡 = pi 执行工位, 审计 = 独立 pi 审计工位; guide 依次接入三者再 `project set-execution`, 每条接入命令显式带 `--executor-mode`。
6. **不按本机猜**: Owner 凭据(门的中央凭据库)由 guide 从门注册表的已声明来源推导, 推不出就明说并请你给 `--owner-connection-json`; 工位缺省目录读声明 project.json `paths.workstations_root`(缺省 `~/lybra-workstations/<项目>/<角色>`), guide 原文列出实际目录与来源。

## 工作流(十一步, 零手工)

### Step 0: 生成项目专属指南

```bash
lybra onboarding guide <项目名> --home-root <home根> --advisor-dir <会话目录> --repo <仓名>=<产品仓> --owner-connection-json <Owner凭据>
```
参数均可选(缺省按 home 根梯、主机名、工位根声明等推导, guide 打印每项来源)。头部几行先看: Owner 凭据(推不出 = ⚠ 行, 照提示补参数重生成)、项目常设授权 ID、工位目录、执行分工。工位目录要换: `--workspace-dir <工位目录> --auditor-dir <审计工位目录>`(两者须不同, 且不在 home 根 / 治理根内)。

### Step 1–2(你): 建项目 + 声明产品仓

```bash
lybra project new <项目名> --home-root <home根> --actor <你的顾问实例>
lybra governance-commit --governance-root <项目根> --actor <你的顾问实例> --paths project.json --paths governance/decision_log.md --paths stage_archive
lybra project set-repos <项目名> --home-root <home根> --repo <仓名>=<产品仓> --dry-run
lybra project set-repos <项目名> --home-root <home根> --repo <仓名>=<产品仓> --actor <你的顾问实例> --confirm
```
`project new` 同时写首份阶段快照「项目创建」(首次 finalize 不被阶段门拦); `set-repos` 经 project.json repos 声明校验, 多仓须 `--default`。
写 project.json 的命令(`set-repos` / `set-repo` / `set-paths` / `set-workstation` / `set-execution`)一律两阶段(AIPOS-F125): 先 `--dry-run` 预演(打印 diff 与校验, 零写入), 确认后 `--confirm` 才写。
**落账**(AIPOS-F94 / F158 / F159): 卡与声明只在盘上 = 未成为可追溯真相, 须精确提交并推送治理仓。**写治理真相的命令 `--confirm` 写后即自动落账**——project 族写命令(提交身份 `--actor`)、`project grant … --confirm`、`migrate … --confirm`; 输出 `落账: verdict=PASS committed=True pushed=True` 即完成, **不要再手跑 `governance-commit`**。落账失败(exit 3)= 写入已生效, 照输出原文给出的补账命令跑一次即可, 勿重复执行写命令。只有 `project new`(建治理根本身, 不自动落账)之后照 guide 第 1 步原样跑那条 `lybra governance-commit --paths …`。治理根须在治理仓(git, 带 origin)内; 不在 = 先 `lybra home git-init --home-root <home根>`, 由 Owner 按其输出配远端并首推。

### Step 3–4(Owner): 签项目常设授权 + 签码接入顾问(enroll-deliver)

把 guide 第 3、4 步原样交给 Owner(你不代敲)。**项目常设授权**(AIPOS-F224): 一份签名覆盖本项目车道 × 任务类型 × 驱动方 / 主机 / 拉起 / 到期 + 总卡数, 范围内 `lybra loop` 自动找到它, 不再逐卡、逐实例签信封; 授权先于接入(工位接入预检须有覆盖它的授权)。形如(不带 `--confirm` = 本地预演零写入):
```bash
lybra project grant sign <项目名> --home-root <home根> --grant-lane <仓名> --task-mode code --task-mode docs --driver <你的顾问实例> --host <主机> --launch-harness pi --max-tasks 50 --expires-at <到期时间> --connection-json <Owner凭据>
lybra project grant sign <项目名> --home-root <home根> --grant-lane <仓名> --task-mode code --task-mode docs --driver <你的顾问实例> --host <主机> --launch-harness pi --max-tasks 50 --expires-at <到期时间> --connection-json <Owner凭据> --confirm
lybra project grant show <项目名> --home-root <home根>
lybra roles --workspace-root <Owner工作区> --connection-json <Owner凭据> enroll-deliver --role advisor --instance <你的顾问实例> --governance-root <项目根> --workspace <项目根> --harness claude-code --harness-dir <会话目录> --token-role owner --owner-authorization-ref <授权ID> --dry-run
lybra roles --workspace-root <Owner工作区> --connection-json <Owner凭据> enroll-deliver --role advisor --instance <你的顾问实例> --governance-root <项目根> --workspace <项目根> --harness claude-code --harness-dir <会话目录> --token-role owner --owner-authorization-ref <授权ID> --confirm
```
guide 打印的授权命令已按本项目填好车道(第 2 步仓名)、全部业务任务类型(审计卡按被审卡判定)、驱动方(你)、主机(各实例名主机段)。之后所有接入命令的 `--owner-authorization-ref` 一律 = 该授权 ID(`lybra project grant show` 可查)。已有授权的项目, guide 第 3 步提示「不必重签」并给 `grant extend` / `grant show`; 仍用旧信封的项目提示 `lybra project grant draft <项目名> --from-envelopes`(预演合并为项目授权)。用到 80% 或到期前 3 天 = 告知(`lybra loop status` / `lybra brief` / Owner 收件箱各一行, 附扩大命令); 越界 = loop 停 `authorization_out_of_bound`, 收件箱选「扩大项目授权」。
**`--launch-harness pi`**(AIPOS-F95): 授权 `lybra loop` 在工位自动拉起 pi(执行体 / 审计体等待前按声明模板拉起一次, 过程汇总到顾问界面); 授权不带它时缺省 = 手工模式(Owner 在工位敲 `/go`)。guide 打印的授权命令已带; 要不要保留由 Owner 决定——先问 Owner(两种模式见 advisor-commands「两种开工模式」)。
Step 4 Codex 会话: guide 加 `--advisor-harness codex` 生成 `--harness codex`(会话在他机加 `--advisor-host`, guide 生成 `--harness-host <会话所在机>`)。Claude Code 会话也可在他机(AIPOS-F243): guide 加 `--advisor-host <会话所在机> --advisor-dir <该机上的会话目录>`, 第 5 步给开局在会话目录落盘章程与技能的两条命令。

### Step 5(你): 顾问就位复核

```bash
lybra onboarding check <项目名> --step 5 --home-root <home根>
lybra sync --harness-root <项目根> --workspace-root <项目根> --dry-run
```
凭据已由 Step 4 落治理根 `.lybra/`(`lybra loop` 按治理根取驱动方凭据); 顾问件按 distribution 声明交付到会话目录: Claude Code = 技能 `.claude/skills/` + 顾问章程 `.claude/rules/lybra-advisor.md`, Codex = 顾问章程 `AGENTS.md`(会话目录在本机时; 他机会话本机无落点不落盘, 改为开局拉取: `lybra sync` 的 pull 行给出 `ssh <治理根主机> 'cd <治理根> && lybra charter --role advisor --instance <实例>'`, AIPOS-F138; Claude Code 他机会话的 pull 行另给章程落盘命令(落 `.claude/rules/lybra-advisor.md`)与技能命令 `ssh <治理根主机> 'cd <治理根> && lybra skills --role advisor --instance <实例>' | tar -xf -`(落 `.claude/skills/`), AIPOS-F243)。在会话目录重启会话即加载。sync 的 plan 为空 = 稳态; 会话目录里已有同名非 Lybra 文件 = 拒不覆盖(移走或换目录)。

### Step 6–8(你): 按执行分工接入三实例(enroll-deliver)

```bash
lybra roles --workspace-root <项目根> enroll-deliver --role auditor --instance <审计体实例> --governance-root <项目根> --workspace <审计工位目录> --executor-mode workstation --actor <你的顾问实例> --owner-authorization-ref <授权ID> --dry-run
lybra roles --workspace-root <项目根> enroll-deliver --role executor --instance <执行体实例> --governance-root <项目根> --workspace <工位目录> --executor-mode workstation --actor <你的顾问实例> --owner-authorization-ref <授权ID> --dry-run
lybra roles --workspace-root <项目根> enroll-deliver --role executor --instance <子agent执行实例> --governance-root <项目根> --workspace <项目根> --executor-mode subagent --harness claude-code --actor <你的顾问实例> --owner-authorization-ref <授权ID> --dry-run
```
每条先 `--dry-run` 预演(角色 / 实例 / 治理根 / 落点 / 覆盖授权, 不签码零写入), 无误再同参改 `--confirm`。第 6 步 = 审计工位(独立 pi), 第 7 步 = pi 执行工位(非代码卡), 第 8 步 = 子 agent 执行者(代码卡; 凭据落治理根, `--harness` = 你的会话 kind, 无工位、不 sync、不起 pi)。子 agent 执行者与 pi 执行工位是两个不同实例(同机时 guide 给 pi 执行工位主机段加 `-pi`); 一个目录只登记给一个实例(guide 给的工位目录互不相同, 自己指定目录也须不同)。`--actor` = 签发实例(你自己, 按你在治理根连接文件的凭据签码; 同一角色接入多个顾问实例时必填, AIPOS-F157)。注册码只在本进程内(不打印、不落盘、不进 argv); 工位 enroll 按项目授权推导 owner_policy_ref(工位实例名主机段须在授权主机内)。

**失败出口**(首个外部项目接入实录的缺口已修, 再遇报 bug):
- `ENROLL_ENVELOPE_MISSING` / `推导 owner_policy_ref 失败` → 第 3 步项目授权未签或不覆盖该实例(驱动方 / 主机); `lybra project grant show` 核对(预检拒 = 未签码、未改凭据)
- `401 Unauthorized` → 门拒绝凭据或码(发码门 ≠ 本门); 核对 connection.json mcp.rpc_url 后重跑该步(失败时码已吊销)
- `.pi/ 接线缺失` / `lybra_bin` 缺失 / `workspace_root` 写错 → 重跑该接入步校正; 仍在 = 报 bug

### Step 9(你): 声明执行分工

```bash
lybra project set-execution --workspace-root <项目根> --subagent-executor <子agent执行实例> --pi-executor <执行体实例> --auditor <审计体实例> --dry-run
lybra project set-execution --workspace-root <项目根> --subagent-executor <子agent执行实例> --pi-executor <执行体实例> --auditor <审计体实例> --actor <你的顾问实例> --confirm
lybra roles --workspace-root <项目根> list
```
产品按卡类型强制分工(AIPOS-F256 / F260, 不可按项目选配): 代码卡 = 子 agent(你派工); 非代码卡(docs / governance / config / validation) = pi 执行工位(loop 按授权拉起); 审计 = pi 审计工位。不带退役旗标 `--pi-allowed-task-mode`。`roles list` 三实例 ✓ 且末行「执行分工 … pi 执行工位 = <执行体实例>」= 已生效。未声明 pi 执行工位 = 非代码卡起草即拒 `EXECUTOR_PI_UNDECLARED`(出口同本步)。过渡在途: 新分工生效前已认领的非代码卡按原执行方做完; 待认领与新卡按新分工, 违例按拒因给的出口改派。

### Step 10(你): 工位 sync + 稳态复核 + 自检

```bash
lybra sync --harness-root <工位目录> --workspace-root <项目根>
lybra sync --harness-root <工位目录> --workspace-root <项目根> --dry-run
lybra onboarding check <项目名> --step 10 --home-root <home根> --workspace-dir <工位目录>
lybra project workstation-plan <项目名> --home-root <home根>
```
审计工位同法(`<审计工位目录>`)。sync 落齐技能 / 扩展 / 章程并补挂缺失的 `.pi` 挂载; 自检逐项点名缺项。工位章程由产品渲染(母本 + 项目声明, 不手写): 之后 `lybra project check-workstation` 的「章程新鲜度」项核它是否最新, `lybra loop` 拉起 pi 前也自动核, 过期先 sync 该工位再拉起(AIPOS-F164; 项目可在 project.json 声明 `"charter_auto_sync": false` 关闭自动 sync)。

### Step 11(你): 首卡

```bash
lybra --workspace-root <项目根> draft create --from-json <卡稿JSON>
lybra --workspace-root <项目根> draft publish --path <草稿路径>
lybra loop --task-id <卡ID> --workspace-root <项目根>
lybra --workspace-root <项目根> kickoff --task-id <卡ID>
```
发卡前 guide 先给卡号形状声明(card-policy, 值经 Owner 确认)。`lybra loop` 自动找到项目授权(不带信封), 一段式认领(建工作树)→ 等交回 → 派审 → 等审计报告 → 裁决 → finalize → 结案 → N6 落账(自动 `lybra governance-commit --task-id <卡ID>`)。**代码卡**: loop 提示派子 agent 时取 `lybra kickoff` 自包含开工稿, 在你的会话里派生子 agent 照稿执行(子 agent 写完 RETURN 即停)。**非代码卡**: loop 按授权在 pi 执行工位拉起 pi; 授权未放行拉起时 Owner 在工位目录起 `pi`, pi 内无参 `/go`(go 扩展由分发声明注册, 只查本实例已认领的卡; 不贴卡号、不报裁决)。exit 3 = 执行体尚未交产物, 确认已派出 / 已拉起后重跑同一条; exit 2 落账拒 = 按原文处理(如他人暂存)后重跑。
跟进(loop 在后台跑时):
```bash
lybra loop status --task-id <卡ID> --workspace-root <项目根> --wait 540
```
按输出的顾问下一动作处理(怎么循环见顾问章程「持续推进守则」)。查漏: `lybra state lint --workspace-root <项目根>` 的 GOVERNANCE_UNCOMMITTED。

### 按需声明(只列, 不强制)

guide 末尾「按需声明」一节逐项给用途、何时需要与预演命令(确认 = 同参改 `--confirm`): 卡号形状(card-policy)、合并后回归契约(set-test-contract)、生产发布目标(release-target; 之后生产发布只经 `lybra release plan → apply → verify`)、pi 模型(set-model)、看板顾问卡(project headline)。照 guide 原文跑, 值由 Owner / 你按项目实况定。

## 诊断工具

```bash
lybra onboarding check <项目名> --step <步骤号> --home-root <home根>
```
步骤号与 guide 同一编号(1–11), 输出缺什么、怎么修。

## 与执行体侧的边界

- **lybra-onboarding**(本 skill): 从 0 接新项目, advisor 侧用, 一次性流程
- **执行体侧**: 代码卡子 agent 照 `lybra kickoff` 开工稿执行; pi 工位开工由工位章程 + loop 拉起或无参 `/go` 承担; 认领 / 交回 / 派审由驱动方经产品完成(`lybra loop`, 见 advisor-commands), 执行体零门

## 记住

1. **命令从产品拉**(先跑 `lybra onboarding guide`, 不要自己拼)
2. **零手工编辑**(全靠命令; 治理树、凭据、授权、技能都由产品写)
3. **Owner 两条**(第 3 步项目授权、第 4 步顾问接入交 Owner 敲, 其余你做)
4. **失败即停报错带路**(每步输出自带下一步, 不猜不绕)

---

**本 skill 随 distribution.schema `advisor-skills` 条目 copy_tree 原样下发(不渲染; AIPOS-F92 起落顾问会话目录 .claude/skills/);工位本地改动会被 sync 覆盖, 改动须回流母本。**
