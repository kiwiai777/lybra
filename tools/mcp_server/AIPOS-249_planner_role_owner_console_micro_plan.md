# AIPOS-249 — Slice planner 角色(第三 token 角色 + owner console + 确认门迁入对话)(片)

- **Status**: APPROVED(R 方向审计 PASS + 七钩裁定 + R-1..R-5 折入 §8;Owner "折入后视为
  APPROVED,进实现";前置夹具 F-launch-4/5/6 已修、O3 环境干净)
- **Authority**: 实现授权(不 commit——收口后候 cc 增量审计 → Owner O3[全程人话]→ finalize)
- **定位**:v1.0 体验层核心片。落地"BYO 外接 planner agent + Owner 顾问控制台",把
  Owner 侧自然语言交互从粗糙 TUI 迁到顾问会话;确认门以 harness 审批弹窗形态保留"人手放行"。
- **Parent 裁定(全部在治理仓 `governance/direction_log/2026-07-direction-decisions.md`,已读)**:
  - **A(07-09)**:planner = 第三 token 角色,scope = 只读真相 + draft-submit;永不可
    claim/return/confirm/publish。BYO 外接 agent 持此 token;内置 copilot 降为备胎。
  - **B(07-12)**:确认门迁入顾问对话——Owner 顾问会话另持 **owner token**,`owner_confirm`
    类调用一律经 **agent harness 工具审批弹窗**执行(模型自己按不了)= "人手放行"新形态;
    `owner_confirm` **永禁**加入免审白名单(SKILL 明写,F-06 防线)。TUI `/confirm` 降为备用。
  - **C(07-11/12)**:顾问职责继承 lybra-method 仓方法论文本(`<!-- lybra:planner-inherit -->`
    标记对接,一份文本不写两遍):建项引导(提问库 + 协作模式共创轮,SPEC 未定形不出第一张卡
    = 开工前置门)、gate 状态人话叙述(裸 JSON 永不给 Owner)、按 SPEC 控制出卡顺序、守角色
    边界、轮末评分呈报(实验区纪律)、治理文档为 draft 品种之一。
  - **D 红线**:planner 写面仅限 drafts 区(`5_tasks/drafts/`),落地永远 Owner 过门;
    Lybra 零调度(出卡顺序是顾问的 agent 层行为);planner 不得给自己编排的工作配亲缘审计者
    (独立性检查照旧绑实例)。

---

## §0 真实现状台账(executed-✓,全部 file:line 落地核实)

| # | 事实 | 出处 |
|---|---|---|
| 1 | **角色 token 唯一铸造真源 = `ROLE_SPECS`**(tuple of dict:`role`/`token_ref`/`scopes`,可选 `projects`)。现有 5 角色:executor `["queue_claim","queue_return"]` / owner `["queue_claim","queue_return","owner_confirm","draft_publish"]` / owner-dispatch `["audit_dispatch"]` / auditor `["queue_claim","audit_verdict"]` / copilot `[]` | `tools/aipos_cli/service_mode.py:41-78` |
| 2 | 铸 token:`_role_token_entry`(`secrets.token_urlsafe(32)` + fingerprint sha256:12)→ `build_connection_config` → `rotate_report` → `write_connection_config`(0600 落 `~/.lybra/local/connection.json`)。CLI = `serve rotate` | `service_mode.py:262-281,284-313,463,324-344`;`aipos_cli.py:898-904,1229-1230` |
| 3 | **新增角色改动面**:①`ROLE_SPECS` 加一条;②若是全新 scope:`tools.py:35-51` 加 `*_SCOPE` 常量 + `_*_scope_allowed()` + 写工具 handler 加门 + 可见性 `tools.py:2164-2178` 加分支;③角色枚举测试 `test_service_mode.py:519-521` | 读码 |
| 4 | **scope 强制机制**:`_capability_has_scope`(查 `capability.operations` 含 scope + token_ref + 未过期)→ 各 `_*_scope_allowed()` → handler 开头 `if not _X_scope_allowed(): return _scope_denied_result_for(...)`。单一 dispatch choke-point 顺序 = **project gate → ★A1 operation-scope → controlled-execute** | `tools.py:198-214,306-349,221-230,290-303,267-287` |
| 5 | **每个写/confirm 工具的 scope 门**(逐一):intake dry/confirm(INTAKE)/ owner_decision(OWNER_DECISION)/ draft_publish(DRAFT_PUBLISH,**confirm 额外 owner_confirm** :987-988)/ queue_claim(**confirm 额外 owner_confirm** :1113-1114)/ queue_return(**confirm 额外 owner_confirm** :1351-1352)/ audit_dispatch(AUDIT_DISPATCH)/ audit_verdict(AUDIT_VERDICT) | `tools.py:882-899,920-931,959-988,1026-1114,1224-1352,1518-1550,1610-1647` |
| 6 | **★A1 关键**:`lybra_intake_submit_confirm` **不强制 owner_confirm**——`execute_dry_run(..., owner_confirmation_token=str(...) or None)`,持 INTAKE_SCOPE 者可**自完成**落盘。对比:draft_publish/queue_claim/queue_return 的 confirm 结构性额外要 owner_confirm | `tools.py:898-916` vs :987/1113/1351 |
| 7 | **写面结构性锁死 drafts**:external_intake 链路 `EXTERNAL_INTAKE_DRAFTS_DIR = 5_tasks/drafts/external_intake` 常量 + `target_path = DIR / f"{_safe_id(...)}.md"`(safe_id 经 `EXTERNAL_TAG_PATTERN` 正则)——**agent 无法指定任意路径**。该 draft metadata 已含 `assigned_to:planner`/`agent_instance:planner`/`task_mode:planning`/`artifact_policy:draft_only`/`needs_owner:True`/`draft_status:draft`/`output_target:5_tasks/drafts/external_intake` | `external_intake_writer.py:13,218-270` |
| 8 | **另一写面 `create_draft`**(落 `5_tasks/drafts/`,`DRAFTS_DIR=5_tasks/drafts`)——**未暴露为 MCP 写工具**(仅 CLI/authoring/controlled-execute 内部调);`publish_draft` = 把 draft **搬**到 `queue/pending/`(需 DRAFT_PUBLISH_SCOPE + owner_confirm) | `draft_writer.py:311-365,368,275`;`draft_validator.py:11` |
| 9 | **MCP 只读面 = 恰 5 个**(`READ_TOOL_DESCRIPTORS`,对所有连接默认可见、无 scope):`lybra_queue_list` / `lybra_project_status`(gate 项目视图,非治理 project_status.md)/ `lybra_task_preview` / `lybra_validate` / `lybra_context_pack_build` | `tools.py:1728,806-865,2149-2182` |
| 10 | **planner 四类读面覆盖**:(a)queue ✅ queue_list;(b)records ⚠️ task_preview 只 surface sessions/claims,`returns`/`audit_dispatches`/`audit_verdicts` **被计算但不返回**(`preview.py:87-88`);(c)audit verdict ❌ 无 MCP 读面(物理存 `5_tasks/records/audit_verdicts/`);(d)governance 文档(decision_log/roadmap/status/reports/drafts)❌ **完全无 MCP 读面**(board_adapter 有 `get_drafts`/`get_owner_decision_records`/`get_orchestration_*` reader 但未 wire) | `preview.py:25,87-88`;`records.py:423-431`;`board_adapter.py:697,757,794` |
| 11 | copilot(scopes=[])今天读真相 = **仅 queue + 单任务 preview**(`rehydrate_truth` 只 wire `_QUEUE_LIST` + 可选 `_TASK_PREVIEW`);读不到 governance/audit verdict。planner 若复用此模式,读面同样窄 | `copilot.py:342-350,7-9` |
| 12 | **F-06 实证在册**:AIPOS-191 真机——受限真 agent 越出 prompt **自签自批** owner 确认;owner token 交给任何 agent 会话 = 决策门存在被模型旁路的结构风险(direction_log 07-12 决策1 依据) | direction_log 2026-07-12;`ephemeral-executors-role-claims` memory |
| 13 | **lybra-method 继承源**:`~/lybra-method`(HEAD `aee2ed6`;pillar 增补在 `855bc11`)。SKILL.md 有 **7 个 `<!-- lybra:planner-inherit pillar=X -->` 成对段**:task-card-discipline / two-role / owner-gate / role-contract / advisor-duties / capability-scoring-rubric / scoreboard-reporting。`questions/_common.md` 第 2 轮 = 协作模式共创(串行/星形原语);`templates/project-spec.md` 有「工作流编排」节 | 读 `~/lybra-method/SKILL.md:16-166`,`git log`,`DRAFT_STRUCTURE.md:49` |

## §1 硬红线(D + 派生,验收逐条查)
1. **planner 写面结构性只能落 drafts 区**:planner token 无论如何调用,产出只能落
   `5_tasks/drafts/**`;试图指定 drafts 外路径 = 结构性回落或拒绝(不是靠纪律,靠代码常量)。
2. **落地永远 Owner 过门**:planner **结构性不能** publish(drafts→queue/pending 需
   DRAFT_PUBLISH_SCOPE + owner_confirm,planner 无);不能 claim/return/confirm/audit_*。
   scope 拒绝是 ★A1 同族,gate 侧强制,非客户端自律。
3. **确认门 = 无模型经手的人手放行**:owner_confirm 类调用在顾问会话里经 **harness 工具
   审批弹窗**执行;`owner_confirm` **永禁**加入免审白名单(F-06 替代防线,SKILL/文档明写)。
4. **Lybra 零调度**:出卡顺序、串/并编排是顾问的 agent 层行为(SKILL 教);gate 不排程、
   不触发下游、不因上游 RETURNED 自动起下游。
5. **不配亲缘审计者**:planner 编排的工作,审计者独立性照旧绑实例 + 亲缘检查(既有
   `board_adapter.py` INDEPENDENCE 守卫不动);本片不弱化。
6. **一份文本不写两遍**:方法论/顾问职责/评分文本从 lybra-method 的 7 个 pillar 段继承,
   继承机制见 §3-Q4;planner SKILL 不重写方法论正文。
7. 纯增量:现有 5 只读工具 + executor 连接器 + gate 校验语义零回归;新增只读面(若有)必只读。

## §2 架构(三角色 token + 确认门迁入对话 + 零调度)

```
┌─ 第三方 BYO 规划顾问会话 ─┐      ┌─ Owner 顾问控制台会话(单人自用主形态)─┐
│ SKILL: lybra-planner       │      │ SKILL: owner-console                     │
│ token: planner             │      │ token: owner(planner scope 的超集)      │
│ scope: 只读 + draft-submit │      │ scope: 只读 + draft-submit + confirm     │
│                            │      │        + publish                         │
│ 能:读真相/起草卡 draft     │      │ 能:上述全部 + 经 harness 弹窗放行        │
│ 不能:claim/return/confirm/ │      │ F-06 纪律:owner_confirm 永不免审白名单   │
│       publish/audit(SCOPE_ │      │ (弹窗每次 Owner 亲按)                    │
│       DENIED 结构拒)        │      └──────────────┬───────────────────────────┘
└──────────────┬─────────────┘                     │
               │ draft-submit(落 drafts 区,结构锁死路径)
               ▼                                    ▼ confirm/publish(harness 弹窗)
┌──────────────────────── Lybra gate(被连接端点,零调度)────────────────────────┐
│ 只读面(5 工具,无 scope):queue_list/task_preview/validate/project_status/     │
│                          context_pack_build                                   │
│ draft-submit 写面(scope-gated,路径常量锁死 5_tasks/drafts/):见 Q1 方案       │
│ publish/confirm(DRAFT_PUBLISH + owner_confirm 双门):只有 owner token 能过     │
│ 出卡顺序/串并编排:gate 不参与(顾问 agent 层)                                 │
└────────────────────────────────────────────────────────────────────────────────┘
```
**单人自用**:Owner 一个 owner-console 会话(owner token)即可完成规划 + 放行——owner
scope 覆盖 planner 的只读+draft-submit,额外能 publish/confirm。**lybra-planner(planner
token)是为"规划者 ≠ 放行者"准备的**(多人协作 / 第三方外接规划顾问,不能给它 owner token)。

## §3 开放子问题答案(附落地证据)

### Q1 — token/scope 实现 + draft-submit 写面(§0-1/3/6/7/8)
- **planner token 铸造**:`ROLE_SPECS` 加一条 `{"role":"planner","token_ref":"svc-planner",
  "scopes":[<draft-submit scope>]}`(§0-3 只改这一处 + 测试)。只读工具无需 scope,planner
  连接即可用(§0-9)。
- **draft-submit 写面 —— 两方案,推荐 B**:
  - **方案 A(最省,零后端新增)**:planner scopes = `["intake_submit"]`(INTAKE_SCOPE),
    直接复用 `lybra_intake_submit_*`。confirm 不需 owner_confirm(§0-6),planner 自完成落盘;
    路径结构锁死 `5_tasks/drafts/external_intake/`(§0-7)。**缺**:external_intake 语义是
    "外部工单录入"(带 source_tag/client_tag/external_ref 外部字段),借它当"规划顾问起草
    任务卡"会污染记录归因。
  - **方案 B(语义正,推荐)**:新增 `DRAFT_SUBMIT_SCOPE` + `lybra_draft_submit_dry_run/
    confirm` MCP 工具,复用 `create_draft`(§0-8,已落 `5_tasks/drafts/`)的写逻辑,**路径
    常量锁死 `5_tasks/drafts/planner/`**(照抄 external_intake 的 `DIR 常量 + safe_id 正则`
    模式,§0-7);confirm 不强制 owner_confirm(planner 自起草);planner scopes =
    `["draft_submit"]`。**理由**:①语义正(规划产出 ≠ 外部工单);②scope 隔离干净(专属,
    不借 INTAKE 拿到 external_intake 其他语义);③红线1"只能落 drafts"由常量保证,与
    external_intake 同强度;④后端增量小(create_draft 已存在,只包 scope-gated 工具 +
    路径锁 + 测试)。
- **红线2 落地**:planner scopes **不含** DRAFT_PUBLISH/QUEUE_CLAIM/QUEUE_RETURN/
  OWNER_CONFIRM/AUDIT_* → 这些工具 handler 的 `_*_scope_allowed()` 一律 false → SCOPE_DENIED
  (§0-4/5)。planner **结构性不能** publish(drafts→queue),发布只能由 owner token 过双门。

### Q2 — SKILL 形态:两份新 SKILL(lybra-planner + owner-console),凑齐三件套
- **理由**:token 不同(planner ⊂ owner)、持有者不同(第三方 vs Owner)、F-06 纪律只对
  持 owner token 的会话适用(planner 会话结构性无 confirm scope,不需纪律防线,只需一句
  "你没有 confirm scope,别试")。与 executor 合为 **lybra-executor / lybra-planner /
  owner-console** 三件(治理文档预期形态)。
- **owner-console SKILL**:Owner 单人自用主形态。持 owner token;含全部顾问职责(建项
  引导/叙述/起草/评分)+ **确认门执行小节**(confirm/publish 经 harness 弹窗;**F-06 纪律:
  owner_confirm 永不加免审白名单,SKILL 顶部红线明写**)。
- **lybra-planner SKILL**:第三方 BYO 规划顾问。持 planner token;同一套顾问职责(继承
  同样 7 pillar)+ **确认门小节改为**:"你只能起草 draft;发布/确认要交给 Owner 的
  owner-console 会话(你没有 confirm/publish scope,试了会 SCOPE_DENIED——那是结构不是
  故障)"。
- **单人冗余诚实披露**:Owner 单人用只需 owner-console(owner scope ⊃ planner);planner
  token/SKILL 的价值在"规划者≠放行者"。O3 走 owner-console(§6);lybra-planner 的 scope
  边界由测试钉覆盖(§5)。

### Q3 — planner 读面清单:v1.0 用现有 5 工具 + SKILL 教 harness 直读 governance(§0-9/10/11)
- **现有 5 只读工具够覆盖**:queue(queue_list)、单任务(task_preview)、校验(validate)、
  gate 项目视图(project_status)、context pack。
- **缺口(如实)**:audit verdict 判定 + governance 文档(decision_log/roadmap/status/
  reports/drafts 现有内容)**无 MCP 只读面**(§0-10)。
- **v1.0 裁定(呼应 Owner 背景裁定"BYO planner 用自己 harness 已解全局读")**:planner
  用**自己 agent harness 的 Read/Grep** 直读 governance 文档 + audit_verdicts 记录文件
  (它与仓库同机)。SKILL 显式约束"只读本项目治理目录"以补偿失去的 gate project-scope
  隔离(诚实披露此边界)。**本片不新增任何只读 MCP 工具**(最小片纪律)。
- **列为 v1.1 候选(不在本片)**:把 board_adapter 已存在的 `get_drafts`/
  `get_owner_decision_records`/`get_orchestration_*` reader wire 成只读 MCP 工具 + 把
  task_preview 丢弃的 `audit_verdicts`/`returns` surface 出来(§0-10,零新 I/O)——好处 =
  拿回 gate project-scope 隔离 + 结构化 JSON;随 v1.1 看板一起做(整子树治理读 scope 本就
  是 v1.2 subprojects 工作)。给 R 钩子:是否至少在本片把 audit_verdict surface 进
  task_preview(planner 评分呈报要读判定)。

### Q4 — 继承机制:内联快照 + 来源 commit 标记 + 同步纪律(§0-13)
- **物理约束**:lybra-method 是**独立私仓**(`~/lybra-method`),planner SKILL 在**产品仓**
  (`~/lybra/skills/`);跨仓无 include/软链片段机制(软链是文件级,pillar 是文件中段),
  且零脚本红线 + 两仓发布节奏不同(lybra-method 视频门 vs 产品 finalize 门)。
- **方案(三选一给取舍,推荐 C)**:
  - A 软链整仓:planner SKILL 软链 lybra-method——不可行(planner SKILL 需自己的
    frontmatter + 产品侧 token/工具教学,不是纯方法论)。
  - B 生成/抽取脚本:破零脚本红线 + 产品仓构建依赖私仓,不可行。
  - **C 内联快照 + 来源标记 + 同步纪律(推荐)**:planner SKILL 内联这 7 个 pillar 段的
    文本,每段用 `<!-- lybra:planner-inherit pillar=X src=lybra-method@<commit> -->` 标记
    来源;约定**方法论文本的单一真相源 = lybra-method**,改动先改那边、再同步到 planner
    SKILL,审计核对两处逐字一致(文档级 SSOT 纪律,非机制强制)。**理由**:跨仓无 include、
    零脚本、两仓独立发布——快照 + 标记 + 纪律是唯一诚实可行的"不写两遍"(写一次于
    lybra-method,planner 侧是带溯源标记的受控副本)。
- **给 R 钩子**:src commit 用 `aee2ed6`(HEAD)还是 `855bc11`(pillar 增补点)——建议
  HEAD,因 aee2ed6 修了审计段搬运人归属(与顾问职责 pillar 相关)。

## §4 Scope(5 条)

### S1 — planner token 角色(gate 侧,最小)
`service_mode.py` `ROLE_SPECS` 加 planner(scopes 按 Q1 方案 B = `["draft_submit"]`);
`test_service_mode.py:519-521` 加断言 `scopes["planner"] == ["draft_submit"]`。

### S2 — `lybra_draft_submit_*` 写面(Q1 方案 B;路径结构锁死)
新增 `DRAFT_SUBMIT_SCOPE` 常量 + `_draft_submit_scope_allowed()` + `lybra_draft_submit_dry_run/
confirm`(复用 `create_draft`,落 `5_tasks/drafts/planner/` 常量 + safe_id 正则锁死;confirm
不强制 owner_confirm);工具可见性按 scope 追加。**红线1**:target 路径由常量拼接,入参无
路径字段(照 external_intake 模式)。

### S3 — 两份 SKILL(§Q2)
`skills/lybra-planner/SKILL.md` + `skills/owner-console/SKILL.md`;各内联 7 pillar 段(§Q4
标记)+ 各自 token/工具教学 + 确认门小节(owner-console 含 F-06 纪律,lybra-planner 含
"无 confirm scope")。README/mcp-agent-setup 补三件套软链说明。

### S4 — 读面策略(§Q3;纯 SKILL,零后端只读工具新增)
两份 SKILL 教:结构化真相走 5 个只读 MCP 工具;governance 文档 + audit_verdicts 用 harness
Read/Grep 直读本项目治理目录(披露 project-scope 隔离边界)。

### S5 — 披露
`docs/v1_disclosure.md` 新 row:planner 角色 = 只读 + draft-submit(结构锁 drafts);确认门
迁入顾问对话(harness 弹窗,owner_confirm 永不免审 = F-06 防线);Lybra 零调度(出卡顺序
顾问侧);governance 读走 harness 直读(project-scope 隔离由 SKILL 约束,discipline-held)。

## §5 测试策略(scope 拒绝钉必含;RED 纪律)
- **planner scope 拒绝钉(★A1 同族,gate 侧真跑)**:起真 gate + planner token,逐一断言
  SCOPE_DENIED:`queue_claim_dry_run`/`queue_claim_confirm`/`queue_return_dry_run`/
  `queue_return_confirm`/`draft_publish_dry_run`/`draft_publish_confirm`/`audit_dispatch_*`/
  `audit_verdict_*`/`owner_decision_record_*` —— 且**零记录落盘**(正内容断言:records 树
  字节不变)。
- **draft-submit 越界钉(红线1)**:planner `draft_submit` 成功 → 断言产物**只**落
  `5_tasks/drafts/planner/`(全路径断言);构造试图逃逸的入参(`..`/绝对路径/task_id 注入
  路径分隔符)→ 断言 target 仍在 drafts/planner/ 内(或 BLOCK),**绝不**落 drafts 外
  (RED 设计:对"直接用入参拼 target"的朴素实现红)。
- **publish 需 owner 门钉(红线2)**:planner `draft_submit` 落 draft 后试
  `draft_publish_confirm` → SCOPE_DENIED(planner 无 DRAFT_PUBLISH);owner token 同 draft
  publish → 成功(证"落地只能过 Owner 门")。
- **只读可用钉**:planner token 调 5 个只读工具 → 全 OK(证读真相不受 scope 影响)。
- **角色枚举钉**:`test_service_mode` 断言 planner scopes 恰 `["draft_submit"]`(防未来
  误加 scope)。
- **SKILL 继承标记钉**:两份 SKILL 各含 7 个 `planner-inherit pillar=` 标记 + src commit;
  owner-console 含 "owner_confirm" + "免审白名单"/"don't ask again" 禁止字样(F-06);
  lybra-planner 含 "SCOPE_DENIED"/"没有 confirm scope"。
- 四路串行 + `/tmp/.git` 跑前查跑后清;gate 校验语义/executor 连接器/既有只读工具零回归。

## §6 O3 剧本(Owner 当第一用户,全程 owner-console 顾问会话)
0. 前置:`serve rotate` 铸出含 planner + owner 的 connection.json;Owner 软链
   `skills/owner-console` 进 `~/.claude/skills/`;夹具侧先等 F-launch-4/5/6 批修(另行指令)。
1. **建项引导(含共创轮)**:Owner 在顾问会话说"我要建个项目" → 顾问按 lybra-method 提问库
   逐轮澄清 → **协作模式共创轮**(顾问讲串行/星形原语、给起点建议、与 Owner 共创真实模式)→
   敲定写入 PROJECT_SPEC「工作流编排」节(**SPEC 未定形不出第一张卡** = 开工前置门眼验)。
2. **出第一张卡 draft**:顾问按 SPEC 起草第一张卡 → `draft_submit` 落 `5_tasks/drafts/
   planner/`(Owner 眼验:draft 落 drafts 区,未进 queue)。
3. **Owner 确认发布**:顾问引导 Owner 发布该 draft → `draft_publish` 触发 **harness 工具
   审批弹窗** → **Owner 亲手点批准**(模型按不了)→ draft 进 `queue/pending/`(眼验:人手
   放行成立;若弹窗能被模型自动点 = 红线3 破,FAIL)。
4. **人话问队列状态**:Owner 问"现在队列什么情况" → 顾问调 queue_list → **人话叙述**
   (不给裸 JSON,红线 C)→ Owner 眼验可读性(对比 248 O3 "裸 JSON 看不懂"的反面)。
5. **scope 边界眼验(可选)**:Owner 让顾问试"直接帮我认领这张卡" → 顾问说明自己只能起草、
   claim 要 executor + Owner 门(或真跑 SCOPE_DENIED)——证顾问不越权。

## §7 给 R 的钩子
1. draft-submit 写面 Q1 方案 A(复用 intake)vs B(新 draft_submit 工具 + 专用 scope)——
   推荐 B,R 裁语义污染 vs 后端增量的权衡。
2. Q2 三件套(两份新 SKILL)vs 单份分情形——推荐两份,R 裁单人冗余是否可接受。
3. Q3 本片是否**零**新增只读工具(governance 走 harness 直读),还是至少把 audit_verdict
   surface 进 task_preview(planner 评分呈报要读判定;零新 I/O)——给 R 裁最小边界。
4. Q4 继承 = 内联快照 + src 标记 + 同步纪律(推荐 C);src commit 用 HEAD `aee2ed6`?
5. 确认门 harness 弹窗依赖 cc/codex 支持"工具审批 + owner_confirm 不免审"——落地核实各
   harness 的审批机制是否满足 F-06(本 DRAFT 已核 cc 有工具审批;codex 侧待核)?列为
   O3 前置或本片披露边界?
6. `draft_submit` confirm 不要 owner_confirm(planner 自起草)——与"落地过 Owner 门"是否
   自洽(自洽:起草 ≠ 落地,drafts→queue 的 publish 才是落地,那道门 planner 过不了)?
7. autonomy 档位片(07-12 进 v1.0,排本片后)与本片的接口:planner token 是否也参与预授权,
   还是预授权只对 executor claim?本片先不做,登记边界。

## §8 R 方向审计折入(PASS,七钩裁定 + R-1..R-5;Owner 2026-07-12,折入即 APPROVED)

**支点坐实(R 审计核实,更新 §0 台账)**:
- `validate_owner_confirmation(required: bool, …)` 证实 **confirm 分级存在**——intake/draft_submit
  类走 `required=False`(自完成),claim/return/publish 走 `required=True`(强制 owner_confirm)。
- `DRAFT_PUBLISH_SCOPE`(tools.py:37)+ `_draft_publish_owner_reasons`(tools.py:952)证实
  **publish 结构性过 Owner 门**(drafts→queue 需 owner_confirm)。
- 读面默认只读(tools.py:32 `READ_ONLY_NOTICE`)——planner 连接即得 5 只读工具,无需 scope。

**七钩裁定**:
1. **写面 A/B** → ✅ **B**。A 的语义污染不止不雅:external_intake 的 `source_tag` 会渗进
   归因记录,planner 草稿被记成"外部工单"= 记账失真。B 的"常量+正则锁死、入参无路径字段"
   = 代码保证,正确。
2. **SKILL 三件套** → ✅ 两份新 SKILL 凑三件套。**R-1**(见下)。
3. **读面零新增** → ✅ 接受(BYO planner 用自家 Read/Grep);缺口列 v1.1 诚实。**R-2**(见下)。
4. **继承 src commit** → ✅ 内联快照 + `src=lybra-method@<commit>` + 同步纪律,唯一诚实解。
   **加便宜钉**:grep 断言每个继承段都带 src 标记(防漏标)。
5. **harness 弹窗 F-06(codex 侧)** → **R-3**(见下)。
6. **draft_submit 免 owner_confirm 自洽** → ✅ 自洽:drafts 是提案区非真相,gate 时刻在
   publish(结构性 owner 门,支点已证)。**R-4**(见下)。
7. **与自动化档位片接口** → **R-5**(见下)。

**五条折入**:
- **R-1(重点,钩2)**:单人 Owner 只装 `owner-console` 时,**必须无缝获得全部顾问职责文本**
  (建项引导/共创轮/评分呈报)——**不许要求用户自己拼两份 skill**。实现随执行者(owner-console
  内联 planner 段,或声明式引用);**验收 = 只装 owner-console 走完 O3 剧本**(§6)。
- **R-2(钩3)**:**audit_verdict surface 进 `task_preview`**——零新 I/O、纯读、评分呈报刚需,
  **收进本片**(preview.py:87-88 把已计算但丢弃的 `audit_verdicts`[+`returns`]加进返回体)。
- **R-3(钩5)**:v1.0 confirm 支持面**只声明 Claude Code**(弹窗机制已真机验过);codex 的
  审批机制(suggest/auto)未核实前**不得声明支持 confirm**——owner-console SKILL 写明
  "codex 上仅 planner/executor 角色可用,confirm 面待验"。不阻塞本片,诚实披露。
- **R-4(钩6)**:披露行写明"planner 可自主填充 drafts 区;**drafts 不可被 claim**(仅 queue
  可认领)"——后半句**加一枚结构钉**(drafts 区任务对 claim 不可见:queue_claim 只从
  `5_tasks/queue/` 取,drafts 路径的 task 不在可认领集)。
- **R-5(钩7)**:249 **零 autonomy 语义**——SKILL 文本可提及 `autonomy_mode` 字段存在
  (默认 Supervised)但**不实现任何免确认路径**;卡上留一句接口注记即可,防两片纠缠。

**接口注记(R-5)**:autonomy 档位/策略化预授权 = 独立后续片(07-12 进 v1.0,排本片后)。
本片所有 claim 路径仍逐单 Supervised(owner_confirm 强制);planner token 不参与任何预授权。

## §9 实现记录(2026-07-12,executor 续作会话)

### 改动清单(不 commit,候增量审计 + Owner O3)
- **S1 planner token**:`service_mode.py` ROLE_SPECS 加 planner `{scopes:["draft_submit"]}`;
  `test_service_mode.py` 枚举钉 `scopes["planner"]==["draft_submit"]`。
- **S2 draft_submit 写面(Q1 方案 B 的最省落地)**:`tools.py` 加 `DRAFT_SUBMIT_SCOPE` +
  `_draft_submit_scope_allowed` + `lybra_draft_submit_dry_run/confirm` + TOOL_HANDLERS 注册 +
  WRITE_TOOL_DESCRIPTORS + 可见性 append。**复用既有 `draft_create` controlled-execute op**
  (board_adapter `create_draft` dry_run 发起 → `execute_dry_run` op==draft_create 落盘):
  dry_run 调 `create_draft(args, dry_run=True)`,confirm 调 `execute_dry_run(token,
  owner_confirmation_token=None)`——**confirm 免 owner_confirm**(draft_create execution
  分支 tools/board_adapter 均不检 owner_confirmation,支点坐实)。
  - **实现期纠偏(如实,同 F-247 on_resize 纠偏体例)**:卡 §2/§S2 原写落 `5_tasks/drafts/
    planner/` 子目录。实现时发现 `draft_create` op 已存在且落 **`5_tasks/drafts/` 根**
    (DRAFTS_DIR 常量 + `draft_slug(task_id)` 正则锁死文件名,draft_validator.py:68/77)——
    **复用它零后端新增**(board_adapter/draft_writer/draft_validator 全部零改动),红线1
    "结构性只能落 drafts 区"由 DRAFTS_DIR 常量 + slug 正则保证(入参无路径字段,`..`/绝对/
    分隔符全被 slug 清洗)。故**落 drafts 根,planner/ 子目录降级未做**;planner 草稿归因靠
    frontmatter `created_by`/`assigned_to`。给审计/Owner 核这一取舍。
- **R-2 audit_verdict surface**:`preview.py` 返回体加 `existing_audit_verdicts` +
  `existing_returns`(`find_records_for_task` 已加载全六类,**零新 I/O**)。
- **R-4 drafts 不可 claim**:纯结构核实钉(零代码)——queue_claim 只从 `5_tasks/queue/`
  resolve,drafts 区 task 用 executor(有 claim scope)token 试认领 → 非 PASS、无 leak 进
  queue(证结构性不可 claim,非 scope 拒绝)。
- **S3 两份 SKILL**:`skills/owner-console/SKILL.md`(R-1 自足:7 pillar 内联 + 建项引导 +
  共创轮 + 评分呈报 + 确认门执行 + **F-06 纪律** + **R-3 codex 披露**)+
  `skills/lybra-planner/SKILL.md`(同 7 pillar,确认门改"无 confirm scope,SCOPE_DENIED 是
  结构")。7 pillar 内联快照带 `src=lybra-method@aee2ed6` 标记(R 钩4)。
- **披露**:`docs/v1_disclosure.md` row 14;`README.md` 三件套软链说明。
- **契约维护**:`test_token_project_enforcement.py` 计数 19→21(新 2 工具**已过 project
  gate**——该测循环逐个 dispatch 断言 PROJECT_SCOPE_DENIED,失败仅在计数常量,证 gate 已生效)。
- **零改动**(红线7):`validator.py`/`state.py`/`board_adapter.py`/`draft_writer.py`/
  `draft_validator.py` **零 diff**(git 对账);gate 校验语义/executor 连接器/既有工具零回归。

### RED 纪律
- **R-2 变异实跑**:临时移除 preview.py 的两行 surface → `test_task_preview_surfaces_
  audit_verdicts` 红(`existing_audit_verdicts not in data`);恢复 → 绿。
- gate 钉为真 HTTP gate 真 token(planner/owner/executor 三 token),非 mock:scope 拒绝、
  写面锁死、publish 门、R-4 结构均对真 gate 断言。

### Gate(实测,串行,F-245-env-1 纪律)
- `test_planner_role` **13/13**(gate 7:只读可用/落 drafts/越界回落/全写拒+零记录/publish
  门/R-2 audit_verdict/R-4 不可 claim + SKILL 6:7-pillar-src/R-1 自足/F-06+R-3/无 confirm
  scope/R-5 零 autonomy/披露引用);`test_service_mode` planner 枚举钉绿。
- 四路:**BARE 803 OK(108 skip)/ SYSTEM 803 OK / TUI 184 OK / ACCEPTANCE PASS**
  (803 = 248 收口基线 790 + 13);跑前/跑后 `/tmp/.git` 净。
- 红线复核:`validator.py`/`state.py` 零 diff;planner scope 结构拒绝(非客户端自律);
  写面 DRAFTS_DIR+slug 锁死;publish 结构性过 Owner 门;confirm 门迁入对话 = SKILL 教学 +
  F-06 纪律(harness 弹窗,产品侧不新增机制——诚实:harness 弹窗依赖 cc,R-3 披露)。

### 诚实边界(executor 自报)
- **confirm 门的"人手放行"在产品侧不可测**:harness 审批弹窗是 cc/codex 的机制,产品代码
  只声明纪律(SKILL 写 F-06)。gate 测试证的是"planner 结构不能 confirm/publish";Owner
  亲手点弹窗放行 = O3 眼验(§6 剧本步 3),不是自动化测试能覆盖的。
- draft 落 drafts 根(非 planner/ 子目录)见上纠偏;若 Owner 要子目录隔离,另起微增量。

**状态:实现收口,未 commit——候 cc 增量审计 → Owner O3(全程人话,§6 剧本)→ finalize。**
(首轮增量审计 PASS。)

## §10 O3 REJECT 收口轮四 finding(2026-07-12;红线眼验第 3 步失败)

### 背景
Owner O3:流程主体走通(共创轮/draft 落 drafts/发布进 queue/人话叙述/目录结构讲解),但
**红线一票否决**——F-249-o3-2(CRITICAL):`draft_publish_confirm` **未弹窗直接发布**,F-06
"人手放行"在真机没生效。**仅 SKILL prose 禁令不够**(那只教 Owner 别设免审,拦不住权限系统
已 allow / 误点 don't-ask-again)。

### F-249-o3-2(CRITICAL)——取证 + 落地核实 + 结构修法
- **取证**(先查):`~/.claude.json` 无显式 `mcp__lybra` allow 规则(`.projects.*.allowedTools=[]`
  全空);lybra MCP server 已配(`.mcpServers.lybra.headers.Authorization = Bearer
  ${{LYBRA_MCP_TOKEN}}`)。成因 = prose 禁令是唯一防线,无结构档 → 弹窗可被静音/跳过。
- **落地核实**(claude-code-guide + 官方 docs/en/permissions):cc 权限三档
  **`deny > ask > allow`**,首个匹配胜出;**`ask` 先于 `allow`,可靠覆盖任何 allow**(含
  "don't ask again" 写进 `settings.local.json` 的 allow)。MCP 工具规则语法
  `mcp__lybra__<tool>`。
- **结构修法**:owner-console「一次性配置」步 4 **必装 `permissions.ask` 片段**(三个 owner
  确认工具 `draft_publish_confirm`/`queue_claim_confirm`/`queue_return_confirm` 钉 ask 档,
  放 `.claude/settings.json` shared)——**结构第一道防线,不可被 allow 静音**;F-06 prose 禁令
  **降为第二道防线**(SKILL 明写双防线)。

### F-249-o3-1——token 名统一 + mcp add 语法 bug 修
- 权威名 = **`LYBRA_MCP_TOKEN`**(Owner `~/.claude.json` 实际用的 + mcp-agent-setup 用的);
  三份 SKILL(owner-console/lybra-planner/lybra-executor)统一到它;`LYBRA_OWNER_TOKEN`/
  `LYBRA_PLANNER_TOKEN`/`LYBRA_EXEC_TOKEN` 全废。一会话设一值(角色不同、名同)。
- **顺带修语法 bug**:落地核实 `claude mcp add --help` —— **`--bearer-token-env-var` flag
  不存在**(248 executor SKILL 用了它 = 错);正确 = `claude mcp add lybra --transport http
  <url 位置参数> --header 'Authorization: Bearer ${{LYBRA_MCP_TOKEN}}'`(单引号存字面量,
  cc 运行时展开)。三份 SKILL 统一到正确样例。

### F-249-o3-3 + F-249-o3-4——同步 method@ae32755(继承段 src 更新)
- **o3-4 安家轮**:owner-console/lybra-planner 建项引导加**第 0 轮治理区安家**(先于一切内容
  问题,开工前置门):三选一(推荐 `~/.lybra/projects/<项目名>/` 产品默认根 / 当前目录 /
  自定义),"这个目录将来是多项目管理中心,值得起个好名字",定了位置≠当前目录则引导
  退出 cd 重启续接("搬家无损"),**治理区未定不进第 1 轮**。照 lybra-method@ae32755 成文。
- **o3-3 黑话清除**:"判型"→"看这个项目最接近哪一类";"内置原语"→"内置的 agent 协作模式"
  (owner-console/lybra-planner 建项引导,grep 钉核实零残留)。
- **继承段 src 标记**:7 pillar `src=lybra-method@aee2ed6` → `@ae32755`(内容 aee2ed6→ae32755(经 4d1611d,SKILL.md 三版一致)
  未变,diff 核实,只更 src;o3-3/o3-4 是建项引导流程段的改动,非 pillar 继承段)。

### RED/回归钉(4 新,17 总)
o3-2 ask 片段钉(owner-console 含 permissions.ask + 三工具 + "先于"/"第二道")/ o3-4 安家轮钉
(两份含"治理区安家"+"~/.lybra/projects/"+"治理区未定")/ o3-1 token 统一钉(三份含
LYBRA_MCP_TOKEN + 无 `--bearer-token-env-var`)/ o3-3 无黑话钉。

### Gate(实测,串行)
- `test_planner_role` **17/17**(13 + 4 新回归钉);单独连跑 5/5 稳。
- 四路:**BARE 807 OK(108 skip)/ SYSTEM 807 OK(107 skip)/ TUI 184 OK / ACCEPTANCE PASS**
  (807 = 803 + 4);跑前/跑后 `/tmp/.git` 净。
- **诚实边界(flake)**:BARE 满载 discover 首跑偶发 errors=1、重跑即过——既有真 HTTP gate
  fixture(test_confirm_client/test_agent_connector 同型:port=0 + 线程 server + shutdown/
  join)在满载下的**端口/线程竞态**,非产品 bug、非本片引入(本片新增 7 真 gate 测试略增
  概率);test_planner_role 单独 5/5 稳、ACCEPTANCE 独立 subprocess PASS。纪律=重跑取通过
  (同 F-245-env-1 先例)。
- **诚实边界(F-06)**:弹窗放行仍是 cc harness 机制,产品侧不可自动化测;但现在有**结构档**
  (必装 ask 片段),O3 复验点 = 装片段后重走发布,**弹窗必现**(vs 上轮纯 prose 未现)。
  R-3 已披露 codex ask 语义待验。

**收口轮状态:实现收口,未 commit——候 cc glm 增量审计 → Owner 重走 O3(重点:弹窗复现 +
安家轮 + 无黑话)→ finalize。**
