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
