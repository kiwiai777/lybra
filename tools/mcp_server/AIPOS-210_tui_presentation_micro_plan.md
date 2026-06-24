# AIPOS-210 — TUI presentation / branding micro-plan (DRAFT)

- status: draft
- authority: NONE — 未经 Owner 批准不实现;本稿仅供复核
- date: 2026-06-24
- task-id: AIPOS-210 (proposed)
- epic: v1.0 Scope B — TUI 呈现/品牌(独立后续片,AIPOS-209 §4 预留);**纯呈现层,零逻辑/零红线影响**
- discipline: 不改 gate/copilot/任何逻辑;不引新依赖;不实现至获批。

---

## §1 范围(纯呈现)

1. **启动 banner**:Lybra logo + "lybra" 名,终端 **ASCII/ANSI 码图**(参考 Hermes 风格),启动时显。
2. **绿色品牌主题**:交互文字配色走 agent 界面观感(参考 codex,但 **codex 蓝 → Lybra 绿**)。

---

## §2 设计约束(写进实现)

- **单一主题 token(强约束)**:品牌绿在**一处定义**(单个 token),**禁色字面量散落 app.py**。codex 蓝→Lybra 绿**一处可切**。可 grep 断言:除 token 定义处外无 `#rrggbb`/`green` 等色字面量。
- **优雅降级**:
  - **NO_COLOR** env(以及非 TTY / Textual 单色)→ 不上色,纯文字。
  - **窄/单色终端**:banner 在窄宽下**回落纯文字 `LYBRA`**(不炸、不错位)。宽度阈值判定 → 宽则 ASCII 码图,窄则纯文字。
- **隔离不变**:呈现逻辑落 **tui 层**;**仅 app.py 导 textual**;copilot.py / gate 核心 / aipos_cli **不碰**;**不引新第三方**(textual 自带渲染 / Rich 标记即可)。

### §2.1 设计落点(建议,§5a 待决)

为同时满足"单 token 可 grep 断言 + banner 窄宽回落可 core-lane 测 + 仅 app.py 导 textual",建议**纯逻辑入新 `tools/lybra_tui/presentation.py`(零 textual)**,app.py 仅消费:
- `presentation.py`(**零 textual**):`LYBRA_GREEN`(单一 token)、`banner(width: int) -> str`(宽→ASCII 码图,窄→`"LYBRA"`)、`color_enabled(env) -> bool`(NO_COLOR/TTY 判定)。**纯函数,可 core-lane 测**。
- `app.py`(唯一 textual 导入):把 `LYBRA_GREEN` 注入 Textual CSS 变量(`$accent` 等,一处)、启动时 `banner(terminal_width)` 渲染、按 `color_enabled` 决定上色。
- 此法沿用既有 state.py(纯)/app.py(textual)模式;**仍满足"仅 app.py 导 textual"**(presentation.py 不导)。
- 若 Owner 坚持"全在 app.py",则 banner/token 测改 tui-lane(需装 textual),core-lane 只保留 grep 断言 —— 见 §5a。

---

## §3 明确不做

改 gate/copilot/任何逻辑;新依赖;web;R2/R5;(7) README·npm;(8) 验收脚本;键位/交互行为改动(纯视觉)。

---

## §4 必保

- **所有 AIPOS-206 不变量未触**(纯呈现,不碰 copilot 只读/★A1/零写/DRAFT→Owner→gate/RF-5/L0–L3/单项目)。
- **core lane 无 textual 仍绿**(banner/主题不进 gate 路径;presentation.py 零 textual)。
- 依赖隔离:仅 app.py 导 textual;无新第三方;pyproject/npm 分发不变。

---

## §5 待决项(Owner 拍)

- **(a) 落点**:① 纯 `presentation.py` + app.py 消费(建议;可 core-lane 测 banner/降级/token)② 全在 app.py(banner/token 测走 tui-lane,core-lane 仅 grep)。**建议 ①**。
- **(b) 品牌绿值**:具体 hex(如 `#3fb950` GitHub 绿 / `#00d787` 等)——单 token,Owner 定值;实现给一个默认、一处可改。
- **(c) banner 风格/字样**:ASCII "LYBRA" 码图样式(Hermes 风参考);是否含 tagline 一行(建议仅 logo+name,tagline 留 README 片)。

---

## §6 测试

- **T1 banner 渲染 + 窄终端回落**:`banner(wide)` 含 ASCII 码图;`banner(narrow)` == 纯文字 `"LYBRA"`;无异常/错位(core-lane,经 presentation.py)。
- **T2 降级**:`color_enabled` 在 `NO_COLOR` set / 非 TTY → False;否则 True。
- **T3 单 token**:grep 断言——除 `presentation.py` token 定义行外,app.py/presentation.py 无散落色字面量(`#rrggbb` / 具名色)。
- **T4 依赖隔离回归**:仅 app.py 导 textual;presentation.py 零 textual;copilot/gate 无。
- **T5 全量回归**:`tools/` 全绿(core lane 无 textual);banner/主题不进 gate 路径。

---

## §7 cc glm 审计点

1. **纯呈现零红线**:无 gate/copilot/逻辑改动;206 不变量全未触。
2. **单 token 化**:品牌绿一处定义,无色字面量散落(grep 实证);codex 蓝→绿一处可切。
3. **优雅降级**:NO_COLOR / 窄终端回落纯文字 `LYBRA`,不炸不错位(实测)。
4. **隔离不变**:仅 app.py 导 textual;presentation.py 零 textual;无新第三方;core lane 绿。
5. **范围克制**:无 web/R2/R5/(7)/(8);纯视觉。

---

## §8 与后续片分界

- 本片(AIPOS-210)= 启动 banner + 绿色主题(单 token + 降级)。
- 不含:(6) RF-3/§9 披露、(7) README/npm、(8) 验收脚本、AIPOS-206b、R2、R5、交互/键位改动。

---

> **DRAFT 结束。** 待你复核(尤其 §2 单 token 化 + 降级 + 隔离不变、§5a 落点)+ 拍 §5 a/b/c → Owner 批 → 实现 + §6 测 → cc glm 审计(§7)→ 你抽查 → finalize。不实现至获批。
