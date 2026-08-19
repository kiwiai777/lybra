# Skill: truth-navigator

**描述**: 治理仓时间线真相导航算法（AIPOS-R6M 大项C②）

**何时使用**: 冷启动/压缩后/读到互相冲突的治理文档时

**适用角色**: advisor, executor, auditor

---

## 核心算法（固化为分发 skill，同源引用设计段，禁复写第二份判据表）

当你在治理仓中遇到以下情况时，使用此导航算法确定真相：

1. **冷启动会话**（无历史上下文）
2. **上下文压缩后**（丢失早期信息）
3. **读到互相冲突的治理文档**（不同文件说法不一）

### 导航步骤（LOOP-REDESIGN §2 N6 时间线宪法）

#### ① 读 stage_archives 最新一篇 = 当前坐标

路径：`stage_archive/<date>_<stage-name>.md` (治理工作区根下, 见 config.schema governance_structure.paths.stage_archive)

- 查找编号最大的阶段归档文件
- 这是"三个月后的人只读这一篇+其后的 decision_log 即可上手"的基线
- 包含：阶段目标/交付清单/关键裁决指针/遗留账/下一阶段入口

**如果 stage_archive/ 为空或不存在**：从 FOUNDATION-BACKLOG.md 和 LOOP-REDESIGN.md 开始。

#### ② 读该篇之后的 decision_log 全部条目 = 增量真相

路径：`governance/decision_log/YYYY-MM/YYYY-MM-DD-<slug>.md`

- 按时间顺序读取 stage_archive 日期之后的所有 decision_log 条目
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

### 两目录分工判据（LOOP-REDESIGN §2 N6 Owner 2026-08-15 追钉）

#### decision_log/ = 决策粒度·事件驱动·当天落

**计入判据**: "改变未来行为规则的单点决定"

包括：
- Owner 裁定/仲裁
- 信封授权与吊销
- 纪律新增
- 设计不变量增改
- superseding 动作（新文档+老文档打标+decision_log 落条）
- 角色权限变更

每事件一文件，决策发生当场落（N6 收账兜底核对），内容只有：
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

一阶段一篇（如 R6 线收口/冻结三连跑/迁移门评估/发布门）。阶段没关不写，写了=阶段正式关账的标志。

---

## 实操示例

### 场景 1: 冷启动会话，需要了解当前 loop 设计

1. 读 `stage_archive/` 找最新篇（如果为空，读 LOOP-REDESIGN.md）
2. 读该篇标注的日期之后的所有 `decision_log/2026-08/*.md`
3. 如果看到 LOOP-REDESIGN.md 文件头有 `status: active`，那就是当前权威
4. 如果看到两份设计文档互相冲突，检查 decision_log 有无 superseding 记录

### 场景 2: 读到两份互相冲突的治理文档

文档 A: `governance/OLD-DESIGN.md`
```yaml
status: superseded
decided_at: 2026-08-01T10:00:00Z
superseded_by: governance/LOOP-REDESIGN.md
```

文档 B: `governance/LOOP-REDESIGN.md`
```yaml
status: active
decided_at: 2026-08-11T12:00:00Z
superseded_by: null
```

**结论**: 采用文档 B（LOOP-REDESIGN.md），因为：
- 文档 A 状态为 superseded
- 文档 B 状态为 active
- 文档 A 明确指向文档 B

### 场景 3: 查找某个决策的权威依据

假设你看到代码注释说"Owner 裁定 X"，但不确定在哪里：

1. 搜索 `decision_log/` 目录中的所有 .md 文件，查找关键词
2. 找到条目后，查看其中的"指向权威载体"字段
3. 跟随引用到 gate 记录/设计文档的具体章节/卡 ID

---

## 注意事项

1. **治理仓项目根目录本身也来自配置 schema**（LOOP-REDESIGN §2 N6），不写死代码
2. **多项目/换机/演进都只改配置**，不改导航算法
3. **禁复写第二份判据表**：两目录分工判据只在此 skill 和 LOOP-REDESIGN §2 N6 中存在，代码引用此 skill，不自创判据

---

## 关联设计

- **设计权威**: `governance/LOOP-REDESIGN.md` §2 N6（时间线宪法）
- **固化路径**: `config.schema.json` governance_structure
- **执法**: `tools/hooks/governance-pre-commit` (大项 B)

---

**此 skill 母本住产品仓 `agents/skills/truth-navigator/`，纳入 distribution 规格按角色类别分发到工位。**
