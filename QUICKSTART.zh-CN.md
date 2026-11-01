# Lybra 快速上手 —— 从安装到第一张卡结案

本篇带一台**新机器**按**现行**流程走到第一张卡结案:一份项目常设授权、顾问驱动 loop、
代码卡走子 agent、其余一切走 **pi** 工位。下文所有名字均为示例——把 `my-project`、
`advisor.my-project.h1` 等换成你自己的。

<a href="QUICKSTART.md">English</a> · <b>简体中文</b> · <a href="README.zh-CN.md">项目概览</a>

## 角色一览

| 角色 | 跑在哪 | 做什么 |
|---|---|---|
| Owner | 门所在机 | 开门;签发接入与**项目常设授权**;读 Owner 收件箱;看板巡视。**每项目签一次**,不是每卡一次。 |
| 顾问 | 本地命令行 agent 会话(缺省 Claude Code,支持 Codex) | 接入项目;起草并发布任务卡;用 **`lybra loop`** 推进每张卡并以 `lybra loop status` 跟进;维护看板头条。 |
| 子 agent 执行者 | 顾问会话内派出 | 按 `lybra kickoff` 开工稿干**代码**卡。 |
| pi 执行工位 | 独立目录,跑 **pi** | 干**一切非代码卡**(文档 / 治理 / 配置 / 验证)。`/go` 开工,或授权允许时由 `lybra loop` 拉起。 |
| pi 审计工位 | 另一个独立目录,跑 **pi** | 审计报告。**永远不是**执行者本人。 |

执行者与审计者不接触门——一切门动作都由产品代为完成。

## 前置条件

- `PATH` 上有 **Node.js 18+**、**Python 3** 与 **git**。
- **pi**(执行 / 审计工位用),至少配置一个模型——用 `pi --list-models` 检查。
- 一个顾问会话:**Claude Code**(缺省)或 **Codex**。
- 顾问必须能在治理机上跑本地命令(或能 ssh 进治理机)。

## 第 1 步 —— 安装

```bash
npm install -g lybra
lybra --help
```

`lybra --help` 打印用法树(serve、onboarding、project、roles、sync、draft、loop、board、
owner、release……)。任何子命令 `--help` 自解释;不传 `--confirm` 的命令都是只读的。

## 第 2 步 —— 开门(Owner)

选一个存放 Owner 凭据(门中央注册库)的目录,在那里开门:

```bash
lybra serve --workspace-root ~/lybra-gate start
lybra serve --workspace-root ~/lybra-gate status
```

预期:**看板**在 `127.0.0.1:7117`、**门**(MCP)在 `127.0.0.1:7118`,并打印脱敏后的凭据状态
——token 值永不上屏。`status` 显示两个监听与凭据目录。端口被占?双双改绑:

```bash
lybra serve --workspace-root ~/lybra-gate start --board-port 7119 --mcp-port 7120
```

Ctrl-C 停门(前台)。目前门常驻安装(systemd / launchd)尚未产品化——见
[README · 范围与边界](README.zh-CN.md#范围与边界)。

## 第 3 步 —— 建项目(顾问)

生成项目接入向导——它按 *你的* 项目逐条打印每一步:确切命令、验收方式与失败退出码:

```bash
lybra onboarding guide my-project --repo app=~/code/my-project
lybra project new my-project
lybra project set-repos my-project --repo app=~/code/my-project --dry-run
lybra project set-repos my-project --repo app=~/code/my-project --confirm
```

- `project new` 在治理 home(缺省 `~/.lybra/projects`)下建项目;向导为这一步打印唯一一条
  治理落盘命令,照跑即可。首次在这台机器上提示 "not a git repository"?见 FAQ。
- `set-repos` 是两相的:`--dry-run`(缺省)零写入预览仓库校验;`--confirm` 写 `project.json`
  并自动入治理库。
- **Codex** 顾问:向导命令加 `--advisor-harness codex`。
- 查任一步状态:`lybra onboarding check my-project --step 3`。

## 第 4 步 —— 签项目常设授权(Owner)

一份签名覆盖整个项目——车道、任务模式、驱动方、主机、拉起权限与硬边界——取代逐卡信封:

```bash
lybra project grant show my-project
lybra project grant sign my-project --grant-lane app --task-mode code --task-mode docs --driver advisor.my-project.h1 --launch-harness pi --max-tasks 50 --expires-at 2027-01-31T00:00:00Z --confirm
```

- `grant show`(只读)查看当前授权与已迁移信封。
- 不带 `--confirm` 的 sign 是**本地预览**:打印完整授权 diff 与校验。
- 各参数含义:`--grant-lane` 授权覆盖的仓库车道;`--task-mode` 任务模式(code、docs、
  governance、config、validation——audit 是独立通道);`--driver` 哪位顾问可驱动;
  `--launch-harness pi` 允许 loop 拉起 pi 工位(不签则工位只能 `/go` 开工);
  `--max-tasks` / `--expires-at` 硬边界;`--host` 把车道绑到主机。
- 之后修订:`lybra project grant extend` / `lybra project grant revoke`。

## 第 5 步 —— 接入顾问与各工位

产品用 **`lybra roles enroll-deliver`** 接入角色——一次性注册码不出现在命令行(shell 历史里
没有码字面量)。按**你的向导打印的原样**执行——它们已带真实实例名与授权引用。形态如下:

```bash
lybra roles enroll-deliver --role advisor --instance advisor.my-project.h1 --governance-root ~/.lybra/projects/my-project --workspace ~/.lybra/projects/my-project --harness claude-code --harness-dir ~/sessions/my-project --owner-authorization-ref auth-2026-11-01-01 --confirm
lybra roles enroll-deliver --role executor --instance subexec.my-project.h1 --governance-root ~/.lybra/projects/my-project --workspace ~/.lybra/projects/my-project --executor-mode subagent --harness claude-code --owner-authorization-ref auth-2026-11-01-01 --confirm
lybra roles enroll-deliver --role executor --instance exec.my-project.h1 --governance-root ~/.lybra/projects/my-project --workspace ~/my-project-executor --owner-authorization-ref auth-2026-11-01-01 --confirm
lybra roles enroll-deliver --role auditor --instance audit.my-project.h1 --governance-root ~/.lybra/projects/my-project --workspace ~/my-project-auditor --owner-authorization-ref auth-2026-11-01-01 --confirm
```

- `--role executor --executor-mode subagent` + `--harness claude-code` 接入**子 agent 执行者**
  (代码卡),活在顾问会话里;不带它、指向 pi 工位目录的 `--role executor` 接入 **pi 执行
  工位**。
- 然后,在每个 pi 工位上拉取章程、技能与扩展:

```bash
lybra sync --harness-root ~/my-project-executor --workspace-root ~/.lybra/projects/my-project
lybra sync --harness-root ~/my-project-auditor --workspace-root ~/.lybra/projects/my-project
```

`sync` 可安全重跑(幂等);它列出已交付件与暂未交付件。别的主机上的远程工位在接入时记录
`--harness-host`(向导会处理)。

## 第 6 步 —— 声明执行分工

每项目声明一次谁执行什么——代码卡归子 agent、一切非代码卡归 pi 执行工位、审计归独立
pi 审计工位:

```bash
lybra project set-execution my-project --subagent-executor subexec.my-project.h1 --pi-executor exec.my-project.h1 --auditor audit.my-project.h1 --confirm
lybra project workstation-plan my-project
```

不带 `--confirm` 可预览。`workstation-plan` 打印只读的工位计划(接入向导展示的同一视图)。

## 第 7 步 —— 起顾问会话

- **Claude Code:**在接入时用 `--harness-dir` 指定的目录里开会话。Lybra 技能已交付到
  `.claude/skills`(重跑 `lybra sync` 刷新)。
- **Codex**(治理机上或经 ssh):会话开始时把章程与技能拉进会话目录:

```bash
lybra charter --role advisor --harness codex --workspace-root ~/.lybra/projects/my-project
lybra skills --role advisor --harness codex --workspace-root ~/.lybra/projects/my-project
```

把技能流管道给会话目录里的 `tar -xf -`(命令的 `--help` 打印确切的一行写法)。然后在彼处
启动 Codex。

## 第 8 步 —— 发布第一张卡并用 lybra loop 推进(顾问)

```bash
lybra draft create --from-json card.json
lybra draft validate --path 5_tasks/drafts/myproj-1.md
lybra draft publish --path 5_tasks/drafts/myproj-1.md
lybra loop --task-id MYPROJ-1
```

- `draft create` 按 JSON 卡稿在 `5_tasks/drafts/` 下落草稿(`--help` 列出全部字段;不用
  JSON 也能用 `--title` 等 CLI 参数)。`validate` 校验 frontmatter 与队列一致性;`publish`
  按状态机把它移入队列。
- `lybra loop --task-id MYPROJ-1` 自动找到项目常设授权(不必再摆弄信封),推进整卡:
  认领(门建卡工作树)→ 执行者干活并交回 → 派审 → pi 审计 → 裁决 → finalize → 结案并落
  治理提交。
- **代码卡**:loop 提示顾问派出子 agent,并把自足的开工稿交给它:

```bash
lybra kickoff --task-id MYPROJ-1
```

- **非代码卡**:loop 在执行工位拉起 pi(授权允许时),或打印哪个工位需要 `/go`(Owner 或
  操作员去那里敲)。
- loop 退出码:`0` 完成 · `2` 门拒(输出自带原因)· `3` 等待超时 / 停滞 / 步数用尽 ·
  `4` 步骤不可推导或命令不可解析 · `5` 无有效授权(去签常设授权)· `6` 部署已更新 ·
  `7` 本卡已有另一个 loop 在跑。

## 第 9 步 —— 跟进、等待与检查

```bash
lybra loop status --task-id MYPROJ-1 --wait 540
lybra agent watch --workspace-root ~/.lybra/projects/my-project --timeout 600
lybra next --task-id MYPROJ-1
lybra state lint --workspace-root ~/.lybra/projects/my-project
lybra board open --workspace-root ~/.lybra/projects/my-project
lybra owner inbox
```

- `loop status --wait <秒>` 有界阻塞,然后按退出码返回顾问的下一步动作:`0` 完成或继续、
  `3` 继续等(再跑一次)、`5` 需要 Owner(查 `lybra owner inbox`)、`6` 需调查、`7` 顾问
  修、`8` 部署已变、`2` 用法错误。
- `agent watch` 是纯客户端的文件哨兵——有界、不连门,任何能跑 bash 的 agent 都可用
  (退出码见 [`docs/agent_watch_exit_codes.md`](docs/agent_watch_exit_codes.md))。
- `state lint` 校验队列 / frontmatter / 记录一致性;`next` 打印卡的下一步与首个可跑命令;
  `history search` 按关键词检索已结案卡。
- **看板**(缺省 `127.0.0.1:7117`)是仪表盘:头条卡(顾问维护——见下节「按需声明」)、
  Owner 收件箱、队列、工位页与历史。`board open` 以一次性票据打开;URL 里没有明文 token。

## 按需声明

写什么就是什么,每条都可 `--dry-run` 预览、`--confirm` 落治理库:

```bash
lybra project card-policy show
lybra project card-policy set-task-id-pattern my-project --pattern MYPROJ-\d+ --confirm
lybra project set-test-contract --timeout-seconds 1800 --owner-ref arb-2026-11-05-01 --confirm
lybra project release-target declare --id web --host h1 --repo app --allow dist/ --release-root /srv/my-project --entry current --owner-ref arb-2026-11-05-02 --confirm
lybra project set-model --model PROVIDER/ID --owner-ref arb-2026-11-05-03 --confirm
lybra project headline --instance advisor.my-project.h1 --title "我的客户项目" --now "正在结第一张卡" --done "接入完成" --confirm
```

- **card-policy** —— 项目的卡号模式(影响草稿校验)。
- **set-test-contract** —— 合并后回归契约(全量套件时限;被 `lybra regression baseline`
  复用——它记录 main 侧基线,多数卡不必重跑主线全量)。
- **release-target** —— 声明生产主机 + 允许清单 + 发布根;之后卡上的生产发布只经产品动词
  `lybra release plan` → `apply` → `verify`(回退:`release rollback`)——绝不裸 ssh/scp/
  rsync。`lybra project release-target list` 列出已声明目标。
- **set-model** —— 项目级 pi 模型(必须是你本地 pi 已配置的之一;用 `pi --list-models`
  检查)。
- **headline** —— 顾问在看板上的摘要卡:一个标题、"now"、"done" 与可选的"next"。

## 核心概念

- **文件即真相。** 卡、记录、裁决与决策都落在治理根——持久、可审、比任何模型会话都活得久。
- **是门,不是引擎。** 门不运行、不唤醒 agent。Owner 开门;工位 harness 由 `/go` 开工,或
  ——仅当授权允许——由顾问的 loop 拉起。
- **执行者零门。** 执行者与审计者永不接触门;一切门动作都是产品的。
- **起草者 ≠ 执行者 ≠ 审计者。** 顾问起草驱动、执行者干活、独立 pi 审计裁决。没有独立审计
  PASS,就没有 finalize。
- **一份授权,多张卡。** 单份 Owner 常设授权覆盖整个项目;逐卡摆弄信封的时代已退役。

## 常见问题

**7117 / 7118 端口已被占用。** 按 `--board-port 7119 --mcp-port 7120` 开门(第 2 步),
连接文件会同步更新给客户端。

**`lybra loop` 退出码 3,卡还在"跑"。** 工位还没交回——先看看板或工位,再跑
`lybra loop status --task-id MYPROJ-1 --wait 540`。若那里报告了早退,会写明原因;想看报告
为何被拒,预览入库:`lybra artifact ingest --task-id MYPROJ-1 --kind return --dry-run`。

**某条落盘命令提示 "not a git repository"。** 治理 home 还不是 git 仓——一次性本地初始化:
`lybra home git-init --home-root ~/.lybra/projects`,之后 Owner 添加远端并推首个提交。

**看板切换语言。** 用看板页上的语言开关——英文 / 简体中文都已内置。
