# Lybra Quick Start — 从接入到第一张卡结案

本指南带你走一遍现行流程:接入一个项目 → 发布第一张卡 → 由顾问一条命令推进到结案。
全程零手工编辑文件:每一步命令都由产品生成或给出。

---

## 角色一览

| 角色 | 在哪 | 做什么 |
|---|---|---|
| **Owner** | 任意终端 + 各工位 | 起门;签发顾问注册码、签信封(向导里标出的两步);手工模式下在执行 / 审计工位敲 **`/go`**(或在信封里授权 `lybra loop` 自动拉起工位) |
| **顾问** | 一个 Claude Code 会话(顾问技能由产品交付) | 接入项目、出卡、用 **`lybra loop`** 推进每张卡 |
| **执行 / 审计工位** | 各自的工位目录(如 pi) | 被 `/go` 打开或被授权的 `lybra loop` 拉起后只做卡:在卡分支提交、把报告写到项目声明的落点;不调用任何门动词 |

---

## 前置条件

- **Node.js 18+**、**Python 3**、**git** 在 PATH 上
- 一个 Claude Code 会话做顾问;执行 / 审计工位所用的 harness(如 pi)

---

## 第一步:安装

```bash
npm install -g lybra
lybra --version
```

---

## 第二步:Owner 起门

```bash
lybra serve --workspace-root <门工作区> start    # 前台运行: 看板 7117 + 门 7118
lybra serve --workspace-root <门工作区> status   # 脱敏状态
```

门只扫描 **home 根**发现项目(`--home-root` > `LYBRA_HOME_ROOT` > `~/.lybra/config.json` 的 home_root >
缺省 `~/.lybra/projects`)。

---

## 第三步:生成本项目的接入向导

```bash
lybra onboarding guide my_project --repo app=/abs/path/to/app
```

向导按你的项目打印全部 9 步:每步的执行方、原样可执行的命令、验证判据与失败出口。可选参数
(`--home-root`、`--advisor-dir`、`--workspace-dir`、`--auditor-dir` 等)缺省时向导会打印推导结果与来源。

| 步 | 执行方 | 做什么 |
|---|---|---|
| 1 | 顾问 | `lybra project new my_project`:在 home 根下建治理根(队列 / 记录 / 治理文档 / project.json + 首份阶段快照),并落账 |
| 2 | 顾问 | `lybra project set-repos`:声明产品仓,并落账 |
| 3 | **Owner** | 签发一次性顾问注册码(`lybra roles … enroll-code`) |
| 4 | **Owner** | 一条命令签三张信封:驱动方 / 执行 / 审计(`lybra envelope mint --confirm`) |
| 5 | 顾问 | 凭码 enroll 到治理根,顾问技能交付到 Claude Code 会话目录(`lybra roles enroll --harness claude-code`) |
| 6 | 顾问 | 为执行 / 审计工位各发一个注册码 |
| 7 | 顾问 | 工位 enroll + `lybra sync` 落齐技能 / 扩展 / 章程 |
| 8 | 工位 | 自检(`lybra onboarding check my_project --step 8`),起工位 harness |
| 9 | 顾问 | 首卡(见第四步) |

每步之后的落账(`lybra governance-commit --paths <本步产物>`)向导已原样给出;治理根须在治理仓(git)内,
不在时向导先给出 `lybra home git-init --home-root <home 根>`。任一步卡住:
`lybra onboarding check my_project --step <n>` 输出缺什么、怎么修。

---

## 第四步:发布第一张卡并推进

顾问出卡:

```bash
lybra --workspace-root <治理根> draft create --from-json <卡稿JSON>
lybra --workspace-root <治理根> draft publish --path <草稿路径>
```

顾问推进(Owner 第 4 步签的信封授权):

```bash
lybra loop --task-id <卡ID> --workspace-root <治理根> --envelope <信封ID>
```

`lybra loop` 一条命令走完:认领(门在同一步建卡工作树)→ 等执行工位交回 → 派审 → 等审计报告 → 裁决 →
finalize → 结案 → 治理落账(N6)。走到需要工位干活的一步时:信封授权了拉起就在工位拉起 harness,
否则提示去哪个工位敲 `/go`;然后有界等待产物,未等到即 exit 3(两种模式见第五步)。

| 退出码 | 含义 | 下一步 |
|---|---|---|
| 0 | 已结案并落账 | — |
| 2 | 门拒(原样透传拒因) | 按拒因处理 |
| 3 | 等产物超时 / 停滞 / 拉起的进程早退 | 手工模式:在对应工位敲 `/go`;授权拉起:看输出的早退原因。之后重跑同一条 `lybra loop`(重跑 = 显式再拉起一次) |
| 4 | 不可推导 / 派生命令解析失败 | 看输出的 missing_records |
| 5 | 无有效信封 | 按输出的 `lybra envelope mint` 出口请 Owner 签 |

---

## 第五步:工位开工(两种模式)

| 模式 | 前提 | 怎么开工 |
|---|---|---|
| **手工(缺省)** | 信封没带 `--launch-harness`,或 `lybra loop --no-launch`,或条件不满足(harness 无拉起模板 / 工位定位不到或身份不符 / 跨机工位) | loop 提示「请在 <工位目录> 的 <harness> 会话敲 /go」;Owner 在执行工位(之后在审计工位)敲无参 **`/go`** |
| **授权拉起** | Owner 同意后亲自签带 `--launch-harness pi` 的信封 | `lybra loop` 在本机工位按声明模板拉起一次 harness,进度汇总成一行显示,产物就绪 / 超时 / 早退 / 中断即清整个进程组 |

两种模式下工位做的事相同:产品选出本实例已认领的卡并核验,工位在卡分支 `card/<卡ID>` 上提交,
把报告(带 `commit_sha` / `tree_hash` / `branch` frontmatter)写到项目声明的落点,写完即停。
认领、交回、派审、裁决、finalize 都由顾问的 `lybra loop` 完成——不贴卡号、不报裁决。手工 `/go` 始终可用。

---

## 第六步:等待与查看

```bash
lybra agent watch --workspace-root <治理根> --timeout 600   # 纯客户端文件哨兵: 变化即 exit 0, 超时 exit 2
lybra next --task-id <卡ID> --workspace-root <治理根>      # 这张卡的当前状态与下一步
lybra state lint --workspace-root <治理根>                 # 队列 × frontmatter × records 一致性, 含未落账真相
lybra board open --workspace-root <治理根>                 # 本地看板(默认 7117)
```

`agent watch` 的完整退出码见 `docs/agent_watch_exit_codes.md`。

---

## 核心概念

- **文件即真相**:卡、记录、裁决、决策都在治理根的文件里,并经 `lybra governance-commit` 进治理仓
- **门不是引擎**:门不运行、不唤醒 agent;推进由顾问的 `lybra loop` 在 Owner 信封授权下完成,工位只有在信封明确授权拉起时才由 loop 拉起
- **执行体零门**:执行 / 审计工位只交产物,所有门动作由产品完成
- **执行者 ≠ 审计者**:没有独立审计 PASS,不 finalize

本指南覆盖门、治理根与工位在同一台机器上的形态;其他形态以 `lybra onboarding guide` 的实际输出为准。

---

## 常见问题

### Q1:门启动失败,提示端口被占用?

```bash
lybra serve --workspace-root <门工作区> start --mcp-port 7119 --board-port 7120
```

### Q2:`lybra loop` 一直 exit 3?

工位还没交产物。手工模式到对应工位敲 `/go`(授权拉起模式先看输出的早退 / 超时原因),写完后重跑同一条 `lybra loop`;产物入口为何拒收可用
`lybra artifact ingest --task-id <卡ID> --kind return --dry-run` 查看。

### Q3:落账报 not a git repository?

治理根须在治理仓(git)内:`lybra home git-init --home-root <home 根>`(一次性本地 init),Owner 按其输出配远端并首推。

### Q4:如何切换语言?

看板右上角有语言切换器(中文 / English)。
