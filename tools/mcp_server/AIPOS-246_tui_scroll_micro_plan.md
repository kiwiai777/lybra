# AIPOS-246 — TUI 会话区滚动(F-245-o3-1,substantive)

- **Status**: DRAFT(R 方向审计 PASS,已折 R-1..R-3 + 五钩裁定)
- **Authority**: NONE(DRAFT only,不 commit / 不 push / 不改 truth,等 Owner 批)
- **Parent**: F-245-o3-1(AIPOS-245 O3 登记,Owner 连续两轮受阻 → 优先级提升,先于 W 终审)
- **定位**: 纯 TUI 交互层(客户端呈现/输入)——**零 gate/confirm/claim/校验语义改动**;
  `tools.py` / `state.py` 零 diff。

---

## §0 症状与调研结论(executed-✓ 台账)

**症状(O3 真机)**:滚轮只动终端回滚,VerticalScroll 不动;Owner 无法回看历史。

**落地读码 + 装机 API 核实(全部执行过,非猜测)**:

| # | 事实 | 出处 |
|---|---|---|
| 1 | 每条消息 mount 后强制 `widget.scroll_visible()` ×3 处:`_append`(app.py:429)、`_markdown`(:438)、thinking 行(:1252) | 读码 |
| 2 | **thinking 计时器 0.5s tick 更新行内容**;配合 #1,任何上滚位置在下一条消息/思考帧到来时被拽回底部 —— **即便滚轮事件真的进来了,也会在 ≤0.5s 内被拉回**,与"滚不动"不可区分 | 读码 :1254/:1299 |
| 3 | `#conversation` = VerticalScroll,`can_focus = True`(装机核实),但 app 恒 `set_focus(prompt)`;PgUp/PgDn 被 PromptArea(TextArea)吃掉做光标移动 → **键盘无滚动通道**(鼠标失效时零替代) | 装机核实 + 读码 :411/:274 |
| 4 | **Textual 8.2.8 自带 `Widget.anchor()`**(装机核实源码):anchored 容器新内容到来自动跟底;**用户上滚 → `_anchor_released=True` 停跟**(release_anchor @ widget.py:815);**滚回底部 → `_check_anchor` 自动复位跟随**(:824-830,watch_scroll_y 驱动)。语义 = 聊天软件标准 UX,无需自研状态机 | 装机源码 :815-830/:1962 |
| 5 | 终端层:Textual 默认开鼠标上报;O3 链路 iTerm2→SSH→WSL 存在鼠标事件丢失可能(AIPOS-237 kitty fix 只动键盘协议)。**但 #1/#2 的"拽回"足以独立解释全部症状** —— 鼠标层是否真丢,修掉拽回后 O3 一测便知 | 读码 + 推断(诚实标注:未实测 Owner 终端) |
| 6 | **R-1 版本二分(executed-✓,逐版下 wheel grep 实证)**:0.50.1/0.55.0/0.61.0 无 `anchor`;0.79.1–3.0.0 有**同名异构旧 API**(`anchor(*, animate)`,锚"子件进父视野",无释放/复位语义);**4.0.0 起** = 本片所需 `anchor(anchor: bool)` + `_anchor_released`/`_check_anchor` 释放-复位语义(与 8.2.8 同构,4.0.0/4.1.0/5.0.0/6.1.0/7.0.0/8.0.0 逐版核实)。**同名异构旧 API 的存在坐实"禁 hasattr 降级"(R-1)**:hasattr 在 3.x 上假绿且调错语义 | pip download + unzip grep(逐版) |
| 7 | **R-2 核实解释器环境**:上表 #3/#4 的装机核实均在 **WSL2 `~/o3-textual-venv`(textual 8.2.8 ≥ 4.0)** 执行;Owner O3 真机(macOS `~/o3-textual-venv`)版本**未实测** → O3 清单第 0 步先对账(见 §4) | 如实标注 |

**成因裁定(可证伪)**:主因 = **app 层强制置底(#1+#2)**;次候选 = 终端鼠标层(#5)。
修 S1+S2 后:若滚轮好了 → 主因成立;若滚轮仍死但 PgUp/PgDn 好用 → 终端层坐实,
键盘通道已兜底,另登终端配置 runbook 注记(不改产品)。

---

## §1 硬红线(验收逐条查)

1. **纯 TUI 交互层** — gate/confirm/claim/校验语义零改动;`tools.py`/`state.py` 零 diff。
2. **AIPOS-244/245 全部旧钉保留绿**(pending 严格模态 / default-yes 零调用 / 引导文案 / 真接线)。
3. **不引入轮询/定时滚动/自动行为** — anchor 是 Textual 原生被动机制,thinking 计时器不变(只摘掉它的 scroll 调用)。
4. **executed-✓** — §0 台账已核;实现中每处改动对照现状行号。
5. **行为变化如实披露** — "永远强制置底" → "跟随直到你上滚"是**本片目的本身**,在披露/测试中明说,不偷渡其他行为。

---

## §2 Scope(3 条)

### S0 — textual 地板抬升(R-1,先于代码)
- **改动**:`textual>=0.50` → **`textual>=4.0`**,共 4 处披露站点(executed-✓ grep 清册):
  `pyproject.toml:20`(tui extra)/ `README.md:49` / `docs/v1_acceptance_runbook.md:22` /
  `docs/v1_release_macos_runbook.md:113`;另 `docs/v1_macos_track2_exercise.md:26`(venv 搭建行)同步。
- **理由(R-1)**:所需 `anchor(bool)`+释放/复位语义 4.0.0 起才有;0.79–3.0 同名异构旧 API 使
  hasattr 降级**假绿且调错语义** → 硬地板,不降级。gate 零依赖不涉(textual 只在 tui extra)。

### S1 — anchor 取代强制置底(主修)
- **改动**:`on_mount` 对 `#conversation` 调一次 `.anchor()`;**删除** 3 处逐消息 `scroll_visible()`
  (:429/:438/:1252)。效果:底部时新消息自动跟随(现状体验不变);**上滚后新消息/thinking 帧
  不再拽回**(症状根除);滚回底部自动恢复跟随(Textual 原生 `_check_anchor`)。
- **S1b(小而必要;R 附加边界)**:Owner **自己提交输入**时 `scroll_end()` 一次(你打字 = 你要看
  当前回合;聊天软件通例)。只挂在 `on_prompt_area_submitted` 入口——**仅 Owner 提交触发;
  gate 事件 / 新消息 / worker 回包 / thinking 帧一律不得触发回底**(R 裁定边界,测试钉)。
- **红线自检**:被动机制,零轮询;内容/文案零变。

### S2 — 键盘滚动通道(鼠标失效兜底)
- **改动**:PromptArea `_on_key` 拦截 `pageup`/`pagedown`/`end` 转发 app(与既有 ↑/↓ 边缘转发
  同构):PgUp/PgDn → `#conversation.scroll_page_up()/scroll_page_down()`;End → `scroll_end()`
  (即手动回底 + 复位跟随)。焦点始终留在 prompt,打字不受扰。
- **牺牲披露(R 裁定:入正式披露,不止 /help)**:TextArea 原生 PgUp/PgDn 光标翻页在 prompt
  (常 1–3 行)内无实用价值,让位给会话滚动;`/help` 补键位说明 + `docs/v1_disclosure.md`
  加一行(PgUp/PgDn/End 被 TUI 征用为会话滚动)。
- **红线自检**:纯输入路由;不碰 Shift+Tab(mode)/↑↓(历史/下拉)既有语义。

### S3 — 鼠标层:只诊断,不改产品
- S1 落地后 O3 实测滚轮:若仍死 → 终端层坐实,登 runbook 注记(iTerm2 "Enable mouse reporting" /
  tmux mouse 等链路排查),**产品零改动**;若好了 → 主因闭合,S3 无事可做。
- **不做**:任何鼠标协议 hack(AIPOS-237 式驱动 patch 是最后手段,本片不动)。

---

## §3 测试策略(pilot 真键,防"装机构不同"假绿)

- **★ anchor 行为对(真 Textual `run_test` + 真按键/真滚动,非 mock)**:
  (a) **上滚不拽回**:灌 N 条消息 → 滚离底部(释放 anchor)→ 再 mount 新消息 + thinking 帧 →
  断言 `scroll_y` 不变(修前此测试 **RED**:scroll_visible 拽回;**R 裁定:RED 实跑输出
  原文入审计卡台账**,非"声称红过")。
  (a′) **R-3 专条**:anchor 释放期间(上滚态)`_start_thinking` + 连续 `_tick_thinking` 多帧 →
  断言 `scroll_y` 全程不动(thinking 计时器是 0.5s 高频源,单独钉)。
  (a″) **S1b 边界钉(R 裁定)**:上滚态下,gate 事件/新消息/worker 回包到来 → **不回底**;
  仅 Owner 提交输入 → 回底。
  (b) **底部跟随**:在底部 → mount 新消息 → 断言仍在底部(现状体验保留)。
  (c) **回底复位**:释放后 `scroll_end` → 再 mount → 断言跟随恢复。
- **S2**:prompt 聚焦下按 PgUp → `scroll_y` 减小;PgDn 对称;End → 回底 + 后续跟随。
- **S1b**:上滚状态下提交一行输入 → 回底。
- **回归**:244/245 全部旧钉 + `_simulate_input` 镜像不涉及(镜像只覆盖输入拦截语义,滚动在
  widget 层)— 如实说明镜像不扩展的理由。
- **四路(串行,F-245-env-1 纪律)**:BARE(skip 守卫)/ SYSTEM / TUI / ACCEPTANCE 全绿。

## §4 O3 验收(Owner 真机)
0. **版本对账(R-2)**:`~/o3-textual-venv/bin/python -c "import textual; print(textual.__version__)"`
   须 ≥ 4.0;旧 venv 若 <4.0 → 先 `pip install -U "textual>=4.0"` 再走清单(REBUILD=1 只重打
   lybra,不动 venv,须单独核)。
1. 灌长会话 → 滚轮上滚:**能动、且停得住**(新消息/thinking 不拽回);
2. 滚回底部 → 恢复跟随;
3. PgUp/PgDn/End 键盘通道(鼠标坏也能回看);
4. 打字提交 → 自动回底看当前回合;
5. 若滚轮仍死 → 报终端层,键盘通道兜底可用(S3 runbook 分支)。

## §5 不在本片(登记)
- 终端鼠标协议产品侧适配(S3 诊断分支的后续,若坐实另起);
- 滚动位置跨 `/clear`/会话持久化;
- "跳到底部"可视 affordance(键盘 End 已覆盖;可视按钮属 v1.1 打磨)。

## §6 给 R 的钩子
1. 成因裁定(§0:主因=强制置底)证据链是否成立?anchor 原生机制替代自研状态机是否正确取向?
2. S1 删 3 处 `scroll_visible` 是否有漏(该 3 处即全集?`grep -n scroll_visible` 对账)?
3. S1b(提交即回底)是否越界(它是主动行为,但由 Owner 输入触发——是否可接受)?
4. S2 牺牲 TextArea 原生 PgUp/PgDn 是否可接受?End 键复位语义是否清楚?
5. §3(a) 修前 RED 的设计是否足以防假绿?
