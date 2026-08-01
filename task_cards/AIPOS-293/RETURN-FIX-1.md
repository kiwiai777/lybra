# RETURN-FIX-1 — AIPOS-293 FIX-1 回报

## 一句话结论

**完成。** a-d 四项全部齐全,前派改动已到位,契约测试 15/15 通过,零回归。

## 做了什么

1. **读卡核对**:读 FIX-1.md 准绳 a-d,逐项对照已有代码
2. **现状盘点**:确认前派已实现:
   - a) Option C 双模式 UI(mode toggle: directory/file)+ 后端双模式路由
   - b) 结构文件模式:读取→parse_yaml→validate_structure→_check_no_credentials→预览→确认导入
   - c) 错误人话化:_humanized_error + _ERROR_CODE_TO_I18N(13 码)+ i18n 双语全覆盖 + 智能 yaml 提示
   - d) 契约测试 15 条已写
3. **跑契约测试**:`python3 -m pytest web/board/tests/test_aipos293_fix1_dual_mode.py -v` → 15/15 passed
4. **跑全量回归**:`python3 -m pytest web/board/tests/ -v` → 284 passed, 6 failed(预存失败,与 FIX-1 无关)
5. **排除 "Unknown error"**:grep 确认 import 流程无 "Unknown error" 字面量(overview.html 两处残留属 workspace card / Option B 流程,不在 FIX-1 范围)

## 改动清单

**本派未改动任何文件。** 前派改动已完整就位,本派仅做核对+测试+回报。

前派改动文件(已就位):
| 文件 | 改动性质 |
|------|----------|
| `web/board/app.py` L2831-2864 | `_ERROR_CODE_TO_I18N` 映射 + `_humanized_error` helper |
| `web/board/app.py` L2866-3008 | `_project_structure_preview_route` 双模式(directory+file) |
| `web/board/app.py` L3010-3165 | `_project_structure_import_route` 双模式(directory+file) |
| `web/board/static/overview.html` | Option C 双模式 UI(mode toggle + file panel + yaml hint) |
| `web/board/static/i18n.js` | 13 个 error.import.* 键(zh+en)+ 6 个 mode toggle 键(zh+en) |
| `web/board/tests/test_aipos293_fix1_dual_mode.py` | 15 条契约测试 |

## 测试/验证结果原文

### 契约测试(FIX-1 专项)

```
$ cd ~/projects/lybra && python3 -m pytest web/board/tests/test_aipos293_fix1_dual_mode.py -v

web/board/tests/test_aipos293_fix1_dual_mode.py::DualModeContractTests::test_all_error_paths_have_i18n_key PASSED
web/board/tests/test_aipos293_fix1_dual_mode.py::DualModeContractTests::test_directory_mode_import_yaml_hint PASSED
web/board/tests/test_aipos293_fix1_dual_mode.py::DualModeContractTests::test_directory_mode_preview_default_mode PASSED
web/board/tests/test_aipos293_fix1_dual_mode.py::DualModeContractTests::test_directory_mode_preview_success PASSED
web/board/tests/test_aipos293_fix1_dual_mode.py::DualModeContractTests::test_directory_mode_yaml_hint PASSED
web/board/tests/test_aipos293_fix1_dual_mode.py::DualModeContractTests::test_file_mode_import_invalid_project_id PASSED
web/board/tests/test_aipos293_fix1_dual_mode.py::DualModeContractTests::test_file_mode_import_missing_project_id PASSED
web/board/tests/test_aipos293_fix1_dual_mode.py::DualModeContractTests::test_file_mode_preview_not_file PASSED
web/board/tests/test_aipos293_fix1_dual_mode.py::DualModeContractTests::test_file_mode_preview_not_yaml PASSED
web/board/tests/test_aipos293_fix1_dual_mode.py::DualModeContractTests::test_file_mode_preview_path_not_exists PASSED
web/board/tests/test_aipos293_fix1_dual_mode.py::DualModeContractTests::test_file_mode_preview_schema_validation_failure PASSED
web/board/tests/test_aipos293_fix1_dual_mode.py::DualModeContractTests::test_file_mode_preview_success PASSED
web/board/tests/test_aipos293_fix1_dual_mode.py::DualModeContractTests::test_no_unknown_error_in_responses PASSED
web/board/tests/test_aipos293_fix1_dual_mode.py::I18nKeyCompletenessTests::test_error_keys_exist_in_both_locales PASSED
web/board/tests/test_aipos293_fix1_dual_mode.py::I18nKeyCompletenessTests::test_mode_toggle_keys_exist_in_both_locales PASSED

============================== 15 passed in 0.06s ==============================
```

### 全量回归

```
$ cd ~/projects/lybra && python3 -m pytest web/board/tests/ -v
======================== 6 failed, 284 passed in 46.55s ========================
```

6 个失败均为预存问题(与 FIX-1 无关):
- `test_four_area_i18n.py` × 2(AIPOS-266 四区 i18n,非 import 流程)
- `test_local_read_api.py` × 2(本地读 API,governance 路由)
- `test_project_map_and_verify_bench.py` × 2(项目地图 schema)

### "Unknown error" 排查

```
$ grep -rn "Unknown error" web/board/app.py web/board/static/overview.html web/board/static/i18n.js
# app.py: 仅出现在注释(禁止声明)
# overview.html L281: workspace card 错误回退(非 import 流程,在 FIX-1 范围外)
# overview.html L502: Option B init 错误回退(非 import 流程,在 FIX-1 范围外)
```

import 流程(Option C)内无 "Unknown error" 字面量。兜底走 `error.import.unexpected_error` i18n 键,文案给出下一步建议。

## a-d 逐项核对

| 项 | 要求 | 状态 | 证据 |
|----|------|------|------|
| a | Option C 双模式(directory + file) | ✅ | overview.html mode toggle; app.py preview/import 双模式分支 |
| b | 结构文件:读取→schema 校验→预览→确认导入 | ✅ | parse_yaml→validate_structure→_check_no_credentials→preview→import_project_structure |
| c | 错误人话化 + "Unknown error" 禁止 + 智能提示 | ✅ | _humanized_error 13 码; i18n 双语; suggest_file_mode; yaml hint |
| d | 契约测试:双模式通径 + 三类错误 + 零回归 | ✅ | 15/15 passed; 含双模式通径 + 5 类错误断言 + i18n 完整性 + 零回归 |

## 排除物 + 理由

- **未修改 overview.html L281/L502 的 "Unknown error" 回退**:这两处在 workspace card 错误显示和 Option B init 流程中,不在 FIX-1 的 Option C import 范围内。扩大范围需顾问授权。
- **未 commit/push**:任务卡未明确授权 finalize。

## 异常与自作判断

无偏离。前派实现完整,本派仅做核对+测试验证+回报。

## 实际使用的模型 + 自报 token 用量

`model=kiwiai/qwen3.7-plus, tokens≈N/A(_pi 未提供 token 计数接口;本派工作量小:读 6 文件 + 跑 2 次 pytest + 写 1 文件)`

## 待办 / 移交

- 顾问可考虑:overview.html L281/L502 的 "Unknown error" 回退是否也需 i18n 化(超出 FIX-1 范围,但属同一 "Unknown error 禁止" 主题)
- 6 个预存测试失败需各自卡跟进(与 FIX-1 无关)

下一棒:auditor 跑 → `/claim /home/kiwi/projects/lybra/task_cards/AIPOS-293/AUDIT-REPORT-AIPOS-293R.md`(审计已存在,可先复核);或顾问起草 FIX-1 审计卡 → 落 `task_cards/AIPOS-293/AUDIT-FIX-1.md` 后 `/claim` 之。
