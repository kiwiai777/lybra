# AIPOS-242 (Slice D) — /project 视图回归 gate 真相源(F-o3-2 + F-launch-3 + env-pin + F-o3-18)

- **status: draft**
- **authority: NONE** — no product code, no commit until R direction-audit + Owner approval.
- **parent:** macOS Track-2 O3 findings **F-o3-2**(/project list 列错家)+ **F-launch-3**(launcher
  TUI 行缺 `LYBRA_HOME_ROOT`)+ **env-pin**(launcher 把 `LYBRA_ACTIVE_PROJECT` pin 进 gate env)+
  **F-o3-18**(switch 乐观宣称不核实)。
- **★ 核心原则(Owner 锚点):项目视图的唯一真相源是 gate,不是客户端。** F-o3-2 与 F-o3-18 是同一类病
  的两个症状——客户端自行解析 / 乐观宣称,与 gate 实际认知脱节。

## §0 调研结论(先行,已核代码)

1. **解析序(`workspace_config.resolve_active_project`,AIPOS-230 §1a)**:
   explicit > **env `LYBRA_ACTIVE_PROJECT`** > 工作区 config > **全局 `~/.lybra/config.json`** >
   单项目回退 > fail-closed `PROJECT_AMBIGUOUS`。
2. **env-pin 病根实锤**:launcher `:189` serve 行携带 `LYBRA_ACTIVE_PROJECT="$PROJECT_A"` → gate
   永远停在第 2 级;`/project switch` 写的是第 4 级(全局 config)→ **switch 结构上永远驱动不了
   gate**。live `PROJECT_SCOPE_DENIED` 演示因此不可能(gate 恒 resolve lybra,永不出 scope)。
3. **写读同频确认**:TUI `set_active_project` 写真实用户 `~/.lybra/config.json`(honors $HOME);
   gate 子进程(serve Popen,继承 env+$HOME)第 4 级读同一文件 → 去掉 env-pin 后 switch **本可**
   驱动 gate,机制现成,无需新写通道。
4. **F-o3-2 病根**:`app.py:721 _cmd_project_list` 裸用 `resolve_home_root()`(客户端 env/默认)+
   `_project_candidates(home)`(客户端列目录)。launcher TUI 行(`:235-243`)未传
   `LYBRA_HOME_ROOT` → 客户端落到默认 `~/.lybra/projects` → "no established projects"(gate 明明
   看着 disposable home 的 2 个项目)。
5. **F-o3-18 病根**:`app.py:746 _cmd_project_switch` 写完 runtime config 直接打印
   *"The gate now resolves '<name>' …"* —— **从没问过 gate**。env-pin 场景下静默失败仍显示成功。
6. **gate 只读面盘点**:现有 4 个 read-tool(`lybra_queue_list` / `task_preview` / `validate` /
   `context_pack_build`)**无一报告 gate 自身解析的 home / active project / 项目列表**
   (`visible_tool_descriptors` 内部用 `_resolve_active_project_for` 只为收窄列表,不对外)。
   **最近邻已查证并排除**:board HTTP 面的 `/api/governance`(`web/board/app.py:91` →
   `get_governance` → `_resolve_governance_dir`)内部确会解析 active project,但 (i) 它在 board
   端口、非 token-gated 的 MCP gate 面(TUI 是 gate client,走 board = 绕过 enforcement 造第二真相
   源,恰是本片要治的病);(ii) 载荷是治理文档、非项目视图。不复用。
   → **需加一个最小只读 read-tool**(§1);内部机制全部现成(`_resolve_active_project_for`、home
   模型、单项目回退的"established"判据),零新解析逻辑。

## §1 产品修法

### (a) 新最小只读 read-tool:`lybra_project_status`(gate 的自我报告)
返回 **gate 视角** 的结构化只读快照(全部复用现有 helper,零写、零新解析逻辑):
```json
{ "home_root": "<gate 解析的 home>",
  "active_project": "lybra" | null,
  "resolution_error": null | "PROJECT_AMBIGUOUS: ...",
  "projects": ["demo", "lybra"],
  "workspace_root": "<gate repo_root>" }
```
- `projects` = established 判据与单项目回退**同源**(`<home>/<child>` 有 `5_tasks/queue` +
  `project.json`),不另造第二套判据。
- 注册进 `TOOL_HANDLERS` + `READ_TOOL_DESCRIPTORS` → **自动经 dispatch 唯一咽喉被 project-gate**:
  **不设豁免**(18 gated/0 exempt → **19 gated/0 exempt**)。若有单测 pin 死 18 计数,更新为 19 并
  在卡内披露(加一个被 gate 的工具 ≠ 改 enforcement 语义;`_project_gate` 字节不动)。
- **越界行为即特性**:active=demo 而 token=['lybra'] 时,本工具与其它 18 个一样返回
  `PROJECT_SCOPE_DENIED`,其标准 deny 消息**本身携带 gate 解析的 active project**
  (`"active project 'demo' is not in the token's projects ['lybra']"`)——这正是诚实信号(§1c 用)。

### (b) `/project list` → gate 视角(F-o3-2)
- 主路径:`observe("project_status")` → 显示 **"Projects (as resolved by the GATE)"** + gate 的
  active + home_root,标注来源。
- `PROJECT_SCOPE_DENIED` → 不装死:显示 deny + 从标准消息解析出的 gate-active("gate 正 resolve
  '<x>',你的 token 只含 [...] → 所有 gated 读都会被拒;switch 回 scope 内项目可恢复")。
- 传输错误 → 诚实报错。**取消静默客户端回退**(要么 gate 真相,要么明说拿不到;不再裸
  `resolve_home_root()` 列客户端猜的家)。`/project new` 的本地 scaffold 语义不变(本就是 Owner
  本地文件动作)。

### (c) `/project switch` → 写后核实,按真结果报(F-o3-18)
写 runtime config + session + copilot rebind(不变),**然后做一次 gated 探测**
(`observe("project_status")`),四分支照实报:
1. 200 且 `active_project == name` → `gate now resolves '<name>' ✓`
2. `PROJECT_SCOPE_DENIED` 且 deny 消息命名 **`<name>`** → gate **已跟随** switch;报
   "gate resolves '<name>';token scoped [...] → gated 读将返回 PROJECT_SCOPE_DENIED"(这正是
   O3 的 enforcement 演示态,依然是 switch 成功)
3. 200 但 active ≠ name,或 deny 命名**别的项目** → **响亮 MISMATCH**:"switch 已写 runtime
   config,但 gate 仍 resolve '<other>' — 多半是 serve 进程带着 `LYBRA_ACTIVE_PROJECT` env pin
   (解析序 env > config);修 serve 启动环境后重试"
4. 探测传输失败 → "已写 config,但**无法核实** gate 是否跟随"(不乐观宣称)
- deny 消息的解析:正则取单引号内项目名;该消息是产品自有、被 enforcement 单测锁定的稳定格式,
  另加一条**客户端解析 pin 测试**(格式若变,测试先红)。**不改 deny 本身**(红线:不碰
  `_project_gate`)。

### (d) launcher 两处(Owner 工具,不入产品仓)
- **TUI 行 += `LYBRA_HOME_ROOT="$HOME_DIR"`**(F-launch-3):客户端本地动作(/project new、
  /home git-init)与 gate 同家。
- **serve 行去掉 `LYBRA_ACTIVE_PROJECT` pin**;初始 active 改为**播种全局
  `~/.lybra/config.json`**(写前保存旧值,teardown 恢复——launcher 会临时改真实用户 runtime
  config,这是全局 config 设计的固有含义,披露 + 可逆)。`LYBRA_HOME_ROOT` env 保留(home 与
  active 是两回事,disposable home 正需要它)。

## §2 红线(R make-or-break)

- **enforcement 语义零改动**:`_project_gate` / `_capability_in_project` / deny 构造字节不动;新
  工具**不豁免**(0 exempt 保持);唯一允许的测试改动 = 18→19 计数披露性更新(若存在)。
- 新 read-tool **零写**(纯读 + 现有 helper;无 scope 需求,与其余 read-tool 同待遇);
  `test_scope_reachability` 语义不受影响。
- copilot scopes `[]` / ★A1 / 双根 / zero-dep 核不动;TUI 仍 thin client(gate 真相,客户端不造
  第二真相源)。
- 不许任何"乐观宣称"残留:switch 输出四分支全部以探测实况为据。

## §3 Verify

- 单测:新工具 payload 正确性(home/active/projects/`PROJECT_AMBIGUOUS` 面)+ 新工具**被 gate**
  (in-scope 200 / out-of-scope DENIED,复用咽喉测试模式)+ TUI list gate 视角 + switch 四分支
  (含**模拟 env-pin 的 MISMATCH 负例**)+ deny 消息解析 pin。
- 四路:BARE / SYSTEM / TUI / ACCEPTANCE 全绿。
- **O3 验收(Mac,Owner 锚点)**:`/project` 列出 disposable home 的 lybra+demo(gate 视角);
  `switch demo` → `/queue` → **live `PROJECT_SCOPE_DENIED`**(首次真机达成);`switch lybra` →
  读恢复;switch 报告与 gate 实况一致——含**故意 mismatch 负例**(临时给 serve 带回 env-pin →
  switch 必须响亮报 MISMATCH,不许绿)。

## §4 R direction-audit hooks

- 判(a)新工具的必要性结论(§0-6:现有 4 read-tool 无一近似)与最小面设计(字段够不够/多不多)。
- 判"越界即 deny + 从标准消息解析"的方案 vs 给 deny 加结构化字段(后者触 `_project_gate` 红线,
  已排除;确认正则+pin 测试可接受)。
- 判 launcher 播种真实 `~/.lybra/config.json`(存旧值/teardown 恢复)的可逆性披露是否足够。
- 核 §0 调研有无遗漏的近似物(如 board HTTP 面有无项目报告端点可复用)。
