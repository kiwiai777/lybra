# tools/aipos_cli — `lybra` 命令的实现

本目录是 `lybra` CLI 的 Python 实现。npm 入口 `bin/lybra` 委托到
`python3 -m tools.aipos_cli.aipos_cli`;源码检出时也可直接用模块入口。

**参数以 `lybra --help` / `lybra <子命令> --help` 为准**——本页只讲各命令在流程里的位置, 不复制参数表
(复制 = 第二源 = 漂移)。顾问侧的命令快查表随分发下发, 母本在
[`agents/skills/advisor-commands/SKILL.md`](../../agents/skills/advisor-commands/SKILL.md)。

## 现行流程里的命令

| 阶段 | 谁 | 命令 |
|---|---|---|
| 接入新项目 | 顾问(Owner 只敲向导标出的两步) | `lybra onboarding guide <项目名>` 打印该项目专属的全部步骤与失败出口;其中建项目 = `lybra project new`, 声明产品仓 = `lybra project set-repos`(写 project.json 的 `set-repo` / `set-repos` / `set-paths` / `set-workstation` / `set-meta` 同一两阶段:缺省预演, 打印将写的 project.json diff 与校验结果、零写入;`--confirm` 才写;输出首行写明目标项目、来源与 project.json 绝对路径。目标项目 = 显式项目名 > `--workspace-root` / 当前目录所在治理根的 project.json#project > 拒, 绝不回落 home 级活动项目;显式项目名与所在治理根不一致 = 拒并列出两者;`freeze-legacy`、`dispatch-mode set` 同一解析。`set-meta --phase/--note` 写 project.json 说明键, 键须先在 config.schema project_json 声明);逐步自检 `lybra onboarding check`。顾问会话 harness:`--advisor-harness` 取值读 `distribution.schema` `harness_semantics.kinds`(`advisor_session=true` 的 kind, 缺省 `claude-code` = 既有向导);`codex` = Codex 会话(可在他机, `--advisor-host` 给会话所在机, 其短名作顾问实例名 host 段, 不默认成治理根所在机)——第 5 步写 `lybra roles enroll --harness codex [--harness-dir] [--harness-host]`, 凭据落治理根 `.lybra/`, role 如实记 `{kind, dir, host}`, 不建 `.pi` 接线、不分发 `.claude/skills`(声明无 codex 件) |
| 出卡 / 改卡 | 顾问 | `lybra draft create` → `lybra draft publish`;`lybra queue amend` / `withdraw` / `rework` |
| 推进一张卡 | 顾问 | `lybra loop --task-id <卡ID>`:Owner 信封授权下认领(门内建卡工作树)→ 等交回 → 派审 → 等审计报告 → 裁决 → finalize → 结案 → 治理落账;单步查看 `lybra next --task-id <卡ID>` |
| 开工 | 执行 / 审计工位 | 两种模式:手工(缺省)= Owner 在工位敲 `/go`;授权拉起 = Owner 签带 `--launch-harness` 的信封后由 `lybra loop` 在本机工位拉起 harness(`--no-launch` 强制手工)。工位只在卡分支提交并把报告写到项目声明的落点, 不调用任何门动词 |
| 等待产物 | `lybra loop` 内部 / 任何能跑 bash 的 agent | `lybra agent watch --workspace-root <治理根>`(纯客户端文件哨兵, 退出码见 [`docs/agent_watch_exit_codes.md`](../../docs/agent_watch_exit_codes.md)) |
| 工位分发 | 工位 | `lybra roles enroll`(凭注册码) + `lybra sync`(工位发起拉取);`--harness` 合法值 = `harness_semantics.kinds` 的键(未知值拒并列出合法值, 先于兑换、零写盘) |
| 治理落账 / 体检 | 顾问 | `lybra governance-commit`(卡由 loop 结案后自动落账;非卡改动用 `--paths`);`lybra state lint`;冷启动简报 `lybra brief` |
| Owner 动作 | Owner | `lybra envelope mint`(签信封);`lybra roles … enroll-code`(发注册码);`lybra serve`(起门);`lybra board`(看板) |

## 声明单源

命令行为由声明驱动, 改行为先改声明:

- `schema/verbs.schema.json` —— 门动词、`lybra loop` 出口码与信封授权动词、写 project.json 命令的两阶段语义(`two_phase_protocol.project_json_writers`)
- `schema/transitions.schema.json` —— 节点、下一步推导、分支约定
- `schema/config.schema.json` —— 工作树根、项目声明(project.json)结构
- `schema/distribution.schema.json` —— 各角色工位分发物

## 模块文档

- [`docs/health-monitoring.md`](docs/health-monitoring.md) —— `agent watch --health` 旁路健康观察
- [`docs/project-structure-schema.md`](docs/project-structure-schema.md) —— `lybra project export|import` 结构文件

## 测试

常驻夹具总入口 `tests/run-all.sh`;模块单测在 `tools/aipos_cli/tests/`。

run-all 执行器(`runall_discovery`)每个测试文件在沙箱里启动(AIPOS-F126, `runall_isolation`):真实 home 根与被守卫治理仓只读,
测试写入当场失败;Linux 上按 bwrap(含独立网络命名空间)→ Landlock 探测,接入方机器不需 root;都不可用(如 macOS)时输出头部
「隔离:无, 降级为快照守卫」。声明 `project.json` `test_contract.isolation {mode: auto|bwrap|landlock|none, network: isolated|host}`,
单次可用 `--isolation <mode>`。
