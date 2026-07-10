# AIPOS-247 — `--mouse` 可选旗标 + banner 挪进对话流(微片)

- **Status**: APPROVED(R 方向审计 PASS + Owner 裁定"折入后即视为 Owner APPROVED",2026-07-09)
- **Authority**: 实现授权(R 四钩裁定 + R-A/R-B/R-C 已折入,见 §6.5;收口后队列回 W 终审)
- **Parent**: AIPOS-246 S3 结论 + Owner 裁定 A(DL-20260708-02 已登):`run(mouse=False)` 是
  AIPOS-237 F-o3-12b 的刻意取舍;要滚轮 = 每会话可选地重开该取舍,**默认忠于 237**。
- **定位**: 纯 TUI 客户端(启动旗标 + 呈现布局);零 gate/confirm/claim/校验语义改动;
  `tools.py`/`state.py` 零 diff。
- **明确不做(Owner 记录性说明)**:"正屏 transcript(Claude Code 式,滚轮+原生复制并存)"
  = v1.1 架构方向候选,本片不做、不展开。

---

## §0 真实现状台账(executed-✓)

| # | 事实 | 出处 |
|---|---|---|
| 1 | `__main__.py:125` `build_app(...).run(mouse=False)`,注释即 F-o3-12b 取舍(commit 6fbe7c7);textual `App.run(mouse: bool = True)` 直通 driver `_enable_mouse_support`(`?1000h/?1006h`,装机 8.2.8 linux_driver:128-137) | 读码 + AIPOS-246 S3 pty 实证 |
| 2 | banner 是**固定层**:`compose()` :389 `Vertical(Static(id="banner"), id="brandbar")` 悬在 `#conversation` 之上,不随对话滚动;banner 内容在 `on_mount`(:410-415)按 `self.size.width` 渲染 **一次** | 读码 |
| 2′ | **实现期纠偏(executed-✓)**:DRAFT 原文称"`_render_banner` 按宽度重渲染,`on_resize` 触发"——**不实**。`app.py` 现无 `_render_banner` 函数、**从未有过 `on_resize` 处理器**(`grep -rn on_resize tools/lybra_tui/` 零命中;`git log -S on_resize -- tools/lybra_tui/app.py` 零提交)。banner 只在 mount 时渲染一次,终端改宽后本就不重渲染。故 S2 的"on_resize 改 query-存在才更新"**无对象,落空为零改动**;resize 观感按 R 钩2 裁定列 O3 眼验 | 读码 + git 史对账 |
| 3 | 开屏欢迎语 = `on_mount` 里两条 `_system(...)`(AIPOS-245 A6),已在对话流内 | 读码 |
| 4 | o3-launch TUI 行现无 mouse 相关参数 | 读工具 |

## §1 硬红线
1. **默认行为零变化**:不传 `--mouse` → 与 246 收口后逐字节同行为(mouse=False、237 取舍原样)。
2. 纯客户端;不碰 gate/scope/★A1/双根/zero-dep;244/245/246 旧钉全绿。
3. **披露如实**:`--mouse` = 每会话重开 F-o3-12b(得滚轮/点击,失原生选中——iTerm2 逃生门 Option+拖拽);不承诺"两全"(那是 v1.1 候选)。
4. banner 入流是**布局迁移**,ASCII/配色/宽度自适应字节不变;`/clear` 后 banner 随流清走(如实披露,claude-code 同型)。

## §2 Scope(3 条)

### S1 — `--mouse` 旗标
- `__main__.py`:argparse `--mouse`(`store_true`,default False,help 写明取舍)→ `run_tui(..., mouse=...)` → `app.run(mouse=mouse)`。
- 开启时 startup 提示一行(`_system`):`鼠标模式:滚轮/点击进 TUI;选中复制用 Option+拖拽(iTerm2)。`(P-A:告知代价;不开不打印)。
- 披露:`docs/v1_disclosure.md` row 12 追加 `--mouse` 每会话反转注记。

### S2 — banner 挪进对话流
- `compose()` 去掉 `#brandbar` 固定层;`on_mount` 把 banner Static(保留 `id="banner"`,加 `turn` 类)
  **作为 `#conversation` 首个子件 mount**,欢迎语紧随其后(开屏观感 = 246 复验过的"欢迎语在 banner
  正下"不变);内容超屏后 banner 随记录自然滚走。
- `on_resize` 的宽度重渲染改为 query 对话流内的 `#banner`(存在才更新;`/clear` 后不存在 → 跳过,
  不再重建 —— 披露)。
- CSS:`#brandbar` 规则删除;`#banner` 保高度自适应。

### S3 — o3-launch 开关(Owner 工具,仓外)
- `MOUSE=1 ~/o3-launch.sh` → TUI 行追加 `--mouse`(默认不加,忠于 237);launcher 打印当前模式一行。

## §3 测试策略
- **S1**:①默认:argparse 解析无 `--mouse` → `run` 收到 `mouse=False`(mock `App.run` 或参数透传单测,锁"默认零变化"红线);②`--mouse` → `mouse=True` + startup 提示出现;③无提示当默认(负向)。
- **S2(pilot 真跑)**:①开屏:banner 是 `#conversation` 首子件,欢迎语在其后(顺序断言);②灌超一屏 → banner 滚出视口(`banner.region` 不在可视区 / scroll 后不可见),**懒挂 anchor/246 七测全绿不动**;③`/clear` → banner 移除、`on_resize` 不炸(跳过分支)。
- **RED 纪律**:S2① 顺序断言对当前代码 RED(banner 现在不在 conversation 内);实跑原文入卡。
- 四路串行 + `/tmp/.git` 跑前查跑后清。

## §4 O3 验收(Owner 真机;R-B/R-C 折入)
1. 默认启动:一切如 246(banner 开屏可见、欢迎语正下、滚动/键盘通道如旧);内容多了 banner 随流滚走;
   **且开屏输出零新增——不得出现任何鼠标提示行(R-C 负向)**;
2. `MOUSE=1` 启动:launcher 打印 `mouse mode: ON` 一行;TUI 开屏一行代价告知;**滚轮直接滚动会话区**、
   点击可用;Option+拖拽仍能选中复制;
   **R-B 加看点(滚轮 × anchor 交互)**:(a) 滚轮上滚 = 释放 anchor,新消息/thinking 不拽回,
   停得住;(b) 滚轮滚回底部(或 End)= 复位跟随,后续新消息继续跟;
3. `/clear` 后无 banner、无异常;
4. **resize 眼验(R 钩2 + §0-2′ 纠偏)**:改终端宽度——banner 保持 mount 时的渲染(现状同型,
   本就无 on_resize 重渲染);观感是否可接受由 Owner 裁定,若不可接受另起微片。

## §5 给 R 的钩子
1. S1 是否严格"默认零变化"(唯一分叉点在 argparse→run 透传)?
2. S2 banner 入流后 `on_resize` 语义(/clear 后跳过不重建)是否可接受,或应重建?
3. 开启 `--mouse` 时的 startup 提示是否够(P-A 告知代价),要不要在 `/help` 也提?
4. §3 S2① 的 RED 设计是否成立?

## §6.5 R 方向审计折入(Owner 2026-07-09 04:58 裁定原文,折入后即 APPROVED)

**四钩裁定**:
1. **钩1** → **唯一分叉点可 grep 对账 + 接线测试**:`mouse` 标识符的透传链
   (argparse → `run_tui(mouse=)` → `build_app(mouse=)` → `app.run(mouse=mouse)` + on_mount 提示分支)
   须 `grep -n mouse` 可整链对账;接线单测锁"默认 `run` 收到 `mouse=False`"。
2. **钩2** → **resize 列 O3 眼验**(叠加 §0-2′ 纠偏:现状本无 on_resize,入流后行为同型——
   banner 按 mount 时宽度渲染一次;真机改宽观感由 Owner O3 眼验裁定,不在本片加重渲染逻辑)。
3. **钩3** → **`--mouse` 并进 Scrollback 披露行(row 12),不加 `/help` 独立行**。
4. **钩4** → **RED 保留入台账**:S2① 顺序断言修前实跑 RED,输出原文入本卡 §7。

**折入项**:
- **R-A**:246 开屏 pin **演进**为"banner 首子件 + welcome 次位"顺序断言,**不许删**
  (`test_o3_2_short_content_not_anchored_until_overflow` 的开屏前提与 246 七测原样保留,
  247 新增顺序钉与其并存)。
- **R-B**:`--mouse` 会话 O3 **加看点**:滚轮与 anchor 释放/复位交互
  (滚轮上滚 = 释放 anchor 停得住;滚回底/End = 复位跟随)→ 已并入 §4 验收清单。
- **R-C**:代价告知**仅 `--mouse` on 打印**;默认会话**零新增输出**,入验收
  (§3 S1③ 负向钉 + §4 默认路第 1 条)。

## §7 实现记录(2026-07-09,executor 续作会话)

### RED 纪律(钩4):S2① 修前实跑原文
`test_s2_banner_first_child_welcome_second` 对修前代码(banner 在固定层 `#brandbar`)实跑:
```
FAIL: test_s2_banner_first_child_welcome_second (...Aipos247MouseBannerFlowTests...)
AssertionError: None != 'banner' : banner must be the FIRST conversation child (in-flow)
```
(首子件是 welcome Static,`id=None`——banner 不在流内。)实现后同测 GREEN。

### 改动清单(产品仓 4 文件 + 工具侧 1 文件)
- `tools/lybra_tui/__main__.py`:S1 —— argparse `--mouse`(store_true,default False,help 写明
  取舍)→ `run_tui(mouse=)` → `build_app(..., mouse=mouse).run(mouse=mouse)`(唯一分叉点,
  R 钩1;`grep -n mouse` 全链可对账)。
- `tools/lybra_tui/app.py`:S2 —— `compose()` 去 `#brandbar` 固定层(`Vertical` import 同删);
  `on_mount` 把 banner Static(保留 `id="banner"`,加 `turn` 类)作为 `#conversation` 首子件
  mount,welcome 紧随;宽度按容器内容宽(`self.size.width - 2`,padding 0 1);CSS 删
  `#brandbar` 规则;S1 —— `LybraTui(mouse=)` + `on_mount` 仅 `self._mouse` 时 `_system`
  一行代价告知(R-C)。`/clear` 走既有 `remove_children`——banner 随流清走,不重建(披露)。
- `tools/lybra_tui/tests/test_tui_app.py`:`Aipos247MouseBannerFlowTests`(5 async pilot 钉:
  S2①顺序/S2②滚出/S2③clear/S1②提示/S1③负向)+ `Aipos247MouseWiringTests`(2 接线钉:
  默认 `App.run` 收 `mouse=False`、`--mouse` 收 True——mock `build_app`,锁分叉点)。
- `tools/lybra_tui/tests/test_serve_tui_install.py`:argparse 双钉(core 路,无 textual:
  `main([...])` → `run_tui` kwargs `mouse` False/True)。
- `~/o3-launch.sh`(工具侧,仓外):S3 —— `MOUSE=1` → TUI 行追加 `--mouse`;两种模式均打印
  `mouse mode:` 一行;`bash -n` 过。
- `docs/v1_disclosure.md`:row 12 并入 `--mouse` 每会话反转注记 + banner 入流(R 钩3:
  不加 `/help` 独立行);refs 加 AIPOS-247。

### Gate(实测,串行,F-245-env-1 纪律)
- 247 新钉 9/9 GREEN;**246 七测原样不动全绿**(`o3_2` 开屏"不足一屏"前提未破:banner 10 行
  + turn 边距入流后开屏仍 ≤ 视口——R-A 的演进授权本轮无需动用,246 钉零修改);
- 四路:**BARE 763 OK(100 skip)/ SYSTEM 763 OK / TUI 176 OK / ACCEPTANCE PASS**
  (763 = 246 基线 754 + 9 新钉;skip 100 = 93 + 7 textual 守卫钉;TUI 176 = 167 + 9);
  跑前/跑后 `/tmp/.git` = 净。
- 红线复核:`tools.py`/`state.py` **零 diff**(git status 对账);默认路零变化 =
  接线钉 + argparse 钉 + S1③ 负向钉三层锁定;244/245/246 旧钉全绿(TUI 176 含全部)。

### 台账纠偏入册
- §0-2′:DRAFT 所称 "`_render_banner`/`on_resize` 重渲染" 不实(grep + `git log -S` 双零证据);
  S2 的 on_resize 子项落空为零改动,resize 观感列 §4-4 O3 眼验(R 钩2 同向)。

**状态:实现收口,候 Owner O3 真机走查(§4)→ 审计 → finalize。**
(首轮已 finalize:审计 AIPOS-247R PASS,product commit `6da9fa9`,DL-20260709-01。)

## §8 F-247-o3-1 ROUND(后补 fix,2026-07-09;归因如实:潜伏自 246 F-246-o3-2)

### 现象(Owner O3 真机)与机制(实证后如实纠偏:假设成立)
单条 copilot 长回复溢出一屏,视图钉在顶部(scroll_y 0,banner 可见),需手动 PgDn。
机制读码+实证:懒 anchor 的挂载检查**只挂在"追加"事件**(`_append`/`_markdown`:mount 前同步
+ 一次 `call_after_refresh`);Markdown 异步排版在两检查之后才长高,且无后续消息再触发 →
anchor 永不挂。246/247 O3 未逮到 = 当时都是多条短消息填屏(每条追加都重查)。
**归因:潜伏自 246 F-246-o3-2 懒挂载设计**(挂载点集合不完备),非 247 本片引入;247 banner
入流仅让症状更可见(banner 停在顶)。

### 修复(纯呈现层;anchor 已挂后释放/复位语义零改动;banner 入流不动)
`on_mount` 对 `#conversation` 挂 `self.watch(convo, "virtual_size", _on_conversation_growth,
init=False)`——**textual 原生 reactive watch(装机 8.2.7/8.2.8 皆有),消息驱动,零轮询/零定时**。
`_on_conversation_growth` = `call_after_refresh(_maybe_anchor)`:
- **为何必须 deferred**(实测入档):virtual_size reactive 在布局中途、容器自身尺寸未结算前
  触发——同步检查读到瞬时假溢出(开屏实测:virtual 17 行 vs 容器未及结算 → `max_scroll_y=17`
  → 误挂 → 短内容被负滚钉底 `scroll_y=-1`,正是 F-246-o3-2 症状复活;deferred 后布局两侧
  已结算,开屏不误挂)。
- `_maybe_anchor` 仍是唯一挂载闸(engage-once 幂等);**释放态不复挂**:用户上滚释放后
  `_anchor_engaged` 已 True → 早退,growth 触发不了任何动作(textual 释放 = 内部
  `_anchor_released` 旗标,`is_anchored`/`_anchored` 保持 True——装机源码核实,测试探针
  据此改用公共行为断言)。

### 测试(3 新钉;RED 纪律)
1. **确定性 RED**:单条消息挂载后 `update()` 长高(无后续追加)→ 断 anchor 挂上 + 视图到底。
   修前实跑原文:`AssertionError: False is not true : content growth must engage the anchor
   (RED pre-fix)`(80×40,追加时 max_scroll_y=0 前提钉住"检查时未溢出")。
2. **O3 同型场景**:单条真 Markdown 长回复(异步排版)→ 视图跟到底。修前实跑原文:
   `AssertionError: False is not true : the view must follow the single long reply`。
3. **释放语义红线钉(修前即绿,如实)**:已挂 → 用户上滚释放 → 长高 + 再追加,scroll_y 全程
   不动(公共行为断言;初版误用 `is_anchored` 探针,经装机源码核实释放不改 `_anchored`
   后改正——测试设计纠偏如实入册)。
- **flake 修(诚实入册)**:首轮四路 ACCEPTANCE FAIL(766 中 1 FAIL = 钉 1 自身)——
  refresh-deferred 检查需 2 个刷新周期,满载下单次 `pilot.pause()` 不够;改**有界等待**
  (≤10 pause 循环,确定性上界,非 sleep;修前无触发源,循环不改 RED 语义)。修后钉 3 连绿
  + 四路重跑全绿。**非产品回归**。

### ROUND Gate(实测,串行)
- `F247o31GrowthAnchorTests` 3/3;246 七测 + 247 九钉零修改全绿;
- 四路:**BARE 766 OK(103 skip)/ SYSTEM 766 OK / TUI 179 OK / ACCEPTANCE PASS**
  (766 = 763 + 3;skip 103 = 100 + 3);跑前/跑后 `/tmp/.git` 净;
- 红线:`tools.py`/`state.py` 零 diff(本轮仅 `app.py` + `test_tui_app.py`);零轮询新增
  (reactive watch 消息驱动);S1b 边界不变;banner 入流字节不动。

### ROUND 审点(给 cc glm 增量审计)
① watch 是否引入定时/轮询(应无:reactive 消息驱动 + call_after_refresh);② deferred 设计
是否必要(开屏瞬时假溢出实测证据);③ 释放态不复挂是否成立(_anchor_engaged 早退链);
④ 3 钉断言是否正内容非 proxy;⑤ flake 修是否如实(有界等待非 sleep,RED 语义不变);
⑥ 246 七测 + 247 九钉零修改。

**ROUND 状态:实现收口,未 commit——候 cc glm 增量审计 → Owner O3 复验(看点一条:copilot
长回复后视图跟到底)→ Owner 授权 finalize(后补 fix commit,治理档挂 247 名下 follow-up
round,归因"潜伏自 246 F-246-o3-2")。**

## §9 F-247-o3-1 ROUND 3(O3 REJECTED 后的诊断先行,2026-07-10)

### O3 判决与本轮纪律
R2 的三钉全绿,但 Owner 真机 `/draft` 出卡(markdown 卡 + blocking reasons + 尾行多块挂载)
后视图仍不跟底 → **O3 REJECTED**。钉未忠实复现真实链路(F-244-2 教训复发)。本轮按 Owner
纪律:①先诊断打点、结论入卡再改码;②修前 RED 用忠实场景;③随批 F-247-o3-2(默认会话隐藏
滚动条);④补 BARE skip 对账。

### 诊断(真 pty + 真 driver + 真 worker 线程 + 真 thinking 计时器,100×50 高屏)
忠实链路:DiagTui(仅加打点)跑真 `app.run(mouse=False)`,pty 打 `/draft`,mock copilot
sleep 2s 后返回 12 字段 frontmatter + 8 段 prose + 4 条 blocking 的卡。观测(打点日志):
- **growth 只 FIRE 一次量级**(markdown 块排版:vh 43→73,max=30,y=0)——一次性事件;
- 尾行 `_system` 的两个追加侧检查跑在 markdown 块长高**之前**(读到 max=0,救不了);
- **watcher 触发点的 max_scroll_y 不可靠(源码序实证)**:`widget.py:4110 _size_updated`
  赋值顺序 = `_size` → `virtual_size`(**reactive watcher 在此同步触发**)→ `_container_size`
  ——watcher 里 `max_scroll_y` 用的容器尺寸是**上一轮旧值**(开屏实测 FIRE 读到
  vh=43/ch=43/max=43 的假溢出;R2 正是为此把检查 defer 掉的);
- **R2 的 deferred 检查是多跳调度链**(源码链实证):`App.call_after_refresh` →
  InvokeLater 消息入 **App 泵**(message_pump.py:463-466)→ `_on_invoke_later` 转发
  `screen._invoke_later`(:521-526,**`app._running` False 时静默丢弃**)→ 追加进
  `screen._callbacks`(screen.py:1274-1282)→ 下一次 `_on_timer_update` 尾部/idle 才
  flush(:1261/:1186)。**一次性事件 + 多跳调度 = 正确性依赖平台时序**:任一跳落在错误
  窗口读到未结算 max(或被丢),挂载机会永久丢失(无再触发源)。
- **诚实边界**:WSL 上 6/6 次实跑该链路都"赢了"竞态(挂上跟底)——**本机未复现败序**;
  败序证据 = Owner macOS 真机 O3 两次(chat 长回复 + /draft 卡)+ 上述结构性证明
  (一次性事件不允许躺在时序依赖的调度链后面,这本身就是设计缺陷)。
- (打点方法坑,如实入册:textual 按 MRO 逐类派发 `on_mount`,DiagTui 覆写若调 super 会
  双挂 banner(DuplicateIds)——诊断脚本据此纠偏;R2 首个"pty 复现成功"判定作废——当时
  SNAP 未注册,最终态无证据。)

### 结论(修复方向,入卡后才动码)
watch 路径没兜住的原因 = **检查值与检查时机分离**:同步读 max_scroll_y 撞 stale container,
defer 读又输给调度。**同一 layout pass 内一致且可靠的一对值是 `size` 与 `virtual_size`**
(`_size_updated` 里 `_size` 先于 virtual_size 赋值)。且 `virtual_size` 有**容器地板**
(compositor:597 `total_region = child_region.reset_offset` 起步再 :627 union 内容 →
vh ≥ ch 恒成立,短内容 vh == ch)→ **`vh > ch` ⟺ 真溢出**,天然免疫 F-246-o3-2 短内容
误挂。修法:watcher 内**同步**判 `virtual_size.height > size.height > 0` 即挂——零 defer、
零调度依赖、零轮询;挂载后语义(释放/复位/S1b)不动;`_maybe_anchor`(追加侧)不动。

### 忠实 RED(修前实跑)
钉 = 逐字节重放 textual 真实生长 pass 入口(`screen.py:1373` 调用形状:
`convo._size_updated(size, 长高的 virtual, container)`,内部赋值序即真实序),然后
**不做任何泵处理**立即断言 anchor 已挂——钉住"挂载不得依赖任何后续调度跳"。
修前 RED 原文:
```
AssertionError: False is not true : growth must engage the anchor with ZERO scheduling
dependence (RED pre-R3-fix)
```
R2 三钉保留作回归;真实 /draft 形状链路(100×50 高屏,开屏不溢出、溢出只能来自卡片排版)
另加行为钉(该钉修前在 WSL 即绿——本机赢竞态,如实;其价值 = 回归钉 + O3 场景同构)。

### R3 实现
- `_on_conversation_growth`:去 defer,watcher 内**同步**判
  `convo.virtual_size.height > convo.size.height > 0` → `convo.anchor()` + engaged 置位。
  零轮询、零 defer、零调度依赖;`_anchor_engaged` 早退保证释放态永不复挂;
  `_maybe_anchor`(追加侧,246 已审)字节不动;S1b/释放/复位语义不动。
- **修后 pty 真 driver 复验**:带 spy 3/3 + **纯产品(SPIES=0)3/3** 全部
  `y=30=max anchored=True engaged=True`(跟到底)。
- **F-247-o3-2(Owner 裁定,随批)**:CSS 层——`#conversation.-hide-vscroll
  {{ scrollbar-size-vertical: 0; }}` + `on_mount` 按 `self._mouse` 置类:默认(无鼠标)
  会话隐藏不可拖拽的滚动条(误导性摆设),`--mouse` 会话保留;滚动行为(PgUp/PgDn/End/
  anchor)零改动。修前 RED 原文:`AssertionError: 2 != 0 : default session: vertical
  scrollbar hidden`(默认滚动条宽 2)。披露 row 12 已补一句。

### BARE skip 对账(上轮遗留,103 vs 102)
skip 构成枚举(BARE -v 实测):**textual 守卫 + 恰 1 条环境条件 skip** =
`tools/mcp_server/tests/test_mcp_tools.py:1653`"positive control requires PyYAML
(registry-verified executor side)"。executor 的 BARE venv(`--without-pip`,零第三方)
`import yaml` 失败 → 该测 skip(+1 = 103);审计者的 bare 环境可 import yaml → 该测运行
(= 102)。**两数各自诚实,差额 = 单一 PyYAML 正控测试的环境差**;executor 侧是更纯的
bare lane。本轮(+3 新钉)对应数 = 106(105 textual + 1 PyYAML)。

### R3 Gate(实测,串行)
- `F247o31GrowthAnchorTests` 6/6(R2 三钉回归 + R3 忠实钉 + /draft 高屏行为钉 +
  o3-2 滚动条钉);246 七测 + 247 九钉零修改全绿;
- 四路:**BARE 769 OK(106 skip)/ SYSTEM 769 OK / TUI 182 OK / ACCEPTANCE PASS**
  (769 = 766 + 3;skip 106 = 103 + 3;TUI 182 = 179 + 3);`/tmp/.git` 跑前查跑后净;
- 红线:`tools.py`/`state.py` 零 diff;零轮询(watcher 同步判,无 timer/defer 新增);
  banner 入流/mouse 旗标透传字节不动;释放语义钉全绿。

### R3 审点(给 cc glm 增量审计)
① 诊断链是否成立(watcher 触发点 `_container_size` 未提交:widget.py `_size_updated`
赋值序;deferred 多跳链:message_pump.py:463-466/:521-526 + screen.py:1274-1282/:1261);
② `vh > ch` 判据的安全前提(virtual_size 容器地板:compositor total_region 起步于自身
region 再 union 内容)是否核实;③ 同步 `convo.anchor()`(内含 scroll_end)在 watcher 上下文
执行是否有副作用(anchored 后 compositor 每帧钉底自校正);④ R3 忠实钉是否真忠实
(`_size_updated` 为 textual 真入口、真赋值序);⑤ WSL 未复现败序的诚实边界声明;
⑥ o3-2 滚动条:默认隐藏/`--mouse` 保留、滚动行为零改动;⑦ skip 对账 103/102 = PyYAML
正控单测环境差。

**R3 状态:实现收口,未 commit——候 cc glm 增量审计 → Owner O3 复验(还是那一条:
`/draft` 出卡后视图自动到底;顺验默认会话滚动条已隐藏)→ Owner 授权 finalize。**
