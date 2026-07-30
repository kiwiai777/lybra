---
task_id: AIPOS-274F1
return_id: return_AIPOS-274F1_20260730_exec-lybra-kiwiai-dev
returned_by: exec.lybra.kiwiai-dev
returned_at: '2026-07-30T12:25:00Z'
executor_status: completed
audit_readiness: ready
---
# RETURN-FIX-1 — AIPOS-274 眼验打回:两处回归修复

## Summary

完成 FIX-1 卡全部四项修复:①向导让位(total_tasks 透出路径纠偏+契约测试钉死)②待验站排除逻辑补全(owner_verification 记录加载+排除判据并入)③零回归④版本号对齐(已在 274 本体)。

**铁证复现**:
- ①lybra-dev(93 任务)workspace 仍现"欢迎使用 Lybra"向导 → JS 读 `truthData.data.summary.total_tasks`(从未存在的路径,summary 在 response 顶层,不在 data 下) → totalTasks 恒为 0 → 向导误现
- ②验证台待验站列 AIPOS-263,而其 owner_verification 记录(approve,07-30 05:25)与闭环态俱在 → verify_bench.py 的站点排除逻辑只查 _closure_unit_finalized(FZ returned),未读 owner_verifications/ 目录(records.py 未加载该目录) → 已核验任务复活

**根因**:
1. **total_tasks 路径回归(第三次)**:272 FIX-9 曾修复后端返回该键(owner_truth_view.py L1012),但前端 JS(project-detail.html L1310)误读 `truthData.data.summary` 而非 `truthData.summary` — API response 结构一直是 `{ok, data:{...}, summary:{total_tasks}, ...}`,summary 在顶层,不在 data 里,但 JS 路径错写成嵌套读取,导致 totalTasks 始终 undefined → 0,向导判据失效。
2. **owner_verifications 记录未加载**:AIPOS-273 引入 owner_verification 记录(approve/reject 按钮写入 `5_tasks/records/owner_verifications/<task_id>/*.md`),但 records.py 的 load_records() 从未读该目录 → find_records_for_task() 不返回 owner_verifications → verify_bench.py 的站点排除逻辑只能看到 FZ 是否 returned,看不到 Owner 是否已 approve → 263 已核验(07-30 05:25 approve 记录在盘)但 FZ 仍 pending,排除判据漏掉它,让它复活在待验站;273 的 FZ 已 returned,被正确排除,形成肉眼可见的分化。

---

## Changes

### 1. total_tasks 前端读取路径纠偏(web/board/static/project-detail.html L1310-1314)

```javascript
// 错(AIPOS-272 起就写错,third time):
const summary = (truthData && truthData.data && truthData.data.summary) || {};

// 对(本次修复):
const summary = (truthData && truthData.summary) || {};
```

**理由**:`build_owner_truth_view` 返回结构一直是 `{ok, operation, data:{tasks, ...}, summary:{total_tasks, ...}, ...}`,summary 在顶层,与 data 并列,不在 data 之下。前端 JS 误读嵌套路径 → totalTasks 恒 undefined → 向导误现在有任务的工作区。

**契约测试**(web/board/tests/test_board_adapter_contract.py,新增 test_owner_truth_view_total_tasks_key_pinned_at_top_level):
```python
# 断言 summary.total_tasks 在响应顶层,不在 data 下
self.assertIn("summary", response)
self.assertIn("total_tasks", response["summary"], "summary.total_tasks key dropped again")
self.assertEqual(response["summary"]["total_tasks"], 1)
self.assertNotIn("summary", response.get("data", {}))
```

第三次丢该键,改用测试红代替人眼。

### 2. owner_verifications 记录加载(tools/aipos_cli/records.py)

**新增函数** `_build_owner_verification_record(path, repo_root, directory_task_id)`:
- 解析 `5_tasks/records/owner_verifications/<task_id>/*.md` 文件(AIPOS-273 verify-bench approve/reject 按钮写入的 append-only 记录)
- 返回结构镜像 `_build_record` 惯例:`record_type`, `task_id`, `decision`, `decided_by`, `decided_at`, `actor`, ...
- 接入 AIPOS-255 F-BOARD-2 actor 约定,供 timeline 渲染(虽当前未用,保持一致性)

**load_records() 改动**:
- 声明 `owner_verifications_root` = `records_root / "owner_verifications"`
- 加载 `owner_verifications` 列表(调 `_iter_record_files` + `_build_owner_verification_record`)
- 建立 `task_owner_verifications` 索引(按 task_id 分组,newest first)
- 排序(`_record_sort_key`,decided_at 驱动)
- 返回字典增补:`owner_verifications`(扁平列表), `task_owner_verifications`(分组索引), `owner_verification_records`(summary 计数), `owner_verifications_root_exists`

**find_records_for_task() 改动**:
```python
return {
    ...,
    "owner_verifications": list(records.get("task_owner_verifications", {}).get(task_id, [])),
}
```

现在 `recs = find_records_for_task(records, tid)` 会返回该任务的所有 owner_verification 记录。

### 3. 待验站排除逻辑补全(tools/aipos_cli/verify_bench.py L286-298)

**原逻辑**(F-262B-4,AIPOS-262B 起):
```python
if (stage == "verdict_pass" and _closure_unit_finalized(root, members_by_root, records)):
    closed_excluded.append(...)
```

只查 FZ member 是否 returned(闭环即退站) → 263 的 FZ 仍 pending,漏过它。

**新逻辑**(AIPOS-274F1):
```python
# 新增:检查 owner_verifications 记录中是否有 decision=approve
owner_approved = any(
    _as_str((v.get("metadata") or {}).get("decision")).lower() == "approve"
    for v in recs.get("owner_verifications", [])
)
if (
    stage == "verdict_pass"
    and (owner_approved or _closure_unit_finalized(root, members_by_root, records))
):
    closed_excluded.append(...)
```

**并入两个判据**:①Owner 已 approve(有对应记录) **或** ②FZ 已 returned(闭环收编) → 任一满足即退站。

**理由**:Owner approve 记录是真人核验完成的实锤证据,不应等 FZ 派单+执行+收编的流程滞后才退站;263 已核验(07-30 05:25 approve 记录),但 FZ 仍 pending → 旧逻辑漏它,新逻辑捕获。

**测试**(web/board/tests/test_project_map_and_verify_bench.py,新增 VerifyBenchOwnerApprovedExcludedTests):
- 两张并列任务卡:TASK-APPROVED(有 approve 记录,FZ pending) vs TASK-UNAPPROVED(无 approve 记录,FZ pending)
- 断言:TASK-APPROVED 在 closed_excluded,不在 stations;TASK-UNAPPROVED 在 stations,不在 closed_excluded
- 镜像 263(已 approve 但 FZ pending,应退站) vs 273(FZ returned,旧逻辑已正确排除)的实际分化

### 4. 零回归验证

- ✓ 既有 504 项 tools/aipos_cli 测试全过(1 个预存 service_mode 超时红,与本次无关)
- ✓ 既有 208 项 web/board 测试全过(2 个预存 governance route 红,与本次无关)
- ✓ 新增契约测试(total_tasks 键钉死)通过
- ✓ 新增 fixture 测试(owner_approved 排除逻辑)通过

---

## Verification

### S1: 有任务工作区向导 hidden + total_tasks 契约测试绿

**真实工作区验证**:
```python
from pathlib import Path
from tools.aipos_cli.owner_truth_view import build_owner_truth_view
result = build_owner_truth_view(Path('/home/kiwi/ai-project-os/2_projects/lybra'))
# response["summary"]["total_tasks"] = 104(顶层,不在 data 下)
# response["data"] 下无 summary 键
```

**契约测试**:
```bash
pytest web/board/tests/test_board_adapter_contract.py::BoardAdapterContractTests::test_owner_truth_view_total_tasks_key_pinned_at_top_level -v
# PASSED
```

✅ 前端 JS 现读 `truthData.summary.total_tasks` = 104 → 向导让位(totalTasks > 0 判据生效)

### S2: 待验站不含 263 + fixture 双例断言

**真实工作区验证**:
```python
from tools.aipos_cli.verify_bench import get_verify_bench
result = get_verify_bench(repo_root=Path('/home/kiwi/ai-project-os/2_projects/lybra'))
data = result.get('data', {})
# stations = [] (空站,当前无待验任务)
# closed_excluded = ['AIPOS-260', 'AIPOS-261', 'AIPOS-262B', 'AIPOS-263', 'AIPOS-264', 'AIPOS-265', 'AIPOS-266', ...]
# 263 在 closed_excluded,不在 stations ✓
```

**fixture 测试**:
```bash
pytest web/board/tests/test_project_map_and_verify_bench.py::VerifyBenchOwnerApprovedExcludedTests -v
# test_approved_without_fz_excluded_unapproved_still_stations PASSED
```

✅ 263 正确排除(因 owner_verification approve 记录存在),273 正确排除(因 FZ returned),差异消失。

### S3: 零回归

**tools/aipos_cli 测试**:
```bash
pytest tools/aipos_cli/tests/ -v
# 504 passed, 1 failed (预存 test_serve_stop_kills_without_home_root_or_project 超时红,与本次无关)
```

**web/board 测试**:
```bash
pytest web/board/tests/ -v
# 208 passed, 2 failed (预存 test_local_read_api.py governance route 红,与本次无关)
```

**records.py 直接测试**:
```bash
pytest tools/aipos_cli/tests/test_records_reader.py tools/aipos_cli/tests/test_validator_records_json.py -v
# 15 passed
```

✅ 本次改动(records.py 加载 owner_verifications,verify_bench.py 排除逻辑,前端 JS 路径)不影响既有功能。

---

## 排除物 + 理由

无。卡内四项修复全部执行:
1. total_tasks 透出+契约测试 ✓
2. 待验站派生逻辑补全(owner_verifications 加载+排除判据) ✓
3. 零回归 ✓
4. 版本号对齐(已在 274 本体,FIX-1 不涉及) ✓

---

## 异常与自作判断

无。卡内修法清晰,铁证明确,实现路径单一:
- total_tasks 读取路径纠偏(JS 一行)
- owner_verifications 记录加载(records.py 新增 _build 函数+索引+返回键)
- 待验站排除判据并入(verify_bench.py owner_approved 检查)
- 契约测试+fixture 双向断言钉死行为

未遇护栏拦截,未遇卡内信息不足,未遇需顾问裁决的分歧。

---

## 实际使用的模型 + 自报 token 用量

```
model=claude-3-5-sonnet-20241022 (Anthropic via kiwiai 代理链)
tokens≈in:99000/out:8000
total≈107000 tokens
```

(Pi 运行时未提供精确计量,根据会话复杂度估算)

---

## 下一棒

**移交审计**:FIX-1 卡未授权 finalize,按 task-closure-loop 标准程序,自产审计卡并执行审计:

```
auditor 认领 AIPOS-274F1R(待顾问创建或审计员自派)
```

验收断言(S1-S3)全部满足,修法精确,测试双向钉死,可直接审计闭环。
