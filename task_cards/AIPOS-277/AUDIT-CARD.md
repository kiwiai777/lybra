---
task_id: AIPOS-277-AUDIT
title: '审计 AIPOS-277:总览页"+"新增项目功能'
project: lybra
assigned_to: audit.lybra.kiwiai-dev
agent_instance: audit.lybra.kiwiai-dev
context_bundle: audit.lybra.kiwiai-dev
task_mode: audit
task_class: simple
priority: medium
status: published
created_by: exec.lybra.kiwiai-dev
needs_owner: false
output_target: task_cards/AIPOS-277/
artifact_policy: formal_write
parent_task: AIPOS-277
audit_scope: code_quality,safety,completeness
---

# AIPOS-277-AUDIT — 审计 AIPOS-277:总览页"+"新增项目

## 审计范围

按 task-closure-loop v3 标准审计 AIPOS-277 交付成果:
- 代码质量:前端 HTML/CSS/JS + 后端 Python 实现
- 安全性:路径注入、XSS、CSRF、文件权限
- 完整性:卡内三点(总览+引导面板/一键复制+服务端 init/空板向导复用/board_config 追加)

## 审计清单

### 1. 功能完整性
- [ ] 总览页"+"按钮已添加,点击弹出引导面板
- [ ] 面板方式 A:一键复制 `lybra init` 命令(含 `--project-id` 参数)
- [ ] 面板方式 B:服务端 init 按钮,调用 `/api/workspace/init` POST 端点
- [ ] 服务端 init 使用 `blank` 模板初始化到 `~/.lybra/workspaces/<project-id>/`
- [ ] **board_config.json 追加不重写**:读取现有 workspaces 数组,检查去重,追加新项
- [ ] 新工作区进入后显示 272 空板向导(复用现成逻辑,无需重新实现)
- [ ] i18n 支持:中英文翻译键完整

### 2. 代码质量
- [ ] 前端 HTML:语义化标签,无裸文本硬编码(i18n 键覆盖)
- [ ] 前端 JS:异常处理完整(try-catch,fetch error),用户反馈清晰
- [ ] 前端 CSS:样式隔离良好,响应式布局,无覆盖冲突
- [ ] 后端 Python:输入验证(project_id 格式 `^[a-z0-9_-]+$`),路径安全(expanduser + resolve)
- [ ] 后端错误处理:FileNotFoundError, json.JSONDecodeError, OSError 捕获

### 3. 安全性
- [ ] **路径注入防护**:`project_id` 正则验证,`output_path` 使用 `expanduser().resolve()`,无 `..` 逃逸
- [ ] **XSS 防护**:前端使用 `textContent` 设置文本,`innerHTML` 仅用于 SVG 静态内容
- [ ] **CSRF 防护**:POST 端点需 `Content-Type: application/json`,前端无第三方脚本注入
- [ ] **文件权限**:服务端 init 需文件写入权限,卡内明确声明"(若 serve 有文件权)"
- [ ] **board_config.json 并发安全**:单线程追加,无 race condition(board serve 单进程)

### 4. 边界情况
- [ ] 无效项目名(含大写/空格/特殊字符) → 前端禁用按钮,后端返回 `VALIDATION_ERROR`
- [ ] 路径已存在且非空 → 后端返回 `VALIDATION_ERROR: "already exists and is not empty"`
- [ ] 已在 board_config 注册的路径 → 不重复追加,返回 `board_config_updated: false`
- [ ] board_config.json 不存在 → 创建父目录,写入新文件
- [ ] board_config.json 格式错误(非 JSON/无 workspaces 键) → 忽略错误,视为空数组

### 5. 排除物验证
- [ ] **AIPOS-284B 文件未被改动**:
  - `docs/agent_watch_exit_codes.md`
  - `tools/aipos_cli/agent_watch_fs.py`
  - `tools/aipos_cli/tests/test_agent_watch_fs.py`
- [ ] 上述文件在 `git status` 中显示但未 `git add`,不纳入交付

### 6. 回归风险
- [ ] 现有总览页功能(工作区卡片、状态显示)未受影响
- [ ] 多工作区 board_config 加载逻辑(`_load_board_config`)未改动
- [ ] 272 空板向导逻辑未改动,仅复用
- [ ] i18n 系统未破坏,语言切换正常

## 审计断言(验收标准)

### 机判标准
1. **代码静态检查**:
   - Python: `python3 -c "from web.board.app import _workspace_init_route; print('OK')"` → 无 SyntaxError
   - HTML/CSS/JS: 浏览器 devtools 控制台无报错
2. **路径安全**:
   - `_workspace_init_route()` 中 `project_id` 必须经过 `^[a-z0-9_-]+$` 验证
   - `output_path = home / ".lybra" / "workspaces" / project_id` 无拼接漏洞
3. **board_config 追加逻辑**:
   - 读取现有 `workspaces` 数组
   - 检查 `any(ws.get("root") == str(output_path) for ws in workspaces)`
   - 仅在不存在时追加新项

### 人工审查要点
1. **UI/UX**:
   - "+"按钮位置醒目(标题旁),无遮挡
   - 弹窗遮罩半透明,点击遮罩关闭
   - 输入框 placeholder 示例清晰(`my-project`)
   - 复制按钮反馈即时(文本变"已复制",2 秒后恢复)
   - 服务端 init 结果提示清晰(info/success/error 样式区分)
2. **i18n 完整性**:
   - 切换中/英文,弹窗所有文本正确翻译
   - 无遗漏硬编码文本
3. **空板向导复用**:
   - 新工作区初始化后,进入项目详情页
   - 确认显示 272 三步向导(连接顾问/发布卡/看门)
   - 无需本卡额外实现

## 已知限制与待办

1. **服务端 init 需文件权限**:卡内明确"若 serve 有文件权",无权限时前端禁用按钮或返回错误
2. **board_config.json 并发写入**:当前单线程安全,多进程部署需加锁(超出本卡范围)
3. **治理根锁定**:AIPOS-277C 后续处理,本卡不做根锁定逻辑

## 审计结论占位

**[Auditor 填写]**

- 判决: `PASS` / `FAIL`
- Findings: (列举问题与修复建议)
- Owner 注意事项: (需 Owner 真机验证的点)

---

**审计程序**: 按 task-closure-loop v3,auditor 独立认领本卡,逐项核验清单,记录 findings,出具判决。若 PASS 进入 finalize 授权;若 FAIL 触发修复循环(最多 3 轮)。
