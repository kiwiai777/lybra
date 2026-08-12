# AIPOS-R0 Schema Package Implementation

## Schema 间依赖图 (AIPOS-SCHEMA-UNIFY-1)

enums.schema.json 是包内唯一值域源。其他 schema 通过 `{"$enum": "<name>"}` 按名引用,
loader 解析展开 + 加载时交叉校验(引用不存在/字面量残留 = SchemaLoadError)。

```
┌─────────────────────────────────────────────────────────────────┐
│                    enums.schema.json                             │
│               (唯一值域源 / Single Source of Truth)              │
│                                                                  │
│  queue_state  verdict  task_mode  task_class  priority           │
│  artifact_policy  autonomy_mode  audit_requirement                │
│  context_isolation  polling_mode  claim_policy  report_mode      │
│  role_category  record_type                                       │
│  task_type  risk_level  validation_verdict  progress_status      │
└──────┬──────────┬──────────┬──────────┬──────────┬───────────────┘
       │          │          │          │          │
  $enum│     $enum│     $enum│     $enum│     $enum│
       ▼          ▼          ▼          ▼          ▼
 ┌──────────┐ ┌────────┐ ┌──────────────┐ ┌────────────┐ ┌──────────┐
 │card.schema│ │verbs   │ │config.schema │ │roles.schema│ │transitions│
 │.json      │ │.schema │ │.json         │ │.json       │ │.schema   │
 │           │ │.json   │ │              │ │(语义引用    │ │.json     │
 │status →   │ │verdict →│ │role →       │ │ role_category)│ │(描述式   │
 │ queue_state│ │ verdict│ │ role_category│ │             │ │ 无机器    │
 │task_mode →│ │autonomy │ │autonomy_mode │ │             │ │ 约束)    │
 │ task_mode │ │ _mode  │ │              │ │             │ │          │
 │priority → │ │validation│ │            │ │             │ │          │
 │ priority  │ │ _verdict│ │            │ │             │ │          │
 │...13 fields│ │progress │ │            │ │             │ │          │
 │ use $enum │ │ _status │ │            │ │             │ │          │
 └─────┬─────┘ └───┬────┘ └──────┬───────┘ └─────────────┘ └──────────┘
       │           │             │
       ▼           ▼             ▼
 ┌─────────────────────────────────────────────────────────────┐
 │              tools/schema_loader.py                          │
 │        (唯一加载实现 / 一机制一实现)                          │
 │                                                              │
 │  resolve_enum_ref()     ← $enum → enums.schema.json 展开    │
 │  resolve_field_enum()   ← 兼容旧 inline enum + 新 $enum    │
 │  cross_validate_schemas() ← 加载时交叉校验                  │
 │  validate_field_value() ← 消费方透明,内部自动解析 $enum    │
 └─────────────────────────────────────────────────────────────┘
       │
       ▼
 ┌─────────────────────────────────────────────────────────────┐
 │                    消费方 (零行为变化)                        │
 │                                                              │
 │  draft_validator.py    ← validate_field_value (N0校验)      │
 │  aipos_cli.py          ← get_enum_values (CLI枚举提示)      │
 │  mcp_server/tools.py   ← get_verb_contract (动词契约)       │
 │  enroll/distribute     ← get_role_spec (角色注册表)         │
 └─────────────────────────────────────────────────────────────┘
```

### 引用规则

1. **新增 schema 必先挂图**: 任何新 schema 文件加入包前,必须更新此依赖图
2. **枚举值域只加 enums.schema.json**: 改值域只改一处,消费方自动跟随
3. **禁止 inline enum 数组**: card/verbs/config 中不允许 `"enum": [...]` 字面量
4. **引用不存在 = 加载即炸**: `cross_validate_schemas()` 在加载时校验所有 $enum 引用
5. **一机制一实现**: 引用解析只在 `tools/schema_loader.py` 一处

## 交付内容

### 1. Schema包五件套（`schema/` 目录）

所有schema文件为版本化JSON格式，机器可读，作为单一数据源：

#### `schema/card.schema.json`
- **全部卡字段定义**（126个字段）
- 包含字段类型、是否必填、枚举值、描述
- 定义了12个必填字段和9个运行时禁止字段
- 涵盖从现有代码清点的164个metadata.get调用和346个卡key

#### `schema/enums.schema.json`
- **枚举表**：队列状态、裁决类型、record_type、角色类别等
- 收拢散落在40+处的硬编码枚举值
- 包含task_mode值域及其分路含义（code全程，docs/governance/config跳N4/N5）

#### `schema/verbs.schema.json`
- **动词契约**：每个gate动词的参数/应答shape
- 两阶段语义（dry_run → confirm）
- **WARN行为契约**：非阻塞，永不吞token，永不挡操作
- 覆盖N0-N6主干动词和G1-G3横切动词

#### `schema/config.schema.json`
- **配置结构定义**：connection/role/policy/workspace配置
- 端口、URL、路径约定
- **治理仓项目根目录与固化目录树**：governance root、5_tasks、stage_archive、N6收账清单路径
- 值在各配置文件，schema定义结构，**代码零写死**

#### `schema/transitions.schema.json`
- **状态机转移表**（声明式）
- N0-N6主干7节点：每态"谁触发下一步"、验证规则、输出
- task_mode分路：code全程N0→N6；docs/governance/config跳N4/N5由顾问审
- G1-G3横切：Owner门、维护动词、派生派工
- 幂等声明：所有转移可重试

### 2. Schema加载器（单一入口）

**`tools/schema_loader.py`** - Python单一加载实现

```python
from tools.schema_loader import (
    load_schema,              # 加载任意schema
    get_card_field_schema,    # 获取字段定义
    get_enum_values,          # 获取枚举值
    get_required_card_fields, # 获取必填字段
    is_field_defined,         # 检查字段是否定义
    validate_field_value,     # 验证字段值
)
```

**关键特性**：
- LRU缓存优化性能
- 自动repo root查找
- 完整的错误处理
- 禁止第二个加载实现（一机制一实现红线）

### 3. N0发卡校验接入

修改 `tools/aipos_cli/draft_validator.py`，在 `validate_draft_metadata` 中接入schema校验：

**新增校验**：
1. ✅ **未定义/拼错字段检测**：明确指出字段名（如materialize_refs）
2. ✅ **必填字段检查**：从schema读取，缺失时BLOCK
3. ✅ **运行时字段拦截**：claim_id等禁止出现在draft
4. ✅ **枚举值校验**：task_mode/priority等必须在允许列表
5. ✅ **类型检查**：字段值类型必须匹配schema定义

**向后兼容**：schema不可用时回退到硬编码验证。

## 验收测试

### 基础测试（`tools/test_schema_loader.py`）
```bash
cd ~/projects/lybra
python3 tools/test_schema_loader.py
```
✅ 所有schema文件加载
✅ 字段查询、枚举查询、验证功能
✅ Draft validator集成

### N0验证测试（`tools/test_n0_validation.py`）
```bash
python3 tools/test_n0_validation.py
```

**验收断言通过**：

1. ✅ **拼错字段检测**：materialize_refs被识别并报告
2. ✅ **缺必填字段**：明确列出missing required field: needs_owner/output_target/artifact_policy，BLOCK verdict
3. ✅ **运行时字段拦截**：claim_id/claimed_by/claimed_at触发BLOCK，明确提示forbidden runtime-state field
4. ✅ **有效卡通过**：完整有效的draft获得PASS/WARN verdict（仅推荐字段警告）
5. ✅ **枚举值校验**：invalid_mode/ultra_super_high被拒，明确列出allowed values

## 零行为变化（除N0新校验）

- ✅ 老卡不追溯：只拦新发布的draft
- ✅ 现有流程零回归：draft_publish在dry_run时执行schema校验
- ✅ 草稿模板对齐：required字段从schema读取，消"发卡即WARN"

## 文件清单

```
schema/
├── card.schema.json          # 卡字段schema（126字段）
├── enums.schema.json         # 枚举表（10+枚举类型）
├── verbs.schema.json         # 动词契约（N0-N6+G1-G3）
├── config.schema.json        # 配置结构（治理仓路径固化）
└── transitions.schema.json   # 状态机转移表（声明式）

tools/
├── schema_loader.py          # Schema加载器（单一入口）
├── test_schema_loader.py     # 加载器测试
└── test_n0_validation.py     # N0校验验收测试

tools/aipos_cli/
└── draft_validator.py        # [修改] 接入schema校验
```

## 实现要点

### 单一源原则
- Schema = 唯一数据定义
- 禁止硬编码：枚举/字段/配置全从schema读
- 禁止第二实现：schema_loader.py是唯一加载器
- 跨语言实现需conformance测试锁定

### 扩展机制
- 新字段/新枚举/新动词：先进schema，代码泛化读取
- 无需改代码，只需更新schema数据
- 这就是"保留扩展"的机制本体（LOOP-REDESIGN v2 §5）

### 根因账对应
收编以下发现：
- **FND-21**（卡schema）：card.schema.json覆盖346 key
- **FND-22**（硬编码）：28处硬编码归config.schema定义
- **FND-23**（枚举+契约锁）：enums.schema + verbs.schema锁定

### 与设计权威对齐
- LOOP-REDESIGN v2 §5：schema包五件 ✅
- LOOP-REDESIGN v2 §2：固化节点+转移表 ✅
- LOOP-REDESIGN v2 §6：一机制一实现 ✅

## 后续步骤（不在本卡范围）

本卡**只立数据+N0校验**，后续步骤：

- **R1**：LoopContext + scope铁律（多项目隔离）
- **R2**：分发器v1（凭据+配置）
- **R3**：工具集中+分发
- **R4**：代码全面改读schema（逐动词verb(ctx)）
- **R5**：worktree隔离
- **R6**：清账+复验

## 使用示例

```python
# 在任何需要schema数据的地方
from tools.schema_loader import (
    get_required_card_fields,
    get_enum_values,
    is_field_defined,
)

# 获取必填字段
required = get_required_card_fields()
for field in required:
    if field not in metadata:
        raise ValueError(f"Missing required field: {field}")

# 获取允许的枚举值
valid_states = get_enum_values("queue_state")
if status not in valid_states:
    raise ValueError(f"Invalid status: {status}")

# 检查字段是否定义
if not is_field_defined(field_name):
    warnings.append(f"Unknown field: {field_name}")
```

## 设计决策

1. **JSON而非YAML**：机器解析更稳定，无YAML的歧义性
2. **单文件加载器**：避免重复实现，强制单一源
3. **向后兼容fallback**：schema不可用时仍能工作
4. **缓存优化**：重复读取不产生IO开销
5. **非侵入接入**：draft_validator只在SCHEMA_AVAILABLE时启用新校验

## 审计要点

- [ ] Schema文件结构完整，版本标记清晰
- [ ] 加载器无第二实现（grep确认）
- [ ] N0校验生效：拼错字段/缺必填/运行时字段全拦截
- [ ] 有效卡能通过：完整draft不被误拒
- [ ] 零行为变化：老卡/现有流程不受影响
- [ ] 测试覆盖：基础+验收测试全通过

---

**交付时间**：2026-08-11  
**符合设计权威**：LOOP-REDESIGN v2 §5（schema包五件）  
**验收标准**：AIPOS-R0任务卡全部断言通过
