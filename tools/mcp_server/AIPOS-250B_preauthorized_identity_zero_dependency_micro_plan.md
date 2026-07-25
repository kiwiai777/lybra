# AIPOS-250B (DRAFT) — PreAuthorized 身份验证零依赖化(方案 B:connection.json token 绑定身份)

- **Status**: **DRAFT**(Authority = NONE;不 commit;交 R 方向审计,过后再走实现)
- **AIPOS 号**: 占位 `250B`(最终号待 roadmap/Owner 定;本片是 AIPOS-250 的架构跟进修复)
- **Parent 裁定**: 治理仓 `direction_log/2026-07-direction-decisions.md` 2026-07-16 条
  "AIPOS-250 架构裁定:零依赖 gate ↔ 自动化档位身份验证的冲突,选方案 B"(已读):
  - 零依赖 gate = 四轮竞品扫描钉死的核心差异化(开箱即用、不逼装环境);**自动化绝不能以牺牲
    零依赖为代价**(方案 A 分裂"轻 gate 无自动化 / 重 gate 有自动化" = 卖点自拆,否决)。
  - **方案 B(Owner 拍板)**:PreAuthorized 身份校验改用**零依赖来源**——`connection.json`
    (JSON,gate 天生零依赖读)里的 **role→授权 canonical 实例绑定**;不碰 YAML profile 注册表。
    语义更正:"谁能被自动放行"由 Owner 亲手发 token 时的授权决定(connection.json,可控),
    **授权即身份,都在那张 token 里**。
  - 走完整 micro-plan DRAFT → R 方向审计 → 实现;修复必含**零依赖(无 PyYAML)条件下的穿透测试**。

---

## ★ 落地核实先行:重要诚实发现(交 R 定问题框架)

裁定的**机制归因**("`registry_available()=False` → 身份永远 unregistered → PreAuthorized
永远回落 Supervised")我**逐条落地核实后无法在当前代码复现**。实测(证据附 §0-6):

- `resolve_instance_id` 对未注册实例**回显输入为 canonical**(`agent_profiles.py:382`
  `resolution=unregistered, canonical_instance_id=value`),**不返回 None、不阻断**。
- 在 `yaml=None`(模拟 bare python)下跑信封内 claim 端到端(`patch(agent_profiles.yaml, None)`
  跑 `test_in_envelope_claim_auto_releases`)→ **仍自动放行、仍落 PreAuthorized 记录**。
- 即当前 `_match_claim_envelope`(`tools.py:1149-1158`)与 `match_claim_envelope`
  (`autonomy_policy.py`)**均不读 `registry_available`**,匹配用的是 `canonical_agent_instance`
  (=回显的自报实例)与 `actor`。

**因此当前可复现的真缺口不是"永远回落",而是更该修的一条**:PreAuthorized 的"严格身份匹配"
实为 **claim 自报实例 == 策略 `agent_or_role` 字符串** —— 身份是 **agent 自我声明的**,**没有
任何"这张 token 到底授权哪个 canonical 实例"的权威绑定**。对一条**无人复核**的自动放行路径,
自报身份是软点(伪造 token 绑定不了,但自报实例字段可任填,只要匹配策略串即放行)。

**为何 Owner O3 看到"回落 Supervised"**:高度可能是**本轮已修的 schema 雷**——修复前 MCP
inputSchema 缺 `autonomy_policy` 声明,顾问根本**armed 不了策略**(payload 被剥/被逼填 evidence →
`MISSING_OWNER_APPROVAL_EVIDENCE`);无活跃策略 → `load_policy` 返 None → 回落 Supervised。观测
到的回落是"策略没建成",被(设计语言暗示的)registry 归因接了过去。

**结论(交 R 裁)**:方案 B 的**方向不变、且更该做**——把 PreAuthorized 身份从"自报回显"升级为
"**token 权威绑定**"(Owner 铸 token 时钉死该 token 授权哪个 canonical 实例,claim 自报必须与之
逐字相等,否则回落)。这既(a)兑现零依赖(JSON 读,永不碰 yaml),又(b)堵上自报身份软点,
还(c)前瞻性防"日后有人给 claim 加 registry fail-closed 门"真的踩零依赖雷。**§1 红线以此为准**;
若 R 认为应改以裁定原始机制框架表述,DRAFT 随裁。

---

## §0 真实现状台账(executed-✓,file:line 落地核实)

| # | 事实 | 出处 |
|---|---|---|
| 1 | **connection.json token 条目现绑 `role/token_ref/scopes/fingerprint/token`(+可选 `projects`/`projects_enforced`),无任何 canonical 实例绑定** | `service_mode.py:282-301`(`_role_token_entry`);ROLE_SPECS `:41-92`(executor 只有 scopes,无 instance) |
| 2 | **gate 认证时把 token 条目映射成 request capability**(`token_ref/role/operations/fingerprint/[projects]`),经 `REQUEST_CAPABILITY` ContextVar 供各 handler 读 | `http_sse.py:135-152`(`_service_role_capability`);`tools.py:43,191-211`(`REQUEST_CAPABILITY`/`_capability_token`) |
| 3 | **`load_service_role_registry` 从 connection.json 只拷 `role/token_ref/scopes/expires_at/fingerprint`(+projects)** —— 新字段若不在此显式拷贝会被**静默丢弃**(有前例:AIPOS-242 F-NEW,`projects` 曾被此处丢、项目门整条静默失效) | `http_sse.py:385-416` + `:408-414` 注释(F-NEW 前例) |
| 4 | **PreAuthorized 匹配点**:`_match_claim_envelope`(gate 侧)传 `canonical_agent_instance`+`actor` 给 `match_claim_envelope`(纯谓词);`agent_or_role ∈ {canonical, actor}` 即过 | `tools.py:1125-1159`;`autonomy_policy.py:match_claim_envelope`(agent/role 分支) |
| 5 | **canonical 解析链**:`_resolve_claim_instance` → `resolve_instance_id(agent_instance, profiles)` + `registry_available()`;profiles 来自 YAML(`custom_agent_profiles.yaml`),`registry_available()==(yaml is not None)` | `tools.py:449-458`;`agent_profiles.py:203-211`(`yaml is not None`),`:370-382`(resolve) |
| 6 | **零依赖回显核实**:`yaml=None` 时 `registry_available()==False`、`resolve_instance_id('exec.cc.local', empty)=={canonical:'exec.cc.local', resolution:'unregistered'}`;`patch(yaml,None)` 跑信封 e2e = **仍自动放行**(不阻断) | 本片 headless 实测(命令记录见实现记录);`agent_profiles.py:382` |
| 7 | **claim 现有身份门**:`actor` 必须 == 解析出的 `canonical_agent_instance`(`INSTANCE_MISMATCH`);这是**自报 vs 自报回显**的一致性,非 token 权威 | `tools.py`(claim dry_run `actor != canonical_agent_instance` 分支) |
| 8 | **rotate 铸 token 无实例入参**:`serve rotate [--project X]` 只能选项目,无"绑定 executor canonical 实例"的入口 | `service_mode.py:304-333`(`build_connection_config` 仅 `project`) |

## §1 生死红线(方案 B 结构守)

1. **零依赖不破(核心 moat)**:PreAuthorized 身份校验路径**绝不 import yaml、绝不依赖
   `registry_available()`**;身份来源 = `connection.json`(JSON,gate 天生零依赖读)。带 PyYAML
   与否,PreAuthorized 行为**逐字一致**。
2. **token 权威身份(升级自报)**:PreAuthorized 放行要求 **claim 自报 `agent_instance`(及
   `actor`)== 该请求 token 在 connection.json 里绑定的授权 canonical 实例**;不等 → 回落
   Supervised。**授权即身份**——Owner 铸 token 时钉死绑定,运行时不再信自报。
3. **严格匹配、有疑回落(fail-safe 不变)**:身份 ∧ task_selector ∧ 时间窗 ∧ 已放行<max_tasks ∧
   status==active(严格 AND);伪造 `owner_policy_ref` / 越界 / 超额 / 过期 / **身份不符** 任一 →
   回落 Supervised,绝不静默放行。
4. **★A1 + 独立审计不受影响**:executor 永无 owner_confirm 能力;PreAuthorized 一段式仍 gate
   落盘、无 agent 按门;return/publish/audit 仍逐单;亲缘审计仍绑独立 canonical 身份(那条链
   不动,YAML 富数据在**非零依赖**场景保留其别名/model_tier 用途——只是不再是 PreAuthorized
   身份验证的必要条件)。
5. **富数据保留、依赖解耦**:YAML profile 注册表(别名解析、model_tier 等富数据)在装了 PyYAML
   的部署里照常可用;方案 B 只把 **PreAuthorized 的身份"必要条件"** 从 YAML 迁到 connection.json,
   不删 YAML 能力。

## §2 架构(身份来源迁移图)

```
── 现状(自报回显,YAML 为设计意图但零依赖下退化)────────────────────────
  claim(agent_instance=<自报>) → resolve_instance_id(YAML profiles)
     ├ 有 PyYAML + 注册 → canonical(注册表背书)
     └ 无 PyYAML / 未注册 → canonical = <自报回显>(unregistered)   ← 身份=自我声明
  match: policy.agent_or_role ∈ {canonical(=自报), actor(=自报)}    ← 无权威绑定

── 方案 B(token 权威,零依赖 JSON)──────────────────────────────────────
  ┌ 授权时刻(Owner 铸 token,一次)────────────────────────────────┐
  │ serve rotate --executor-instance exec.cc.local (拟)            │
  │  → connection.json tokens[].agent_instance = "exec.cc.local"   │  ← 授权即身份
  └───────────────────────────────┬────────────────────────────────┘
                                  │ gate 认证:capability.agent_instance ← token 条目(JSON 读)
  ┌ 运行时(PreAuthorized 匹配)──────────────────────────────────┐
  │ bound = _capability_token().get("agent_instance")   (零依赖)   │
  │ 身份门:claim.agent_instance == bound  且  actor == bound ?     │
  │   ├ 是 → 进 §Q2 严格 AND 其余谓词 → 匹配则一段式自动放行         │
  │   └ 否/ bound 缺 → 回落 Supervised(逐单 owner_confirm)          │
  └────────────────────────────────────────────────────────────────┘
  (resolve_instance_id / registry_available 不再是 PreAuthorized 的必经;YAML 富数据旁挂)
```

## §3 开放子问题答案(附证据)

### Q1 — connection.json 现在绑 canonical 实例了吗?绑定在哪写?
- **现状**:**只绑 role,不绑 canonical 实例**(§0-1,`_role_token_entry` 无 instance 字段)。
- **需新增**:token→canonical 实例的绑定,**在 `serve rotate` 铸 token 时写入**
  `_role_token_entry`(`service_mode.py:282`)+ ROLE_SPECS/rotate 入参(§0-8):
  - executor 角色 token 加 `agent_instance: <canonical>`(如 `exec.cc.local`);
  - 值从何来?**Owner 铸 token 时指定**——拟给 `serve rotate` 加 `--executor-instance <id>`
    (与 `--project` 同族);未指定时**不写该字段**(向后兼容:无绑定的 token → PreAuthorized
    不可用,回落 Supervised,零行为漂移)。
  - **落地必带第二处**:`load_service_role_registry`(`http_sse.py:400-407`)与
    `_service_role_capability`(`:136-152`)**都要显式拷 `agent_instance`**——否则同 AIPOS-242
    F-NEW,字段在 connection.json 里有、但被注册表拷贝层静默丢,身份门永远拿不到 bound(§0-3
    前例钉死这条)。
- **给 R 钩子**:绑定粒度 = 每 token 一个 canonical 实例(简单),还是一个 token 可授权一组
  实例(池)?本片建议**一 token 一实例**(最严、最像"授权即身份");池化留后。

### Q2 — gate 侧改动(零依赖,不 import yaml)
- **改动点**:`_match_claim_envelope`(`tools.py:1125`)或其调用前,加**身份门**:
  `bound = str(_capability_token().get("agent_instance") or "").strip()`;
  `if not bound or bound != claim_agent_instance or bound != actor: return None`(回落)。
  该读取纯 dict 取值,**零依赖**。
- **`resolve_instance_id`/`registry_available` 降级为"非必经"**:PreAuthorized 不再依赖它们得
  canonical;Supervised 路径与亲缘审计的 YAML 富数据用途**保留不动**(§1-5)。是否把
  `_claim_metadata.identity_provenance.registry_available` 保留为"信息性标记"(不再门控)—— 建议
  保留记录、不再作判定输入。
- **给 R 钩子**:身份门放在 `_match_claim_envelope` 内(集中)还是 claim dry_run 顶部(早退)?
  建议 `_match_claim_envelope` 内(与其余谓词同处,单一放行判定点)。

### Q3 — 红线结构落法(不动)
- ①严格匹配:身份门是**逐字相等**(bound==自报),不等即回落(§1-2/3)。
- ②伪造/越界/超额/过期/**身份不符** → 回落 Supervised(§1-3);伪造 token 无法伪造 bound
  (bound 来自已认证 token 条目,不是 claim 入参)。
- ③★A1/独立审计:身份门只加在 PreAuthorized 匹配;confirm 路径、return/audit、亲缘绑定链
  全不碰(§1-4)。

### Q4 — ★零依赖穿透测试(系统性,必做)
- **本轮根因是"测试没复刻零依赖 gate 环境"**(direction_log 明写)。新增测试**在 yaml=None 下跑**:
  1. **零依赖信封内自动放行**:`patch(agent_profiles.yaml, None)`(+ 任何其它 yaml 读点)下,
     token 绑定 `agent_instance=exec.cc.local`、claim 自报同实例 → **仍自动放行**、落 PreAuthorized
     (证零依赖可达)。
  2. **零依赖身份不符回落**:同上但 claim 自报 `agent_instance=other` 或 token 未绑 → **回落
     Supervised**(证 token 权威、自报堵死)。
  3. **有/无 PyYAML 行为一致对拍**:同一信封 claim,`yaml` 在场 vs `yaml=None`,PreAuthorized
     结果逐字相同(证零依赖不破)。
- **测试基建注意**:真 HTTP gate fixture 需让 registry(connection.json)带 `agent_instance`;
  patch yaml 要覆盖 gate 线程读到的模块(同进程 daemon 线程,patch 生效——本片已验此法可行)。
- **给 R 钩子**:是否加一条**元测试**断言"每个 PreAuthorized 判定输入均非 yaml 派生"(防回归
  引入新 yaml 依赖)?建议加(轻量,守住零依赖红线)。

## §4 Scope(结构落法,分批)

- **S1 — token 绑定身份(写入)**:`serve rotate` 加 `--executor-instance`(+ 可选其它角色);
  `_role_token_entry` 写 `agent_instance`;`build_connection_config` 透传。未指定→不写(兼容)。
- **S2 — 注册表/capability 拷贝(防静默丢)**:`load_service_role_registry` + `_service_role_capability`
  各显式拷 `agent_instance`(AIPOS-242 F-NEW 同款,必须两处都补)。
- **S3 — gate 身份门(零依赖判定)**:`_match_claim_envelope` 加 token-bound 身份逐字校验;
  PreAuthorized 脱钩 `registry_available`。
- **S4 — 测试(零依赖穿透)**:§Q4 三钉 + 元测试;真 HTTP gate + yaml=None。
- **S5 — o3-launch(Owner 工具,不在仓)**:铸 token 时带 `--executor-instance exec.cc.local`
  (不再依赖 YAML 注册表);确认 connection.json 出现 `agent_instance` 绑定供 O3 验证。
- **S6 — 披露/SKILL**:disclosure 更新(PreAuthorized 身份源 = connection.json token 绑定,零依赖,
  非 YAML);owner-console/lybra-executor SKILL 提"executor token 由 Owner 铸时绑定 canonical 实例;
  自报实例须与绑定一致方能信封内自动放行"。

## §5 测试清单(RED 纪律;真 gate;**含零依赖穿透**)
1. 零依赖(yaml=None)信封内自动放行(token 绑定实例 == 自报)——**RED 对"仍依赖 registry"的实现红**。
2. 零依赖身份不符/未绑定 → 回落 Supervised(证 token 权威、自报堵死)。
3. 有/无 PyYAML PreAuthorized 结果逐字一致对拍。
4. 伪造 token 无法伪造 bound(bound 来自认证条目,claim 入参改不动它)。
5. 元测试:PreAuthorized 判定输入无 yaml 派生(防回归)。
6. 既有 §5(AIPOS-250)九钉 + 端到端 + 穿透-schema 钉全绿(回归)。
7. 四路串行 + `/tmp/.git` 跑前查跑后清;**BARE lane 必须真正覆盖信封自动放行**(本轮教训:
   BARE 跑了但旧测试未在 yaml=None 下断言 PreAuthorized 放行,故没抓到)。

## §6 O3 剧本更新(方案 B)
1. Owner `serve rotate --executor-instance exec.cc.local` → connection.json token 带绑定。
2. Owner 起信封(task_selector 覆盖 code 类;agent_or_role = exec.cc.local)→ confirm 落盘。
3. executor(该 token)claim 信封内 code 卡、自报 agent_instance=exec.cc.local → **自动放行**;
   记录 PreAuthorized + owner_policy_ref。
4. **零依赖眼验**:同一流程在无 PyYAML 的 gate 上跑 → 仍自动放行(核心新增眼验点)。
5. **身份不符眼验**:executor 自报 agent_instance=impostor → 回落 Supervised(逐单 confirm)。
6. 撤销/到期/超额 → 回落(红线2 眼验,同 250)。

## §7 给 R 的钩子(方向审计)
1. **问题框架**:接受 §"落地核实"的诚实修正吗?——当前真缺口 = 自报身份(非"永远回落");
   方案 B 定位为"自报→token 权威"升级,零依赖是顺带兑现。裁定方向不变,机制表述以此为准?
2. **绑定粒度**(Q1):一 token 一实例 vs 一 token 一组实例(池)?本片建议前者(最严)。
3. **rotate 入参**:`--executor-instance` 单角色够吗,还是需通用 `--role-instance role=id`?
   auditor/planner token 是否也需绑定(planner 只 draft、不 claim,似不需;auditor 走独立链)?
4. **YAML 富数据去留**:确认只解耦"PreAuthorized 身份必要条件",别名/model_tier 富数据保留?
5. **兼容**:未绑定的旧 token → PreAuthorized 不可用、回落 Supervised(不报错)——可接受?
6. **零依赖元测试**是否值得(断言判定输入无 yaml 派生)以防新依赖回归?
7. **实现前置**:本 DRAFT 过 R 后,实现是否与"AIPOS-250 finalize"合并推,还是独立片?(250 主体
   已收口候 finalize;250B 是其架构跟进。)

## §8 边界与非目标
- 不删 YAML profile 注册表、不改亲缘审计独立性链、不动 return/publish/audit 档。
- 不做实例池化(留后)。
- `~/o3-launch.sh` 属 Owner 工具(不在仓、不进任何 finalize pathspec)。
- 本片 DRAFT 不含实现;authority NONE;交 R 方向审计后另行授权实现。
