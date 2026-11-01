<p align="center">
  <img src="docs/assets/lybra-banner.png" alt="Lybra — AI 代理的治理 harness" width="880">
</p>

<p align="center">
  <b>面向长期多角色 AI 项目的治理 harness。</b><br/>
  项目真相固化在文件里,Owner 掌握每一道决策闸门,每一步皆可审计、可复现。
</p>

<p align="center">
  <a href="https://www.npmjs.com/package/lybra"><img alt="npm" src="https://img.shields.io/npm/v/lybra?color=1A7A52&amp;label=npm"></a>
  <img alt="node" src="https://img.shields.io/node/v/lybra?color=1A7A52">
  <img alt="license" src="https://img.shields.io/badge/license-Apache--2.0-1A7A52">
  <img alt="status" src="https://img.shields.io/badge/status-early%20access-1A7A52">
</p>

<p align="center">
  <a href="README.md">English</a> · <b>简体中文</b> · <a href="QUICKSTART.md">Quick start</a> · <a href="QUICKSTART.zh-CN.md">快速上手(中文)</a>
</p>

---

## Lybra 是什么

Lybra 为长期运行的多角色 AI 项目维护**唯一的文件化项目真相**——任务卡、记录、裁决与决策
都落在治理根里,不依赖任何 agent 的记忆。Agent 来来去去,真相与门长存。它优化的是
**可问责性**,而不是裸奔的自治度。

三条原则焊死在产品里:

- **是门,不是引擎。** 门不运行、不唤醒 agent。Owner 开门;agent 在自己的 harness 里干活、
  只交产物。工位 harness 要么由 Owner 以 `/go` 开工,要么——仅当项目授权明确允许——由顾问的
  `lybra loop` 拉起。
- **文件即真相。** 状态固化在持久文件里,不依赖会话压缩记忆——文件比模型活得久。
- **起草者 ≠ 执行者 ≠ 审计者。** 顾问起草并驱动;执行者干活;独立审计者裁决。**执行者与
  审计者永不接触门**——一切门动作(认领、交回、派审、裁决、finalize、结案)都由产品完成。

> 瓶颈从来不是模型,而是 harness。

## 角色与分工

| 谁 | 做什么 |
|---|---|
| **Owner** | 开门(`lybra serve`)、签发接入、给项目签**一次常设授权**(`lybra project grant sign`)、用收件箱决策(`lybra owner inbox`)、看板巡视。不必逐卡签字——一份授权覆盖项目的车道、任务模式、驱动方、主机与硬边界。 |
| **顾问** —— 本地命令行 agent 会话(缺省 Claude Code,支持 Codex;`--harness codex`) | 接入项目、起草并发布任务卡,用 **`lybra loop`** 推进每一张卡,以 `lybra loop status --task-id <卡ID> --wait <秒>` 跟进,只在产品提示需要 Owner 时停手。 |
| **代码卡 → 顾问派出的子 agent** | 顾问用 `lybra kickoff --task-id <卡ID>` 生成的自足开工稿驱动自己的子 agent;子 agent 在卡分支上提交并写报告。 |
| **非代码卡 → pi 执行工位** | 一切文档 / 治理 / 配置 / 验证卡都交给专属 **pi** 工位。loop 在授权允许时拉起该工位的 pi,否则由 Owner 敲 `/go`。 |
| **审计 → 独立 pi 审计工位** | 审计永远由独立于执行者的另一个 pi 工位完成。没有独立审计 PASS,就没有 finalize。 |

执行者与审计者跑在 **pi** 上(自行安装 pi 并配置模型);顾问跑在你自选的本地命令行 agent 上。
完整流程见[快速上手](QUICKSTART.zh-CN.md)(English: [Quick start](QUICKSTART.md))。

## 安装

前置条件:`PATH` 上有 **Node.js 18+**、**Python 3** 与 **git**(门核心无 Python 依赖);
执行 / 审计工位需要 **pi** 且至少配置了一个模型(`pi --list-models`);外加一个顾问会话——
Claude Code 或 Codex。

```bash
npm install -g lybra
lybra --help
lybra setup check
```

`lybra setup check` 是接入向导开头跑的同一道只读安装自检：七项 —— Python ≥ 3.10、Node.js ≥
18、git、**pi** 在 PATH 且已配模型、门在跑且可达、门常驻服务 —— 每项 ✓ / ✗ / ⚠ 加出口
行；任何 ✗ 即退出码 1 并给出修复提示。绝不读凭据与密钥文件。看板「设置自检」页显示同一
结果（看板首页与接入页的一行 ✓N · ✗N · ⚠N 摘要链接到该页）；常驻门下,「门常驻」项还会
核对已装单元的 PATH 是否含 pi 所在目录。

## 项目如何运转

**1. 接入项目** —— 产品按 *你的* 项目逐条打印每个步骤:确切命令、验收方式与失败退出码:

```bash
lybra onboarding guide my-project --repo app=~/code/my-project
lybra project new my-project
lybra project set-repos my-project --repo app=~/code/my-project --dry-run
lybra project set-repos my-project --repo app=~/code/my-project --confirm
```

每条写 `project.json` 的命令(`set-repos`、`set-execution`、`set-model`……)都是两相:
`--dry-run`(缺省)零写入预览 diff 与校验;`--confirm` 落盘并自动入库。
`lybra onboarding check my-project --step 8` 可诊断任一步骤。Codex 顾问会话:向导命令加
`--advisor-harness codex`。

**2. 授权与分工** —— 每项目 Owner 签一次,然后声明谁执行什么:

```bash
lybra project grant sign my-project --grant-lane app --task-mode code --task-mode docs --driver advisor.my-project.h1 --launch-harness pi --max-tasks 50 --expires-at 2027-01-31T00:00:00Z --confirm
lybra roles enroll-deliver --role auditor --instance audit.my-project.h1 --governance-root ~/.lybra/projects/my-project --workspace ~/lybra-workstations/my-project/auditor --owner-authorization-ref auth-2026-11-01-01 --confirm
lybra project set-execution my-project --subagent-executor exec.my-project.h1 --pi-executor exec.my-project.h1-pi --auditor audit.my-project.h1 --confirm
```

常设授权取代逐卡信封:车道 × 任务模式 × 驱动方 / 主机 / harness 拉起,外加总卡数与到期硬边界
(`lybra project grant show` 查看;`extend` / `revoke` 修订)。`set-execution` 声明分工——
代码卡归顾问的子 agent,一切非代码卡归 pi 执行工位,审计永远归独立 pi 审计工位。接入用
`lybra roles enroll-deliver`(一次性注册码不出现在命令行);工位用 `lybra sync` 拉取章程、
技能与扩展。

**3. 跑卡** —— 顾问发布任务卡,一条命令推进到结案:

```bash
lybra draft create --from-json card.json
lybra draft publish --path 5_tasks/drafts/myproj-1.md
lybra loop --task-id MYPROJ-1
lybra loop status --task-id MYPROJ-1 --wait 540
```

```
publish → lybra loop: 认领(门建卡工作树)
            → 代码卡: 顾问子 agent 按 lybra kickoff 开工稿干活
              非代码卡: pi 执行工位(loop 拉起, 或 /go 开工)
            → 报告入库 → 派审
            → pi 审计工位: 写审计报告
            → 裁决入库 → finalize → 结案 → 治理提交
```

跟进与检查:`lybra loop status --task-id MYPROJ-1 --wait <秒>` 有界等待并返回顾问的下一步动作
——`continue_wait`、`owner_needed`、`card_done_take_next`、`investigate` 或 `advisor_fix`;
`lybra agent watch --workspace-root ~/.lybra/projects/my-project` 是纯客户端、有界的文件哨兵,
任何能跑 bash 的 agent 都可用(退出码见
[`docs/agent_watch_exit_codes.md`](docs/agent_watch_exit_codes.md));`lybra next --task-id MYPROJ-1`
给出卡的下一步,`lybra state lint` 检查队列 / frontmatter / 记录一致性,`lybra brief` 给冷启动
摘要。按需声明——卡号策略、合并后回归时限、生产发布目标、项目级 pi 模型、看板头条——见
[快速上手](QUICKSTART.zh-CN.md)。

其他界面:`lybra board`(本地看板,缺省端口 7117;`lybra board open` 以一次性票据打开)与
`lybra tui`(可选终端客户端;需从 PyPI 安装 `textual>=4.0`——`lybra` 本体只发 npm)。看板
接入页还提供「新建项目」——仅 Owner 登录会话,先在一次性副本里预演目录树与 `project.json`
全文（零写入）再确认;与 `lybra project new` 同一实现外加落账,建成后清单标出下一步。macOS
某些 Python 的缺省 CA 路径为空时,TUI 的 HTTPS 需安装 `certifi`(或设 `SSL_CERT_FILE`);
Lybra 绝不关闭证书校验。

## 范围与边界

如实划界：**门常驻安装已产品化** —— `lybra serve install`（Linux：systemd --user 单元；macOS：
launchd agent，launchd 路径尚未在真机实跑验证）`--dry-run` 预演、`--confirm` 安装；手写单元
只报告、绝不接管。单元在安装时固化安装者的 PATH —— 请在能找到 `pi` 的终端里跑
`serve install`;之后 pi 换了位置,用 `lybra serve uninstall --confirm` 加
`lybra serve install --confirm` 重装刷新。尚未产品化：跨机隧道
（手工配置 ssh）、以及无法触达治理根机器的云平台型顾问（顾问必须能在治理机上跑本地命令、或
能 ssh 进治理机）。全部已披露暂缓 / 纪律持有项收录于
**[`docs/v1_disclosure.md`](docs/v1_disclosure.md)**。

## 开发

克隆本仓库后:

```bash
bash tests/run-all.sh        # 常驻 fixture 全量套件
npm run preflight            # 版本单源 + 打包检查 + 干净 HOME 冒烟 + 全量套件
```

## 参与贡献

影响工作流闸门、持久化边界、缺省端口、发布面或审计规则的改动,保持在 Owner 决策流内,并在
finalize 前完成验证。产品改动保持文件权威、窄口径(见
[`docs/release_discipline.md`](docs/release_discipline.md))。

## 关于

Lybra 把 AI 从个人试用工具变成**可管理、可复用、可追溯**、你能问责的系统。

## 许可

Apache-2.0,见 [LICENSE](LICENSE)。
