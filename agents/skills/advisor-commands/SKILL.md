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
| **N0 出卡** | 起草与发卡(卡挂路线图里程碑, AIPOS-F171) | `lybra draft create/publish`, `lybra queue amend/withdraw`, `lybra roadmap show/status/add-milestone/add-criterion/set/remove/verify` |
| **推进** | **Owner 信封授权下, 一条命令把卡从当前节点推到 completed**(产物落盘自动 return→派审→裁决→finalize→close; agent 步只等产物; 信封 `launch_harnesses` 授权时 loop 在工位拉起 harness, 否则手工 `/go`, 见「两种开工模式」) | **`lybra loop --task-id <卡ID>`**(AIPOS-F73D; 替代逐步 `next --run`) |
| **跟进(持续推进)** | Owner 说一次「推进 <lane>」后不等 Owner 推: 跑 loop → 有界等 → 按产品给的顾问下一动作处理, 只在 owner_needed 停(守则全文在顾问章程「持续推进守则」, 本 skill 不复述) | **`lybra loop status --task-id <卡ID> --wait <秒>`**(AIPOS-F136) |
| **更新通知** | 产品上线后的顾问须知由产品送达(AIPOS-F167, Owner 不再转述): `lybra charter` 开头 / `loop status`、`next`、`brief` 末尾出现「Lybra 有 N 条未读更新」→ 读 → 记已读 → 继续推进(守则在顾问章程「持续推进守则」, 本 skill 不复述) | **`lybra updates --actor <你的顾问实例>`** → **`lybra updates --ack --actor <你的顾问实例> --confirm`** |
| **N1 认领** | 监督认领流程 | `lybra loop`(推进行; 一段式: 驱动方信封 PreAuthorized, 门在同一步建卡工作树, **建树失败 = 门拒认领**, 队列不变); 单步查看 `lybra next --task-id <卡ID>`; `lybra my-tasks` 查询 |
| **开工(工位)** | 执行/审计工位开工 | **两种模式**(AIPOS-F95): ①授权拉起 = 信封带 `launch_harnesses`, `lybra loop` 等待前在工位拉起 harness(开工提示 = 产品 `my-tasks` 的 `next_card.kickoff`); ②手工 = Owner 在工位敲 **`/go`**(产品选卡并核验: 非 claimed/非本实例/已结案/产物已交即拒并给原因)。顾问**不贴卡号/卡路径/开工稿**(AIPOS-F90 件③) |
| **开工渲染** | 把卡意图面按 harness 渲染给执行引擎(pi/codex/claude-code), 派子 agent 只看渲染物 | **`lybra card render --task-id <卡ID> --harness <harness>`**(AIPOS-F78 件②; 零门动词/零 token) |
| **N2 执行** | 监督进度 | 无直接干预 (执行体在卡分支提交 + 把 Return 落到项目声明落点; `lybra loop` 经 agent watch 等它落盘) |
| **N3 交回** | 监督交回流程 | `lybra loop`(推进行; 推导核派生 `lybra artifact ingest --kind return`: 校验 Return frontmatter 与分支 tip, **模型字段由产品从会话记录填写**, 自报只作对照) |
| **N4 审计** | 代码卡审计入门 / 审非代码卡 | 代码卡: `lybra loop`(审计报告落盘后派生 `lybra artifact ingest --kind verdict`: 绑被审分支 tip、产品填模型字段、**门把报告全文快照进 records**, 顾问不手提裁决); 非代码卡顾问自审: `lybra audit-verdict`; 手动派审 `lybra audit dispatch` |
| **返工** | 审计 FAIL 后追加返工节(缺省预演, `--confirm` 才写); 登记后 loop 自动等执行体按返工节修复重交 → 下一轮审计 | `lybra queue rework` → `lybra queue rework --confirm` → `lybra loop`(AIPOS-F75, F73C件⑤, F155) |
| **N5 finalize** | 监督交付上线 | `lybra loop`(推进行) |
| **N6 收账/落账** | 治理真相进治理仓(提交+推送) | **每张卡由 `lybra loop` 结案后自动落账**(N6 落账步 = `lybra governance-commit --task-id <卡ID>`, task 范围精确提交, 幂等; AIPOS-F94);**写命令自动落账**(AIPOS-F158/F159/F160):`project` 族写 project.json 的命令、`envelope mint`、`migrate …` 的 `--confirm` 写后即经同一提交口提交推送, 不再手跑;**其余非卡改动**(手改的治理文档、`owner-decision` 决策记录、`project new` 建根产物、落账失败的补账)用 `lybra governance-commit --paths` 精确提交;查漏 `lybra state lint`(GOVERNANCE_UNCOMMITTED);编年史 `generate_backlog_entry.py`, 决策 `lybra owner-decision` |

**关键原则**:
- **推进由产品执行** (`lybra loop --task-id <卡ID>`)，顾问不手搓门动词、不逐步代按。`lybra next --run` 单步入口保留为 loop 的内部执行体与排障单步, **顾问逐步手按 `next --run` 这条人工路径退役**(AIPOS-F73D, Δ=-1)。
- **返工节只能通过 `lybra queue rework` 追加**，禁手写卡面 rework_rounds 字段。
- **next-step 导航**：用 `lybra next --task-id <卡ID>` 查询当前状态与下一步动词(`next-step` 已退役转发)。
- **落账规则(AIPOS-F94)**:卡与记录只在盘上 = 未成为可追溯真相。每张卡结案后由 `lybra loop` 自动执行 N6 落账(该卡与其审计卡的队列文件、草稿、各类记录、台账落点 + 卡编年史, 路径由产品按卡与声明推导, 禁整根);写治理真相的命令写后自动落账(AIPOS-F158/F159/F160, 声明 `schema/verbs.schema.json` `lybra_governance_commit.post_write_landing.commands`):写 project.json 的 `project` 族命令(`set-*` / `freeze-legacy` / `dispatch-mode set`)、`envelope mint`、`migrate record-type` / `migrate decision-log-pointers` 的 `--confirm` 写后即精确提交并推送本次写出的文件, **之后不要再手跑 `governance-commit`**(已落账 = no-op, 多余);落账失败(exit 3)= 写入已生效, 只跑其输出给出的补账命令。其余非卡改动(手改的治理文档、`owner-decision` 决策记录、`project new` 建根产物)一律 `lybra governance-commit --paths <这些路径>`;永不手敲 `git add`/`git commit`。

---

## 命令分类索引

### 🎯 发卡与改卡(N0 出卡)

#### `lybra draft create`
**何时用**:新建任务草稿。
```bash
lybra draft create --from-template basic --task-id <卡ID> --title "..." --project <项目名> --actor <你的顾问实例>
# 生成 5_tasks/drafts/<卡ID小写>.md(--from-json <草稿JSON> 为另一入口, 二选一); --actor 填卡稿 created_by
```
**草稿写错了(还没发布)怎么办(AIPOS-F220)**:改好卡稿 JSON, 用同一命令加 `--replace` 重写这份草稿——不手改草稿文件、不换卡号:
```bash
lybra draft create --from-json <卡稿JSON> --replace --actor <你的顾问实例> --dry-run   # 预演: 新旧差异摘要, 零写入
lybra draft create --from-json <卡稿JSON> --replace --actor <你的顾问实例>             # 旧稿原样留档到 5_tasks/draft_history/<卡ID小写>/<时间戳>.md, 再写新稿
```
只修订本卡**未发布**草稿; 已发布 / 已入队 = 拒并给 `lybra queue amend` 命令(改卡面走它); 没有同 ID 草稿 = 拒(新建去掉 `--replace`)。
不带 `--replace` 撞号时拒因里会直接给出这条命令。

#### `lybra draft publish`
**何时用**:发布草稿到 pending 队列(N0)。
```bash
lybra draft publish --path 5_tasks/drafts/<卡ID小写>.md --actor <你的顾问实例> --dry-run   # 先预演
lybra draft publish --path 5_tasks/drafts/<卡ID小写>.md --actor <你的顾问实例>
```
`--actor` = 签发实例(AIPOS-F156): 记入发布记录 actor = 卡归属(共用仓「取下一张只取本子项目的卡」、loop 拒跑别人的卡都按它判);
按 `--actor` 实例核凭据(治理根连接文件须有该实例的顾问凭据)。同一治理根接入多个顾问实例时必填(缺 = 拒 `PUBLISH_ACTOR_REQUIRED`);
只接入一个时可省(推断为该实例)。
**自动校验**:schema 校验(必填/拼错字段)、N0 容量 lint(交付大项>3 会 WARN)、顾问须知(AIPOS-F167: `advisor_notice` 形不合 = 拒 `ADVISOR_NOTICE_INVALID`; 改 Lybra 产品顾问/审计/执行可见面的卡缺它 = 警告 `ADVISOR_NOTICE_MISSING`, 不拒)、路线图挂接(AIPOS-F171: 引用不存在 = 拒 `ROADMAP_LINK_INVALID`; 已有路线图而卡未挂 = 警告 `ROADMAP_UNLINKED`, 不拒)。

**并行出卡(AIPOS-F188)**: 发布(含 `--dry-run`)时本卡与在途卡(claimed, 含返工中)同一 lane.repo 且 lane.paths 前缀相交 = Warnings 里出
「车道重叠」行(对方卡号 / 重叠路径 / 建议「等对方合入后再起跑」或「起跑但交回前先合入 main」), `lybra loop` 起跑认领前与 `lybra loop status`
同样显示; 只提示不拒, 按提示排序, 不另记「哪些卡宜串行」的纪律。豁免前缀 = project.json `lane_overlap.exempt_path_prefixes`(缺省 tests/、docs/)。

**卡稿 JSON 写顾问须知**(AIPOS-F167; 卡改 CLI 参数 / next_action / 章程 / 卡面字段等顾问可见行为时写, 上线后产品自动送达各项目顾问):
```json
"advisor_notice": {"audience": ["advisor"], "summary": "<一句话要点>", "actions": ["<顾问要做的动作>"]}
```
`audience` 可含 `advisor` / `auditor` / `executor`(含 `advisor` 才进顾问未读提示; 审计 / 执行体的变化随章程渲染送达)。

**卡稿 JSON 挂路线图**(AIPOS-F171; 项目已声明路线图时写, 见下 `lybra roadmap`):
```json
"milestone": "<里程碑id>", "criteria": ["<标准id>"]
```
引用不存在 / 标准不属该里程碑 = 拒 `ROADMAP_LINK_INVALID`; 已有路线图而卡没挂 = 警告 `ROADMAP_UNLINKED`(游离卡, 不拒, 计入偏差);
项目没有路线图文件 = 不校验不警告。

**卡稿 JSON 声明验收产物**(AIPOS-F175; 所挂标准 verify = artifact / owner_check 时必写, 只此一个卡面字段):
```json
"artifacts": [{"criterion": "<本卡 criteria 里的标准id>", "path_glob": "home.png", "kind": "screenshot"},
              {"criterion": "<标准id>", "path_glob": "steps.txt", "kind": "output"}]
```
执行体把文件放进卡产物落点 `<return_root>/<卡ID>/artifacts/`(lybra = `task_cards/<卡ID>/artifacts/`), 交回 / 审计报告引用;
形不合(criterion 不在本卡 criteria / kind 不在 screenshot|output|report / glob 绝对路径或含 ..)= 拒 `CARD_ARTIFACTS_INVALID`。

#### `lybra roadmap`(AIPOS-F171: 路线图声明与编辑)
**何时用**:子项目首次接入结构化路线图(按阶段目标声明里程碑与验收标准)、阶段目标变化、看哪些卡挂在哪个里程碑 / 哪些是游离卡。
```bash
lybra roadmap show --workspace-root <治理根>                                          # 只读: 里程碑 / 标准 / 挂接卡数 / 游离卡(可加 --lane <lane> 过滤)
lybra roadmap add-milestone --workspace-root <治理根> --id <里程碑id> --lane <lane> --title "<一句话目标>"     # 先预演(可加 --target-date YYYY-MM-DD)
lybra roadmap add-milestone --workspace-root <治理根> --id <里程碑id> --lane <lane> --title "<一句话目标>" --confirm --actor <你的顾问实例>
lybra roadmap add-criterion --workspace-root <治理根> --milestone <里程碑id> --id <标准id> --statement "<可核对的一句话>" --verify auto
lybra roadmap set --workspace-root <治理根> --id <id> --field status --value done --confirm --actor <你的顾问实例>
lybra roadmap remove --workspace-root <治理根> --id <id>                              # 有挂接卡 = 拒 ROADMAP_REMOVE_LINKED, 里程碑改 status=dropped
```
缺省预演(整份文件 diff + 将生成的 decision_log 指针名, 零写入); `--confirm` 写入、生成 decision_log 指针(路线图变更本身可见)并自动落账。
文件落点 project.json `paths.roadmap_file`(缺省 `governance/roadmap.yaml`), 格式见 `docs/roadmap.md`; 改动只经本组命令。

#### `lybra roadmap status`(AIPOS-F173: 进度与偏差)
**何时用**:每次阶段推进前(出下一批卡、标里程碑 done 之前)跑一次看自己 lane; Owner 收件箱出现「路线图偏差」事项时。
```bash
lybra roadmap status --workspace-root <治理根> --lane <lane>                          # 只读: 里程碑进度条 / 偏差信号 / 下一步建议
lybra roadmap status --workspace-root <治理根> --json                                # 同一数据 JSON(看板同读)
```
**看到偏差怎么处理**(每条信号输出里都带建议命令, 照做后再跑一次 status 复核):
- 里程碑逾期 → 经 Owner 同意改目标日 `roadmap set --field target_date`, 或补卡加速; 不再做改 `--field status --value dropped`。
- 里程碑停滞 → `lybra loop status` 查挂接卡为何不动; 缺卡就补卡挂到该里程碑。
- 标准无卡覆盖 → 出卡时卡稿写 `"milestone"` / `"criteria"` 覆盖它, 或把已在做的卡 `lybra queue amend` 回挂。
- 游离卡占比高 → 该挂的卡回挂; 探索 / 修复类卡可不挂(只提示)。
- 反复返工 → 复核标准是否可核对(`roadmap set --field statement`)或拆卡。
- 路线图近期被改 → 核对变更经 Owner 同意(看 decision_log 指针)。
逾期 / 停滞 / 无卡覆盖会自动进 Owner 收件箱(类别 `roadmap_drift`); Owner 裁决后按 `lybra owner inbox` 里的裁决行做。
阈值在 project.json `roadmap_drift` 段(缺省与含义见 `docs/roadmap.md`「进度与偏差」)。
每条标准另显示**验证状态五态**(AIPOS-F175): 未验证(原因写缺什么: 未结案 / 审计未过 / 缺运行证据 / 缺产物)/ 自动验证通过 / 待 Owner 抽查 /
Owner 已确认 / 不符·修复卡 X; auto / artifact 标准到「自动验证通过」算满足, owner_check 标准到「Owner 已确认」才算满足。

#### `lybra roadmap verify`(AIPOS-F175: 记录 Owner 抽查结论)
**何时用**:Owner 收件箱出现「验收待 Owner 抽查」(类别 `owner_check`, 附截图 / 输出路径与看板路线图页链接), Owner 看完告诉你「符合」或「不符: …」后。
```bash
lybra roadmap verify --workspace-root <治理根> --criterion <标准id> --result confirm --relayed-by <你的顾问实例> --owner-words "<Owner原话摘要>"            # 先预演
lybra roadmap verify --workspace-root <治理根> --criterion <标准id> --result confirm --relayed-by <你的顾问实例> --owner-words "<Owner原话摘要>" --confirm --actor owner
lybra roadmap verify --workspace-root <治理根> --criterion <标准id> --result mismatch --relayed-by <你的顾问实例> --owner-words "<Owner原话摘要>" --note "<哪里不对>" --confirm --actor owner
```
写既有 owner_verifications 记录(转达人 + Owner 原话)并自动落账; `--relayed-by` / `--owner-words` 缺 = 用法错(exit 2)。
不符 = 同次自动起草修复卡(卡号 `<被抽查卡>-oc<N>`, 挂同一 milestone / criteria, `owner_check_ref` = 抽查记录), 输出给出
`lybra draft publish --path <草稿> --dry-run` 预演命令, 你确认后发布; 修复卡结案审计通过后标准回到「待 Owner 抽查」等 Owner 复查。
只有 verify=owner_check 且已到「待 Owner 抽查」(或已有结论要改)的标准能记录, 其余拒 `VERIFY_NOT_OWNER_CHECK` / `VERIFY_NOT_READY`。
对 owner_check 事项敲 `lybra owner decide` = 用法错, 拒因指回本命令。

#### `lybra updates`(AIPOS-F167: Lybra 更新说明 · 记已读)
**何时用**:`lybra charter` 开头或 `loop status` / `next` / `brief` 末尾出现「Lybra 有 N 条未读更新(自 <版本>): 要点…」时。
```bash
lybra updates --workspace-root <治理根> --actor <你的顾问实例>                       # 读未读全文(--all 看全部, 标已读/未读)
lybra updates --ack --workspace-root <治理根> --actor <你的顾问实例>                 # 预演: 将记已读哪几份(零写入)
lybra updates --ack --workspace-root <治理根> --actor <你的顾问实例> --confirm       # 记已读(写已读标记并自动落账)
```
已读按「本治理根 × 本实例」记: 你 ack 了, 同项目别的顾问实例仍会看到。读完 → ack → 按须知里的顾问动作做 → 直接继续推进, 不必等 Owner。
更新说明在产品侧(部署目录)一份, 所有接入项目共用; 卡工作树里 `python -m` 跑不读真实库(如实说明「库不可用」)。

#### `lybra project upgrade`(AIPOS-F176: 项目升级 · 告诉顾问的新能力)
**何时用**:收到 Lybra 更新后, 或看板总览出现「可升级(N 项)」时。一条命令把本项目跟上最新版本: 配置缺省补齐 / 工位章程同步 / 记录类型补齐 / 决策指针补生成(步骤清单读 `verbs.schema` `lybra_project_upgrade.upgrade_steps`, 各步就是既有命令的同一实现)。
```bash
lybra project upgrade <项目名> --home-root <home根>                                         # 预演(缺省, 零写入): 每步会改什么 / 为什么 / 是否已是最新 + 各顾问实例未读更新数
lybra project upgrade <项目名> --home-root <home根> --confirm --actor <你的顾问实例>          # 依次执行待升级步骤, 各步写后自动落账; 任一步失败即停(已成功步保留)
lybra project upgrade <项目名> --home-root <home根> --guidance                              # 按钮做不到、需你主动用起来的新能力: 为什么 / 话术含命令 / 完成判据
```
幂等: 重跑无事可做。失败(exit 3)按输出的修法修好后重跑同一条命令, 只做剩下的。`--guidance` 各项做到后下次检查即消失(看板升级页同一内容); Owner 也可在看板升级页点「预演升级 → 确认执行」(只对 Owner 会话开放)。

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
# 允许字段唯一声明 card.schema restricted_amend.claimed_card_fields = rework_rounds / output_target / lane / resources / validation_source_task / validation_candidate
```
经门 MCP 改卡(`lybra_queue_amend_dry_run` → `lybra_queue_amend_confirm`)时, 确认只认预演返回的 `dry_run_token`(与返工同一确认令牌口径, AIPOS-F157), 门按预演过的那次修改重放核验后写入。
**老卡回挂路线图(AIPOS-F171)**: 只改 `milestone` / `criteria` 的 amend 在 pending / claimed / blocked / completed 任一状态都允许(不必 `--restricted`), 引用同出卡校验:
```bash
lybra queue amend --task-id <卡ID> --amendments '{"milestone": "<里程碑id>", "criteria": ["<标准id>"]}' \
  --amendment-reason "回挂路线图" --actor <你的顾问实例>
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
拒因码: INGEST_RETURN_MISSING / INGEST_FRONTMATTER_MISSING / INGEST_TIP_MISMATCH / INGEST_TREE_MISMATCH / INGEST_SUMMARY_MISSING / INGEST_REPORT_UNFILLED(AIPOS-F215: 正文仍有占位, 拒因列行号; 例行修法 = 派写报告者补完证据填实后续跑 loop) / INGEST_KIND_MISMATCH(exit 4)。INGEST_TIP_MISMATCH 拒因给两侧完整 sha 与形式诊断(AIPOS-F180B): 形式错误 / 疑似抄错由 loop 同会话续接审计体只改报告头, 真审错了产物按拒因出口重审。通过则走推导核同时给出的同一条 `queue return --confirm` / `audit-verdict --confirm` 薄壳(驱动方 token, actor=卡实例), 并补上产品填写的 `agent_runtime`(运行时模型取自卡面 harness 的会话记录, 取不到记「未声明会话记录」, 与自报不一致标 `model_mismatch`); 裁决入门时门把审计报告全文快照进 `records/audit_verdicts/<卡ID>/report_snapshots/`。

#### `lybra queue withdraw`
**何时用**:撤卡(malformed/方向错误/重复发卡)。写入后自动落账(AIPOS-F184, 不再手工补提交)。Owner 改了目标要换新卡承接 → 用下面的 `queue supersede`。
```bash
lybra queue withdraw --task-id <卡ID> --reason "duplicate" \
  --actor <你的顾问实例>
```

#### `lybra queue supersede`(AIPOS-F184)
**何时用**:Owner 中途改了在途卡的目标(原卡 pending / claimed / blocked 均可)。先按新目标出新卡并发布, 再凭 Owner 原话撤换原卡;
**不要**再以执行体身份 `queue block` 原卡再重出卡(原卡会留在 Owner 收件箱要 Owner 再定一次)。
```bash
# ① 预演(缺省, 零写入): 列搬卡与将写的决策记录; 原卡有工作树 / 卡分支时提示保留(由结案清理处理, 不删分支)
lybra queue supersede --task-id <原卡> --by <新卡> --actor <你的顾问实例> \
  --owner-words "<Owner原话摘要>" --owner-ref <事项ID>
# ② 确认: Owner 原话落 owner_decision_record(同 owner decide --relayed-by)→ 原卡置 withdrawn, 卡面记 superseded_by → 自动落账
lybra queue supersede --task-id <原卡> --by <新卡> --actor <你的顾问实例> --owner-words "<Owner原话摘要>" --confirm
```
`--owner-ref` 可选: Owner 在哪里定的(收件箱事项 ID / 裁决记录 ID / 会话引用)。拒: `--actor` 不是顾问角色类实例 / 新卡不在队列或已撤回 / 新卡 = 原卡 / 缺原话。收件箱该卡的待决事项随卡进 withdrawn 自动消失。

**顾问起草的在途卡契约需更正**(AIPOS-F186; 例: 验收条款自相矛盾, 门拒改已认领卡的验收字段)→ 出更正卡 → `supersede --carry-branch` → 跑新卡 loop。
与 Owner 中途改目标同一命令; 加 `--carry-branch` = 新卡接着原卡分支做, 已做工作不丢、不用手工合并:
```bash
# ① 出更正卡并发布(新卡须 pending、未认领)
lybra draft create --from-json <卡稿JSON> --actor <你的顾问实例>
lybra draft publish --path <草稿路径> --actor <你的顾问实例>
# ② 预演: 列原卡分支名 / tip / 工作树路径 / 未提交改动计数(零写入)
lybra queue supersede --task-id <原卡> --by <新卡> --actor <你的顾问实例> --owner-words "<Owner原话摘要>" --carry-branch
# ③ 确认: 决策记录 → 新卡卡面记 carried_from {原卡, 分支, tip}(修订记录)→ 原卡 withdrawn → 自动落账
lybra queue supersede --task-id <原卡> --by <新卡> --actor <你的顾问实例> --owner-words "<Owner原话摘要>" --carry-branch --confirm
# ④ 跑新卡 loop: 认领时新卡分支从原卡分支 tip 建(原卡分支保留), 开工面提示执行体先把当前 main 合入
lybra loop --workspace-root <治理根> --task-id <新卡>
```
拒: 原卡工作树有未提交改动(产品不代提交 → 先让执行体在原卡分支提交, 再重跑)/ 原卡无卡分支 / 新卡已认领或卡分支已存在 / 新卡已记不同承继。
认领时原卡分支 tip 已不是记录的那个 = 认领被拒(CARRY_TIP_MOVED), 按当前 tip 重做 supersede 或改出新卡。审计与合入的差异基线仍按 main(承继提交一并审), 结案记录写承继关系; 交回的车道检查同按 main...新卡分支, 更正卡 lane.paths 须覆盖原卡已改的路径。

#### `lybra queue correct`(AIPOS-F207)
**何时用**:Owner 推翻**已结案**的卡(审计误判通过 / 顾问误结案, 要按原范围重做)。在途卡改目标用上面的 `queue supersede`;
**不要**手改卡面 / 记录, 也不要另起一张不挂原卡的新卡(路线图标准与依赖会仍认原结案)。
```bash
# ① 预演(缺省, 零写入): 列原结案链(closure / 裁决 / 交回 + sha256)、将写的决策记录、争议标记、承接卡草稿与路线图标准变化
lybra queue correct --task-id <已结案卡> --relayed-by <你的顾问实例> --owner-words "<Owner原话>" --reason "<原结案错在哪>"
# ② 确认: 决策记录(同 owner decide --relayed-by)→ 争议标记(原记录不改)→ 承接卡起草并发布(继承 lane / milestone / criteria /
#    task_mode / harness, 卡专属落点改指承接卡)→ 自动落账; 输出承接卡 loop 命令
lybra queue correct --task-id <已结案卡> --relayed-by <你的顾问实例> --owner-words "<Owner原话>" --reason "<原结案错在哪>" --confirm
# ③ 按输出跑承接卡 loop
```
承接卡号 = `<原卡>C<序号>`(首个未占用); `--followup-title` 可改标题。之后: 依赖原卡的卡认领被拒并提示改依赖为承接卡(`queue amend`);
路线图该卡挂的标准(无其他已验证卡时)= 「不符」· 承接卡; 收件箱「已定待转达」在承接卡开跑前给转达话术。拒: 卡不在 completed /
`--relayed-by` 不是顾问角色类实例 / 该卡已纠正过(承接卡结案后仍不符 = 纠正承接卡)。承接卡发布被拒 = 决策与争议标记照落账, 输出手工发布命令。

#### `lybra queue rework`
**何时用**:追加返工节到卡面(FAIL 裁决后,点杀清单+验收标准)(顾问职责).
```bash
# ① 预演(缺省): 显示将追加的返工节(第几轮 / 依据裁决 / 点杀 / 验收)并预检签发实例, 零写入、不调门
lybra queue rework --task-id <卡ID> --actor <你的顾问实例> \
  --verdict-ref "<裁决记录ID>" \
  --focus-items '["fix item 1", "fix item 2"]' \
  --acceptance-criteria '["criterion 1", "criterion 2"]'
# ② 确认写入: 同一命令加 --confirm(经门两阶段, 按 --actor 实例的凭据签发)
lybra queue rework --task-id <卡ID> --actor <你的顾问实例> --verdict-ref "<裁决记录ID>" \
  --focus-items '["fix item 1", "fix item 2"]' --acceptance-criteria '["criterion 1", "criterion 2"]' --confirm
# ③ 续跑: loop 认出「已登记返工轮」, 等执行体按返工节修复后重新交回, 再自动重交并派下一轮审计(R2/R3…)
lybra loop --task-id <卡ID> --workspace-root <治理根>
# 凭据自动发现(工位 .lybra/connection.json, 或 --connection-json <凭据文件>); token 永不上屏
```
**约束**:
- 只能由顾问类实例签发(`--actor` 即签发实例, 按其凭据签; 未接入本治理根即拒);
- 两阶段(AIPOS-F155, verbs.schema `two_phase_protocol.queue_writers`): 缺省预演, `--confirm` 才写;
- rework_rounds 字段禁手写,只能通过本命令追加;
- 登记后 `lybra loop status` 的下一动作是 `continue_wait`(loop 在等返工交回, 手工提示里附返工要点): 派本卡执行体(子 agent)按返工节修复 → 重跑定向测试 → 把 RETURN.md frontmatter `commit_sha` / `tree_hash` 改为卡分支新 tip, 写完即止; 不再报「裁决 FAIL, 需人工处理」。

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

#### `lybra loop status`(AIPOS-F131 / F136)
**何时用**:看 loop 推进到哪、拉起的工位进程是否活着 / 停滞, 以及**顾问下一动作**; 跟进一张在推进的卡时用 `--wait` 有界等待
(怎么循环见顾问章程「持续推进守则」, 本 skill 只列命令)。
```bash
lybra loop status --task-id <卡ID> --workspace-root <治理根>                 # 只读: 运行记录 + 探活 + 判停滞 + 下一动作
lybra loop status --task-id <卡ID> --workspace-root <治理根> --wait 540      # 有界等到可行动或到时(秒数取会话单次命令超时以内)
lybra loop status --workspace-root <治理根> --json                          # 缺 --task-id = 本项目全部未结束的运行
```
**下一动作**(唯一判定在产品, 判据表 verbs.schema `lybra_loop_status.next_action`): `continue_wait` / `owner_needed`(附事由) /
`card_done_take_next`(附下一条命令) / `investigate`(附下一条命令) / `advisor_fix`(例行情况, 附修法与续跑命令)。`--wait` 的退出码按下一动作(verbs.schema `lybra_loop_status.exit_codes`)。

**`advisor_fix` 用法**(AIPOS-F150 / F152 / F155; 怎么循环见顾问章程「持续推进守则」): 输出的 hint 是编号修法 + 末行续跑命令, 照做即可, 不问 Owner。
例行清单只在 verbs.schema `lybra_loop_status.next_action.routine_rejections` 一处声明, 目前:
- 门拒码 `BRANCH_WRONG_BASE`(交回时基线已前进)/ `MERGE_CONFLICT`(finalize 合入冲突): 派本卡执行体在卡工作树合入最新基线、解冲突、重测、更新交回 frontmatter, 然后末行命令续跑(走既有重审出口);
- 门拒码 `REPO_MERGE_IN_PROGRESS`(同仓另一张卡正在合入): 不改卡, 等持锁卡合完后末行命令重跑;
- 结束原因 `deploy_changed`(他卡部署了新版本, 且发生在执行步之间): 末行命令重跑, 从断点续做(本 loop 自己 finalize 的部署不再触发它)。**手工等待步**(等执行体交回)中他卡部署 = loop 自己换新快照从同一等待步续等(AIPOS-F166, 运行记录旧运行 `deploy_reexec` / 新运行 `continues_run`), 不停、不用重跑; 结束原因 `deploy_reexec` 只在新进程没接上时才是最近一次运行, 同样末行命令重跑;
- 结束原因 `wait_timeout` 且本卡执行方式 = 顾问派子 agent(project.json `execution`, 或等待目标是子 agent 执行者接入; AIPOS-F156 / F157): 不找 Owner、不敲 `/go`——子 agent 仍在做就等它写完交回后末行命令重跑(可加大 `--max-wait`), 已终止或未派就按 `lybra kickoff --task-id <卡ID>` 的开工稿补派; pi 工位 / 手工工位的 `wait_timeout` 仍是 `owner_needed`。
同卡同一例行原因连续到声明次数仍被拒 = 产品自动升 `owner_needed`, 不必自己计数。
**信封用尽 / 过期**(AIPOS-F166): 结束原因 `envelope_exhausted` / `envelope_expired` = `owner_needed(envelope_renew)`, detail 给当前用量 / 上限 / 到期, hint = 续签命令(`lybra envelope mint --confirm`, policy_id = 该驱动方同前缀已有最大序号 +1, 范围照抄用尽的信封)——原样转给 Owner 亲自敲, 签好用原命令续跑。剩余 ≤10% 或 ≤3 次、或 ≤24h 到期时 `loop status` / `brief` 先出一行「信封预警」(阈值声明 verbs.schema `lybra_loop.envelope.renew.warn`), 趁早请 Owner 续签。
**请求授权**(AIPOS-F180): `owner_needed(no_envelope)` / `owner_needed(envelope_renew)` 时收件箱已按卡自动生成授权建议(驱动方 = 你、模式 = 卡面 task_mode、仓 = 卡面 lane.repo、额度 = 业务卡张数(AIPOS-F192: 本卡所需 1 张, 每张含其审计、返工、合入、结案, 不照抄上一信封; 项目 project.json grant_request.max_tasks 显式声明优先)、到期 = 项目缺省天数、理由 = 卡标题), Owner 在看板「待授权请求」一键「按建议签发」。建议不对就用 loop note 写清要什么: `lybra loop note --task-id <卡> --grant-mode <模式> --grant-repos <仓,…> --grant-max-tasks <N> --grant-expires <YYYY-MM-DD> --grant-reason <一句话理由> --workspace-root <治理根>` 预演后加 `--confirm`(字段可只给一部分; 给了 --grant-* 可不写 --ask)。不要再把 `lybra envelope mint` 长命令贴给 Owner; 签好后收件箱该项自动消失(转入「已定待转达」, 你续跑后消失), 用原命令续跑 loop。派 pi 工位的卡(validation / config / docs 等按 project.json execution 路由到 pi)建议自带「允许 loop 拉起 pi 工位」(AIPOS-F201), 不必再手加 --launch-harness pi。Owner 不批准 = `lybra owner decide --item <OI-…> --choice deny_grant …` 记下, 按 Owner 原话调整请求或改卡。
**授权越界**(AIPOS-F224): 项目有常设授权时不再给新开信封的出口——结束原因 `authorization_out_of_bound` = `owner_needed`, 原文点名越界维度(新车道 / 新任务类型 / 驱动方 / 授权外主机 / 禁区 / 总额用尽 / 到期)与一次扩大授权命令(`lybra project grant extend …`); 收件箱同一事项选「扩大项目授权」即生效, 用原命令续跑。
**合并后回归未完成**(AIPOS-F166): 异步回归进程没跑完就没了(机器重启 / 被杀), `loop status` / `brief` / `next` 出一行「合并后回归未完成 … 补跑: `lybra regression run --sha <提交> --workspace-root <治理根>`」: 先原样跑一次(预演, 只读), 再加 `--confirm --actor <驱动方实例>` 补跑(前台; `--background` 后台), 结论落同格式回归事件记录, `lybra regression baseline --sha` 即可复用。仍在跑 / 已有完整结论 = 产品拒。
**等待中的开工提示**(`continue_wait(manual_kickoff)`): 子 agent 模式 = 「顾问派本卡子 agent 执行并写交回, 开工稿 = `lybra kickoff --task-id <卡ID>` 的输出」(AIPOS-F187: 一份自足开工稿, 原样交给子 agent, 可在其后追加本卡特别说明; 不再手写), 照做, 不转述给 Owner; 只有 pi / 手工工位才是「请在 <工位> 敲 /go」。
**取下一张归属**(`card_done_take_next`, AIPOS-F156): 末行命令只给归属本驱动方实例的下一张(门侧发布记录签发实例 = `draft publish --actor`); 共用仓里其他顾问实例的卡不取。
**禁止**:tail/grep 原始运行日志自判; until/sleep 轮询(等待只用 `--wait`)。

#### 派子 agent 的开工稿: `lybra kickoff`(AIPOS-F187)
**何时用**: loop 输出 `continue_wait(manual_kickoff)` 且为子 agent 模式(或 `wait_timeout` 要补派)时, 派本卡执行 / 审计子 agent 前。
```bash
lybra kickoff --task-id <卡ID> --workspace-root <治理根>            # 执行卡; 审计卡按卡自动判为审计体开工稿(或显式 --role auditor)
lybra kickoff --task-id <卡ID> --workspace-root <治理根> --json     # 机读: text + 各段取值
```
输出 = 一份自足开工稿: 身份实例、章程命令(会话在他机 = ssh 拉取式)、卡面路径、工作树 / 分支 / 基于哪个 main sha、承继来源、报告落点与 frontmatter 必填字段、
车道与写权限边界、禁写清单(治理根除报告落点、其他项目根、工位、门写动作、pi)、硬规矩、草稿目录(project.json `paths.scratch_root`, 缺省系统临时目录, 不落治理根)、
验证要求(本分支最终 tip 必跑全量; main 侧先查 `lybra regression baseline`, 按其输出三态处理(标签与处理以开工稿为准, AIPOS-F212), 并附开工稿生成时的现状一行(AIPOS-F188); 棘轮 / 证据复跑一致 / 长命令 run-long)、交回前残留进程检查、写完即止、末尾简短总结。**原样交给子 agent, 本卡特别说明追加在其后; 不再手写开工稿。**
拒(stderr 原文): 卡未认领 / 已结案 / 当前不该本角色动 / 产物已交(与工位 `/go` 同一判据)、实例章程不可取、工作树未建、草稿目录落在治理根内(给 set-paths 出口)。
项目可声明: `lybra project set-paths <项目> --key scratch_root --value <治理根外绝对路径> --confirm`; project.json `test_contract.ratchet`(棘轮一句话)/ `test_contract.residual_processes`(交回前 pgrep 式样)。

#### 卡工作树清理: 自动 + `lybra project cleanup-worktrees`(AIPOS-F187)
卡结案(closure 落盘后)/ 撤回 / 撤换后, 产品自动清理该卡工作树与审计工作树(<卡>R、<卡>R2…): 干净 = `git worktree remove`(分支保留); 有未提交改动 = 不碰,
结案输出与 `loop status` 报路径与改动计数; 被 pending 卡 `carried_from` 引用的原卡树保留到承继卡认领。清理失败不影响结案。存量:
```bash
lybra project cleanup-worktrees --workspace-root <治理根>             # 预演(零写入): 逐仓列 删 / 脏 / 在途 / 承继 / 不认识
lybra project cleanup-worktrees --workspace-root <治理根> --confirm   # 删判为「删」的(分支保留), 其余不动
```
脏树(常见: 子 agent 留下的未跟踪草稿)= 看清内容后自己处理(提交到卡分支或删文件), 再跑一次预演。

#### Owner 待决: `lybra loop note` / `lybra owner inbox` / `lybra owner decide`(AIPOS-F168 / F170)
**何时用**: loop status 给 `owner_needed` 时(怎么停见顾问章程「持续推进守则」)。所有项目需 Owner 定的事集中在一个收件箱, Owner 在那里看、定;
Owner 对你口头说了裁决, 由你落记录(写明你转达、Owner 原话)。任何项目同一组命令, 在治理根所在机运行。
**Owner 介入分级**(AIPOS-F225, 声明 verbs.schema `lybra_owner_inbox.tiers`): 收件箱只列**第一档**(方向 / 设计分叉、改验收 / 换候选 / 改范围、
超出项目授权、超预算 / 超期限、撤卡 / 放弃、返工超硬上限、顾问与审计结论冲突、验收抽查——验收抽查排末尾);
**第二档**(范围不变的返工自动追加到硬上限 4 轮、环境 / 产品有变化后自动重新派审、补审 FAIL 自动派修复卡、路线图偏差、信封将尽、已自动放行记录)
在 `lybra owner inbox` 末段「第二档 · 已自动放行 / 告知」, 不计待定数, Owner 可对「已自动放行」事项选 `revoke_auto` 撤回(撤回后本卡停住, loop 以
`auto_release_revoked` 结束, 待 Owner 另定); **第三档**技术故障 loop status 直接给 `advisor_fix` / `investigate`(detail「第三档(归顾问)」+ 理由), 你自处理。
1. **报 owner_needed 前先写清要定什么**(写进本卡最近一次运行记录, 收件箱据此展示要定什么 / 选项 / 建议 / 第一档事由; 缺省预演, `--confirm` 写入并自动落账;
   `--owner-reason` 必带, 键 ∈ `tiers.tier1.reasons`, 缺或不在清单 = 拒写(exit 6)并指回你自处理; 只给 `--grant-*` 授权建议时缺省 `beyond_authority`):
```bash
lybra loop note --task-id <卡ID> --workspace-root <治理根> --actor <你的顾问实例> --ask "<要定什么>" --owner-reason <第一档事由键> --options <键=标签> --recommend "<建议>"
```
   **撤回自己过时 / 发错的请求**(AIPOS-F232): 原请求留档, 只追加撤回字段; 只许补写人本人(`--actor` = 当初补写的实例)、该事项未被 Owner 裁决
   (已裁决 = 按裁决续跑, 拒 exit 7); 写入后收件箱该项消失。F225 前写的请求(无第一档事由)已自动降为第二档告知「旧请求, 可撤回」(不计待定),
   事项文案里带这条撤回命令:
```bash
lybra loop note --task-id <卡ID> --workspace-root <治理根> --actor <你的顾问实例> --retract --retract-reason "<一句话理由>" --confirm
```
2. **Owner 看收件箱**(只读; 看板 /owner/inbox 同一数据; project.json `status: paused` 的项目缺省不列, `--include-paused` 可看;
   `--stale-before <日期>` = 遗留视图: 按项目分组每项一行「卡 / 标题 / 卡住原因 / 建议: 撤回 | 冻结为历史 | 保留」, 并给同建议的批量裁决命令):
```bash
lybra owner inbox --home-root <home根>
lybra owner inbox --home-root <home根> --stale-before 2026-10-01
```
3. **Owner 口头裁决 → 你落记录**(`--relayed-by` 写你自己的实例, Owner 本人敲写 `owner`; `--owner-words` 写 Owner 原话摘要, 单行; 两者缺 = 拒;
   先预演, 无误再加 `--confirm`, 自动落账; `--items` 用逗号连多个事项 = 同一裁决批量, 整批预演任一被拒整批不写):
```bash
lybra owner decide --item <事项ID> --choice <选项键> --relayed-by <你的顾问实例> --owner-words "<Owner原话摘要>" --home-root <home根>
lybra owner decide --items <事项ID>,<事项ID> --choice freeze_legacy --relayed-by <你的顾问实例> --owner-words "<Owner原话摘要>" --home-root <home根> --confirm
```
   **放行继续**(AIPOS-F201): 卡面带 `needs_owner: true` 的未认领(pending)卡, 收件箱选项 `release`=「放行继续」; Owner 同意后
   `lybra owner decide --item <OI-…> --choice release --relayed-by <你的顾问实例> --owner-words "<Owner原话>" --home-root <home根>` 预演再 `--confirm`
   = 写决策记录 + 经 queue amend 把 needs_owner 置 false(修订理由带决策记录 ID 与 Owner 原话)+ 自动落账, 之后按输出的续跑命令 `lybra loop --task-id <卡> …`。
   不要再用 `queue amend` 手改 needs_owner(那样没有 Owner 决策记录)。卡已认领 / 已阻塞时选 release 被拒并给原因。
   **看板转达话术**(AIPOS-F201): Owner 在看板收件箱每个待决项的每个选项下都能复制一段「告诉 <驱动顾问>」的话术(点名你、带上面这条预演命令与下一步);
   Owner 签发 / 定夺后, 收件箱顶部「已定待转达」(CLI `lybra owner inbox` 末段同文)按顾问合并列出你还没接着做的事(信封 id / 决策 id + 下一条命令),
   你接着做(续跑 loop / 认领)后自动消失。驱动顾问 = 本卡最近 loop 运行的 driver, 无运行 = 卡面 planner_agent / created_by。
   **重新派审**(AIPOS-F202; AIPOS-F225 起为手动兜底——常规出口是第二档自动重新派审: 自上次 BLOCK 起部署或验证根有变化, 原命令重跑 loop 即自动派审并留记录):
   审计连续 BLOCK 的事项(Owner 撤回自动重审后 / 判不出变化而你确要 Owner 定时)恒有选项 `reaudit`=「环境 / 产品已修好, 重新派审」
   (另有 `wait_product`=等修复、`rescope` 改卡面验收等)。Owner 定后 `lybra owner decide --item <OI-…> --choice reaudit --relayed-by <你的顾问实例> --owner-words "<Owner原话>" --home-root <home根>`
   预演再 `--confirm`(可加 `--note <修了什么>`), 然后原命令重跑 loop: 连续 BLOCK 以该裁决为界重新计数, 产品派下一轮审计(R 序号续增)并照常拉起审计;
   新一轮再 BLOCK 1 次 = 例行修环境重审, 连续到声明次数再升 Owner。历史裁决 / 审计卡不改, 不要手改记录或「重置次数」。之前选过 `wait_product`、
   之后部署已更新的, 收件箱该卡新事项多一行「提示: … 可选重新派审」。
   blocked 卡的裁决可选 `withdraw`(经既有撤卡口, 记录带裁决引用)或 `freeze_legacy`(经既有存量冻结口, 清单条目带裁决引用)。之后对应卡
   `lybra loop status` 给 `advisor_fix(owner_ruled)`, detail 显示裁决、转达人与 Owner 原话, 按 hint 续跑; `lybra next --task-id` 附同一裁决行(自定义选项显示标签)。
4. **项目暂停 / 恢复**(Owner 说「某项目先停」; 暂停项目的事项不再刷屏收件箱, 其余行为不变; 先预演, 再确认, 自动落账; 恢复 = `--status active`):
```bash
lybra project set-meta <项目名> --home-root <home根> --status paused --dry-run
lybra project set-meta <项目名> --home-root <home根> --status paused --confirm --actor <你的顾问实例>
```

#### 跨机工位(AIPOS-F110: 执行体在别的机器, 门与治理根在本机)
前提: loop 只在治理根所在机(门机)跑; 门机与工位机 ssh 双向可达——门机→工位(loop 拉起/清理), 工位→门机(执行体读写门机上的治理根/工作树/报告落点)。ssh 凭据只走两端各自的 ssh 配置与密钥(`BatchMode=yes` 不交互索要口令), 永不经 Lybra、不进开工提示。
1. **接入**(在门机治理根所在机运行; land 事件 host = `--ssh` 的 ssh 目标, loop 以它为拉起目标; 注册码经 ssh stdin 送达、签码凭据只在本机读 connection.json 调门, 均不进远端命令行; 先预演再 `--confirm`):
```bash
lybra roles --workspace-root <治理根> enroll-deliver --role executor --instance <执行体实例> \
  --governance-root <治理根> --workspace <工位目录> --ssh <ssh目标> \
  --gate-url <门地址> --actor <你的顾问实例> --owner-authorization-ref <信封ID> --dry-run
```
`--workspace` = 工位机上的目录, `--gate-url` = 工位可达的门地址; 预演无误后同一命令把 `--dry-run` 换成 `--confirm`(Owner 凭据签码时改
`--connection-json <Owner凭据> --token-role owner`, 去掉 `--actor`)。
2. **声明开工材料**(远端视角: 工位上指向门机的 ssh 别名 + 一句话材料访问方式, 如「经 ssh <别名> 读写; 代码提交到卡分支并推回门机产品仓」; 禁含凭据; 未声明 = loop 拒拉起并提示本命令)。写 project.json 的命令一律两阶段(AIPOS-F125): 先把下面的 `--confirm` 换成 `--dry-run` 预演(打印 diff 与校验, 零写入), 确认后原样带 `--confirm` 才写:
```bash
lybra project set-workstation <项目名> --home-root <home根> --instance <执行体实例> \
  --gate-ssh-alias <门机别名> --material-access "<材料访问说明>" --confirm
```
3. **双向可达检查**(只读, 与 loop 同一 ssh 代码路径; 门机→工位: 工位目录在、harness 可执行在远端非交互 PATH; 工位→门机: 经别名 `test -d <治理根>`; 任一 ✗ 先修 ssh 配置再推进):
```bash
lybra project check-workstation <项目名> --home-root <home根> --instance <执行体实例> --harness pi
```
4. **推进**照常 `lybra loop --task-id <卡ID>`: 执行体等待前 loop 经 `ssh -T <ssh目标>` 在工位目录以新进程组起 harness, 开工提示经 ssh stdin 逐字节传入(不进命令行、不经远端 shell 展开), 提示末段写明工作树/报告落点/任务卡在门机及材料访问方式(产品 `render_kickoff` 单源); 产物就绪/超时/早退/中断(含 ssh 断线 SIGHUP)即经 ssh 清远端进程组, 本地 ssh 子进程同清。执行体交付不变: 报告写门机报告落点(经材料通道), 代码提交卡分支并推回门机产品仓(卡面 lane.repo), loop 仍在门机看产物。

#### 工位章程新鲜度(AIPOS-F164: 章程由产品渲染, 拉起前自动核)
工位章程(工位根 `AGENTS.md`)= 产品按母本 + 项目声明渲染的产物(`lybra sync` 写入, 本地清单记母本 / 声明 / 渲染物三指纹), 不是手写文件; 人肉期手写或从未 sync 的章程 = 未渲染, 母本或项目声明变了而工位未 sync = 过期。判定只有一处: 与 `lybra sync --dry-run` 同一实现; 修复只有一处: `lybra sync`。任何项目同一组命令, 在治理根所在机运行。
1. **体检**(只读, 零写入): `check-workstation` 本机工位多一项「章程新鲜度」——最新 = ✓; 未渲染 / 指纹过期 / 缺失 / 核不了(连不上门等)= ✗ 并给修法命令
```bash
lybra project check-workstation <项目名> --home-root <home根> --instance <审计体实例> --harness pi
```
2. **修法**(✗ 时; 先预演看 plan, 再去掉 `--dry-run` 落盘, 只动该工位):
```bash
lybra sync --harness-root <工位目录> --workspace-root <治理根> --dry-run
```
3. **loop 拉起前自动核**: `lybra loop` 每次拉起 pi(执行 / 审计)前核该工位章程; 最新 = 直接拉起; 不新鲜 = 先自动 sync 该工位(只本工位)再复核后拉起, 运行记录 `charter_checks` 与日志各留一行; sync 失败 = 不拉起, 结束原因 `charter_sync_failed`; 项目在 project.json 声明 `"charter_auto_sync": false` 时不自动 sync, 不新鲜即拒拉起(`charter_not_fresh`)。两者 `lybra loop status` 下一动作 = `advisor_fix`(修法 = 上面的 sync 命令, 修好用原命令续跑 loop; 同卡连续同因 = 升 owner_needed)。跨机工位门机不读写工位文件, 不核(在工位所在机跑 `lybra sync --dry-run`)。

#### 工位迁出共用检出(AIPOS-F161: `lybra project relocate-workstation`)
**何时用**: pi 工位(审计 / 执行)放在和别的项目共用的 git 检出里(任何一方 checkout / stash / pull 都连坐全部工位), 要迁到独立落点。任何项目同一命令, 在治理根所在机运行(跨机顾问经 ssh 跑同一命令); 只能迁本项目接入的实例(他项目工位 = 拒, 零写入)。
位置声明只有一处 = 本项目接入日志里该实例最新的位置事件; 迁移追加一行 `relocate` 位置事件, loop 拉起 / `check-workstation` / `enroll-where` / `lybra charter` 都从这里读到新落点(不另写路径表)。
1. **预演**(缺省, 零写入; 不给 `--to` 时打印按声明约定推导的建议落点): 列工位态(凭据只列路径 / 权限 / 大小)、旧 / 新落点、受影响的声明与链接、在途检查(运行记录里的拉起进程 / 等待本实例的 loop / cwd 在工位内的进程)
```bash
lybra project relocate-workstation <项目名> --home-root <home根> --instance <审计体实例> --to <工位目录> --dry-run
```
2. **确认**(须无在途审计 / 执行; 新落点不得在任何 git 检出内、须不存在或为空): 整体迁移工位态(凭据随目录移动, 不读内容)→ 对新落点 sync 并核稳态 → 追加位置事件 → 核查 → 旧落点留只读退役指针(旧目录不删)→ 经治理提交口自动落账(`--actor` 为提交身份)。sync 失败 = 工位态移回、声明不改
```bash
lybra project relocate-workstation <项目名> --home-root <home根> --instance <审计体实例> --to <工位目录> --confirm --actor <你的顾问实例>
```
3. **核查**(只读): `check-workstation` 的「land 事件位置」一行应为新落点
```bash
lybra project check-workstation <项目名> --home-root <home根> --instance <审计体实例> --harness pi
```
4. **回滚**(同样先预演再确认; 迁回最近一次迁移前的落点, 声明追加一行回滚位置事件):
```bash
lybra project relocate-workstation <项目名> --home-root <home根> --instance <审计体实例> --rollback --dry-run
```

#### pi 模型切换(AIPOS-F169: `lybra project set-model`)
**何时用**: Owner 对你说「后面 pi 执行体(或审计体)换成某模型」。Owner 没说 = 不声明, loop 拉起 pi 不传 `--model`, 沿用 pi 自身缺省(现为 `kiwiai/qwen3.7-plus`)。模型声明只在 project.json `execution.pi_models` 一处(须先有 `set-execution` 声明); 只能切到本机 pi 已配置的模型(产品按 `pi --list-models` 核, 不在清单 = 拒并列出相近可用模型, 原样转给 Owner 选)。任何项目同一组命令, 在治理根所在机运行。
1. **预演**(缺省, 零写入): 核模型在 pi 已配置清单中, 列影响的工位(角色 / 实例 / 位置)与下次拉起的模型变化 `旧 → 新`、生效时机(下次拉起生效, 在途已拉起的进程不受影响)
```bash
lybra project set-model <项目名> --home-root <home根> --model <provider/id> --owner-ref "<Owner指示摘要>"                  # 项目缺省(执行与审计)
lybra project set-model <项目名> --home-root <home根> --role auditor --model <provider/id> --owner-ref "<Owner指示摘要>"          # 只换审计体(--role executor 只换执行体)
```
2. **确认**: 同一组参数加 `--confirm --actor <你的顾问实例>`, 写 project.json 并经治理提交口自动落账(之后不手跑 `governance-commit`)
3. **核查**(只读): `check-workstation` 多一行「拉起模型 [角色]: <模型>(来源)」, 本机工位另核该模型仍在 pi 清单中(不在 = ✗); loop 拉起后 `lybra loop status` 拉起行显示「模型 …」, 运行记录 `launches[].model` 记实际传入值
```bash
lybra project check-workstation <项目名> --home-root <home根> --instance <审计体实例> --harness pi
```
按角色覆盖优先于项目缺省; 执行 / 审计章程不变(模型对执行体透明)。跨机工位: 可用性按本机 pi 清单核, 工位所在机的 pi 配置在该机 `pi --list-models` 核。

#### 非代码工作交 pi(AIPOS-F196: 验证卡 task_mode=validation + pi 执行工位 + 经 ssh 操作目标主机)
**分工**(Owner 10-20 定, 任何项目同一组命令, 在治理根所在机运行): 代码卡 = 你派子 agent(`lybra kickoff`); 验证 / 环境 / 文档卡 = pi 执行工位
(loop 自动拉起, 你不派工); 审计 = 独立 pi 审计工位。验证卡保留独立审计、N5 无合入(finalize 写明「验证卡无合入」, 不合并不推送不部署); 环境卡(config)
由你复核(loop 停在「非代码卡快车道:顾问直接审」)。代码卡只做实现与离线测试, 验证 / 环境类工作单独出卡交 pi。

**先生成切换计划(AIPOS-F198)**: 下面各步的命令、顺序与「哪些已完成」由产品推导, 不手写——Owner 在看板项目「工位设置」页选好目标分工后转给你的提示词,
与下面命令输出同一份(只读, 零写入); 照它逐步先预演后确认, 被拒原样报告 Owner:
```bash
lybra project workstation-plan <项目名> --home-root <home根> --pi-modes validation,config,docs --pi-executor <执行体实例> --workspace <工位目录> --host <主机标签> --ssh-alias <ssh别名>
```
输出含: 现状(子 agent 执行者 / pi 执行工位 / 审计工位的接入与章程状态、交 pi 的卡类型、目标主机 ssh 别名、验证根摘要、各卡类型派给谁)、风险、
依赖序步骤(决策日志 → Owner 签覆盖信封 → 接入 → 手写章程接管 / 章程同步 → set-execution → set-host → 核验 → 通知专项顾问)、给项目总顾问与给各专项顾问的两段话术。--workspace(既有工位可省)、--host / --ssh-alias(不经 ssh 可省)可省; Owner 已有决策记录时加 --owner-ref 引用它(决策步即为已完成)。

1. **pi 执行工位接入**(工位目录不迁移; 已有手写章程的工位先接管; enroll-deliver 预演后加 `--confirm`):
```bash
lybra roles enroll-deliver --role <执行角色> --instance <执行体实例> --governance-root <治理根> --workspace <工位目录> --owner-authorization-ref <信封ID> --dry-run
lybra sync --harness-root <工位目录> --workspace-root <治理根> --dry-run
```
   工位 AGENTS.md 若是带手工维护标记(如「禁止 Lybra 分发器覆盖」)的手写章程, sync(含 loop 拉起前自动 sync)一律拒覆盖并给出口 `CHARTER_HAND_MAINTAINED`;
   正式解除只走产品命令且须 Owner 裁决引用(禁手删标记): 先预演备份路径, 再执行; 原章程备份为 `AGENTS.md.hand-maintained-<时刻>.bak`(不删), 采纳记录在
   工位 `.lybra/charter-adoptions.jsonl`; 之后 `check-workstation` 章程新鲜度应为 ✓
```bash
lybra sync --harness-root <工位目录> --workspace-root <治理根> --adopt-hand-charter --owner-ref <Owner裁决引用> --dry-run
lybra sync --harness-root <工位目录> --workspace-root <治理根> --adopt-hand-charter --owner-ref <Owner裁决引用>
lybra project check-workstation <项目名> --home-root <home根> --instance <执行体实例> --harness pi
```
   工位仓若是 Owner 的集中仓(多机同步), 备份文件与渲染后的章程由 Owner 提交同步, 你不 commit 工位仓。
2. **声明执行方式**(列内 task_mode 起草缺省 harness=pi、assigned_to=pi 执行实例; code 不列 = 仍子 agent; 预演后同参加 `--confirm --actor <你的顾问实例>`):
```bash
lybra project set-execution <项目名> --home-root <home根> --subagent-executor <子agent执行实例> --auditor <审计体实例> --pi-executor <执行体实例> --pi-allowed-task-mode validation --pi-allowed-task-mode config --pi-allowed-task-mode docs --dry-run
```
3. **pi 经 ssh 操作目标主机**(pi 工位在治理根所在机, 验证在他机如 Mac): 声明目标主机的 ssh 别名(治理根所在机 `~/.ssh/config` 的 Host 名);
   之后该主机上「工位在本机」实例的验证根节 / 开工提示 / `init-command` 一律给 `ssh <别名> '<命令>'`, helper 经管道送入(不落目标主机)。约束 = 章程 + 目标主机
   helper 自查 + 审计追责(无硬沙箱, Owner 已知悉); 预演后同参加 `--confirm --actor <你的顾问实例>`
```bash
lybra project validation-root set-host <项目名> --home-root <home根> --host <主机标签> --ssh-alias <ssh别名> --owner-ref <Owner授权> --dry-run
```
4. **起草验证卡**(卡稿 frontmatter): `task_mode: validation`、`validation_source_task: <来源卡>`、`validation_candidate: <来源卡所在仓的完整 40 位提交 sha>`、
   `lane.repo: <候选所在仓>`(只读取证, 不建卡工作树)、`output_target: <报告落点 artifacts/ 说明>`、`artifacts`(截图 path_glob)。起草 / 发卡即核候选与来源卡
   (`VALIDATION_SUBJECT_MISSING` / `VALIDATION_CANDIDATE_INVALID` / `VALIDATION_SOURCE_UNKNOWN`); 不写 harness / assigned_to 即按第 2 步缺省派 pi。
   正文写清: 验证目标与步骤、要的截图 / 运行证据、iOS 需模拟器时按开工提示的「iOS 本 run 专用模拟器」九步(建台 → 绑定 → 三参数 xcodebuild → 核验 → 删台 → 证据回传)。
5. **发卡后**: 为验证卡的 pi 执行实例声明验证根并铸 run(候选须 = 卡面候选), 审计卡另声明审计实例自己的验证根(审计 run 须新建自己的模拟器):
```bash
lybra project validation-root declare <项目名> --home-root <home根> --host <主机标签> --base <目标主机base> --task-id <卡ID> --instance <执行体实例> --owner-ref <Owner授权> --host-fingerprint <主机指纹> --confirm --actor <你的顾问实例>
lybra project validation-root run-start <项目名> --home-root <home根> --id <声明id> --candidate <卡面候选> --confirm --actor <你的顾问实例>
lybra loop --task-id <卡ID> --workspace-root <治理根>
```
   loop 自动拉起 pi(开工提示含本卡验证根节与经 ssh 的命令); 交回 → 派审 → 审计 pi → 裁决绑候选 → finalize(无合入)→ 结案。
   收口: 验证结束 `validation-root run-finish`(绑定了模拟器带 `--sim-evidence`)。
   **候选源码送达(AIPOS-F197)**: run-start 输出与开工稿(章程验证根节同源)在目录链初始化之后给「② 候选源码送达」两条命令——
   送达 = 在治理根所在机 `git -C <候选所在仓> archive --prefix=src/ <候选> | ssh <别名> 'tar -x -C <run 目录>'`(只送已提交的候选树;
   pi 在目标主机本机时为本机 `| tar -x -C <run 目录>` 形); 核对 = helper `src-verify`(目标主机按 git 规则算 `<run 目录>/src` 的 tree 指纹,
   须 = 候选 tree, 末行 `SRC_OK`; 混入未提交改动 / 送错版本 = `DENY SRC_TREE_MISMATCH`, 改参凑指纹 = `SRC_SEAL_MISMATCH`), 证据在 run 目录
   `.lybra-vr/src-verify.jsonl`。执行体与审计体照抄开工稿命令, 你不手传源码、不让执行体 rsync / scp; 重取命令 = 重取开工稿或
```bash
lybra project validation-root init-command <项目名> --home-root <home根> --id <声明id> --json
```
   (`source_delivery` 字段)。验证卡起草要点: 正文写清「先送达候选源码并核对 SRC_OK, 再构建 / 起模拟器」, 截图与 src-verify 证据同列报告。
   **helper 按 run 固定(AIPOS-F203)**: `run-start --confirm` 时把当时部署的 helper 固定给本 run(输出「本 run 固定验证 helper sha256 = …」),
   产品升级不打断进行中 run; 新 run 才用新版。项目脚本(如归档核对脚本)要核 helper 摘要, 一律运行时取
   `lybra project validation-root helper --id <声明id> --run-id <run> --sha256`, 卡面 / 脚本禁写死摘要; helper 有变化的版本上线时
   `lybra updates` 会列「helper 摘要变化 旧→新」, 进行中 run 不必处理, 需用新子命令 = 收口后重新 run-start。
6. **环境卡绑定固定候选(AIPOS-F205)**: 依赖准备 / 缓存快照等环境卡(`task_mode` config / docs / governance, 常为 `lane.repo: governance_root`
   只写治理根)要在专用验证根里为某个固定候选干活时, 卡面成对写 `validation_source_task: <来源卡>` + `validation_candidate: <来源卡产品仓的完整 40 位 sha>`
   (缺省不写 = 行为不变)。候选所在仓按**来源卡** `lane.repo` 解析(不按本卡 lane.repo), run-start 候选须 = 卡面候选, 源码从来源卡仓送达;
   依赖缓存 / 临时 HOME / settings 只写本卡验证根本 run 目录(~/.m2 等全局缓存仍拒); 交付契约不变(只写治理根卡仍 Return `outputs` + 你复核结案)。
   起草 / 发卡 / amend 同一判据(`VALIDATION_SUBJECT_MISSING` / `VALIDATION_CANDIDATE_INVALID` / `VALIDATION_SOURCE_UNKNOWN`, 非上列模式写了 = `VALIDATION_BINDING_MODE`)。
   **已认领卡补绑定**(候选绑定属执行前置, 不改验收)= 受限 amend(允许字段 card.schema restricted_amend 已含这两字段), 再 run-start(预演后加 `--confirm`):
```bash
lybra queue amend --restricted --task-id <卡ID> --actor <你的顾问实例> --amendment-reason "环境卡绑定来源卡固定候选" --amendments '{"validation_source_task": "<来源卡>", "validation_candidate": "<卡面候选>"}' --dry-run
lybra project validation-root run-start <项目名> --home-root <home根> --id <声明id> --candidate <卡面候选> --dry-run
```
   之后执行体开工提示的验证根节会列「本卡绑定固定候选」行 + 目录链 / 源码送达 / 核对命令; 本卡尚无声明时该节只给你 declare / run-start 出口, 执行体停线。

#### 文档 / 配置卡只写治理根交 pi(AIPOS-F197: 不建卡工作树, 交回 = 产出清单)
**何时用**: 文档 / 治理 / 配置类卡的产物只落治理根(不写任何产品仓)。任何项目同一组命令, 在治理根所在机运行。
1. **起草**: 卡稿 `task_mode: docs`(或 governance / config)、**`lane.repo: governance_root`**(保留值 = 只写治理根; 不写 lane.repo 会按
   缺省派生到产品仓 = 写仓卡: 建卡分支, 交回仍是分支三键, 裁决 artifact_subject 门侧强制绑分支 tip)、`output_target: <报告落点>/artifacts/`、`artifacts`(要抽查的产物 path_glob);
   要独立审计就写 `audit: required` + `audit_by`(否则由你复核)。不写 harness / assigned_to = 按 `set-execution` 缺省派 pi 执行工位。
2. **开工**: loop 自动拉起 pi; 开工提示给「产物落点」(只写本卡报告落点及其子目录, 写权限边界判定; 不提分支 / 工作树)与报告必填
   `outputs`(产出文件清单); 子 agent 开工稿(`lybra kickoff`)同一契约。
3. **交回**: 产物入口按 `outputs` 逐个核对(存在 / 普通文件 / 非符号链接 / 写权限边界放行)并算 sha256, 门交回记录绑定清单指纹;
   拒因 `INGEST_OUTPUTS_INVALID` / `INGEST_OUTPUT_MISSING` / `INGEST_OUTPUT_OUT_OF_BOUNDARY` 原文给出口(执行体修正后 loop 自动重试)。
4. **你复核结案**(未声明独立审计): loop 停在「非代码卡快车道(只写治理根)」并给出结案命令(带交回绑定指纹); 复核产出后先预演再确认:
```bash
lybra mark-concluded --task-id <卡ID> --actor <你的顾问实例> --report-path <Return相对路径> --conclusion-note "顾问复核: 产出清单逐项核对通过" --dry-run
```
   (`--report-path` 用 loop 结束原文给出的 Return 相对路径; 去掉 `--dry-run` 即结案)。**执行体如实 BLOCK、你按 BLOCK 结案**时加
   `--outcome block`(AIPOS-F207): 依赖本卡的卡不会被当作满足(与门生结案同一判定), 认领拒因提示改依赖为承接卡或移除(`queue amend`);
   不写 `--outcome` = 按 pass 记并提示。**复核不通过要打回原卡返工**(AIPOS-F221; 不是 BLOCK 结案, 卡留 claimed): 同一命令改
   `--outcome rework` 并给返工要点(验收可选):
```bash
lybra mark-concluded --task-id <卡ID> --actor <你的顾问实例> --report-path <Return相对路径> --outcome rework --focus-items '["<返工要点>"]' --acceptance-criteria '["<验收标准>"]' --dry-run
```
   产品落一条顾问复核记录(`records/events/<卡ID>/advisor_review_*.md`)+ 返工轮(与 `queue rework` 同一写函数, 轮次上限同; AIPOS-F225: 到上限后
   卡面候选 / 验收 / 范围自发布起未变 = 门自动追加到硬上限 4 轮, 轮次记 `auto_extension_ref` 并落自动放行记录 `records/events/<卡ID>/auto_release_*.md`,
   Owner 可撤回; 改过验收 / 发布基线不可核 / 到硬上限 = 拒, 升仲裁:
   去掉 `--dry-run` 的超限打回照落复核记录并自动进 Owner 收件箱「返工轮次达上限」, Owner 选「授权本卡追加一轮返工」(extend_rework)后同一命令
   (或审计裁决型卡的 `queue rework`)重跑即登记该轮, 每个裁决只放一轮, AIPOS-F222; `lybra loop note` 回显请求进了哪个收件箱事项, 进不了 = 拒写);
   loop 拉起执行体按返工节重写交回 → 重交回 → 回到你复核(早于该轮登记的旧改写不算); 同一交回不重复打回。**别对快车道卡跑
   `audit-verdict`**: 产品在取凭据前就拒(`FAST_LANE_REVIEW_ONLY`, 拒因给出这两条出口), 与凭据无关。loop 停在这一步时 `lybra loop status` 给 advisor_fix(顾问复核结案), 不进 Owner 收件箱。交回后产出被改 = 拒 `INGEST_OUTPUTS_CHANGED`(让执行体按改后产出
   重写 Return, loop 重交回)。之后 N6 落账照常由 loop / `lybra next` 给出。
   顾问复核结案与门生结案同一口径(AIPOS-F209, 唯一判定 `task_complexity.closure_state`): state lint 不报断层(BLOCK 结论只给信息级提示)、
   loop 收尾补落账、开工核验、补充审计派审、路线图核对都认它; 门上 MCP `lybra_mark_concluded` 同收 `outcome`(与 CLI 同一写路径);
   结案后自动清理本卡工作树(干净删、分支保留, 结果在应答 `data.worktree_cleanup`)。此前顾问复核结案后 loop 运行记录未落账的卡:
   原 `lybra loop --task-id <卡ID>` 重跑一次即自动补落账。
5. **声明独立审计**: 审计 pi 开工提示「工作树: 不建(被审卡只写治理根…)」, 报告必填 `verdict` + `outputs_digest`(产品给值, 照抄); 入口与门侧裁决
   都核「此刻清单指纹 = 交回绑定」; finalize 无合入(finalization `merge_commit = outputs:<指纹>`, 不推送不部署)后结案。

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

**已结案卡补充审计(AIPOS-F162)**:对已结案卡的审计结论有争议(如审计只核了执行体原件、没自己跑)→ 顾问直接发起补审, 不需 Owner:
```bash
# ① 预演(缺省, 零写入): 列出补审卡号 <卡ID>-S<n>R / 审计实例 / 将写的文件
lybra audit dispatch --supplementary --task-id <已结案卡ID> --actor <你的顾问实例> --reason "<争议摘要>" \
  --runtime-evidence-command "<审计须在自己分配工作树重跑的命令>"
# ② 确认写入(同一组参数 + --confirm)
lybra audit dispatch --supplementary --task-id <已结案卡ID> --actor <你的顾问实例> --reason "<争议摘要>" \
  --runtime-evidence-command "<审计须在自己分配工作树重跑的命令>" --confirm
# ③ 推进: 对原卡跑 loop(认领补审卡 → 拉起/等审计体 → 裁决入门 → 落账)
lybra loop --task-id <已结案卡ID>
```
`--runtime-evidence-command` 可省(省 = 沿用原卡卡面的 `audit_runtime_evidence` 声明)、可多次; 命令里可用占位 `{worktree}`/`{commit_sha}`/`{tree_hash}`(审计分配工作树 / 被审 tip / tree)。`--audit-agent-instance` 可省(缺省 = 原卡 audit_by)。
原卡 closure 与原裁决不动、不回滚 main; 补审 PASS 类 → 原卡附「补审通过」; FAIL/BLOCK → 门自动派生关联修复卡 `<卡ID>-fix<n>`(走常规执行/审计/合入), 原卡标「补审未通过·修复卡 X」。同卡同时只允许一张在途补审(`SUPPLEMENTARY_AUDIT_IN_FLIGHT`); 未结案卡拒(`SUPPLEMENTARY_SOURCE_NOT_CONCLUDED`)。链路在 `lybra next --task-id` / `brief` / `loop status --task-id` / `needs-owner` 都可见。声明: transitions.schema `supplementary_audit`。

**出卡声明审计须独立运行(AIPOS-F162)**:卡面要求审计「实际运行逐条核验」时, 不只写在正文里, 卡稿 frontmatter 加一个字段(唯一声明处; 缺省 = 不要求, 行为不变):
```json
"audit_runtime_evidence": {"required": true, "commands": ["<验证命令1>", "<验证命令2 --worktree {worktree} --commit {commit_sha}>"]}
```
验证卡(task_mode=validation 等固定候选卡, AIPOS-F199)审计不建产品工作树, 命令占位改用 `{run_dir}`(审计自己的验证根 run 目录)/ `{src_dir}`(本 run 送达的候选源码)/ `{candidate}` / `{candidate_tree}`, 旧占位 `{worktree}`/`{commit_sha}`/`{tree_hash}` 起草即拒并给迁移提示(已发布未认领的卡用 `lybra queue amend` 改); 审计须先有自己的验证根声明与 run(`project validation-root declare --task-id <审计卡> --instance <审计实例>` → `run-start`), 证据绑定该 run(核归属 / 候选 / src-verify SRC_OK / 日志指纹)。 审计 run 在目标主机(Mac 等)时(AIPOS-F200): 审计命令跑完、run 仍活动时在目标主机生成证据清单(开工稿 ⑧, helper `evidence-manifest`), 再在治理根所在机 `lybra project validation-root evidence-fetch --id <审计声明> --run-id <run>` 经 `set-host` 声明的 ssh 别名只读取回到审计报告落点 `run_evidence/<run-id>/`(审计在 dev 经 ssh 时自己跑; 审计在 Mac 本机时由你在 dev 跑同一命令); 门对副本做与本机 run 同一核对, 主机未声明 ssh 别名 = 先 `set-host`。
**多主机验证(AIPOS-F223)**: 同一审计卡同一审计实例声明了多台主机的验证根(如 dev 编译核包 + Mac 跑界面)时, 卡稿 `audit_runtime_evidence` 加 `"host": "<主机>"` 写明运行证据命令在哪台跑(主机标签 = `validation-root declare --host` 的值); 未写又有多个声明 = 开工稿 / 派审步骤只给拒因一行(`VR_EVIDENCE_HOST_AMBIGUOUS`, 列各声明 id / 主机 + 补声明命令), 裁决入口同判据拒, 报告 run 属未选中主机 = `VR_EVIDENCE_HOST_MISMATCH`; 在途卡照拒因给的 `lybra queue amend --task-id <验证卡> --restricted --amendments '{"audit_runtime_evidence": {...原值, "host": "<主机>"}}' --dry-run` 预演后确认(已认领卡只许改 host, 命令与 required 不变), 补后开工稿 / 派审即按该主机渲染。
派生审计卡会列出这些命令与证据节格式; 审计报告 PASS 类缺 `## 独立运行证据` 节 / 工作树不是审计分配工作树 / 命令缺一条 = 裁决入口拒收(`RUNTIME_EVIDENCE_MISSING`), loop 让审计体补做, 超声明上限 = `loop status` 判 owner_needed(runtime_evidence_exhausted)。FAIL 裁决不受限。声明: transitions.schema `artifact_ingest.verdict.runtime_evidence`。
**命令占位一张词表(AIPOS-F233)**: 卡分支卡(code 等)与固定候选卡(validation 等)的同义占位互为别名——`{worktree}` ≡ `{src_dir}`(审计 / 验证工作目录)、`{commit_sha}` ≡ `{candidate}`(被审主体提交)、`{tree_hash}` ≡ `{candidate_tree}`(其 tree), `{run_dir}` 在卡分支卡按 `{worktree}` 渲染; 写哪套都行, 起草 / 发卡 / amend 自动改成本卡交付主体的规范形并在输出(发卡 = 发布记录)写明改写前后原文, 在途存量卡按别名渲染即可入门。两套词表都没有的 `{xxx}`(awk `'{print}'`、jq、json 片段、笔误 `{workdir}` 等)是字面文本: 不拒、按字面渲染与比对实跑, 只在输出 / 发布记录提示一行并给相近的已知占位(如 `{workdir}` → `{worktree}`), 本意是占位就自己改。卡面声明本身错(形不合)的已发布在途卡 = loop 不拉起审计体, 以 `runtime_evidence_declaration_invalid`(advisor_fix)结束, 照 `loop status` 给的 `lybra queue amend --task-id <卡> --restricted --amendments '{"audit_runtime_evidence": {...}}' --dry-run` 预演后确认。已认领卡 `--restricted` 对 `commands` 只放行「只改占位名」(别名规范化 / 同义改名; 字面花括号的改动、跨语义改名、改命令其余部分照旧拒), 不需 Owner、不撤换、不算范围变化(返工自动放行不失)。审计补做连败 `runtime_evidence_exhausted` 也归顾问(advisor_fix: 换审计体 / 修环境 / 修声明), 真要改验收才选第一档事由升 Owner。报告头值含冒号要加引号(`model: "glm-5.3 (provider: kiwiai)"`); 自由文本键(model / harness 等)漏引号产品容错读出并记 note, 其他键照旧走修报告头。

**验证卡审计 FAIL 的两种出口(AIPOS-F227)**: 审计报告 frontmatter `fail_scope: candidate`(候选不合格)= `lybra next` / `loop status` 给 `candidate_fail`(来源卡修复出新候选 → 另出验证卡 → `queue supersede`); `fail_scope: deliverable`(候选可用、报告 / 证据 / 声明产物缺失或不实)= 给 `candidate_deliverable_fail`(照抄给出的 `queue rework` 同候选登记返工轮, 续跑 loop 让执行体补交付物重交回并派下一轮审计, 轮次计入返工上限); 未声明 = 按 candidate 处理, 同时并列同候选补正出口——审计发现全部指向交付物、无任何候选行为缺陷时可选用。

**派发前可执行核查(AIPOS-F165)**:出卡(draft create/publish)与补审派发(`--runtime-evidence-command`)同一处校验——命令里的绝对路径可执行(含 `cd … &&` 之后、`env …` 之后的那个)须在审计工位主机上存在且可执行, 否则预演即报 `RUNTIME_EVIDENCE_COMMAND_NOT_EXECUTABLE` 并给修法, `--confirm` 拒; 声明含 `sudo` 等提权 = `RUNTIME_EVIDENCE_COMMAND_PRIVILEGED`。修法: 在审计工位主机 `command -v <名>` 实测后写实际路径, 或直接写 PATH 中的名字(如 `node`); 跨机审计工位门机核不到, 标「未核」提示你去该机实测。逐字匹配判据不放松: 审计跑不了声明命令 = 给 BLOCK 写原因, 由你改卡/重派(审计章程硬条款 `no_privilege_escalation`)。

#### `lybra next`(`next-step` 已退役转发, AIPOS-F71)
**何时用**:查询任务当前状态 → 下一步动词+完整参数(AIPOS-R7A 大项C;AIPOS-F71 起唯一实现为 `lybra next`)。
```bash
lybra next --task-id <卡ID>
lybra next --actor <你的顾问实例>   # 项目扫描按归属看(AIPOS-F156): 只推荐归属本实例的卡, 共用仓里其他顾问的卡单列「其他子项目(不取)」
```
**输出**:当前状态、下一步动词、完整命令、触发者、授权语义(由 transitions.schema 生成,禁口述)。
项目扫描(无 `--task-id`)给 `--actor` 时, 你的有效信封不限 lane(总顾问)= 看全量、只标归属; 存量卡(发布记录无归因)按卡面 created_by 标「自报」。

**对外陈述序列必须由它生成**(LOOP-REDESIGN §4.5 A13)——口述 = 记忆叙述 = 漏步漂移。

---

### ⚖️ 审非代码卡(N4 分路)

#### `lybra audit-verdict`
**何时用**:你审非代码卡(docs/governance/config)时提交裁决。只写治理根的卡(无卡分支, AIPOS-F197)按上文「文档 / 配置卡只写治理根交 pi」第 4 步
`lybra mark-concluded` 结案(结案前产品核交回绑定的产出清单), 不走本命令。
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

#### `lybra project grant`(AIPOS-F224: 项目常设授权, 优先于信封)
**何时用**:每个项目 Owner 签一次授权(车道 × 任务类型 × 驱动方 / 允许主机 / 拉起 / 到期 + 总卡数 / 禁区, 禁区缺省全禁), 范围内 `lybra loop` 自动覆盖, 不再逐卡逐周签信封。
覆盖判定优先级: 项目授权先判; 未覆盖才兼容未迁移的旧信封; 已迁入授权的旧信封 = superseded(不挑中、不消耗, 文件不改不删)。总卡数按业务卡计(审计 / 返工派生不另扣, 同信封)。
**越界**(车道 / 任务类型 / 驱动方 / 主机 / 禁区 / 总额 / 到期)= loop 结束原因 `authorization_out_of_bound` → `owner_needed`, 结束原文点名越界维度 + 一次扩大授权命令;
收件箱事项选项 = 「扩大项目授权」(一次裁决同时落授权新修订)/ 不批准 / 暂停。用到 80% 或到期前 3 天 = 告知(不阻塞; AIPOS-F232: `lybra loop status`
(运行用的是该授权)/ `lybra brief` / 收件箱第二档各一行, 附 `lybra project grant extend …` 扩大命令, 原样转 Owner)。验证根声明主机在授权内 = 免 `--owner-ref`。
全新项目(未签授权、也无信封目录)工位接入推导报「本项目尚未签项目授权」并给 `grant sign` 命令(不再误报「信封目录不存在」)。
**Owner 亲自敲**(`--confirm` 经门 owner_decision_record, Owner 凭据); 不带 `--confirm` = 本地预演(零写入, 授权修订全文可见):
```bash
lybra project grant show --workspace-root <项目根>
lybra project grant draft --from-envelopes --workspace-root <项目根>
lybra project grant sign --workspace-root <项目根> --grant-lane <仓名>:code,validation:<你的顾问实例> --host <主机> --max-tasks 60 --expires-at <到期时间>
lybra project grant extend --workspace-root <项目根> --host <主机> --max-tasks 80
lybra project grant revoke --workspace-root <项目根> --reason "Owner 收回授权"
```
写动作加 `--confirm --connection-json <Owner 的 connection.json>` 由 Owner 签发; 看板信封页「项目授权」主视图同样可迁移 / 扩大 / 撤销(F172 同一写路由)。

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
**何时用**:续额/延期已有信封。⚠ 尚未产品化(同 revoke, AIPOS-F92 未做项); 续额暂以新 `--policy-id` 再签一张(`lybra envelope mint --confirm`)——loop 因信封用尽 / 过期停下时, `lybra loop status` 的 hint 即按用尽信封渲染好的这条命令(AIPOS-F166)。
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

#### `lybra roles enroll-deliver`(AIPOS-F151; 取凭据按实例 AIPOS-F157)
**何时用**:为执行体 / 审计体工位(或顾问会话)签码并就地接入——在治理根所在机一条命令完成, **注册码只在本进程内**(不打印、不落盘、不进 argv)。
缺省 = 预演(显示将签的角色 / 实例 / 治理根 / 有效期 / 覆盖信封, 不签码、零写入), `--confirm` 才执行。
```bash
lybra roles --workspace-root <治理根> enroll-deliver --role executor --instance <执行体实例> \
  --governance-root <治理根> --workspace <工位目录> --actor <你的顾问实例> --owner-authorization-ref <信封ID> --dry-run
lybra roles --workspace-root <治理根> enroll-deliver --role executor --instance <执行体实例> \
  --governance-root <治理根> --workspace <工位目录> --actor <你的顾问实例> --owner-authorization-ref <信封ID> --confirm
```
- **`--actor` = 签发实例**:按该实例在治理根 `.lybra/connection.json` 的凭据签码(F134/F154 按实例选凭据唯一口); 未接入本治理根的实例即拒;
  不给 `--actor` = 按 `--token-role`(缺省 advisor, 缺席回落 owner)取, **同一角色接入两个及以上实例时拒**(禁取首个, 出口 = 加 `--actor`)。
- 子 agent 执行者(无工位, 凭据落治理根): `--executor-mode subagent --workspace <治理根>`; 顾问会话: `--role <顾问角色> --instance <顾问实例> --workspace <治理根> --harness claude-code --harness-dir <会话目录>`(Codex: `--harness codex`, 会话在他机加 `--harness-host <会话所在机>`)。
- 跨机工位加 `--ssh <ssh目标>`(见「跨机工位」)。工位类接入须有覆盖该实例的生效信封(无 = 拒 `ENROLL_ENVELOPE_MISSING`, 未签码)。
- 他机会话开局取章程(AIPOS-F138, 产品不推送): `ssh <治理根主机> 'cd <治理根> && lybra charter --role advisor --instance <你的顾问实例>'`(`lybra sync` 的 pull 行给出带真实主机的原文)。

**生成**:工位 `.lybra/` 配置(connection.json / role / policy)、工具包、skills; 之后 `lybra sync --harness-root <工位目录> --workspace-root <治理根>` 落齐分发件。

#### `lybra roles enroll-code`(跨主机、无法同机接入时)
**何时用**:接入方不在治理根所在机、又不能 `--ssh` 送达时, 签一次性自包含注册码交给接收方(同机一律用 `enroll-deliver`, 码不经手)。
```bash
lybra roles --workspace-root <治理根> enroll-code --role auditor --instance <审计体实例> --governance-root <治理根> \
  --actor <你的顾问实例> --owner-authorization-ref <信封ID> --ttl 86400 --dry-run     # 预演: 不出码
lybra roles --workspace-root <治理根> enroll-code --role auditor --instance <审计体实例> --governance-root <治理根> \
  --actor <你的顾问实例> --owner-authorization-ref <信封ID> --ttl 86400 --confirm     # 出码(只显示一次)
```
`--actor` 取凭据规则同 `enroll-deliver`。注册码一次性、有时效; 只经输出的 paste_text 原样交接收方在其工位兑换, 不贴进聊天 / 文档(凭据不过手)。

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
**非卡改动**用下面的 `--paths` 形——只限没有自动落账的改动(手改的治理文档、`owner-decision` 决策记录、`project new` 建根产物、写命令落账失败后的补账);`project` 族 / `envelope mint` / `migrate …` 的 `--confirm` 已写后自动落账(AIPOS-F158/F159/F160), 不再跟一条 `--paths`:
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
