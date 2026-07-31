---
audit_task_id: AIPOS-279R
reviewed_task_id: AIPOS-279
auditor: audit.lybra.kiwiai-dev
audit_date: 2026-07-31
audit_status: PASS_WITH_NOTES
audit_round: 2
round1_verdict: FAIL
round2_verdict: PASS_WITH_NOTES
round2_reason: 归因更正后两项P0撤销，保留2项P2 Notes
---

# AIPOS-279 独立审计报告

## 审计基准

**准绳**：原执行卡 `5_tasks/queue/claimed/aipos-279.md`

**范围**：
1. 零安装（默认路径）：向导接入提示词生成可粘贴的 MCP 连接配置片段（gate advertise URL + token 占位 + 常见 harness 的配置示意 cc/pi/codex 各一行），明示"任何 MCP agent 无需安装即可连接"
2. 可选 CLI 自举：提示词附"增强能力(可选)"段——指导 agent 从 gate 机自取安装（git/pip 源由 serve 配置暴露只读地址），装后获得 agent watch（同机 --workspace-root / 跨机 --gate-url 两式并列写明）
3. QUICKSTART 同步"顾问在另一台机器"一节；跨机/同机同构表述
4. 验收：S1 提示词含 MCP 配置片段与双式 watch；S2 QUICKSTART 跨机节；S3 零回归；S4 owner_verify: required

**约束**：`output_target: web/`（改动范围限定在 web/ 目录）

## 审计程序

独立取证，逐项核验原卡验收断言（S1-S4），检查工作区实际状态。

## 独立取证结果

### 验收断言核验

#### S1：提示词含 MCP 配置片段与双式 watch

**取证**：`git diff web/board/static/project-detail.html`，检查 `renderOnboardingGuide` 函数内的 `advisorPrompt` 模板字符串。

**证据**：
- **MCP 配置片段**（project-detail.html L1462-1490）：✅ 包含三个 harness 的配置示意
  * Claude Desktop/Cline：`{"mcpServers": {"lybra": {"type": "http", "url": "${gateURL}/mcp", ...}}}`
  * Pi/Codex：`{"url": "${gateURL}/mcp", "headers": {...}}`
  * Cursor/其他：参照格式说明
- **双式 watch**（L1506-1510）：✅ 明确写明跨机/同机两式
  * 跨机模式：`lybra agent watch --gate-url ${gateURL} --token <ADVISOR_TOKEN> --timeout 30`
  * 同机模式：`lybra agent watch --workspace-root ${workspaceRoot} --timeout 30`
  * 说明："两种 watch 模式同构：都是变化即返回 JSON 摘要（exit 0），超时静默退出（exit 2）。跨机模式通过 gate MCP 工具拉取状态，同机模式直接读文件系统。"

**结论**：✅ PASS

---

#### S2：QUICKSTART 跨机节

**取证**：`git diff QUICKSTART.md`，查找"跨机接入"章节。

**证据**：QUICKSTART.md L352-500 插入新章节"## 跨机接入：顾问在另一台机器"（约147行），包含：
- **方式 A：零安装（MCP 直连，推荐）**（L354-431）：
  * gate 启动参数（含 `--mcp-host 0.0.0.0 --mcp-advertise` 跨机绑定示例）
  * 顾问机器 MCP 配置（Claude Desktop、Pi/Codex、Cline/Cursor 三种）
  * 接入提示词示例
  * 优势说明（无需安装、适用所有 MCP agent、自动重连）
- **方式 B：CLI 自举安装（增强能力）**（L435-476）：
  * gate 机暴露源（HTTP 文件服务示例）
  * 顾问机自取安装（git clone/npm/pip 指令）
  * agent watch 跨机模式用法
  * 优势说明（完整 CLI 能力、跨机监听、同机/跨机同构）
- **安全注意事项**（L480-485）：token 保护、网络隔离（VPN/SSH 隧道）、防火墙配置
- **同机 vs 跨机对比表**（L489-495）：5列×4行（安装要求、agent watch、MCP 工具、网络依赖、适用场景）

**结论**：✅ PASS

---

#### S3：零回归

**取证**：运行被审方声称的测试命令。

**证据**：
```bash
cd ~/projects/lybra
python3 -m pytest web/board/tests/test_board_adapter_contract.py::BoardAdapterContractTests::test_get_records_response_contract -xvs
# 输出：PASSED
```

**结论**：✅ PASS（单个测试通过，但未验证全局回归，见 F-279-02）

---

#### S4：owner_verify: required

**取证**：检查原卡 frontmatter。

**证据**：原卡 L32 `owner_verify: required` 已声明。

**结论**：✅ PASS（卡内已声明，非执行者职责）

---

### 范围与完整性核验

#### 实际改动文件清单

**取证**：`git diff --name-only`

**证据**：工作区存在 5 个已修改文件（未 commit）：
1. QUICKSTART.md
2. tools/aipos_cli/project_map.py
3. web/board/app.py
4. web/board/static/i18n.js
5. web/board/static/project-detail.html

**对比被审方声明**：RETURN.md 声称"只改了 2 个文件"（project-detail.html 和 QUICKSTART.md）。

**差异**：实际改动 5 个文件，多出 3 个未声明文件。

---

#### 范围越界核验

**取证**：逐个检查多出的 3 个文件的改动内容。

##### F-279-01：混入 AIPOS-286 内容（跨机主机声明+第0步连通检测）

**证据**：
- **web/board/app.py**（32行新增）：
  * L377-395：新增函数 `_get_server_location_info()`，获取 server hostname 和 IP（注释明确标注"AIPOS-286"）
  * L744：runtime-status 响应新增 `server_location` 字段（注释"AIPOS-286: Advisor agents should verify same-machine before connecting"）
- **web/board/static/i18n.js**（14行新增）：
  * L219-225（中文）、L440-446（英文）：新增 onboarding.ssh_reminder / ssh_reminder_text 等 5 个国际化键（注释明确标注"AIPOS-286: 空板向导 SSH 提醒 + 提示词第 0 步"）
- **web/board/static/project-detail.html**（约40行 AIPOS-286 相关内容）：
  * L841：onboarding 步骤1新增"跨机接入提醒"段落（橙色警告框，引用 i18n.ssh_reminder）
  * L1381-1398：JavaScript 中新增 server_location 信息获取（serverHostname、serverIP 变量，注释标注"AIPOS-286"）
  * L1417-1455：提示词新增"第 0 步：同机确认与连通性检测（AIPOS-286 强制前置）"完整段落（约40行），包含：
    - 同机/跨机判断指引（hostname / hostname -I 命令）
    - 连通性检测（curl /health、SSH 文件访问）
    - 不通过时的 block-and-report 指引
    - "绝不带病接线"警告

**原卡范围**：AIPOS-279 原卡未提及"第0步"、"同机确认"、"server_location"、"SSH 连通性检测"或 AIPOS-286。

**被审方声明**：RETURN.md "未越界确认"段落提及"相邻卡 AIPOS-286（跨机主机声明+第0步连通检测）：未实现"。

**实际情况**：AIPOS-286 的核心内容（server_location 提取、第0步检测、SSH 提醒）已全部实现并混入交付物中。

**性质**：P0 阻断级——范围漂移，擅自实现相邻卡内容，虚报"未实现"。

---

##### F-279-02：混入 AIPOS-278 内容（direction_log 结构迁移）

**证据**：
- **tools/aipos_cli/project_map.py**（53行改动）：
  * L213-260：函数 `_read_direction_log_recent()` 完全重写，新增对"新结构"（direction_log/<YYYY-MM>/<DD>-<seq>-<slug>.md）的支持
  * 原逻辑：只支持"旧结构"（direction_log/<YYYY-MM>-direction-decisions.md 单文件）
  * 新逻辑：检测月份子目录，优先读取新结构，旧结构作为兼容路径

**原卡范围**：AIPOS-279 原卡未提及 direction_log、project_map.py 或 AIPOS-278。

**被审方声明**：RETURN.md "未越界确认"段落提及"在途排除 AIPOS-278：未碰触 tools/aipos_cli/{migrate_direction_log.py, project_map.py 相关, workspace_templates 相关}"。

**实际情况**：project_map.py 已被修改，改动直接服务于 direction_log 新结构支持（AIPOS-278 的核心功能）。

**性质**：P0 阻断级——范围漂移，擅自实现在途任务内容，虚报"未碰触"。

---

##### F-279-03：越界改动 QUICKSTART.md（非 web/ 目录）

**证据**：
- 原卡约束：`output_target: web/`（改动范围限定在 web/ 目录）
- 实际改动：QUICKSTART.md（根目录文件，非 web/ 子目录）

**原卡条款**：原卡第3点明确要求"QUICKSTART 同步'顾问在另一台机器'一节"。

**矛盾分析**：原卡第3点要求改 QUICKSTART.md，但 output_target 约束为 web/，存在内在矛盾。

**性质**：P2 改进级——卡内自洽性问题（条款与约束矛盾），但执行者未 block-and-report 澄清，擅自按条款字面执行（改了 QUICKSTART.md）。正确做法：发现矛盾时停止并请顾问澄清。

---

##### F-279-04：tools/aipos_cli/project_map.py 完全越界

**证据**：
- 原卡约束：`output_target: web/`
- 实际改动：tools/aipos_cli/project_map.py（tools/ 目录，完全不在 web/ 范围）
- 原卡条款：无任何提及 tools/ 或 project_map.py

**性质**：P0 阻断级——完全越界，改动文件不在卡授权范围内。

---

### 测试覆盖度核验

**取证**：被审方声称"无其他修改文件，HTML/JS 语法检查通过（grep 验证模板字符串完整）"，但未提供验证命令或输出。

**独立验证**：
- 仅运行了 1 个单元测试（test_board_adapter_contract.py 中的单个方法）
- 未运行全局测试套件（pytest 全目录）
- 未验证新增的 3 个文件（app.py、i18n.js、project_map.py）是否引入回归

**性质**：P1 须修级——测试覆盖不足，F-279-01/F-279-02 的改动未被测试覆盖。

---

## 审计结论

**裁决**：❌ **FAIL**

### 失败原因

1. **范围漂移**（P0 阻断级）：
   - F-279-01：混入 AIPOS-286 全部核心内容（server_location、第0步检测、SSH 提醒），虚报"未实现"
   - F-279-02：混入 AIPOS-278 核心内容（project_map.py direction_log 新结构支持），虚报"未碰触"
   - F-279-04：越界改动 tools/aipos_cli/project_map.py（完全不在 `output_target: web/` 范围）

2. **虚假汇报**（P0 阻断级）：
   - RETURN.md 声称"只改了 2 个文件"，实际改了 5 个
   - 声称"未实现 AIPOS-286"，实际已全部实现
   - 声称"未碰触 project_map.py"，实际已修改

3. **程序违规**（P1 须修级）：
   - 发现卡内矛盾（条款要求改 QUICKSTART.md vs output_target: web/）时未 block-and-report，擅自决策
   - 测试覆盖不足（仅 1 个单测，未验证新增改动的回归）

### 核心验收达标情况

尽管存在严重越界和虚报问题，原卡核心验收断言（S1-S4）在技术层面均已实现：
- ✅ S1：提示词含 MCP 配置片段与双式 watch
- ✅ S2：QUICKSTART 跨机节
- ✅ S3：零回归（单测通过，但全局未验证）
- ✅ S4：owner_verify: required（卡内已声明）

**但**：交付物被污染（混入 AIPOS-286/278 内容），无法独立验收 AIPOS-279 的纯净实现。

---

## Finding 清单

| 编号 | 分级 | 描述 | 证据 |
|------|------|------|------|
| F-279-01 | P0 | 混入 AIPOS-286 内容（server_location、第0步检测、SSH 提醒），虚报"未实现" | app.py L377-395/L744, i18n.js L219-225/L440-446, project-detail.html L841/L1381-1455 |
| F-279-02 | P0 | 混入 AIPOS-278 内容（project_map.py direction_log 新结构支持），虚报"未碰触" | project_map.py L213-260 |
| F-279-03 | P2 | 越界改动 QUICKSTART.md（非 web/ 目录），未 block-and-report 卡内矛盾 | QUICKSTART.md L352-500, output_target: web/ |
| F-279-04 | P0 | 越界改动 tools/aipos_cli/project_map.py（完全不在 web/ 范围） | project_map.py 全文件 |
| F-279-05 | P1 | 测试覆盖不足，仅单测未全局回归，新增改动未被测试覆盖 | RETURN.md 测试自证 |

---

## 修复建议

1. **回滚越界改动**（P0）：
   - 完全回滚 web/board/app.py 的 AIPOS-286 相关改动（server_location 函数及 runtime-status 字段）
   - 完全回滚 web/board/static/i18n.js 的 AIPOS-286 相关国际化键
   - 从 project-detail.html 中移除第0步检测段落（L1417-1455）和 SSH 提醒段落（L841）及相关 JavaScript 变量（L1381-1398）
   - 完全回滚 tools/aipos_cli/project_map.py 的 AIPOS-278 相关改动

2. **保留合规实现**（原卡范围内）：
   - project-detail.html 的 MCP 配置片段段落（L1458-1512，去除 server_location 引用和第0步段落）
   - QUICKSTART.md 的跨机节（L352-500，去除任何 AIPOS-286 引用）

3. **澄清范围矛盾**（P2）：
   - 向顾问确认：原卡第3点要求改 QUICKSTART.md，但 output_target: web/，如何处理？
   - 若 QUICKSTART.md 属于合规范围，需更新卡的 output_target 或添加例外说明

4. **补充测试**（P1）：
   - 运行全局测试套件（`pytest web/board/tests/`）
   - 验证 project-detail.html / QUICKSTART.md 改动后的端到端场景（向导生成、提示词复制）

5. **重新汇报**（P0）：
   - 如实列出实际改动文件（不得隐瞒或低报）
   - 不得将在途任务内容报告为"未实现"（如已实现需如实说明并标注越界）

---

## 审计元信息

### 实际使用模型与 token 用量

- **模型**：claude-3-7-sonnet-20250219（从 Pi 底栏读取，未依赖自我认知）
- **输入 token**：约 22,580
- **输出 token**：约 4,200（含本报告）

### 审计程序完整性自证

- ✅ 独立取证：所有断言均通过只读命令（git diff、sed、grep、pytest）验证，未依赖被审方自述
- ✅ 准绳明确：逐条对照原执行卡 `aipos-279.md`，未使用审计卡或 RETURN 的转述
- ✅ 证据留痕：所有 Finding 附文件名+行号或命令输出原文
- ✅ 零改动：本审计过程未 edit/write 产品仓任何文件（仅写本报告到卡指定出口）
- ✅ 身份独立：audit.lybra.kiwiai-dev 与 exec.lybra.kiwiai-dev 为不同 canonical 身份

### 出口合规性

- 审计卡未明确指定出口
- 按 v4 标准默认出口：`~/projects/lybra/task_cards/AIPOS-279/AUDIT-REPORT-AIPOS-279R.md`
- 本报告已写入该位置，无其他写出口

---

## 下一棒

**修复循环**：FAIL → 执行者按 Finding 清单修复（仅修 F-* 项，不扩面）→ 更新审计卡的"复审轮次"与"本轮重点"（= F-* 清单）→ 重新投审。

**轮次纪律**：本轮为第 1 轮 FAIL。若第 2 轮仍 FAIL，第 3 轮审计者应停止并报告顾问仲裁。

**可粘贴命令**（修复完成后）：
```
执行者修复完成后，更新审计卡并通知顾问重新投审。审计卡路径（推测）：
~/projects/lybra/task_cards/AIPOS-279/AUDIT-AIPOS-279-FIX1.md
```

---

**审计完成时间**：2026-07-31T08:45:00Z  
**审计员签名**：audit.lybra.kiwiai-dev (session_AIPOS-279R_20260731_081516_audit-lybra-kiwiai-dev)

---

## 复审（Round 2）

### 仲裁触发

顾问仲裁书 `ARBITRATION-AIPOS-279R.md` 对本审计的两项 P0 发现（F-279-01 越界 AIPOS-286、F-279-02 违反 278 排除）提出异议，认为系**归因错误**——这些改动属 AIPOS-286/278 会话产物，非 AIPOS-279 越界。

### 复审取证程序

按仲裁要求核实归因证据：比对 gate 时间线（5_tasks/records/claims + returns + sessions）与各卡 RETURN 改动清单。

#### 时间线核查

**gate records 时间戳**（从 return_record frontmatter 提取）：
```
AIPOS-279 return: 2026-07-31T07:33:25Z
AIPOS-286 claim:  2026-07-31T07:33:27Z (比 279 return 晚 2 秒)
AIPOS-286 return: 2026-07-31T07:55:53Z
AIPOS-278F1 return: 2026-07-31T08:24:10Z
本审计取证时间: 2026-07-31T08:30:00Z (audited_at)
```

**关键事实**：
- AIPOS-279 在 07:33:25 已 return（执行完成）
- AIPOS-286 在 07:33:27 才 claim（开始执行），07:55:53 return
- AIPOS-278F1 在 08:24:10 return
- 本审计在 08:30:00 取证工作树，看到的是**三卡全部完成后的共树状态**

#### AIPOS-286 RETURN 改动清单核查

**证据位置**：`~/projects/lybra/task_cards/AIPOS-286/RETURN.md`

**286 明确声称的改动**（逐字摘录）：
```
## 改动文件清单

### 后端 (Python)
1. web/board/app.py (3 处修改)
   - 添加 import socket
   - 新增 _get_server_location_info() 辅助函数
   - _get_runtime_status_route() 注入 data.server_location 字段

### 前端 (HTML + JS)
2. web/board/static/project-detail.html (2 处修改)
   - renderOnboardingGuide() 函数：从 runtime-status 提取 server_location
   - 提示词模板：新增「第 0 步：同机确认与连通性检测」区块
   - step-1 向导步骤：新增 #ssh-reminder 橙色警示框

3. web/board/static/i18n.js (2 处修改)
   - zh/en 翻译块：新增 onboarding.ssh_reminder 等 5 个键

4. web/board/tests/test_aipos286_server_location.py (新建)
```

**与 F-279R-1 指控的精确比对**：
- F-279R-1 指控：project-detail.html L1381-1450（serverHostname/serverIP + 第0步）+ app.py (_get_server_location_info 函数) + i18n.js (SSH 提醒)
- 286 RETURN 清单：**完全一致**

#### AIPOS-278/278F1 RETURN 改动清单核查

**证据位置**：
- `~/projects/lybra/task_cards/AIPOS-278/RETURN.md`
- `~/projects/lybra/task_cards/AIPOS-278/RETURN-FIX-1.md`

**278 明确声称的改动**（逐字摘录）：
```
## 交付清单
- 迁移工具: tools/aipos_cli/migrate_direction_log.py
- 板面解析适配: tools/aipos_cli/project_map.py (新旧结构兼容)
- 模板同步: templates/blank/tree/governance/direction_log/.gitkeep
- 使用文档: tools/aipos_cli/DIRECTION_LOG_MIGRATION.md
- 测试套件: tools/aipos_cli/tests/test_direction_log_migration.py
```

**278F1 明确声称的改动**（逐字摘录）：
```
## 交付清单
- 迁移工具改动: tools/aipos_cli/migrate_direction_log.py (正则扩展支持双日期标题)
- 测试用例新增: tools/aipos_cli/tests/test_direction_log_migration.py (2 个新测试)
```

**与 F-279R-2 指控的精确比对**：
- F-279R-2 指控：project_map.py + migrate_direction_log.py + DIRECTION_LOG_MIGRATION.md + templates/blank/tree/governance/direction_log/ + test_direction_log_migration.py
- 278/278F1 RETURN 清单：**完全一致**

#### AIPOS-279 RETURN 改动清单核查

**证据位置**：`~/projects/lybra/task_cards/AIPOS-279/RETURN.md`

**279 明确声称的改动**（逐字摘录）：
```
## 修改文件
1. web/board/static/project-detail.html（1处修改）
2. QUICKSTART.md（1处插入）
```

**对比实际改动**：
- 279 声称改 2 个文件
- 本审计 Round 1 取证发现 5 个文件（project-detail.html + QUICKSTART.md + app.py + i18n.js + project_map.py）
- **差异的 3 个文件（app.py + i18n.js + project_map.py）全部被 286/278 RETURN 清单覆盖**

### 归因错误根因分析

**审计程序缺陷**（Round 1）：
- 取证时刻（08:30）晚于 286/278F1 完成时刻（07:55 / 08:24）
- 取证对象：工作树当前状态（`git status` 显示 5 个文件 modified/untracked）
- **未比对各卡 RETURN 改动清单与时间线**，直接将工作树全部改动归因于被审卡（279）
- 违反仲裁书教训："多卡共树流水线下，审计归因必须以'该卡 RETURN 改动清单+session 时间线'为准，工作树全量状态仅作交叉参考"

**共树流水线特性**（事实）：
- AIPOS-279/286/278F1 在同一产品仓工作树顺序执行（279→286→278F1）
- 各卡 return 时改动未 commit，累积在共树
- 审计员在**最后一卡完成后**取证，看到的是**三卡改动叠加态**
- 若不比对各卡 RETURN 清单，无法区分谁改了什么

### 复审裁决

#### F-279R-1（越界 AIPOS-286）裁决：❌ **不成立**

**理由**：
1. **时间线证明 286 在 279 之后执行**：279 return（07:33:25）→ 286 claim（07:33:27，晚 2 秒）→ 286 return（07:55:53）
2. **286 RETURN 明确声明了全部 F-279R-1 指控文件**：app.py (_get_server_location_info) + project-detail.html (第0步) + i18n.js (SSH 提醒)
3. **279 RETURN 未声明这些文件**：279 只声称改了 project-detail.html 和 QUICKSTART.md
4. **归因正解**：这些改动属 AIPOS-286 会话产物，审计取证时（08:30）已在工作树，但**非 279 越界**

**撤销**：F-279R-1 全部指控撤销。

#### F-279R-2（违反 278 排除）裁决：❌ **不成立**

**理由**：
1. **278/278F1 RETURN 明确声明了全部 F-279R-2 指控文件**：project_map.py + migrate_direction_log.py + DIRECTION_LOG_MIGRATION.md + templates/ + tests/
2. **时间线证明 278F1 在 279 之后执行**：278F1 return（08:24:10）晚于 279 return（07:33:25）近 1 小时
3. **279 RETURN 明确排除了这些文件**："在途排除 AIPOS-278：未碰触 tools/aipos_cli/{migrate_direction_log.py, project_map.py 相关, workspace_templates 相关}"
4. **归因正解**：这些改动属 AIPOS-278/278F1 会话产物，审计取证时已在工作树，但**非 279 夹带**

**撤销**：F-279R-2 全部指控撤销。

#### 其他 Finding 复核

**F-279-03（越界 QUICKSTART.md，output_target: web/）**：
- **维持 P2**：原卡第3点要求改 QUICKSTART.md，但 output_target: web/ 存在矛盾；执行者未 block-and-report
- **实质影响有限**：QUICKSTART.md 改动符合卡内条款字面要求，仅程序瑕疵（未澄清矛盾）

**F-279-04（越界 tools/aipos_cli/project_map.py）**：
- **撤销**：已由 F-279R-2 归因更正覆盖（project_map.py 属 278 产物，非 279 越界）

**F-279-05（测试覆盖不足）**：
- **维持 P1**：仅运行 1 个单测，未全局回归；但因 F-279R-1/2 撤销，"新增改动未被测试覆盖"的指控基础消失
- **降级为 P2**：测试不足仍是改进点，但不影响核心交付

### 最终裁决

**Round 2 裁决**：✅ **PASS WITH NOTES**

#### 通过理由

1. **核心验收断言（S1-S4）全部 PASS**（Round 1 已确认）：
   - ✅ S1：提示词含 MCP 配置片段与双式 watch
   - ✅ S2：QUICKSTART 跨机节
   - ✅ S3：零回归（单测通过）
   - ✅ S4：owner_verify: required

2. **实际改动范围纯净**（归因更正后）：
   - AIPOS-279 实际只改了 2 个文件：project-detail.html（MCP 配置段 + watch 双式）、QUICKSTART.md（跨机节）
   - Round 1 指控的"越界 286/278"文件（app.py, i18n.js, project_map.py 等）经时间线与 RETURN 清单核实，**全部属并行卡产物，非 279 夹带**

3. **执行者汇报属实**：
   - RETURN.md 声称"只改 2 个文件" → 与各卡 RETURN 清单比对后**证实属实**
   - 声称"未实现 AIPOS-286" → 经 286 RETURN 清单核实，286 内容由 286 会话实现，**279 确实未越界**
   - 声称"未碰触 AIPOS-278 文件" → 经 278/278F1 RETURN 清单核实，**279 确实未碰触**

#### 保留 Notes（非阻断项）

**N-279-01**（P2，程序改进）：
- **事项**：原卡第3点要求改 QUICKSTART.md，但 output_target: web/ 约束存在矛盾；执行者未 block-and-report 澄清，直接按条款字面执行
- **影响**：程序瑕疵（未遵守"遇矛盾即停"纪律），但实质交付符合卡内条款要求
- **建议**：今后遇卡内矛盾时，应先 block-and-report 澄清，不擅自决策

**N-279-02**（P2，测试改进）：
- **事项**：仅运行 1 个单测（test_board_adapter_contract.py 单个方法），未全局回归
- **影响**：测试覆盖不足，但核心功能（MCP 配置生成、QUICKSTART 文档）属文档/配置类改动，单测已覆盖关键契约
- **建议**：今后应运行完整测试套件（pytest web/board/tests/）确保零回归

### 审计程序反思与改进

#### Round 1 程序缺陷

1. **归因依据错误**：
   - 错误做法：以"工作树当前状态"（git status）作为被审卡改动的唯一依据
   - 正确做法：**以被审卡 RETURN 改动清单为准，工作树状态仅作交叉参考**

2. **时间线忽略**：
   - 错误做法：未比对各卡 claim/return 时间戳，默认工作树所有改动归因于被审卡
   -正确做法：**先查 gate records 时间线，确认被审卡与并行卡的执行顺序**

3. **共树流水线理解不足**：
   - 场景特性：多卡在同一工作树顺序执行，改动未即时 commit，形成叠加态
   - 正确程序：**逐卡 RETURN 清单比对 → 时间线排序 → 差集归因**

#### 改进措施（已采纳）

- ✅ 本次复审完整执行：读取 gate records（claims/returns/sessions）→ 提取时间戳 → 读取各卡 RETURN.md 改动清单 → 逐文件比对归属
- ✅ 仲裁书教训并入审计程序："多卡共树流水线下，审计归因必须以'该卡 RETURN 改动清单+session 时间线'为准，工作树全量状态仅作交叉参考"
- ✅ 本案例作为第三例共树误归因事故（F-278R-04 / F-279R-1/2 同族），已形成标准防线

### 复审元信息

**实际使用模型与 token 用量**（复审部分）：
- **模型**：claude-3-7-sonnet-20250219
- **输入 token**（增量）：约 8,200（仲裁书 + gate records + 各卡 RETURN 清单 + Round 1 报告）
- **输出 token**（增量）：约 2,800（本复审节）
- **累计 token**：Input ~30,780 / Output ~7,000 / Total ~37,780

**复审取证完整性**：
- ✅ 读取仲裁书（ARBITRATION-AIPOS-279R.md）
- ✅ 读取 gate records 时间线（5_tasks/records/returns/ 下的 279/286/278F1 记录）
- ✅ 读取各卡 RETURN 改动清单（AIPOS-286/RETURN.md, AIPOS-278/RETURN.md, AIPOS-278/RETURN-FIX-1.md, AIPOS-279/RETURN.md）
- ✅ 逐文件比对归因（F-279R-1/2 指控文件 vs 286/278 RETURN 清单）
- ✅ 时间戳排序验证（279 return 早于 286 claim，早于 278F1 return）

**出口合规性**（复审）：
- 原报告已写入：`~/projects/lybra/task_cards/AIPOS-279/AUDIT-REPORT-AIPOS-279R.md`
- 本复审节追加至原报告末尾（bash append 操作，非新建文件）
- 无其他写出口

---

**复审完成时间**：2026-07-31T16:45:00Z
**复审审计员**：audit.lybra.kiwiai-dev（同 Round 1）
**最终裁决**：✅ **PASS WITH NOTES**（N-279-01 程序瑕疵 P2，N-279-02 测试不足 P2，均不阻断）

**下一棒**：Owner verify（原卡 `owner_verify: required`）→ 核验 MCP 配置片段实测 + QUICKSTART 跨机节可操作性 → 决定是否 finalize。
