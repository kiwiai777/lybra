# Skill: truth-navigator

**描述**: 治理仓时间线真相导航算法（AIPOS-R6M 大项C②）

**何时使用**: 冷启动/压缩后/读到互相冲突的治理文档时

**适用角色**: advisor, executor, auditor

> **示例中的尖括号为占位, 以本工位 .lybra/role 与 project.json 为准**(治理根与目录落点读 config.schema governance_structure 与 project.json 声明)。本 skill 项目无关。

---

## 核心算法（固化为分发 skill，同源引用设计段，禁复写第二份判据表）

当你在治理仓中遇到以下情况时，使用此导航算法确定真相：

1. **冷启动会话**（无历史上下文）
2. **上下文压缩后**（丢失早期信息）
3. **读到互相冲突的治理文档**（不同文件说法不一）

### 导航步骤（时间线宪法）

#### ① 读 stage_archives 最新一篇 = 当前坐标

路径：`<stage_archive>/<date>_<stage-name>.md`（`<stage_archive>` = config.schema `governance_structure.paths.stage_archive` 声明的目录, 相对治理根）

- 查找编号最大的阶段归档文件
- 这是"三个月后的人只读这一篇+其后的 decision_log 即可上手"的基线
- 包含：阶段目标/交付清单/关键裁决指针/遗留账/下一阶段入口

**如果阶段归档目录为空或不存在**：从卡粒度编年史（project.json `paths.foundation_backlog`, 项目声明了才有）和项目设计文档（`governance_structure.paths.governance_docs` 目录下项目自定的设计文档, 如有）开始。

#### ② 读该篇之后的 decision_log 全部条目 = 增量真相

路径：`<decision_log_dir>/YYYY-MM/YYYY-MM-DD-<slug>.md`（`<decision_log_dir>` = `governance_structure.paths.decision_log_dir`, 相对治理根）

- 按时间顺序读取最新阶段归档日期之后的所有决策日志条目
- 每个条目格式：一句话结论 + decided_by + 指向权威载体（gate 记录/设计§节/卡 ID）
- **只记决策粒度事件**（Owner 裁定/仲裁/信封授权与吊销/纪律新增/设计不变量增改/superseding 动作/角色权限变更）
- **不记进度汇报/卡完成/执行细节/发现登记**（那些归 records、backlog、ledger）

#### ③ 文档状态头裁 active/superseded

所有治理文档必须携带状态头 frontmatter：

```yaml
---
status: active         # 或 superseded
decided_at: <ISO8601>
superseded_by: <ref>   # 如果 superseded，指向新文档
---
```

- **status: active** = 当前有效
- **status: superseded** = 已被取代，不再有效
- **禁把无状态头或 superseded 文档当权威**

#### ④ 仍冲突以时间线后者为准

如果经过①②③仍有冲突：

- 以 **decision_log 时间线后者为准**（decided_at 字段）
- 后发裁决覆盖前发裁决

### 两目录分工判据（时间线宪法）

#### decision_log/ = 决策粒度·事件驱动·当天落

**计入判据**: "改变未来行为规则的单点决定"

包括：
- Owner 裁定/仲裁
- 信封授权与吊销
- 纪律新增
- 设计不变量增改
- superseding 动作（新文档+老文档打标+decision_log 落条）
- 角色权限变更

每事件一文件，决策发生当场落（收账时兜底核对），内容只有：
- 一句话结论
- decided_by
- 指向权威载体

**不计入**: 进度汇报/卡完成/执行细节/发现登记（那些归 records、backlog、ledger）

#### stage_archives/ = 阶段粒度·低频·阶段关账才写

**计入判据**: "三个月后的人只读这一篇+其后的 decision_log 即可上手"

包括：
- 阶段目标
- 交付清单
- 关键裁决指针
- 遗留账
- 下一阶段入口

一阶段一篇（如某条主线收口/迁移评估/发布门）。阶段没关不写，写了=阶段正式关账的标志。

---

## 实操示例

### 场景 1: 冷启动会话，需要了解当前 loop 设计

1. 读阶段归档目录（`<stage_archive>/`）找最新篇（如果为空，读项目自定的设计文档, 如有）
2. 读该篇标注的日期之后的所有 `<decision_log_dir>/<YYYY-MM>/*.md`
3. 如果看到设计文档文件头有 `status: active`，那就是当前权威
4. 如果看到两份设计文档互相冲突，检查 decision_log 有无 superseding 记录

### 场景 2: 读到两份互相冲突的治理文档

文档 A: `<governance_docs>/<旧设计文档>.md`
```yaml
status: superseded
decided_at: 2026-08-01T10:00:00Z
superseded_by: <governance_docs>/<新设计文档>.md
```

文档 B: `<governance_docs>/<新设计文档>.md`
```yaml
status: active
decided_at: 2026-08-11T12:00:00Z
superseded_by: null
```

**结论**: 采用文档 B（新设计文档），因为：
- 文档 A 状态为 superseded
- 文档 B 状态为 active
- 文档 A 明确指向文档 B

### 场景 3: 查找某个决策的权威依据

假设你看到代码注释说"Owner 裁定 X"，但不确定在哪里：

1. 搜索决策日志目录（`<decision_log_dir>/`）中的所有 .md 文件，查找关键词
2. 找到条目后，查看其中的"指向权威载体"字段
3. 跟随引用到 gate 记录/设计文档的具体章节/卡 ID

---

## 注意事项

1. **治理仓项目根目录本身也来自配置 schema**（`governance_structure.paths.governance_root`），不写死代码
2. **多项目/换机/演进都只改配置**，不改导航算法
3. **禁复写第二份判据表**：两目录分工判据只在此 skill 与 config.schema（`stage_archive` / `decision_log_dir` 的 semantics）中存在，代码引用此 skill，不自创判据

---

## 关联设计

- **设计权威**: 本项目设计文档（`governance_structure.paths.governance_docs` 目录下项目自定, 如有）中的时间线宪法章节
- **固化路径**: `config.schema.json` governance_structure（目录键 `stage_archive` / `decision_log_dir` / `governance_docs`）+ 本项目 project.json `paths`（卡编年史 `foundation_backlog` 为项目可选声明；落点以本项目 project.json 声明为准）
- **执法**: `tools/hooks/governance-pre-commit` (大项 B)

---

**此 skill 母本住产品仓 `agents/skills/truth-navigator/`，纳入 distribution 规格按角色类别分发到工位。**
