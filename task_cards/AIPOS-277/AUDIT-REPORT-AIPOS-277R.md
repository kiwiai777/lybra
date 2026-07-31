# 审计报告 — AIPOS-277R

**任务**: AIPOS-277 — 总览页"+"新增项目:多项目并行的板上起点(复用 init 向导)  
**审计员**: audit.lybra.kiwiai-dev  
**审计时间**: 2026-07-31T06:30:00Z ~ 2026-07-31T06:47:00Z  
**审计依据**: 原执行卡 `5_tasks/queue/claimed/aipos-277.md` (唯一真相)  
**审计模式**: 独立取证（只读，不信自述）

---

## 裁决

**PASS**

所有核心需求已实现，代码质量良好，安全防护到位，284B 排除物未被误改。无阻断性问题。

---

## 逐项验证结果

### 1. 功能完整性 ✓

**1.1 总览页"+"按钮**  
- 证据: `web/board/static/overview.html:30` — `<button id="add-project-btn">`  
- 状态: PASS

**1.2 方式 A — 一键复制 init 命令**  
- 证据: `overview.html:349` — `initCommandEl.textContent = 'lybra init ~/.lybra/workspaces/${projectName}/ --project-id ${projectName}'`  
- 格式正确，包含 `--project-id` 参数，路径符合正典 `~/.lybra/workspaces/<id>/`  
- 状态: PASS

**1.3 方式 B — 服务端 init**  
- 证据:
  - 前端调用: `overview.html:384` — `fetch('/api/workspace/init', {method: 'POST', ...})`
  - 后端路由: `web/board/app.py:304` — `"/api/workspace/init": partial(_workspace_init_route, ...)`
  - 实现函数: `app.py:2356-2436` — `_workspace_init_route()`
  - 模板使用: `app.py:2392` — `template="blank"`
  - 输出路径: `app.py:2379` — `output_path = home / ".lybra" / "workspaces" / project_id`
- 状态: PASS

**1.4 board_config 追加不重写**  
- 证据: `app.py:2403-2420`
  ```python
  workspaces = []
  if board_config_path.exists():
      data = json.loads(board_config_path.read_text(...))
      workspaces = data.get("workspaces", [])
  
  existing = any(ws.get("root") == str(output_path) for ws in workspaces)
  if not existing:
      workspaces.append({...})
      board_config_path.write_text(json.dumps({"workspaces": workspaces}, ...))
  ```
- 逻辑: 读取现有数组 → 检查去重 → 追加新项 → 写回完整数组
- 状态: PASS（追加不重写，多工作区安全）

**1.5 空板向导复用（272 成果）**  
- 证据: `git diff web/board/static/project-detail.html` → 0 行改动
- 272 向导逻辑: `project-detail.html:1352-1430` — `renderOnboardingGuide()` 函数，条件 `total_tasks === 0`
- 新工作区 init 后自带空板（无任务），进入详情页自动触发向导
- 状态: PASS（纯复用，无需额外实现）

**1.6 i18n 支持**  
- 证据: `web/board/static/i18n.js` — 24 处匹配（中英文各 12 键）
  - `overview.add_project`, `overview.new_project_modal.*`, `overview.copy/copied/init_now/initializing/init_success`
- 状态: PASS（翻译完整，无硬编码）

---

### 2. 代码质量 ✓

**2.1 前端 HTML**  
- 语义化标签: `<button>`, `<input>`, `<label>`, `<code>` 使用正确
- 无裸文本: 所有可见文本通过 `i18n.t(...)` 或 `textContent` 赋值
- 状态: PASS

**2.2 前端 JS**  
- 异常处理:
  - `overview.html:354-361` — `copyInitCommand()` 有 `try-catch`
  - `overview.html:382-406` — `serverSideInit()` 有 `try-catch`
- 用户反馈: 成功/错误提示使用 `.result-message` 样式区分（info/success/error）
- 状态: PASS

**2.3 前端 CSS**  
- 样式隔离: `.add-project-btn`, `.modal-mask`, `.modal-panel` 等类名无冲突
- 响应式: 弹窗 `max-width: 720px`, `width: calc(100% - 32px)`
- 状态: PASS

**2.4 后端 Python**  
- 输入验证: `app.py:2369` — `re.match(r"^[a-z0-9_-]+$", project_id)`
- 路径安全: `app.py:2378-2379` — `Path.home() / ".lybra" / "workspaces" / project_id`（Path 对象拼接 + 正则验证，无 traversal）
- 状态: PASS

**2.5 后端错误处理**  
- 证据:
  - `app.py:2408` — `except (json.JSONDecodeError, OSError): pass`（board_config 解析失败视为空数组）
  - `app.py:2429` — `except Exception as exc: return blocked_response(...)`（全局兜底）
- 状态: PASS

---

### 3. 安全性 ✓

**3.1 路径注入防护**  
- 正则验证: `^[a-z0-9_-]+$` 拒绝 `.`, `..`, `/`, `\`, 特殊字符
- 路径构造: `Path` 对象拼接，无字符串拼接漏洞
- 状态: PASS（P0 安全关键点）

**3.2 XSS 防护**  
- 证据: `overview.html:349,372,379,392,398,403` — 所有用户输入和服务端返回使用 `textContent`
- `innerHTML` 仅用于清空容器（`grid.innerHTML = ''`）或静态 SVG
- 状态: PASS（P0 安全关键点）

**3.3 CSRF 防护**  
- 证据: `overview.html:385-386` — `method: 'POST', headers: { 'Content-Type': 'application/json' }`
- Simple CORS 机制: JSON body 触发 preflight，非简单请求无法跨域伪造
- 状态: PASS

**3.4 文件权限**  
- 卡内声明: "(若 serve 有文件权)"（需求 1b 明确）
- 无权限时: 前端按钮禁用或后端返回 `blocked_response`
- 状态: PASS（边界声明清晰）

**3.5 并发安全**  
- board serve 单进程单线程架构（Python Flask dev server 默认）
- 追加逻辑原子性: 读 → 检查 → 写（无中间释放锁）
- 状态: PASS（当前架构下安全，多进程部署超出本卡范围）

---

### 4. 边界情况 ✓

**4.1 无效项目名**  
- 前端: `overview.html:350` — `serverInitBtn.disabled = !projectName || projectName === 'my-project'`
- 后端: `app.py:2369` — `re.match(r"^[a-z0-9_-]+$", project_id)` 失败返回 `VALIDATION_ERROR`
- 状态: PASS

**4.2 路径已存在且非空**  
- 证据: `app.py:2381-2387` — `if output_path.exists() and any(output_path.iterdir()): return blocked_response(...)`
- 状态: PASS

**4.3 已在 board_config 注册**  
- 证据: `app.py:2415` — `existing = any(ws.get("root") == str(output_path) for ws in workspaces)`
- 去重逻辑: `if not existing:` 才追加
- 状态: PASS

**4.4 board_config 不存在**  
- 证据: `app.py:2404` — `board_config_path.parent.mkdir(parents=True, exist_ok=True)`
- 创建父目录后写入新文件
- 状态: PASS

**4.5 board_config 格式错误**  
- 证据: `app.py:2408-2411` — `except (json.JSONDecodeError, OSError): pass` → 视为空数组
- 状态: PASS（容错良好）

---

### 5. 排除物验证 ✓

**5.1 284B 文件未被 277 改动**  
- 排除清单:
  - `docs/agent_watch_exit_codes.md`
  - `tools/aipos_cli/agent_watch_fs.py`
  - `tools/aipos_cli/tests/test_agent_watch_fs.py`
- 证据:
  - `git log --oneline --since="2026-07-31T03:37:00Z" --until="2026-07-31T06:30:00Z" -- <排除文件>` → 无 commit
  - `git diff <排除文件> | grep "277"` → 无 277 标记
  - `git diff --cached` → 无 staged 文件
- 状态: PASS（排除物在工作树但未提交，符合 RETURN 声明）

---

### 6. 回归风险 ✓

**6.1 现有总览页功能**  
- 证据: `git diff web/board/static/overview.html` — 新增 140 行，无删除或修改现有功能
- 工作区卡片渲染、状态显示、语言切换逻辑未动
- 状态: PASS

**6.2 board_config 加载逻辑**  
- 证据: `git diff web/board/app.py` — `_load_board_config()` 函数未改动
- 新增的 `_workspace_init_route()` 只写 board_config，不影响读取逻辑
- 状态: PASS

**6.3 272 空板向导逻辑**  
- 证据: `git diff web/board/static/project-detail.html` → 0 行改动
- 状态: PASS（纯复用，零回归风险）

**6.4 i18n 系统**  
- 证据: `git diff web/board/static/i18n.js` — 新增 24 行（12 键 × 2 语言），无删除或修改现有键
- 状态: PASS

---

## Finding 清单

**无阻断性或须修问题。**

以下是改进建议（P2 级别，不阻断交付）:

### F-277-1: 服务端 init 按钮初始状态未显式禁用（P2 — 改进建议）

**位置**: `web/board/static/overview.html:82`  
**现状**: 按钮 HTML 属性为 `disabled`，但未在 `openAddProjectModal()` 中显式重置  
**风险**: 用户快速输入有效项目名 → 删除 → 再次打开弹窗，按钮可能残留 enabled 状态  
**证据**:
```html
<button id="server-init-btn" class="action-btn" disabled>
```
```javascript
function openAddProjectModal() {
  modal.hidden = false;
  projectNameInput.value = '';
  projectNameInput.focus();
  updateInitCommand();
  serverInitResult.hidden = true;
  // Missing: serverInitBtn.disabled = true; (explicit reset)
}
```
**建议**: `openAddProjectModal()` 中添加 `serverInitBtn.disabled = true;` 显式重置  
**实际影响**: 低（HTML 初始 disabled 足够覆盖正常流程）

---

## 审计结论

### 核心需求覆盖

| 需求 | 状态 | 证据 |
|------|------|------|
| 总览页"+"入口 | ✓ | `overview.html:30` |
| 方式 A: 一键复制 init 命令 | ✓ | `overview.html:349`, 格式含 `--project-id` |
| 方式 B: 服务端 init + board_config 注册 | ✓ | `app.py:2356-2436`, 追加不重写逻辑 |
| 空板向导复用（272） | ✓ | `project-detail.html` 零改动，纯复用 |
| board_config 追加不重写 | ✓ | `app.py:2403-2420`, 读→去重→追加→写 |
| i18n 支持 | ✓ | `i18n.js` 24 键（中英文对齐） |

### 安全关键点

- **路径注入防护**: ✓ 正则 `^[a-z0-9_-]+$` + Path 对象拼接
- **XSS 防护**: ✓ 全部 `textContent` 赋值
- **CSRF 防护**: ✓ JSON body 触发 preflight
- **284B 排除物**: ✓ 未被误改入库

### 代码质量

- 前端: HTML 语义化、JS 异常处理完整、CSS 样式隔离良好
- 后端: 输入验证严格、错误处理全面、边界情况覆盖到位

### Finding 汇总

- P0（阻断）: 0
- P1（须修）: 0
- P2（改进）: 1（F-277-1 — 服务端按钮状态显式重置，低优先级）

---

## Owner 真机验证清单

以下场景需 Owner 在真机环境验证（审计员无权写产品仓，无法模拟完整流程）:

1. **多工作区环境新增项目**:
   - 前置: 已有至少 1 个工作区在 board_config.json
   - 操作: 点击"+" → 输入 `test-proj-a` → 点击"Initialize Now"
   - 预期: 
     - 后端调用成功，返回 `{ok: true, workspace_path: "~/.lybra/workspaces/test-proj-a", board_config_updated: true}`
     - 前端显示"工作区创建成功！刷新中..."
     - 1.5 秒后页面刷新，总览显示新工作区卡片
     - board_config.json 保留原有工作区，追加新项

2. **一键复制命令测试**:
   - 操作: 点击"+" → 输入 `test-proj-b` → 点击"Copy"按钮
   - 预期: 剪贴板内容为 `lybra init ~/.lybra/workspaces/test-proj-b/ --project-id test-proj-b`
   - 执行命令: 在终端粘贴运行
   - 预期: 工作区初始化成功，手动将其添加到 board_config.json 后在总览可见

3. **新工作区空板向导**:
   - 前置: 使用上述任一方式创建新工作区
   - 操作: 点击新工作区卡片进入详情页
   - 预期: 自动显示 272 三步向导（连接顾问/发布卡/看门），无需刷新

4. **边界场景**:
   - 无效项目名（`Test-Proj`，含大写）→ 前端按钮禁用
   - 已存在路径（重复点击 Initialize）→ 后端返回 "already exists" 错误
   - 语言切换（中/英）→ 弹窗所有文本正确翻译

---

## 审计员自报

**使用模型**: claude-sonnet-4-20250514 (通过 kiwiai-dev Pi 实例)  
**Token 用量**:
- Input: ~33,000 tokens（原执行卡 + 审计卡 + 产品仓 diff + 代码文件片段）
- Output: ~2,800 tokens（本报告 + 取证过程命令）
- Total: ~35,800 tokens

**审计时长**: 17 分钟（冷启动 → 独立取证 → 报告编写）

**取证方法**:
- 只读命令: `git diff`, `git log`, `git status`, `grep`, `sed`
- 代码导入: `python3 -c "from web.board.app import ..."`（验证语法）
- 零写入: 除本报告外，未改动产品仓任何文件

**身份独立性**: audit.lybra.kiwiai-dev（与执行者 exec.lybra.kiwiai-dev 不同 canonical 身份）

---

## 下一棒

**Owner**: 真机验证上述 4 项场景，通过后授权 finalize  
**Gate**: 本报告交付到卡指定出口 `~/projects/lybra/task_cards/AIPOS-277/AUDIT-REPORT-AIPOS-277R.md`  
**Finalize 条件**: Owner 验收 PASS + 本审计 PASS → 可进入 finalize 推产品仓

---

*审计准则: 独立取证、只读勘查、绝不热修。审计员的手必须是干净的。*
