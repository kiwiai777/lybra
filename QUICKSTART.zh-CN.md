# Lybra 快速上手 —— 从安装到第一张卡闭环

本篇带你在一台**新机器**上按**现行**流程走到第一张卡闭环:十一步接入向导、一次项目常设授权、
顾问驱动的 loop、子 agent 干代码卡、**pi** 工位兜住其余一切。文中所有名字都是示例 ——
把 `my-project`、`advisor.my-project.h1`、…… 换成你自己的。

[English](QUICKSTART.md) · **简体中文** · [项目总览](README.zh-CN.md)

## 角色一览

| 角色 | 跑在 | 职责 |
|---|---|---|
| Owner | 门所在机器 | 跑安装自检;起门(前台或常驻安装);签注册与**项目常设授权**;读 Owner 收件箱;看板。**一个项目只签一次**,不是一卡一签。 |
| 顾问 | 本地命令行 agent 会话(默认 Claude Code,支持 Codex) | 完成项目接入;起草并发布任务卡;用 **`lybra loop`** 驱动每张卡并用 `lybra loop status` 盯到底;维护板面标题卡。 |
| 子 agent 执行者 | 在顾问会话内拉起(`exec.<项目>.<主机>`) | 按 `lybra kickoff` 的开工简报干**代码卡**。 |
| pi 执行工位 | 一个工位目录(缺省 `~/lybra-workstations/<项目>/executor`),跑 **pi** —— 实例 `exec.<项目>.<主机>-pi` | 干**所有非代码卡**(文档 / 治理 / 配置 / 验证)。由 `/go` 启动,或授权允许时由 `lybra loop` 拉起。 |
| pi 审计工位 | 另一个工位目录(缺省 `~/lybra-workstations/<项目>/auditor`),跑 **pi** | 审计 return。**绝不**与执行者同体。 |

执行者与审计者永远不直接连门 —— 所有门动作都由产品代做。

## 前置条件

- **Node.js 18+**、**Python 3.10+**、**git** 都在 `PATH` 上。
- **pi**(执行 / 审计工位用),至少配置了一个模型 —— `pi --list-models` 可查。
- 一个顾问会话:**Claude Code**(默认)或 **Codex**。
- 顾问必须能在治理根所在机器上跑本地命令(或 ssh 上去)。

以上一条命令全查:`lybra setup check`(第 1 步)。

## 第 1 步 —— 安装并自检

```bash
npm install -g lybra
lybra --help
lybra setup check
```

- `lybra --help` 打印用法树(setup、serve、onboarding、project、roles、sync、draft、loop、
  board、owner、release、……)。任何子命令用 `--help` 自解释;不带 `--confirm` 的命令都是只读的。
- `lybra setup check` 是只读的安装自检(绝不读凭据与密钥文件)—— 也是接入向导开头跑的同一道
  前置检查。共七项,每项一行 ✓ / ✗ / ⚠,末尾给出口行;出现任何 ✗ 即退出码 1:
  **Python ≥ 3.10** · **Node.js ≥ 18** · **git** · **pi** 在 PATH · **pi 的已配置模型** ·
  **门在跑且可达** · **门常驻服务已安装**。失败行自带出口 —— 门不可达那行会同时给出前台起门
  与常驻安装两条命令。探测别的门地址用 `--gate-url`;`--json` 输出板面复用的结构化结果。

## 第 2 步 —— 创建项目(顾问)

**向导第 1–2 步。** 生成项目的接入向导 —— 它为*你的*项目按序打印**全部十一步**,每步给出确切
命令、验证方法与失败出口。向导开头的前置检查已经替你跑过 `lybra setup check`(在另一台机器上
生成向导时加 `--skip-preflight`):

```bash
lybra onboarding guide my-project --repo app=~/code/my-project
lybra project new my-project
lybra project set-repos my-project --repo app=~/code/my-project --dry-run
lybra project set-repos my-project --repo app=~/code/my-project --confirm
```

- 向导第 1 步 `project new` 在治理家目录(缺省 `~/.lybra/projects`)下建项目,并打印本步要跑的
  那条治理落账行。首次在机器上遇到 "not a git repository"?见 FAQ。
- 向导第 2 步 `set-repos` 走两相:`--dry-run`(缺省)零写入预演仓库校验;`--confirm` 写入
  `project.json` 并自动落账进治理。
- **Codex** 顾问:向导命令加 `--advisor-harness codex`。
- 查任何一步的状态:`lybra onboarding check my-project --step 2`(`--step 0` = 前置检查的 ✗ 项;
  步号沿用向导的 1–11)。

## 第 3 步 —— 起门

**向导夹在 2、3 步之间的插曲。** 从授权起,所有动作都要过门。向导会为*你的*治理根打印两种
起法。前台(终端保持打开;Ctrl-C 停门)—— 试用足够了:

```bash
lybra serve --workspace-root ~/.lybra/projects/my-project start
```

常驻 —— Linux 装 systemd --user 单元,macOS 装 launchd agent(launchd 路径尚未在真机 macOS 上
实跑验证)。缺省即 `--dry-run`:零写入打印完整单元文件与确切安装命令;`--confirm` 写入单元、
当场启用并回读状态:

```bash
lybra serve --workspace-root ~/.lybra/projects/my-project install --dry-run
lybra serve --workspace-root ~/.lybra/projects/my-project install --confirm
```

- 已存在的同名单元绝不被覆盖;已有手写单元在跑 `serve start` 时,install 只报告、不接管
  (`--confirm` 拒绝)。要拆掉 install 建的单元用
  `lybra serve --workspace-root ~/.lybra/projects/my-project uninstall --confirm`
  (手写单元绝不被碰)。
- 预期:**板**在 `127.0.0.1:7117`,**门**(MCP)在 `127.0.0.1:7118`;
  `lybra serve --workspace-root ~/.lybra/projects/my-project status` 打印脱敏状态(凭据值绝不
  打印)。用 `lybra setup check` 复核 —— 门一项变 ✓。端口被占?见 FAQ。

## 第 4 步 —— 签项目常设授权(Owner)

**向导第 3 步 —— Owner 两个一次性动作中的第一个。** 一份签名覆盖整个项目 —— 车道、任务模式、
驱动者、主机、拉起权限与硬边界 —— 取代逐卡信封:

```bash
lybra project grant show my-project
lybra project grant sign my-project --grant-lane app --task-mode code --task-mode docs --driver advisor.my-project.h1 --launch-harness pi --max-tasks 50 --expires-at 2027-01-31T00:00:00Z --confirm
```

- `grant show`(只读)查看当前授权与迁移过的信封。
- 不带 `--confirm` 的 sign 是**本地预演**:打印完整授权 diff 与校验结果。
- 各参数含义:`--grant-lane` 授权覆盖的仓库车道;`--task-mode` 任务模式(code、docs、governance、
  config、validation —— audit 是独立通道);`--driver` 哪个顾问可以驱动;`--launch-harness pi`
  允许 loop 拉起 pi 工位(不加则工位只能 `/go` 启动);`--max-tasks` / `--expires-at` 硬边界;
  `--host` 把车道绑定到主机。
- 之后要改:`lybra project grant extend` / `lybra project grant revoke`。

## 第 5 步 —— 注册顾问与三个工位

**向导第 4–8 步 —— 第 4 步是 Owner 的第二个一次性动作。** 产品用 **`lybra roles
enroll-deliver`** 注册角色 —— 一次性注册码绝不走命令行(shell 历史里不留代码字面量)。请按
**你的向导**打印的确切命令执行,里面带真实实例名与授权引用。形态如下:

Owner 把顾问签入治理根(向导第 4 步)—— 顾问会话目录缺省就是治理根本身,`.claude/skills`
直接落在那里:

```bash
lybra roles enroll-deliver --role advisor --instance advisor.my-project.h1 --governance-root ~/.lybra/projects/my-project --workspace ~/.lybra/projects/my-project --harness claude-code --harness-dir ~/.lybra/projects/my-project --token-role owner --owner-authorization-ref auth-2026-11-01-01 --confirm
```

顾问就位复核(向导第 5 步)—— 新顾问自查前置条件、确认投递的顾问侧文件已稳定,然后开会话:

```bash
lybra onboarding check my-project --step 5
lybra sync --harness-root ~/.lybra/projects/my-project --workspace-root ~/.lybra/projects/my-project --dry-run
```

- **Claude Code:** 在会话目录(缺省 = 治理根)里起会话;Lybra 技能已投递进 `.claude/skills` ——
  (重)启会话即可加载。
- **Codex**(在治理根机器上或经 ssh):会话启动时把章程与技能拉进会话目录:

```bash
lybra charter --role advisor --harness codex --workspace-root ~/.lybra/projects/my-project
lybra skills --role advisor --harness codex --workspace-root ~/.lybra/projects/my-project
```

技能流经管道交给会话目录里的 `tar -xf -`(命令的 `--help` 给出确切一行)。然后在那个目录里起 Codex。

接着顾问注册三个工位(向导第 6–8 步)—— 工位目录缺省在 `~/lybra-workstations/<项目>/…` 下;
pi 执行工位的实例名以 `-pi` 结尾;子 agent 执行者的凭据落在治理根:

```bash
lybra roles enroll-deliver --role auditor --instance audit.my-project.h1 --governance-root ~/.lybra/projects/my-project --workspace ~/lybra-workstations/my-project/auditor --executor-mode workstation --owner-authorization-ref auth-2026-11-01-01 --confirm
lybra roles enroll-deliver --role executor --instance exec.my-project.h1-pi --governance-root ~/.lybra/projects/my-project --workspace ~/lybra-workstations/my-project/executor --executor-mode workstation --owner-authorization-ref auth-2026-11-01-01 --confirm
lybra roles enroll-deliver --role executor --instance exec.my-project.h1 --governance-root ~/.lybra/projects/my-project --workspace ~/.lybra/projects/my-project --executor-mode subagent --harness claude-code --owner-authorization-ref auth-2026-11-01-01 --confirm
```

- `--executor-mode workstation` + 工位目录 = 注册 **pi 工位**(审计 / pi 执行);
  `--executor-mode subagent` + `--harness claude-code` = 在顾问会话内注册**子 agent 执行者**
  (代码卡)。别的主机上的远程工位在注册时记录 `--harness-host`(向导会处理好)。

## 第 6 步 —— 声明执行分工

**向导第 9 步。** 每项目一次,声明谁干什么 —— 代码卡给子 agent、所有非代码卡给 pi 执行工位、
审计给独立的 pi 审计工位:

```bash
lybra project set-execution my-project --subagent-executor exec.my-project.h1 --pi-executor exec.my-project.h1-pi --auditor audit.my-project.h1 --confirm
lybra project workstation-plan my-project
```

不带 `--confirm` 即预演。`workstation-plan` 打印只读的工位计划视图(与接入向导展示的同一份)。

## 第 7 步 —— 同步工位并自检

**向导第 10 步。** 向每个 pi 工位投递章程、技能与扩展,复核稳态,并跑工位自检:

```bash
lybra sync --harness-root ~/lybra-workstations/my-project/executor --workspace-root ~/.lybra/projects/my-project
lybra sync --harness-root ~/lybra-workstations/my-project/auditor --workspace-root ~/.lybra/projects/my-project
lybra onboarding check my-project --step 10 --workspace-dir ~/lybra-workstations/my-project/executor
lybra onboarding check my-project --step 10 --workspace-dir ~/lybra-workstations/my-project/auditor
```

- 预期:再跑一次 `sync --dry-run` 打印 `up-to-date: 0 file(s) to fetch/render`;各项检查打印
  `✓ Step 10 prerequisites satisfied`;`lybra project workstation-plan my-project`(第 6 步)显示
  三个实例均已注册、章程新鲜。
- `sync` 幂等可重跑;它列出投递了什么、还有什么未投递。

## 第 8 步 —— 发布第一张卡并跑 lybra loop(顾问)

**向导第 11 步。** 首卡之前,向导还会让你确认卡号模式(`lybra project card-policy` —— 见
按需声明)。然后:

```bash
lybra draft create --from-json card.json
lybra draft validate --path 5_tasks/drafts/myproj-1.md
lybra draft publish --path 5_tasks/drafts/myproj-1.md
lybra loop --task-id MYPROJ-1
```

- `draft create` 从你的 JSON 卡在 `5_tasks/drafts/` 下落草稿(`--help` 列出全部字段;不用 JSON
  也有 `--title` 等 CLI 旗标)。`validate` 查 frontmatter 与队列一致性;`publish` 按状态机把卡
  推进队列。
- `lybra loop --task-id MYPROJ-1` 自动找到项目常设授权(不用再倒腾信封)并推进卡片:认领(门
  建卡工作树)→ 执行者做完交回 → 派审 → pi 审计 → 裁决 → finalize → 结案落账。
- **代码卡**:loop 会提示顾问拉起自己的子 agent,并把自足的开工简报交给它:

```bash
lybra kickoff --task-id MYPROJ-1
```

- **非代码卡**:loop 在执行工位拉起 pi(授权允许时),或打印哪个工位需要 `/go`(由 Owner /
  运维在那里按下)。
- loop 退出码:`0` 完成 · `2` 门拒绝(输出自带原因)· `3` 等待超时 / 停滞 / 步数用尽 ·
  `4` 步骤不可推导或命令不可解析 · `5` 无有效授权(去签 grant)· `6` 部署已被更新 ·
  `7` 已有别的 loop 在跑这张卡。

## 第 9 步 —— 观察与排查

```bash
lybra loop status --task-id MYPROJ-1 --wait 540
lybra agent watch --workspace-root ~/.lybra/projects/my-project --timeout 600
lybra next --task-id MYPROJ-1
lybra state lint --workspace-root ~/.lybra/projects/my-project
lybra board open --workspace-root ~/.lybra/projects/my-project
lybra owner inbox
```

- `loop status --wait <秒>` 有界阻塞后返回顾问的下一个动作与退出码:`0` 完成或继续、`3` 继续
  等(再跑一次)、`5` 需要 Owner(看 `lybra owner inbox`)、`6` 需排查、`7` 顾问侧修复、
  `8` 部署已变更、`2` 用法问题。
- `agent watch` 是纯客户端文件哨兵 —— 有界、不连门、任何能跑 bash 的 agent 都能执行(退出码见
  [`docs/agent_watch_exit_codes.md`](docs/agent_watch_exit_codes.md))。
- `state lint` 校验队列 / frontmatter / records 一致性;`next` 打印一张卡的下一步与首个可执行
  命令;`history search` 按关键词找结案卡。
- **板**(缺省 `127.0.0.1:7117`)是仪表盘:标题卡(顾问维护,见下面按需声明)、Owner 收件箱、
  队列、工位页与历史。`board open` 用一次性票据打开;URL 里没有明文凭据。

## 按需声明

接入向导在结尾把这些列出来 —— **列出、不强制**;每项可 `--dry-run` 预演,`--confirm` 落账:

```bash
lybra project card-policy show
lybra project card-policy set-task-id-pattern my-project --pattern MYPROJ-\d+ --confirm
lybra project set-test-contract --timeout-seconds 1800 --owner-ref arb-2026-11-05-01 --confirm
lybra project release-target declare --id web --host h1 --repo app --allow dist/ --release-root /srv/my-project --entry current --owner-ref arb-2026-11-05-02 --confirm
lybra project set-model --model PROVIDER/ID --owner-ref arb-2026-11-05-03 --confirm
lybra project headline --instance advisor.my-project.h1 --title "My Customer Project" --now "Closing the first card" --done "Onboarding complete" --confirm
```

- **card-policy** —— 项目卡号模式(影响草稿校验)。
- **set-test-contract** —— 合并后回归契约(全量超时;`lybra regression baseline` 复用它记录
  main 侧基线,多数卡不再重跑)。
- **release-target** —— 声明生产主机 + 允许清单 + 发布根;之后卡上的生产发布只走产品动词
  `lybra release plan` → `apply` → `verify`(回退 `release rollback`)—— 绝不裸 ssh / scp /
  rsync。`lybra project release-target list` 查已声明目标。
- **set-model** —— 项目级 pi 模型(必须是你 pi 已配置的;`pi --list-models` 可查)。
- **headline** —— 顾问在板上的摘要卡:一个标题、"now"、"done" 与可选 "next"。

## 核心概念

- **文件即真相。** 卡、records、裁决与决策都落在治理根 —— 可存续、可回溯,比任何模型会话都长寿。
- **是门,不是引擎。** 门从不运行、也不唤醒任何 agent。Owner 起门(前台或常驻安装);工位 harness
  由 `/go` 启动,或 —— 仅当授权允许 —— 由顾问的 loop 拉起。
- **执行者零门。** 执行者与审计者不碰门;一切门动作都是产品的。
- **起草 ≠ 执行 ≠ 审计。** 顾问起草驱动、执行者干活、独立 pi 审计裁决。没有独立审计 PASS,就
  没有 finalize。
- **一份授权,许多卡。** 一份 Owner 常设授权覆盖整个项目;逐卡倒腾信封已退役。

## FAQ

**7117 / 7118 端口被占。** 起门时加 `--board-port 7119 --mcp-port 7120`(第 3 步),客户端连接
文件会随之更新。

**`lybra setup check` 门的项是 ✗ / ⚠。** 门没在跑或不可达 —— ✗ / ⚠ 行本身就写着两条出口:前台起门
(`lybra serve --workspace-root <治理根> start`)或常驻安装(`lybra serve install`,第 3 步)。
`--gate-url` 探测别的地址。

**`serve install` 报告存在手写单元。** install 绝不接管它没生成的单元 —— 要么留用你的手写单元,
要么自行停用并移除后再跑 install。

**卡在 "running" 但 `lybra loop` 退出 3。** 工位还没交回 —— 看板或看工位,然后重跑
`lybra loop status --task-id MYPROJ-1 --wait 540`。若那里报了提前退出,原因会写明;想看一次
return 为何被拒,可以预演收账:
`lybra artifact ingest --task-id MYPROJ-1 --kind return --dry-run`。

**落账命令报 "not a git repository"。** 治理家目录还不是 git 仓库 —— 一次性本地初始化:
`lybra home git-init --home-root ~/.lybra/projects`,然后 Owner 添加远端并推送初始提交。

**切换板面语言。** 用板面页上的语言切换 —— 英文 / 简体中文都内置。
