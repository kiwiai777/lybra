# AIPOS-213 — README + npm packaging (prepare-only) micro-plan (DRAFT)

- status: draft
- authority: NONE — 未经 Owner 批准不实现;本稿仅供复核
- date: 2026-06-24
- task-id: AIPOS-213 (proposed)
- epic: v1.0 Scope B — backlog (7) README/npm;对外收尾,**prepare-only**(不实际 publish)
- discipline: claims ⊆ `docs/v1_disclosure.md`(承 AIPOS-212);Quickstart = 真实命令;不改 gate/任何逻辑;不实现至获批。

---

## §1 范围

1. **对外 README 重写**(对齐 v1.0 真相 + 不过度宣称)。
2. **npm 打包元数据核对 + `npm pack` dry-run 验证**(publish-ready,但**不 publish**)。
3. **★ publish 边界**(见 §2.3)。

---

## §2 范围细化

### §2.1 README(`README.md` 重写)

- **DG-7 定位开篇**:"**可问责的单-agent 自治闭环 + 任意 MCP agent 经 Form B 接入的问责 gate**";三句根则:**gate not engine**(客户端连 Owner 启动的 gate,gate 不自跑 agent)、**files = truth**、**起草者 ≠ 确认者 ≠ 执行者**(R1)。
- **Quickstart 对齐真实安装路径(AIPOS-209)——只写存在的命令,且区分两类受众(Owner 修正:lybra 仅 npm 分发、不在 PyPI,故 `pip install lybra[tui]` 跑不通)**:
  - **npm 终端用户**:
    ```
    npm install -g lybra                 # gate core (npm/bin; Node 18+ & Python 3 on PATH; gate core zero-dep)
    pip install "textual>=0.50"          # enable the TUI (textual is on PyPI; lybra itself is NOT on PyPI)
    lybra init ./ws --project-id p       # scaffold a workspace
    lybra serve --workspace-root ./ws    # Owner starts the gate (rotates roles incl. copilot)
    lybra tui --gate-url http://127.0.0.1:7118 --workspace-root ./ws --project p \
              --llm-base-url <openai-compat> --llm-model <model> --llm-key-env LYBRA_PLANCHAT_LLM_KEY
    ```
  - **源码 / dev**(from a clone):`pip install ".[tui]"`(等价装 textual extra)。
  - README 明示:**"lybra 经 npm 分发,不在 PyPI;TUI 的 textual 单独 `pip install textual`"**。键名/flag 与 AIPOS-208/209 实现一致;key 经 env 注入(`LYBRA_PLANCHAT_LLM_KEY`),**不写裸 token**。
- **v1.0 能力段**:TUI 首屏 chat-to-task(DG-8,卡 conformance 代码保证)、Planning Copilot 只读(★A1)、gated publish confirmer=owner(AIPOS-204)、Supervised 闭环、Form A(Claude)/Form B(任意 MCP agent 经 gate)。
- **品牌**:沿用 `docs/assets/lybra-banner.png`(web 展示,已在)+ TUI 截图(§5b)。
- **"Scope & limits" 段**:直接引 `docs/v1_disclosure.md`(九类 disclosed-deferred);指向 `docs/v1_acceptance_runbook.md`(验收走查)+ acceptance 自动门命令。
- **修正旧 README 偏差**:现 Quickstart 用 `lybra board`/`lybra mcp` + 裸 `LYBRA_MCP_TOKEN`/`LYBRA_CAPABILITY_TOKEN`,且"three peer surfaces"未含 TUI —— 重写对齐 v1.0(serve+tui 主路径;mcp/board 作补充面),裸 token 示例改 env 口径。

### §2.2 npm 元数据 + pack dry-run

- `package.json` 核对/更新:`name=lybra`、`version=0.2.0`、`license=Apache-2.0`、`bin`、`repository`/`homepage`/`bugs`、`engines` 已在;**`description` 更新为 DG-7 定位句**;**补 `keywords`**(如 ai-agent, accountability, governance, mcp, audit, harness, tui)。
- **`files` 数组核对**:**含** `tools/`(因 `bin/lybra` spawn `python -m tools...`,Python 包必须随包)+ docs/、bin/、config/、templates/ 等;**排除** `tools/**/tests/**`、`__pycache__`、`*.pyc`、`.DS_Store`、`._*`、`task_cards/`、`.codex/`(现已在);**复核无证据/secrets 夹带**(connection.json 不在仓、task_cards 排除、无 key 文件)。
- **`npm pack --dry-run`**:产物清单核对——含 tools/ Python 包 + docs/assets PNG + README;**不含** tests/任何证据/secrets/`._*`。清单写入报告。

### §2.3 ★ publish 边界(必守)

- **实际 `npm publish` = 对外不可逆公开发布**,属**独立的、需 Owner 显式授权的 release 行为**,**不在本片**。
- 本片只做**准备 + 验证**(README + 元数据 + `npm pack` dry-run);**真正 publish 待 Owner 单独授权**(走 5a release 纪律:精确 pathspec、双仓边界、手动)。
- micro-plan + 报告显式标注此边界。

---

## §3 明确不做

实际 `npm publish`(独立 Owner 授权);改 gate/任何逻辑;web(206b);R2/R5;CI 接线;新机制。

---

## §4 必保

- **claims ⊆ disclosure**:README 任何 claim 不超出 `docs/v1_disclosure.md` 所披露/已证范围;不过度宣称("异构问责闭环"不作自我宣称)。
- **Quickstart = 真实命令**:只写存在的 `lybra` 子命令/flag(init/serve/tui/...);不写不存在的命令。
- **依赖隔离口径正确**:gate 核心零依赖(`npm install -g lybra` 即可跑 gate);TUI 经 **`pip install "textual>=0.50"`**(npm 用户)或 **`pip install ".[tui]"`**(源码/dev)启用 —— **`pip install lybra[tui]` 不可用**(lybra 不在 PyPI)。
- secrets 仅 fingerprint;示例用 env,不写裸 token;无证据/secrets 入包。

---

## §5 待决项(Owner 拍)

- **(a) README 章节深度/结构**:建议——Hero(banner+定位)→ What/Why → Quickstart(serve+tui)→ Capabilities(v1.0)→ How it works(gate/loop)→ **Scope & limits**(引 disclosure/runbook)→ Contributing → License。克制,不堆。
- **(b) TUI 截图**:建议——临时 workspace 跑 `lybra tui` 截 banner+首屏,**不泄 secrets**(状态栏仅 fingerprint),存 `docs/assets/`;**截图本身由 Owner 取**(需真终端;cc 不持 owner token/不进交互),或 v1.0 先用 banner PNG、TUI 截图随后补。
- **(c) publish-now vs prepare-only**:**建议 prepare-only**(README + 元数据 + pack dry-run)+ **publish 待 Owner 单独授权**。

---

## §6 测试

- **T1 README 诚实守护(仿 test_disclosure)**:README 引 `docs/v1_disclosure.md` + `docs/v1_acceptance_runbook.md`(存在性);**无过度宣称**(反向断言:无"heterogeneous accountability loop"自我宣称;DG-7 定位句在)。
- **T2 Quickstart 命令真实 + 无 broken-install(Owner 加固)**:解析 README 代码块里的 `lybra <subcmd>`,断言每个 subcmd ∈ 真实 CLI subparser 集(防写不存在命令);**反向断言:README 不得含 `pip install lybra[tui]`**(防 broken-install 复发);且 npm 路径**确含 `pip install` + `textual`**(TUI 启用口径正确)。
- **T3 files 数组守护**:`package.json` `files` 含 `tools/`,且含排除 `task_cards/`/`._*`/`__pycache__`/tests(防证据/secrets 夹带)。
- **T4 全量回归**:`tools/` 全绿;`ACCEPTANCE: PASS` 不回退。
- (npm pack dry-run = 实现期人工验证 + 报告清单,不入单测以免依赖 npm。)

---

## §7 cc glm 审计点

1. **claims ⊆ disclosure**:README 无超出 v1_disclosure.md 的宣称;无过度宣称措辞(T1)。
2. **Quickstart 对齐真实命令**:只写存在的子命令/flag(T2);key 走 env 非裸 token。
3. **files 不夹带证据/secrets**:含 tools/、排除 tests/task_cards/._*;`npm pack` dry-run 清单干净(报告)。
4. **publish 边界**:本片 prepare-only;实际 publish 标为独立 Owner 授权 release,未执行。
5. **依赖隔离口径**:gate 核心零依赖、TUI 需 .[tui] —— README 表述正确。
6. 纯文档/元数据;无 gate/逻辑改动;全量绿 + ACCEPTANCE PASS。

---

## §8 与后续片分界

- 本片(AIPOS-213)= README 重写 + npm 元数据核对 + pack dry-run(prepare-only)。
- 不含:实际 npm publish(独立 Owner 授权)、AIPOS-206b、R2/R5、CI 接线。
- 其后:Owner 授权 → `npm publish`(独立 release,走 5a)。

---

> **DRAFT 结束。** 待你复核(尤其 claims ⊆ disclosure、Quickstart 对齐真实命令、files 不夹带证据/secrets、publish 边界 prepare-only)+ 拍 §5 a/b/c → Owner 批 → 实现 + §6 测 → cc glm 审计(§7)→ 你抽查 → finalize。不实现至获批。
