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
# 允许字段唯一声明 card.schema restricted_amend.claimed_card_fields = rework_rounds / output_target / lane
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
- 结束原因 `wait_timeout` 且本卡执行方式 = 顾问派子 agent(project.json `execution`, 或等待目标是子 agent 执行者接入; AIPOS-F156 / F157): 不找 Owner、不敲 `/go`——子 agent 仍在做就等它写完交回后末行命令重跑(可加大 `--max-wait`), 已终止或未派就按开工材料补派; pi 工位 / 手工工位的 `wait_timeout` 仍是 `owner_needed`。
同卡同一例行原因连续到声明次数仍被拒 = 产品自动升 `owner_needed`, 不必自己计数。
**信封用尽 / 过期**(AIPOS-F166): 结束原因 `envelope_exhausted` / `envelope_expired` = `owner_needed(envelope_renew)`, detail 给当前用量 / 上限 / 到期, hint = 续签命令(`lybra envelope mint --confirm`, policy_id = 该驱动方同前缀已有最大序号 +1, 范围照抄用尽的信封)——原样转给 Owner 亲自敲, 签好用原命令续跑。剩余 ≤10% 或 ≤3 次、或 ≤24h 到期时 `loop status` / `brief` 先出一行「信封预警」(阈值声明 verbs.schema `lybra_loop.envelope.renew.warn`), 趁早请 Owner 续签。
**合并后回归未完成**(AIPOS-F166): 异步回归进程没跑完就没了(机器重启 / 被杀), `loop status` / `brief` / `next` 出一行「合并后回归未完成 … 补跑: `lybra regression run --sha <提交> --workspace-root <治理根>`」: 先原样跑一次(预演, 只读), 再加 `--confirm --actor <驱动方实例>` 补跑(前台; `--background` 后台), 结论落同格式回归事件记录, `lybra regression baseline --sha` 即可复用。仍在跑 / 已有完整结论 = 产品拒。
**等待中的开工提示**(`continue_wait(manual_kickoff)`): 子 agent 模式 = 「顾问按开工材料派本卡子 agent 执行并写交回」(`lybra my-tasks --actor <执行体实例> --task-id <卡ID>` 给工作树 / 报告落点 / 卡路径), 照做, 不转述给 Owner; 只有 pi / 手工工位才是「请在 <工位> 敲 /go」。
**取下一张归属**(`card_done_take_next`, AIPOS-F156): 末行命令只给归属本驱动方实例的下一张(门侧发布记录签发实例 = `draft publish --actor`); 共用仓里其他顾问实例的卡不取。
**禁止**:tail/grep 原始运行日志自判; until/sleep 轮询(等待只用 `--wait`)。

#### Owner 待决: `lybra loop note` / `lybra owner inbox` / `lybra owner decide`(AIPOS-F168 / F170)
**何时用**: loop status 给 `owner_needed` 时(怎么停见顾问章程「持续推进守则」)。所有项目需 Owner 定的事集中在一个收件箱, Owner 在那里看、定;
Owner 对你口头说了裁决, 由你落记录(写明你转达、Owner 原话)。任何项目同一组命令, 在治理根所在机运行。
1. **报 owner_needed 前先写清要定什么**(写进本卡最近一次运行记录, 收件箱据此展示要定什么 / 选项 / 建议; 缺省预演, `--confirm` 写入并自动落账):
```bash
lybra loop note --task-id <卡ID> --workspace-root <治理根> --actor <你的顾问实例> --ask "<要定什么>" --options <键=标签> --recommend "<建议>"
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
派生审计卡会列出这些命令与证据节格式; 审计报告 PASS 类缺 `## 独立运行证据` 节 / 工作树不是审计分配工作树 / 命令缺一条 = 裁决入口拒收(`RUNTIME_EVIDENCE_MISSING`), loop 让审计体补做, 超声明上限 = `loop status` 判 owner_needed(runtime_evidence_exhausted)。FAIL 裁决不受限。声明: transitions.schema `artifact_ingest.verdict.runtime_evidence`。

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
