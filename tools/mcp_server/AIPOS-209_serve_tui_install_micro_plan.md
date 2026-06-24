# AIPOS-209 — serve↔TUI + install (minimal installable path) micro-plan (DRAFT)

- status: draft
- authority: NONE — 未经 Owner 批准不实现;本稿仅供复核
- date: 2026-06-24
- task-id: AIPOS-209 (proposed)
- epic: v1.0 Scope B — backlog (5) serve↔TUI + install;承 AIPOS-205/206/207/208 + dogfood 全链(DL-…-09/-11/-12/-13)
- discipline: 范围克制,**最小可安装路径,不镀金**;不改 gate 核心;延续依赖隔离 + 所有 206 不变量;不实现至获批。

---

## §1 目标

把一路手动的步骤收敛成**正式启动面**:
```
安装 → lybra serve（serve rotate 铸角色含 copilot）→ lybra tui（进 AIPOS-208 首屏，带 copilot 配置）→ 已 live 证的全链
```
本片主要是**接线 + 文档 + 最小便利**,不引入新机制、不越界到 R2(多项目 home)。

---

## §2 范围

### §2.1 安装路径(承 DG-7 分发,核实非假设)
- **分发 = npm/bin**(`package.json` bin/lybra = node shim → spawn python,`PYTHONPATH=packageRoot`);`version 0.2.0`。**不变**。
- **`pip install .[tui]`** 装 `textual`(+ 未来 copilot 第三方依赖,如有;现 copilot 裸 urllib 零第三方)。**gate 核心仍零依赖可单装可跑**(`pyproject dependencies=[]`)。
- **不接管 npm 分发**(`[tool.lybra] distribution=npm` 不变;pyproject 仅 deps/extras)。
- 文档化两条安装形态:① npm/bin(运行 CLI/serve)② `pip install .[tui]`(用 TUI 客户端)。core CI lane 不装 textual 仍绿。

### §2.2 serve↔tui 接线 + LLM 配置启动面收敛
- `lybra serve`(已存)= Owner 启动 gate(serve rotate 铸 executor/owner/copilot;loopback)。**Owner 拥有 gate 生命周期**(gate-not-engine,不改)。
- `lybra tui`(AIPOS-205/206/208 入口)连 Owner 启动的 gate;**最小便利**:`--connection-json` 缺省回落 `<workspace>/.lybra/local/connection.json`(存在则用),减少手动传参;不存在则照旧报错提示先 `lybra serve`。
- **LLM 配置启动面**(承 AIPOS-208):`--llm-base-url` / `--llm-model` / `--llm-key-env`(默认名 **`LYBRA_PLANCHAT_LLM_KEY`**)。key **仅经 env 注入、fingerprint-only、绝不入 argv / connection.json / 日志**。三者齐 + `--project` 才启 copilot 首屏;否则回落 observe。
- 启动面把"配 LLM 即 chat-to-task 首屏"作默认体验文档化(AIPOS-208 已实现,本片只收敛文档 + connection.json 缺省)。

### §2.3 5a PR-flow / release 纪律(固化 53a2a42 教训)
落一份简短 **release 纪律**(产品仓 `docs/release_discipline.md` 或等价),固化:
- **精确 pathspec**:`git add <显式路径>`,**禁 `git add -A`**;commit 前 `git diff --cached --name-only` 核对。
- **双仓边界**:产品仓(lybra)与治理仓(ai-project-os)分开 commit;不交叉污染(53a2a42 KPRX 教训)。
- **release/分支**:在分支上做、`git pull --rebase` 后 push;不在脏树上扫入无关改动。
- **finalize 仅手动**:audit PASS + Owner 批准后才 commit/push;不自 finalize。
- (纯文档/流程,无码改;供未来 finalize 引用。)

---

## §3 待决项(Owner / 复核拍)

- **(a) serve+tui = 两命令 vs 一启动器**:建议**两命令**(`lybra serve` 长驻 = Owner 拥 gate 生命周期;`lybra tui` = 客户端;合一启动器会模糊 gate-not-engine 边界 + 镀金)。
- **(b) v1.0 workspace 约定**:建议**单 workspace(现 git 式,`--workspace-root`/`AIPOS_WORKSPACE_ROOT`,默认 cwd 发现)**;**不引入单一默认 home**;**多项目 home(Model 2)= R2,本片不做**。
- **(c) connection.json + LLM key env 落地**:`serve rotate` 写 `.lybra/local/connection.json`(**0600**,已实现);LLM key 经 **env**(`LYBRA_PLANCHAT_LLM_KEY`)注入,文档建议 0600 文件 source 或 shell export,**绝不入 argv / connection.json / git**;启动面只显 fingerprint。建议照此文档化,不新增密钥存储机制。

---

## §4 明确不做

改 gate 核心;TUI 主题 / banner(独立后续片);web fetch(AIPOS-206b);多项目 / home(R2);decision_log 目录化(R5);(8) 验收脚本;新增密钥管理机制;一键启动器(除非 (a) 选它)。

---

## §5 必须保持

- **依赖隔离**:textual(+ 未来 copilot 第三方)仅 `tui` extra;`tools/mcp_server` + `tools/aipos_cli` 不 import;pyproject 不接管 npm 分发;core lane 无 textual 可装可跑。
- **所有 AIPOS-206 不变量**:copilot 只读(scopes [])、★A1、零文件写、DRAFT→Owner→gate(confirmer=owner)、RF-5、L0–L3 三纪律、单项目。
- **secrets**:token/key 仅 fingerprint,不入 argv/日志/git;connection.json 0600。

---

## §6 测试

- **T1 gate 核心零依赖可装可跑**:无 textual 下 core lane 全绿(承现状 454/1 skip);gate 起停正常。
- **T2 tui extra 起首屏**:装 textual 后 `lybra tui`(配 LLM)进 chat-to-task 首屏;未配回落 observe(tui lane / 既有 app 测延伸)。
- **T3 serve↔tui 接线连通**:`--connection-json` 缺省回落 `.lybra/local/connection.json`;连 Owner 启动 gate 成功;缺失则报"先 serve"提示。
- **T4 LLM key 不泄漏**:key 仅经 env;启动面/日志只显 fingerprint;不入 argv/connection.json(断言)。
- **T5 依赖隔离回归**:仅 app.py 导 textual;copilot/gate 无;全量绿。
- **T6 5a 纪律文档存在**:release_discipline.md 含精确 pathspec / 禁 -A / 双仓边界 / 手动 finalize(存在性 + 内容点检,非码)。

---

## §7 cc glm 审计点

1. **最小可安装路径成立**:install → serve → tui → 全链;无新机制、无镀金。
2. **依赖隔离延续**:tui extra 唯一 UI 依赖处;gate 核心零依赖可单装可跑;pyproject 不接管 npm。
3. **206 不变量不回退**:copilot 只读 ★A1、零文件写、DRAFT→Owner→gate、RF-5、单项目 —— 仍绿。
4. **workspace 不越界 R2**:单 workspace;无 home / 多项目引入。
5. **secrets**:LLM key 仅 env+fingerprint,不入 argv/connection.json/git;connection.json 0600。
6. **5a 纪律固化**:release_discipline.md 落定 53a2a42 教训(精确 pathspec / 禁 -A / 双仓 / 手动 finalize)。
7. 全量 tools/ 绿;`--connection-json` 缺省回落不破既有显式传参路径。

---

## §8 与后续片分界

- 本片(AIPOS-209)= 最小可安装路径(install→serve→tui 接线 + connection.json 缺省 + LLM 启动面收敛 + 5a 纪律文档)。
- 不含:(6) RF-3/§9 披露、(7) README/npm 打磨、(8) 验收脚本、TUI 主题/banner、AIPOS-206b、R2、R5。

---

> **DRAFT 结束。** 待你复核(尤其 §3b workspace 约定不越界 R2、§5 依赖隔离延续、§2.3 5a 纪律)+ 拍 §3 a/b/c → Owner 批 → 实现 + §6 测 → cc glm 审计(§7)→ 你抽查 → finalize。不实现至获批。
