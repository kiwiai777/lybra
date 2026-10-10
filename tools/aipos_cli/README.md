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
| 外部项目合入交给 Lybra(`finalize_mode` 切 `internal`) | 顾问(声明写入经 Owner 两阶段确认) | 适用于人肉期接入、原由外部 FINALIZE 卡合入的项目(任何项目同一组命令, 不写死项目/卡号)。**前置**:① 产品仓在治理根所在机、已用 `lybra project set-repos <项目> --repo <仓名>=<绝对路径> …` 声明, 卡 `lane.repo` 写仓名;② 各产品仓工作树在 `main` 且干净(脏树 finalize 拒并列脏文件);③ 在途 PASS 待合入卡先合完或确认无(外部模式下同仓推导已互斥, 见下)。**步骤**:① 不部署的仓随 set-repos 一并声明 `--no-deploy <仓名>`(可重复;set-repos 为整段声明, 未给即清空, 预演 diff 可见)→ finalize 不调部署脚本, finalization 记录 `deploy_status=not_applicable` 并写依据;② `lybra project set-paths <项目> --key finalize_mode --value internal` 预演后 `--confirm`;**既有项目声明阶段档案位置**(AIPOS-F145):项目阶段档案(阶段快照)不在缺省 `<治理根>/stage_archive/`(如人肉期放在 `governance/stage_archives/`)时, 同一写入口 `lybra project set-paths <项目> --key stage_archive_root --value <相对治理根路径>` 预演后 `--confirm`——finalize 阶段闸门、brief、governance-commit、close 鲜度检查与 `governance add stage` 都按此落点(唯一读取口 `workspace_config.stage_archive_root`), 未声明 = 缺省落点, 落点下无快照 = 阶段闸门拒并附同一出口;③ 之后 `lybra loop` 在 N4 PASS 后派生 `lybra finalize`(在本机合并)。**推送**:仓无 origin 远端或无 `origin/<基线>` = 推送不适用, 结果与 finalization 记录如实标 `push_status=not_applicable`(其余值 `pushed` / `already_synced` / `not_requested`, 声明 `transitions.schema` N5.record.push_status)。**同仓串行**:同一产品仓同时只合一张——内部 finalize 合并前取仓级锁(产品仓 git common-dir 下 `lybra-finalize.lock`, 工作树共享), 另一张在途即 BLOCK `REPO_MERGE_IN_PROGRESS`(点名持锁卡, 可重试);外部模式推导对同仓后到的 PASS 卡返回 `await_repo_turn`(不派 FINALIZE, loop 等先合入卡的 finalization 记录) |
| 推进一张卡 | 顾问 | `lybra loop --task-id <卡ID>`:Owner 信封授权下认领(门内建卡工作树)→ 等交回 → 派审 → 等审计报告 → 裁决 → finalize → 结案 → 治理落账;单步查看 `lybra next --task-id <卡ID>` |
| 看 loop 进度 | 顾问 / Owner(治理根所在机, 经 ssh 亦可) | `lybra loop status [--task-id <卡ID>] [--json]`:读 loop 运行记录(落点 project.json `paths.loop_runs_root`, 缺省 `5_tasks/records/loop_runs/<卡ID>/`)+ 本机探活 + 判停滞, 给出运行中 / `stalled` / `launch_dead` / `loop_dead` / 已结束(含结束原因)、当前步与等待对象、拉起进程存活 / 已运行 / 距最近输出、记录与日志路径;缺 `--task-id` 列本项目全部未结束的运行。loop 的人读输出同时写进记录旁的日志(路径由 loop 首行与 status 给出), 后台跑 loop 不必自选 `/tmp` 落点。**后台跑 loop 后一律用 `lybra loop status` 看进度, 禁 tail / grep 原始日志自判**(判据唯一在产品: 状态集、停滞阈值与结束原因声明在 `schema/verbs.schema.json` `lybra_loop.run_record`)。记录与日志只含步骤 / 进程 / 活动类别与时间 / 结束原因, 不含会话正文、工具参数与凭据 |
| 取下一张 / 按子项目看进度 | 顾问 / Owner | 结案后取下一张 = `lybra next`(不带 `--task-id` = 项目扫描):首行「下一张可推进卡」= pending、可推导(依赖全部满足)的卡中优先级最高者(priority 值序读 `enums.schema`, 同级按卡号), 附可照抄的认领命令;没有就写明原因。依赖判据唯一:卡 `depends_on` 每一项都满足 `dependency_condition`(缺省 = 被依赖卡已结案;值域与判据声明在 `schema/card.schema.json` `dependency_gate`)才算满足, 只读被依赖卡的门生记录, 不读卡面自报;依赖未满足的卡 `lybra next --task-id` 不派生认领、门 claim 拒(`DEPENDENCY_UNMET`)。按子项目(lane = 卡解析后的产品仓, project.json `repos.items` 仓名;单仓项目 = code_repo 路径)看:`lybra next` / `lybra brief` / `lybra loop status` / `lybra needs-owner` 同一 `--lane <仓名>` 过滤(参数声明 `schema/verbs.schema.json` `lane_view`;子项目跨仓时重复给出 `--lane <仓a> --lane <仓b>` 一并看);不带 `--lane` 时 `brief` 与 `needs-owner` 按 lane 分组输出。lane 解析不了的卡:不带 `--lane` 时标「(lane 不可解析)」照列并给原因;带 `--lane` 时不混入所选 lane, 在输出末尾单独一组「未归 lane N 张」(计数 + 卡号, 补卡面 `lane.repo` 或冻结);`--lane` 写了未声明的仓 = 拒并列出可选值。**视图口径(AIPOS-F141)**:四个视图共用唯一可见卡入口 `machine_zone.visible_cards`(声明 `lane_view.visible_cards`), 与 `next` 同一口径——存量冻结卡(`lybra project freeze-legacy`)缺省不列, 末尾汇总「冻结 N 张未列」;`--include-frozen` 照列并标 `[frozen]`(只读);冻结清单读不出 = 不隐藏任何卡并点名错误;`lybra loop status --task-id <卡>` 是显式点名, 冻结也照列 |
| 开工 | 执行 / 审计工位 | 两种模式:手工(缺省)= Owner 在工位敲 `/go`;授权拉起 = Owner 签带 `--launch-harness` 的信封后由 `lybra loop` 在本机工位拉起 harness(`--no-launch` 强制手工)。工位只在卡分支提交并把报告写到项目声明的落点, 不调用任何门动词 |
| 等待产物 | `lybra loop` 内部 / 任何能跑 bash 的 agent | `lybra agent watch --workspace-root <治理根>`(纯客户端文件哨兵, 退出码见 [`docs/agent_watch_exit_codes.md`](../../docs/agent_watch_exit_codes.md)) |
| 工位分发 | 工位 | `lybra roles enroll`(凭注册码) + `lybra sync`(工位发起拉取);`--harness` 合法值 = `harness_semantics.kinds` 的键(未知值拒并列出合法值, 先于兑换、零写盘) |
| 治理落账 / 体检 | 顾问 | `lybra governance-commit`(卡由 loop 结案后自动落账;非卡改动用 `--paths`);`lybra state lint`;冷启动简报 `lybra brief` |
| 指定治理根 | 所有角色 | 经 ssh 跑命令时先 `cd` 到项目治理根(含 project.json 与队列), 或显式 `--workspace-root <治理根>`(全局 `lybra --workspace-root <根> <命令>` 与子命令后 `<命令> --workspace-root <根>` 同义, 两处不同即拒 `GOVERNANCE_ROOT_CONFLICT`);不在任何治理根内又未给 = 拒 `GOVERNANCE_ROOT_UNRESOLVED`, 绝不回落 home 级活动项目。读写命令同一解析 `workspace_config.resolve_governance_root`, 序/文案/可带子命令级参数的命令清单声明在 `schema/verbs.schema.json` `governance_root_resolution`;`next` / `needs-owner` / `brief` / `loop status` / `state lint` / `queue` 首行标注解析到的项目与来源(`--json` 时走 stderr) |
| Owner 动作 | Owner | `lybra envelope mint`(签信封);`lybra roles … enroll-code`(发注册码);`lybra serve`(起门);`lybra board`(看板) |
| 同一项目多顾问实例 | Owner + 各顾问 | 每个顾问实例各一张带 `--instance` 的注册码(顾问/规划方类码缺实例即拒)、各自 `lybra roles enroll` 到同一治理根(凭据按实例并存, 重接入只轮换本实例;`.lybra/role` 按实例分槽 `instances.<实例>`, 各顾问开局 `lybra charter --role advisor --instance <本实例>` 取本实例槽渲染的章程, 未登记实例即拒并列出已登记实例);各签一张按实例覆盖的信封, 限定 lane 时加 `lybra envelope mint --lane-repo <仓名>`(仓名取 project.json `repos.items`, 不可解析即拒;不给 = 不限 lane, 行为不变);**子项目跨仓**:信封与视图写仓集合(`--lane-repo <仓a> --lane-repo <仓b>` 重复给出, 不按逗号拆), 共用仓可同时出现在多个顾问的集合里, 同仓合入由 F135 自动排队;集合内任一仓不可解析即拒;存量单值信封按单元素集合读, 行为不变;推进一律 `lybra loop --task-id <卡ID> --actor <顾问实例>`(只用该实例自己的凭据, 无凭据即拒;两个及以上顾问实例时缺 `--actor` 即拒)。步骤详见 [`docs/mcp-agent-setup.md`](../../docs/mcp-agent-setup.md)「Several advisors on one project」 |

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
