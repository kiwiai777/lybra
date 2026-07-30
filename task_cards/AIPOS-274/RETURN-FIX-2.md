# RETURN-FIX-2 — AIPOS-274F2 执行回报

## 1. 一句话结论

**完成。** 后端 owner-truth 与 verify-bench 两个 API 的 summary 已镜像进 data.summary(顶层保留兼容);HTTP 层契约测试 7 项全绿;215 项既有测试零回归(2 项 pre-existing 失败与本次无关)。

## 2. 做了什么

1. 读 FIX-2 准绳卡(`~/projects/lybra/task_cards/AIPOS-274/FIX-2.md`),确认修法字面要求。
2. 定位 `build_owner_truth_view()` 返回结构:`summary` 在顶层,`data` 下无 `summary`。
3. 定位 `get_verify_bench()` 返回结构:`summary` 在顶层,`data` 下无 `summary`(stations 已在 data 内,但 summary 计数未镜像)。
4. 修改 `owner_truth_view.py`:提取 summary 为局部变量,同时放入 `data.summary` 和顶层 `summary`(同一对象引用)。
5. 修改 `verify_bench.py`:同样提取 summary 为局部变量,在 `data` 内增加 `summary` 键,顶层 `summary` 参数复用同一对象。
6. 更新 `test_board_adapter_contract.py` 中的 `test_owner_truth_view_total_tasks_key_pinned_at_top_level`:原先 `assertNotIn("summary", data)` 改为 `assertIn("summary", data)` + 断言 `data.summary.total_tasks == 1`。
7. 新建 `test_aipos274f2_envelope_alignment.py`:7 项 HTTP 层契约测试,启动真实 ThreadingHTTPServer,GET `/api/owner-truth` 和 `/api/verify-bench`,断言 `data.summary.total_tasks`、`data.summary.stations`、`data.stations` 等在 HTTP 线上正确解析。

## 3. 改动清单

| 文件 | 改动性质 |
|------|----------|
| `tools/aipos_cli/owner_truth_view.py` | `build_owner_truth_view()` 返回结构:summary 提取为局部变量,镜像进 `data.summary` |
| `tools/aipos_cli/verify_bench.py` | `get_verify_bench()` 返回结构:summary 提取为局部变量,镜像进 `data.summary` |
| `web/board/tests/test_board_adapter_contract.py` | 更新 F1 测试:原 `assertNotIn` 改为 `assertIn` + 双向断言 |
| `web/board/tests/test_aipos274f2_envelope_alignment.py` | **新建**:7 项 HTTP 层契约测试 |

## 4. 测试/验证结果原文

### 新测试(7 项全绿)
```
$ python3 -m pytest web/board/tests/test_aipos274f2_envelope_alignment.py -v
...
test_http_data_summary_closure_units_present PASSED
test_http_data_summary_stage_counts_present PASSED
test_http_data_summary_total_tasks_matches_top_level PASSED
test_http_data_tasks_still_present PASSED
test_http_data_stations_present PASSED
test_http_data_summary_previewable_matches PASSED
test_http_data_summary_stations_matches_top_level PASSED
============================== 7 passed in 3.56s ===============================
```

### 既有契约测试(11 项全绿)
```
$ python3 -m pytest web/board/tests/test_board_adapter_contract.py -v
...
11 passed in 0.06s
```

### 验证台测试(14 项全绿)
```
$ python3 -m pytest web/board/tests/test_project_map_and_verify_bench.py -v
...
14 passed in 7.07s
```

### 全量 web/board 测试(215 passed, 2 pre-existing failed)
```
$ python3 -m pytest web/board/tests/ -v
...
======================== 2 failed, 215 passed in 38.24s ========================
```

2 项失败为 `test_local_read_api.py` 的 governance 路由解析测试,在 main 分支未改动前同样失败(`git stash` 验证),与本次改动无关。

## 5. 排除物 + 理由

- **前端不改**:FIX-2 卡明确要求"前端不改(以后端对齐前端为准)"。前端 `project-detail.html` 已在 F1 中修正为读顶层 `summary`,本次后端镜像后两条路径均可工作。
- **未 commit/push**:任务卡未授权 finalize。

## 6. 异常与自作判断

无偏离。严格按 FIX-2 准绳卡字面执行。

## 7. 实际使用的模型 + 自报 token 用量

model=qwen3.7-plus, tokens≈in:45000/out:8000(估算,pi 底栏模型名 qwen3.7-plus)

## 8. 待办 / 移交

- 顾问验收 S1:raw 探针 `data.summary.total_tasks` 应等于顶层 `summary.total_tasks`
- 顾问验收 S3:HTTP 层契约测试入套全绿(已 7 项)
- 顾问验收 S4:零回归(已确认 215 passed)

下一棒:advisor 验收 → 检查 `data.summary.total_tasks` 在真实 workspace 上的值,确认向导让位正常
