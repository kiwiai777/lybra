---
name: advisor-commands
description: 顾问动词手册——所有产品命令的参数/示例/何时用,标注已退役手搓片段
version: 1.0.0
role: advisor
---

# advisor-commands — 顾问动词手册

**角色**:advisor(顾问)专用。这是你的**命令快查表**,所有 lybra 产品命令的参数、示例、使用时机。

> **示例中的尖括号为占位, 以本工位 .lybra/role 与 project.json 为准**:`<你的顾问实例>`/`<执行体实例>`/`<审计体实例>` 读 `.lybra/role` 的 instance 与项目声明的角色实例;`<项目名>`/`<治理根>`/`<代码仓>` 读 project.json;`<卡ID>` 写真实卡 ID。本 skill 项目无关, 任何项目的顾问照此替换即可执行(AIPOS-F84 件①: 技能不渲染, 原样分发)。

## 为什么(长期有效)

**问题源**:历史顾问靠手写脚本直接调门接口(代按认领、手按 Owner 两跳确认、手提裁决),
参数易漂移、缺参报错不友好、每次压缩后要翻文档重拼, 换一个顾问就推不动。产品命令(`lybra` CLI)参数由 schema 驱动,
缺参自报错含可抄示例;AIPOS-F90 起一张卡从发布到结案只靠 `lybra loop` 推进, 顾问零手写门接口。

**长期有效性**:产品命令集随 loop 演进持续增长;顾问作为第一交互面,需要稳定的命令索引;
本 skill = 命令快查表 + 退役债标注,随产品命令上线同步更新。

---

## 工作流阶段表 (AIPOS-F73C件④ · AIPOS-F73D件④ 加「推进」行)

顾问在各阶段的职责与可用命令：

| 阶段 | 职责 | 主要命令 |
|------|------|----------|
| **N0 出卡** | 起草与发卡 | `lybra draft create/publish`, `lybra queue amend/withdraw` |
| **推进** | **Owner 信封授权下, 一条命令把卡从当前节点推到 completed**(产物落盘自动 return→派审→裁决→finalize→close; agent 步只等产物; 信封 `launch_harnesses` 授权时 loop 在工位拉起 harness, 否则手工 `/go`, 见「两种开工模式」) | **`lybra loop --task-id <卡ID>`**(AIPOS-F73D; 替代逐步 `next --run`) |
| **N1 认领** | 监督认领流程 | `lybra loop`(推进行; 一段式: 驱动方信封 PreAuthorized, 门在同一步建卡工作树, **建树失败 = 门拒认领**, 队列不变); 单步查看 `lybra next --task-id <卡ID>`; `lybra my-tasks` 查询 |
| **开工(工位)** | 执行/审计工位开工 | **两种模式**(AIPOS-F95): ①授权拉起 = 信封带 `launch_harnesses`, `lybra loop` 等待前在工位拉起 harness(开工提示 = 产品 `my-tasks` 的 `next_card.kickoff`); ②手工 = Owner 在工位敲 **`/go`**(产品选卡并核验: 非 claimed/非本实例/已结案/产物已交即拒并给原因)。顾问**不贴卡号/卡路径/开工稿**(AIPOS-F90 件③) |
| **开工渲染** | 把卡意图面按 harness 渲染给执行引擎(pi/codex/claude-code), 派子 agent 只看渲染物 | **`lybra card render --task-id <卡ID> --harness <harness>`**(AIPOS-F78 件②; 零门动词/零 token) |
| **N2 执行** | 监督进度 | 无直接干预 (执行体在卡分支提交 + 把 Return 落到项目声明落点; `lybra loop` 经 agent watch 等它落盘) |
| **N3 交回** | 监督交回流程 | `lybra loop`(推进行; 推导核派生 `lybra artifact ingest --kind return`: 校验 Return frontmatter 与分支 tip, **模型字段由产品从会话记录填写**, 自报只作对照) |
| **N4 审计** | 代码卡审计入门 / 审非代码卡 | 代码卡: `lybra loop`(审计报告落盘后派生 `lybra artifact ingest --kind verdict`: 绑被审分支 tip、产品填模型字段、**门把报告全文快照进 records**, 顾问不手提裁决); 非代码卡顾问自审: `lybra audit-verdict`; 手动派审 `lybra audit dispatch` |
| **返工** | 追加返工节 | `lybra queue rework --confirm` (AIPOS-F75, F73C件⑤) |
| **N5 finalize** | 监督交付上线 | `lybra loop`(推进行) |
| **N6 收账/落账** | 治理真相进治理仓(提交+推送) | **每张卡由 `lybra loop` 结案后自动落账**(N6 落账步 = `lybra governance-commit --task-id <卡ID>`, task 范围精确提交, 幂等; AIPOS-F94);**非卡改动**(项目声明、治理文档、信封与决策记录)用 `lybra governance-commit --paths` 精确提交;查漏 `lybra state lint`(GOVERNANCE_UNCOMMITTED);编年史 `generate_backlog_entry.py`, 决策 `lybra owner-decision` |

**关键原则**:
- **推进由产品执行** (`lybra loop --task-id <卡ID>`)，顾问不手搓门动词、不逐步代按。`lybra next --run` 单步入口保留为 loop 的内部执行体与排障单步, **顾问逐步手按 `next --run` 这条人工路径退役**(AIPOS-F73D, Δ=-1)。
- **返工节只能通过 `lybra queue rework` 追加**，禁手写卡面 rework_rounds 字段。
- **next-step 导航**：用 `lybra next --task-id <卡ID>` 查询当前状态与下一步动词(`next-step` 已退役转发)。
- **落账规则(AIPOS-F94)**:卡与记录只在盘上 = 未成为可追溯真相。每张卡结案后由 `lybra loop` 自动执行 N6 落账(该卡与其审计卡的队列文件、草稿、各类记录、台账落点 + 卡编年史, 路径由产品按卡与声明推导, 禁整根);非卡改动(`project.json` 声明、治理文档、信封与决策记录、接入向导各步产物)一律 `lybra governance-commit --paths <这些路径>`;永不手敲 `git add`/`git commit`。

---

## 命令分类索引

### 🎯 发卡与改卡(N0 出卡)

#### `lybra draft create`
**何时用**:新建任务草稿。
```bash
lybra draft create --from-template basic --task-id <卡ID> --title "..." --project <项目名>
# 生成 5_tasks/drafts/<卡ID小写>.md(--from-json <草稿JSON> 为另一入口, 二选一)
```

#### `lybra draft publish`
**何时用**:发布草稿到 pending 队列(N0)。
```bash
lybra draft publish --path 5_tasks/drafts/<卡ID小写>.md --dry-run   # 先预演
lybra draft publish --path 5_tasks/drafts/<卡ID小写>.md
```
发布者身份取驱动方凭据绑定的实例(本命令无 `--actor`)。
**自动校验**:schema 校验(必填/拼错字段)、N0 容量 lint(交付大项>3 会 WARN)。

#### `lybra queue amend`
**何时用**:改卡(如 `needs_owner: true → false` 解放行阻塞)。
```bash
lybra queue amend --task-id <卡ID> --amendments '{"needs_owner": false}' \
  --amendment-reason "PreAuthorized release" --actor <你的顾问实例>
```
**claimed 卡受限改车道(AIPOS-F78 前置零⑤, 治 output_target 三次漏列)**: 卡已认领后交回撞 `CHANGES_OUT_OF_SCOPE` → 顾问补车道目录, 不重发卡:
```bash
lybra queue amend --restricted --task-id <卡ID> --actor <你的顾问实例> \
  --amendments '{"lane": {"repo": "<代码仓>", "paths": ["src/", "tests/"], "roles": ["executor"]}}' \
  --amendment-reason "补车道: 漏列 tests/"
# 允许字段唯一声明 card.schema restricted_amend.claimed_card_fields = rework_rounds / output_target / lane
```

#### `lybra card render`(AIPOS-F78 件②)
**何时用**:派子 agent(Claude Code / Codex / pi)开工前, 把卡的意图面渲染成该引擎的开工物。同一源三输出, 渲染物 `grep lybra_` = 0。
```bash
lybra card render --task-id <卡ID> --harness pi --stdout            # 三行: 工作树 / 报告落点(含必填 frontmatter) / 卡路径
lybra card render --task-id <卡ID> --harness codex                  # Prompt.md + Plan.md 写入工作树根(.worktrees/<卡ID>)
lybra card render --task-id <卡ID> --harness claude-code --out-dir <输出目录>   # CLAUDE.md 片段
```
**落点全读声明**: 工作树=config.schema worktree_root; Return 落点=project.json `paths.return_root`(未声明缺省 task_cards; 另一常见形=5_tasks/records/returns); 分支=transitions N5.branch_integration。

#### `lybra artifact ingest`(AIPOS-F78 件③ / F90 件②, 由 loop/next --run 触发, 非人用)
**何时用**:排障——执行体/审计体说"写完了"但 loop 没动, 看产物入口为何拒。return/verdict 步的派生命令就是它(带 `--kind`)。
```bash
lybra artifact ingest --task-id <卡ID> --kind return --dry-run      # 只校验: 找 Return(声明落点)、必填 frontmatter(commit_sha/tree_hash/branch/model)、分支 tip==commit_sha
lybra artifact ingest --task-id <卡ID>R --kind verdict --dry-run    # R 卡: 找审计报告(verdict + commit_sha==被审分支 tip)
```
拒因码: INGEST_RETURN_MISSING / INGEST_FRONTMATTER_MISSING / INGEST_TIP_MISMATCH / INGEST_TREE_MISMATCH / INGEST_SUMMARY_MISSING / INGEST_KIND_MISMATCH(exit 4)。通过则走推导核同时给出的同一条 `queue return --confirm` / `audit-verdict --confirm` 薄壳(驱动方 token, actor=卡实例), 并补上产品填写的 `agent_runtime`(运行时模型取自卡面 harness 的会话记录, 取不到记「未声明会话记录」, 与自报不一致标 `model_mismatch`); 裁决入门时门把审计报告全文快照进 `records/audit_verdicts/<卡ID>/report_snapshots/`。

#### `lybra queue withdraw`
**何时用**:撤卡(malformed/方向错误/重复发卡)。
```bash
lybra queue withdraw --task-id <卡ID> --reason "duplicate" \
  --actor <你的顾问实例>
```

#### `lybra queue rework`
**何时用**:追加返工节到卡面(FAIL 裁决后,点杀清单+验收标准)(顾问职责).
```bash
lybra queue rework --task-id <卡ID> --actor <你的顾问实例> \
  --verdict-ref "<裁决记录ID>" \
  --focus-items '["fix item 1", "fix item 2"]' \
  --acceptance-criteria '["criterion 1", "criterion 2"]' \
  --confirm
# 凭据自动发现(工位 .lybra/connection.json, 或 --connection-json <凭据文件>); token 永不上屏
```
**约束**:
- 只能由 advisor 调用(需 queue_rework scope);
- 必须带 `--confirm` (两阶段门动词);
- rework_rounds 字段禁手写,只能通过本命令追加。

---

### 🚀 推进与监督

#### `lybra loop`
**何时用**:卡已认领(或已发布)后, 顾问用一条命令把它推到 completed(AIPOS-F73D)。前提: Owner 已签发覆盖本卡/本驱动方的 autonomy 信封(`lybra envelope mint`, 见下)。
```bash
lybra loop --task-id <卡ID>
# 可选参数全写形:
lybra loop --task-id <卡ID> --envelope <信封ID> --actor <你的顾问实例> --max-steps 20 --max-wait 1800 --interval 15 --json
```
**每轮**:`next` 推导 → 账务命令(claim/return/dispatch/verdict/finalize/close)先过 argparse 解析再经 `next --run` 同一执行体执行并重推导;agent 步(执行体/审计体在干活)只调 `agent watch --expect` 有界等待产物(落点读项目声明 project.json `paths.return_root`/`paths.verdict_root`, 未声明缺省=`task_cards/<卡ID>/RETURN.md` 与 `task_cards/<卡ID>R/RETURN.md|audit_report.md`, 骨架不算);closure 记录存在且治理已落账(该卡范围已提交并推送)即 exit 0——未落账则先执行推导核派生的 N6 落账步 `lybra governance-commit --task-id <卡ID> --actor <驱动方> --governance-root <治理根>`(AIPOS-F94; 落账步声明在 transitions `nodes.N6.landing`, 信封独立授权 `governance_commit`(verbs.schema `lybra_loop.envelope.allowed_verbs`); 遇他人暂存 / 护栏拒 / 推送未完成 = exit 2 透传拒因, 不重试、不动他人暂存; 治理根不在 git 仓 = exit 4, 出口 `lybra home git-init --home-root <home根>`)。
**AIPOS-F78 起**: 账务动词一律驱动方(advisor)token 提交、actor=卡实例(执行体/审计体 token 零账务 scope); claim 经 Owner 信封一阶段放行(信封 `agent_or_role` 须覆盖驱动方实例或 `advisor`); return/verdict 步经 `artifact ingest` 校验 Return/报告 frontmatter 与分支 tip; close 的三字段(finalize_commit_hash/finalize_return_ref/verdict_ref)从 finalization(`merge_commit`)/return/verdict 记录自填, 缺一即 exit 4 点名; 驱动方身份读工位 `.lybra/role` instance 或驱动方 token 绑定实例, 不再占位 `advisor`。
**AIPOS-F90 起**: `--envelope`/`--actor` 贯穿推导与执行(派生的 claim/return/verdict/close 都带同一 `--owner-policy-ref`); 认领从 pending 一步走通(门内同步建工作树, 建树失败=拒认领); 等门应答超时不报假失败——薄壳按 verbs.schema 声明回读真相(已由本实例认领=成功), loop 遇执行端报失败先回读该步门生记录, 已落即继续、绝不重复执行同一步。
**四出口(verbs.schema `lybra_loop.exit_codes` 唯一声明)**:0=completed;2=门拒(透传拒因原文, 不重试);3=等待产物超时/停滞或 --max-steps 用尽(输出等的是哪份产物);4=推导不可推导/派生命令解析失败(输出 missing_records);5=无有效信封(输出 `lybra envelope mint` 申领出口)。
**红线**:不自己唤醒 agent——拉起只由 `lybra loop` 在 Owner 信封 `launch_harnesses` 授权下按声明模板完成(见下「两种开工模式」), 否则开会话由 Owner 在工位敲 `/go`;禁 sleep 自旋(等待一律经 watch);token 永不上屏;禁直调 board_adapter。

#### 两种开工模式(AIPOS-F95)
| 模式 | 前提 | 谁做什么 |
|------|------|----------|
| **授权拉起** | Owner **同意**后, Owner 亲自敲 `lybra envelope mint ... --launch-harness pi ... --confirm` 铸信封(信封 `launch_harnesses` 含该卡 harness) | 顾问跑 `lybra loop --task-id <卡ID>`: 执行体/审计体等待前, loop 按 enums.schema `harness.launch` 模板在工位(本项目 enrollment_log land 事件的 `host`/`workstation`)拉起一次 harness, 过程汇总成 `[<harness> <卡ID> pid=…] 工具 …/助手 …/错误 …` 一行式进度显示在顾问界面; 产物就绪/超时/早退/中断即清整个进程组; 拉起与收尾记进该卡 session record Events |
| **手工** | 信封无 `launch_harnesses`(缺省), 或给 `--no-launch`, 或条件不满足(harness 无模板 / 工位定位不到或身份不符 / 跨机工位材料未声明或 ssh 不可达) | loop 输出 `manual: 请在 <工位目录> 的 <harness> 会话敲 /go`(跨机: `请在 <host>:<dir> 的 <harness> 会话敲 /go(跨机工位)`), Owner 在工位敲 `/go`, loop 照旧经 watch 等产物 |

- **授权须经 Owner**: 顾问先向 Owner 说明「将由 loop 在 <工位目录> 拉起 <harness>」并取得同意, 铸信封命令由 Owner 亲自敲; 顾问永不代敲、永不自行拉起。
- **loop 在后台跑时顾问只读其输出**(进度行与出口原文), **禁 `until`/`sleep` 轮询**; 等它结束再读结果。
- 一次 loop 对一张卡至多拉起一次, 不自动重试; 早退(exit 3 附 stderr 末尾)/超时后重跑 `lybra loop` = 显式再拉起。
- 跨机工位(land 事件 host ≠ 本机)经 ssh 拉起, 接入步骤见下「跨机工位」; 材料未声明或 ssh 不可达 = 自动退回手工并给原因。

#### 跨机工位(AIPOS-F110: 执行体在别的机器, 门与治理根在本机)
前提: loop 只在治理根所在机(门机)跑; 门机与工位机 ssh 双向可达——门机→工位(loop 拉起/清理), 工位→门机(执行体读写门机上的治理根/工作树/报告落点)。ssh 凭据只走两端各自的 ssh 配置与密钥(`BatchMode=yes` 不交互索要口令), 永不经 Lybra、不进开工提示。
1. **接入**(Owner 亲自敲, 在门机产品仓根下; land 事件 host = `--ssh` 的 ssh 目标, loop 以它为拉起目标; 注册码经 ssh stdin 送达、Owner 凭据只在本机读 connection.json 调门, 均不进远端命令行):
```bash
python3 -m tools.aipos_cli.enroll_deliver --role executor --instance <执行体实例> \
  --target-workspace <工位目录> --target-harness <工位目录> --ssh <ssh目标> \
  --gate-url <门地址> --owner-policy-ref <信封ID> --connection-json <Owner凭据>
```
2. **声明开工材料**(远端视角: 工位上指向门机的 ssh 别名 + 一句话材料访问方式, 如「经 ssh <别名> 读写; 代码提交到卡分支并推回门机产品仓」; 禁含凭据; 未声明 = loop 拒拉起并提示本命令):
```bash
lybra project set-workstation <项目名> --home-root <home根> --instance <执行体实例> \
  --gate-ssh-alias <门机别名> --material-access "<材料访问说明>"
```
3. **双向可达检查**(只读, 与 loop 同一 ssh 代码路径; 门机→工位: 工位目录在、harness 可执行在远端非交互 PATH; 工位→门机: 经别名 `test -d <治理根>`; 任一 ✗ 先修 ssh 配置再推进):
```bash
lybra project check-workstation <项目名> --home-root <home根> --instance <执行体实例> --harness pi
```
4. **推进**照常 `lybra loop --task-id <卡ID>`: 执行体等待前 loop 经 `ssh -T <ssh目标>` 在工位目录以新进程组起 harness, 开工提示经 ssh stdin 逐字节传入(不进命令行、不经远端 shell 展开), 提示末段写明工作树/报告落点/任务卡在门机及材料访问方式(产品 `render_kickoff` 单源); 产物就绪/超时/早退/中断(含 ssh 断线 SIGHUP)即经 ssh 清远端进程组, 本地 ssh 子进程同清。执行体交付不变: 报告写门机报告落点(经材料通道), 代码提交卡分支并推回门机产品仓(卡面 lane.repo), loop 仍在门机看产物。

#### `lybra mark-concluded` / `lybra queue close --conclusion-note`(AIPOS-F78 前置零⑨)
**何时用**:已 PASS 但不走 finalize 的卡(如产物由续卡承接):
```bash
lybra mark-concluded --task-id <卡ID> --actor <你的顾问实例> --conclusion-note "工作在 card/<卡ID> 完成, 由续卡 <续卡ID> 承接交回"
```
PASS 裁决 + 承接声明即放行登记承接世系(F53 lineage 读 `conclusion_note`/`continuation_task_id`); FAIL/BLOCK 仍拒(走 `queue rework`)。`queue close` 亦可带 `--conclusion-note`。

#### `lybra audit dispatch`
**何时用**:派审(手动指定审计者,或 FIX 打回后复审)。
```bash
lybra audit dispatch --task-id <卡ID> --actor <你的顾问实例> \
  --agent-instance <执行体实例> --owner-policy-ref <信封ID> \
  --audit-task-id <卡ID>R --audit-agent-instance <审计体实例>
```
**生成**:dispatch 记录 + 带 `reviewed_task_id` 的 R 卡。

#### `lybra next`(`next-step` 已退役转发, AIPOS-F71)
**何时用**:查询任务当前状态 → 下一步动词+完整参数(AIPOS-R7A 大项C;AIPOS-F71 起唯一实现为 `lybra next`)。
```bash
lybra next --task-id <卡ID>
```
**输出**:当前状态、下一步动词、完整命令、触发者、授权语义(由 transitions.schema 生成,禁口述)。

**对外陈述序列必须由它生成**(LOOP-REDESIGN §4.5 A13)——口述 = 记忆叙述 = 漏步漂移。

---

### ⚖️ 审非代码卡(N4 分路)

#### `lybra audit-verdict`
**何时用**:你审非代码卡(docs/governance/config)时提交裁决。
```bash
lybra audit-verdict --reviewed-task-id <卡ID> --verdict PASS \
  --actor <你的顾问实例> --findings-summary "..."            # 两阶段: 先预演
lybra audit-verdict --reviewed-task-id <卡ID> --verdict PASS \
  --actor <你的顾问实例> --findings-summary "..." --confirm  # 再确认落记录
```
**禁止**:手写裁决文件(必经 gate MCP 落 `5_tasks/records/audit_verdicts/`,归因在案)。
**禁止**:审自己执行的卡(升级 Owner 或独立审计)。

---

### 🔐 信封与仲裁(Owner 决策)

#### `lybra envelope mint`
**何时用**:签发 PreAuthorized 信封(驱动方 `lybra loop` 一阶段推进所需; 工位 enroll 推导 owner_policy_ref 所需)。
**Owner 亲自敲**(签信封须 Owner 凭据的 owner_confirm; 顾问只给出命令, 不代敲)。AIPOS-F92: `--confirm` 经门 owner_decision_record
envelope 路径真实落盘(输出以门生记录为准: policies/<信封ID>.md + owner_decisions 记录); `--dry-run` 同一 writer 本地预演。
`--policy-id`/`--agent-or-role` 可重复、按顺序成对, 一条命令签一组(接入向导第 4 步即一条签三张):
`--launch-harness <harness>`(可选, 可重复, AIPOS-F95): 授权 `lybra loop` 拉起该 harness(须有 enums.schema `harness.launch` 模板, 现只 `pi`); 缺省 = 只手工 `/go`。信封正文 Boundary 按声明渲染(列出 loop 动词集合与拉起授权实值)。
```bash
lybra envelope mint --confirm --workspace-root <项目根> --connection-json <Owner凭据> \
  --policy-id <信封ID> --agent-or-role <你的顾问实例> --max-tasks 60 --task-mode code \
  --expires-at <到期时间> --decision-summary "loop envelope" --actor owner
# 授权拉起(Owner 同意后): 加 --launch-harness pi
lybra envelope mint --confirm --workspace-root <项目根> --connection-json <Owner凭据> \
  --policy-id <信封ID> --agent-or-role <你的顾问实例> --max-tasks 60 --task-mode code \
  --expires-at <到期时间> --decision-summary "loop envelope (launch pi)" --actor owner --launch-harness pi
lybra envelope mint --dry-run --workspace-root <项目根> --policy-id <信封ID> --agent-or-role <执行体实例> \
  --max-tasks 60 --task-mode code --expires-at <到期时间> --decision-summary "preview" --actor owner
```

#### `lybra envelope revoke`
**何时用**:吊销信封(紧急情况/额度滥用)。⚠ 尚未产品化: 现命令走非信封决策路径, 预演即被 writer 拒(缺 AIPOS-110 字段), 经门落盘未实现(AIPOS-F92 未做项); 紧急停用请报 Owner。
```bash
lybra envelope revoke --policy-id <信封ID> \
  --revocation-reason "Emergency stop" --actor owner
```

#### `lybra envelope renew`
**何时用**:续额/延期已有信封。⚠ 尚未产品化(同 revoke, AIPOS-F92 未做项); 续额暂以新 `--policy-id` 再签一张(`lybra envelope mint --confirm`)。
```bash
lybra envelope renew --policy-id <信封ID> \
  --add-tasks 30 --new-expiry 2026-10-01T00:00:00Z \
  --decision-summary "Q3 extension" --actor owner
```

#### `lybra owner-decision`
**何时用**:记录 Owner 仲裁/豁免/政策变更决策。
```bash
lybra owner-decision --decision-id arb-2026-08-16-01 \
  --decision-type arbitration --task-id <卡ID> \
  --decision-summary "Approve despite FAIL: emergency hotfix" \
  --actor owner
```
**典型场景**:审计争议、FIX 打回超 2 轮升级、紧急豁免。

---

### 📦 角色供给(enroll-deliver)

#### `lybra roles enroll`
**何时用**:在工位目录用注册码(`roles enroll-code` 产出)兑换凭据, 初始化工位(凭据+配置+工具包)。
```bash
lybra roles enroll --code <注册码> --workspace <工位目录> --verify
```
**生成**:`.lybra/` 配置(connection.json/role/policy)、工具包、skills。

#### `lybra roles enroll-code`
**何时用**:为跨机角色生成一次性注册码(未来:enroll-deliver 跨机形态)。
```bash
lybra roles enroll-code --role auditor --ttl 86400 --governance-root <治理根> \
  --reason "Onboarding auditor" --json
```
注册码一次性、有时效;不贴进聊天/文档(凭据不过手)。

---

### 📊 收账与治理(N6)

#### `generate_backlog_entry.py`(Lybra 产品仓脚本, 非 `lybra` 子命令)
**何时用**:生成卡编年史条目(项目在 project.json `paths.foundation_backlog` 声明了卡编年史时; close 也会自动生成)。
```bash
python3 <Lybra产品仓>/tools/generate_backlog_entry.py <卡ID> --governance-root <治理根>
```
**输出**:可追加到项目卡编年史(`paths.foundation_backlog` 声明的文件)的 markdown 段。

**N6 收账固化清单**(LOOP-REDESIGN §2 N6):
1. 卡编年史本卡条目(项目声明了 `paths.foundation_backlog` 时; 未声明跳过)
2. decision_log 指针(如有 Owner 裁定/仲裁/信封授权与吊销)
3. stage_archive 快照(阶段关账时)
4. 治理仓 push(push 是节点一部分,不 push = 没收口)—— 只经下面的 `lybra governance-commit`

#### `lybra governance-commit`(治理收尾唯一提交口)
**何时用**:治理仓落库(N6 收账 / 台账追加 / 裁定入档)。真相层唯一提交口,永不手敲 `git add`/`git commit`。
**卡的落账(AIPOS-F94)**:`--task-id` 不带 `--paths` = task 范围精确提交——产品按卡与声明推导路径(本卡与审计卡的队列文件 / 草稿 / `records/<类型>/<卡ID>/` / 台账落点 + 卡编年史; 被 .gitignore 排除者列出不提交), 禁整根; 已提交且已推送 = no-op(幂等)。`lybra loop` 结案后自动执行它, 顾问只在 loop 之外补账时手敲:
```bash
lybra governance-commit --task-id <卡ID> --actor <你的顾问实例> --governance-root <治理根> --dry-run   # 先看推导出的清单
lybra governance-commit --task-id <卡ID> --actor <你的顾问实例> --governance-root <治理根>
lybra state lint --workspace-root <治理根>   # GOVERNANCE_UNCOMMITTED 点名已结案未落账的卡并给上面这条出口
```
**非卡改动**(声明 / 治理文档 / 信封与决策记录)用下面的 `--paths` 形:
**AIPOS-F79 铁律:先 `--dry-run` 看清单,再去掉 `--dry-run` 正式提交;他项目一律 `--paths`。**
```bash
# ① 预演:只读列出将提交的具体文件(modified/added/deleted/untracked_selected),不 add/不 reset/不 stash
lybra governance-commit --governance-root <治理根> --actor <你的顾问实例> \
  --paths governance/ORCHESTRATOR-RESUME.md --paths governance/decision_log/INDEX.md \
  --dry-run --json
# ② 正式提交(同一命令去掉 --dry-run):只 git add -- <paths>,提交后 git show --name-only HEAD 与清单逐条核对,再走 F69 fetch→rebase→push
lybra governance-commit --governance-root <治理根> --actor <你的顾问实例> \
  --paths governance/ORCHESTRATOR-RESUME.md --paths governance/decision_log/INDEX.md
# 路径多时用清单文件(每行一路径,# 行忽略;与 --paths 二选一,同一实现)
lybra governance-commit --governance-root <治理根> --actor <你的顾问实例> --paths-file <清单文件>
```
**规则**:
- `--paths` 相对治理根(文件或目录,可重复);越出治理根/指向他项目/`.`(整根)一律拒(fail-closed,退出码 1,`rejected_paths` 列出)。
- 预暂存文件:在 `--paths` 内 = 纳入清单;在 `--paths` 外 = 拒并列出(`pre_staged_outside`)。
- **你的项目一律 `--paths`**;无 `--paths` 的整根提交(`git add -A -- .`)是产品为其自身治理工作区保留的形态(实现见 governance_commit)——无 `--paths` 的 `--dry-run` 会列出整根范围并 WARNING 标出未跟踪文件数。
- AIPOS-R6M 文件级护栏(frontmatter/decision_log 只增/record_type)照旧对选定文件生效;禁用「清理/忽略/搬走历史材料」规避,不加 .gitignore。
- 退出码:0=PASS(含无待收 no-op 与 dry-run);1=BLOCK/FAIL;2=参数用法错误。参数与退出码声明在 `schema/verbs.schema.json` `lybra_governance_commit`。

---

## 已退役路径(手写门接口债)

**状态**:顾问手写脚本直接调门接口(代按认领、手按两跳确认、手提裁决)的路径**已退役**(LOOP-REDESIGN §4.5 A12, AIPOS-F90 件③),
不再保留任何示范;**实操只走产品命令**(上面列出的 `lybra` CLI, 推进只用 `lybra loop`)。

### 退役原因
1. **参数易漂移**:手拼 JSON 字典,卡头字段改名/枚举值变更时没编译期检查。
2. **缺参不友好**:报错只说 `missing key`,不说哪些参数必填、合法值域。
3. **压缩后重拼**:上下文压缩丢失手搓片段,顾问要翻文档重写。
4. **产品命令优势**:参数由 verbs.schema 驱动,缺参自报可抄示例,与 gate 同版本同 deploy。


| 原手搓操作 | 替代产品命令 | 退役日期 |
|-----------|------------|---------|
| 手写门接口脚本(发卡/改卡) | `lybra draft publish`、`lybra queue amend` | AIPOS-F73C |
| 手写门接口脚本代按认领(含 Owner 两跳确认) | `lybra loop --task-id <卡ID>`(信封一段式认领) | AIPOS-F90 |
| 手提裁决 | `lybra loop`(审计报告落盘后派生 `artifact ingest --kind verdict`) | AIPOS-F90 |
| 给工位贴卡号/卡路径冷启动 | 工位 `/go`(产品选卡并核验) | AIPOS-F90 |
| 手写 owner_decisions/*.md | `lybra envelope mint/revoke/renew` | AIPOS-R7A |
| 手写 owner_decisions/*.md | `lybra owner-decision` | AIPOS-R7A |
| 手写 audit_verdicts/*.md | `lybra audit-verdict` | 已上线 |
| 手搓 dispatch 记录 | `lybra audit dispatch` | 已上线 |
| 口述"下一步做 X" | `lybra next`(原 `next-step`, AIPOS-F71 并入) | AIPOS-R7A |
| 顾问逐步手按 `lybra next --run`(代按推进) | `lybra loop --task-id <卡ID>` | AIPOS-F73D |

**过渡期已结束**:上表产品命令均已上线(AIPOS-R7A / AIPOS-F73C 交付),ADVISOR-COMMANDS.md 已不在治理仓,手搓片段全退役;
门命令只以本 skill 命令快查表与 `lybra <子命令> --help` 为准。

---

## 常见反模式(禁止)

| 反模式 | 为什么禁 | 正确做法 |
|--------|---------|---------|
| 手写直调门接口的脚本 | 参数易漂移, 换顾问即推不动 | 推进只用 `lybra loop`, 其余用 `lybra` 产品命令 |
| 给工位贴卡号/开工稿 | 绕过产品开工核验(已结案卡被重审覆盖报告) | Owner 在工位敲 `/go` |
| 口述「下一步做 X」 | 记忆叙述 = 漏步 | `lybra next --task-id <卡ID>` 生成 |
| 手写裁决文件 | 绕过 gate 归因 | `lybra audit-verdict` |
| 对外消息/交接里留着占位 `<卡ID>` | 接收方冷启动不知道 | 写真实卡 ID(本 skill 的尖括号只是示例占位, 用前替换) |
| 缺参数自己猜 | 猜错 = 撞墙 | 命令缺参自报,照抄 |

---

## 本 skill 维护

- **目标读者**:advisor(顾问)快查命令用。
- **同步触发**:产品命令新增/改参时,同步更新本 skill 对应章节。
- **退役标注**:历史手搓片段不删除,标注为"已退役"+替代命令+退役日期(审计追溯)。
- **分发**:按 distribution.schema `advisor-skills` 条目分发到顾问工位(copy_tree 原样, 不渲染);executor/auditor 工位不装此 skill。
- **项目无关(AIPOS-F84 件①)**:正文禁写具体实例/项目/机器/绝对路径, 一律尖括号占位;命令示例占位替换为合法值后须能被 `lybra` argparse 解析(夹具 tests/test_aipos_f84_advisor_skills_generic.py 守)。

---

## 参考

- **LOOP-REDESIGN v2 §4.5**:顾问侧固化点表 A1..A13(动词包+next-step 导航)。
- **verbs.schema.json**:所有 gate 动词的参数定义(产品命令的单一源)。
- **transitions.schema.json**:状态机转移表(next-step 的单一源)。
- **ADVISOR-COMMANDS.md**:已退役(过渡期结束, 不再保留);底层 gate 动词参数以 verbs.schema.json 为准。
