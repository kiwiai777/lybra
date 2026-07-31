---
task_id: AIPOS-279-AUDIT
title: 'AIPOS-279 审计卡：onboarding 增补验收'
project: lybra
assigned_to: audit.lybra.kiwiai-dev
agent_instance: audit.lybra.kiwiai-dev
context_bundle: audit.lybra.kiwiai-dev
task_mode: code
task_class: simple
priority: high
status: pending
created_by: exec.lybra.kiwiai-dev
reviewed_task_id: AIPOS-279
output_target: ~/projects/lybra/task_cards/AIPOS-279/
artifact_policy: audit_only
needs_owner: false
---

# AIPOS-279-AUDIT — 审计验收清单

## 被审计任务

- **任务卡**：AIPOS-279
- **标题**：onboarding 增补:顾问零安装接入(MCP 配置入提示词)+可选 CLI 自举安装+跨机 watch 文档
- **Executor**：exec.lybra.kiwiai-dev
- **交付状态**：completed
- **RETURN 路径**：~/projects/lybra/task_cards/AIPOS-279/RETURN.md

## 审计断言（卡内验收 S1-S4）

### S1：提示词含 MCP 配置片段与双式 watch

**检查项**：
- [ ] `web/board/static/project-detail.html` 中 `renderOnboardingGuide` 函数生成的 `advisorPrompt` 包含：
  - [ ] Claude Desktop/Cline 格式的 MCP 配置示例（`mcpServers.lybra`）
  - [ ] Pi/Codex 格式的 MCP 配置示例（`url` + `headers`）
  - [ ] Cursor/其他工具的参照说明
  - [ ] token 占位符 `<ADVISOR_TOKEN>`
  - [ ] gate URL 变量 `${gateURL}/mcp`
- [ ] 提示词包含 agent watch 双式：
  - [ ] 跨机模式：`--gate-url ${gateURL} --token <ADVISOR_TOKEN>`
  - [ ] 同机模式：`--workspace-root ${workspaceRoot}`
  - [ ] 两者说明同构（exit 0/2 契约）

**验证方法**：
```bash
grep -A 50 "const advisorPrompt" ~/projects/lybra/web/board/static/project-detail.html | grep -E "mcpServers|Pi / Codex|agent watch.*--gate-url|agent watch.*--workspace-root"
```

---

### S2：QUICKSTART 跨机节

**检查项**：
- [ ] `QUICKSTART.md` 存在独立章节"## 跨机接入：顾问在另一台机器"
- [ ] 章节包含方式A（零安装 MCP 直连）：
  - [ ] `lybra serve` 跨机绑定示例（`--mcp-host 0.0.0.0 --mcp-advertise`）
  - [ ] Claude Desktop/Pi/Cline 配置示例
  - [ ] token 占位符和 IP 占位符
- [ ] 章节包含方式B（CLI 自举安装）：
  - [ ] Git 克隆源说明（含占位符 `<GATE_MACHINE_GIT_URL>`）
  - [ ] npm/pip 安装步骤
  - [ ] `agent watch --gate-url` 跨机示例
  - [ ] `agent watch --workspace-root` 同机对比
- [ ] 安全注意事项段落（token 保护/网络隔离/防火墙）
- [ ] 同机vs跨机对比表（至少3列：安装要求/agent watch/适用场景）

**验证方法**：
```bash
grep -n "## 跨机接入" ~/projects/lybra/QUICKSTART.md
grep -A 20 "方式 A：零安装" ~/projects/lybra/QUICKSTART.md | grep -E "mcp-host|mcp-advertise|mcpServers"
grep -A 20 "方式 B：CLI 自举" ~/projects/lybra/QUICKSTART.md | grep -E "git clone|agent watch --gate-url"
grep "同机 vs 跨机对比" ~/projects/lybra/QUICKSTART.md
```

---

### S3：零回归

**检查项**：
- [ ] 未修改 board adapter 合约测试无关的代码
- [ ] 运行 `web/board/tests/test_board_adapter_contract.py` 全测试通过
- [ ] 运行 `web/board/tests/` 下至少5个测试文件无失败
- [ ] HTML/JS 语法无错误（浏览器控制台无报错，或 eslint/jshint 检查）

**验证方法**：
```bash
cd ~/projects/lybra
python3 -m pytest web/board/tests/test_board_adapter_contract.py -xvs
python3 -m pytest web/board/tests/test_aipos274f2_envelope_alignment.py -xvs
python3 -m pytest web/board/tests/test_aipos283_queue_close_contract.py -xvs
# 检查 HTML 语法（grep 验证模板字符串完整）
grep -c "const advisorPrompt = \`" ~/projects/lybra/web/board/static/project-detail.html
# 输出应为 1
```

---

### S4：owner_verify: required

**检查项**：
- [ ] 卡内 frontmatter 声明 `owner_verify: required`
- [ ] 审计结论需注明"等待 Owner 核验"
- [ ] 本审计卡自身不触发 finalize，仅出具审计意见

---

## 边界检查（红线遵守）

### 相邻卡 AIPOS-286 未越界

**检查项**：
- [ ] 未实现"跨机主机声明"逻辑（AIPOS-286 职责）
- [ ] 未实现"第0步连通检测"逻辑（AIPOS-286 职责）
- [ ] 仅在文档和提示词中说明跨机接入方式，未触碰连通检测代码

**验证方法**：
```bash
git diff HEAD --name-only | grep -E "(connection|health|check)" || echo "未触碰连通检测相关文件"
```

### 在途排除 AIPOS-278 文件

**检查项**：
- [ ] 未修改 `tools/aipos_cli/migrate_direction_log.py`
- [ ] 未修改 `tools/aipos_cli/project_map.py` 相关代码
- [ ] 未修改 `workspace_templates` 相关文件

**验证方法**：
```bash
git diff HEAD --name-only | grep -E "(migrate_direction_log|project_map|workspace_templates)" && echo "违反排除约束" || echo "PASS"
```

---

## 质量检查

### 文档可读性

**检查项**：
- [ ] QUICKSTART 跨机节的中文表述清晰（无错别字/语句通顺）
- [ ] 示例代码格式正确（缩进/bash 标记/占位符一致）
- [ ] 对比表对齐且信息完整（Markdown 表格语法正确）

### 提示词实用性

**检查项**：
- [ ] MCP 配置片段可直接粘贴（JSON 格式正确，无多余转义）
- [ ] token 占位符明确（`<ADVISOR_TOKEN>` 大写+尖括号，易识别）
- [ ] gate URL 动态生成（从 runtime-status API 取真实值，fallback 有明示）
- [ ] 职责说明与 charter 一致（红线/车道/发布确认等关键点）

---

## 审计结论模板

```markdown
# AIPOS-279 审计结论

**审计员**：audit.lybra.kiwiai-dev
**审计时间**：<时间戳>
**结论**：[PASS / CONDITIONAL_PASS / FAIL]

## 断言检查结果

- S1 提示词含 MCP 配置片段与双式 watch：[✅ PASS / ❌ FAIL + 原因]
- S2 QUICKSTART 跨机节：[✅ PASS / ❌ FAIL + 原因]
- S3 零回归：[✅ PASS / ❌ FAIL + 原因]
- S4 owner_verify required：[✅ 已声明]

## 边界检查结果

- 相邻卡 AIPOS-286 未越界：[✅ PASS / ❌ FAIL + 原因]
- 在途排除 AIPOS-278 文件：[✅ PASS / ❌ FAIL + 原因]

## 质量评价

- 文档可读性：[优秀 / 良好 / 需改进 + 具体问题]
- 提示词实用性：[优秀 / 良好 / 需改进 + 具体问题]

## 审计建议

[可选：改进建议、补充测试项、Owner 特别关注点]

## Owner 核验要点

本卡 `owner_verify: required`，建议 Owner 重点核验：
1. MCP 配置片段在主流 harness（Claude/Pi/Cline）中实测可用
2. 跨机接入文档的安全注意事项是否足够（VPN/防火墙/token 保护）
3. CLI 自举段的假设（gate 机暴露 HTTP 源）是否符合实际部署场景

---

**审计完成，等待 Owner 裁定。**
```

---

## 审计员指引

1. **逐项验证**：运行验证方法中的命令，勾选检查项
2. **截图取证**（可选）：MCP 配置片段/QUICKSTART 跨机节关键段落
3. **边界检查优先**：先确认未越界 AIPOS-286 和排除 AIPOS-278
4. **零回归测试**：至少运行 S3 列出的3个测试文件
5. **填写结论**：按模板填写，PASS 时简洁，FAIL 时详细说明原因和修复建议
6. **提交审计卡**：写入 `AUDIT-VERDICT.md`（本目录），verdict 字段取 PASS/CONDITIONAL_PASS/FAIL

---

**开始审计，祝顺利！**
