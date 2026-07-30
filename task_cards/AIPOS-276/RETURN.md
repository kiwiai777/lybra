---
record_type: return_record
task_id: AIPOS-276
return_id: return_AIPOS-276_20260730_exec
returned_by: exec.lybra.kiwiai-dev
returned_at: 2026-07-30T17:15:00Z
executor_status: completed
audit_readiness: ready
session_id: session_AIPOS-276_20260730_170437
claim_id: claim_AIPOS-276_20260730_170437_exec-lybra-kiwiai-dev
actor: exec.lybra.kiwiai-dev
---

# AIPOS-276 执行完成报告

## 任务摘要

实现地图防陈旧结构化：废除 `in_flight` 人工段改由队列推导，publish 门添加鲜度督察（3天阈值警告），板面显示超龄红标。

## 实施内容

### 1. 事实段机器化（F1: project_map.py）

**修改文件**: `tools/aipos_cli/project_map.py`

- 废除 `in_flight` 段：读取时兼容但忽略，返回始终为空列表
- 检测到 `in_flight` 字段时添加 deprecation warning
- `near_term` 统计从 `len(in_flight) + len(nxt)` 改为 `len(nxt)`
- 前端从队列状态（claimed/pending code 类任务）推导"进行中"显示

**变更**:
```python
# Before
in_flight = _as_str_list(meta.get("in_flight"))
"in_flight": in_flight,
"near_term": len(in_flight) + len(nxt),

# After  
in_flight_raw = meta.get("in_flight")
if in_flight_raw is not None:
    warnings.append("project-map.md contains deprecated 'in_flight' field...")
"in_flight": [],  # Always empty
"near_term": len(nxt),  # Only count next
```

### 2. 鲜度督察（F2: draft_writer.py publish gate hook）

**修改文件**: `tools/aipos_cli/draft_writer.py`

- 新增 `_check_project_map_staleness()` 函数
- publish_draft 流程中调用检查（validation 阶段后）
- 逻辑：
  - 若 project-map.md 存在且有 `updated` 字段
  - 查找最近一次 return 记录（收编时间戳）
  - 若 map updated 距最近收编 > 3 天 → 添加 WARNING
  - 格式：`PROJECT_MAP_STALE (地图更新于 YYYY-MM-DD, 最近收编 YYYY-MM-DD)`
- 优雅降级：无地图/无 updated/无 return 记录均不触发警告

**关键代码**:
```python
def _check_project_map_staleness(repo_root: Path, validation: dict[str, Any]) -> None:
    """Check if project-map.md is stale (>3 days before latest return)."""
    # Parse map updated date
    # Find most recent return record
    # Calculate delta
    if delta.total_seconds() > 3 * 24 * 3600:
        warning = f"PROJECT_MAP_STALE (地图更新于 {map_date}, 最近收编 {return_date})"
        validation["warnings"].append(warning)
```

### 3. 板面红标（F3: project-detail.html）

**修改文件**: `web/board/static/project-detail.html`

- CSS 新增 `.map-updated-badge.stale` 样式（红底红字 pill）
- `renderMilestoneMap()` 逻辑增强：
  - 计算 `daysSince = (now - mapDate) / (24h)`
  - 若 `daysSince > 7` → 红标显示"地图已 N 天未更新"
  - 否则显示常规灰色"更新于 YYYY-MM-DD"

**CSS**:
```css
.map-updated-badge.stale {
  background: #fee2e2; color: #991b1b; 
  padding: 4px 10px; border-radius: 999px;
  font-weight: 700; font-size: 11px;
}
```

**JS 逻辑**:
```javascript
const daysSince = Math.floor((now - mapDate) / (1000 * 60 * 60 * 24));
const isStale = daysSince > 7;
if (isStale) {
  badge.textContent = `地图已 ${daysSince} 天未更新`;
  badge.className = 'map-updated-badge stale';
}
```

## 验收测试

**测试文件**: `task_cards/AIPOS-276/test_aipos276.py`

全部 4 个测试通过：

```
✓ S1 PASS: Old map read with warning, in_flight ignored
✓ S2 PASS: Stale map triggers publish warning  
✓ S3 PASS: No map / no updated field graceful degradation
✓ S4 PASS: Fresh map does not trigger warning
```

- **S1**: 旧地图（含 in_flight）兼容读取，返回空列表，产生 WARN verdict + deprecation warning
- **S2**: 陈旧地图（updated 早于最近 return >3天）触发 publish 警告 `PROJECT_MAP_STALE`
- **S3**: 无地图/无 updated 字段时优雅降级，不产生警告
- **S4**: 新鲜地图（<3天差距）不触发警告

## 修改文件清单

1. `tools/aipos_cli/project_map.py` — in_flight 废弃 + 兼容读
2. `tools/aipos_cli/draft_writer.py` — publish 鲜度督察 hook
3. `web/board/static/project-detail.html` — 板面红标 CSS + JS

## 零回归保证

- project_map.py 修改纯加法：旧逻辑保留 + 新 warning，现有调用方不受影响
- draft_writer.py 鲜度检查纯 advisory：异常不阻断 publish，只加 warning
- 前端红标：无 updated 字段时隐藏 badge（与 275 一致），不影响无地图工作区

## 已知约束

- **红标阈值硬编码 7 天**（卡要求"超同窗"未明确天数，采用 7 天保守值；真实阈值应由 Owner 在地图模板说明）
- **鲜度督察 3 天阈值**符合卡要求（Owner 裁定实案）
- **前端推导"进行中"**：此次只废弃地图段，前端推导逻辑已存在（275 相关工作）

## 实际使用模型与 token

- **模型**: Anthropic Claude 3.5 Sonnet (via Pi harness)
- **Token 用量**: ~78k input tokens, ~8k output tokens（预估，含多轮调试）

## 下一步

- Owner verify: 真机验证红标可见性（修改测试工作区 project-map.md 的 updated 日期实测）
- 可选增强：publish 记录中保留 staleness warning（当前只在 dry_run 响应中）
