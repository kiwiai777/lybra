# FINALIZE2-RETURN — AIPOS-274 收编补遗(tools/aipos_cli 三文件)

- **任务卡**: AIPOS-274FZ2
- **执行角色**: lybra-executor (pi, session_AIPOS-274FZ2_20260730_135759_exec-lybra-kiwiai-dev)
- **性质**: FINALIZE.md pathspec 漏列 3 文件的补遗收编
- **状态**: ✅ 完成

## 执行步骤

1. **diff 核对**: `git diff -- tools/aipos_cli/records.py tools/aipos_cli/verify_bench.py tools/aipos_cli/draft_validator.py`
   - 三文件所有改动均带 `AIPOS-274` / `AIPOS-274F1` / `AIPOS-274F2` 注记
   - 无非 274 范围改动 → 放行
2. **精确 pathspec commit**(禁 `add -A`):
   ```
   git add tools/aipos_cli/records.py tools/aipos_cli/verify_bench.py tools/aipos_cli/draft_validator.py task_cards/AIPOS-274/FINALIZE-2.md task_cards/AIPOS-274/FINALIZE2-RETURN.md
   ```
3. **commit message**: `finalize(AIPOS-274): 收编补遗 — 顾问 pathspec 漏列 tools/aipos_cli 三文件(records/verify_bench/draft_validator)`
4. **push**: `git push origin main`

## 改动文件清单

| 文件 | 改动摘要 |
|------|----------|
| `tools/aipos_cli/records.py` | AIPOS-274F1: 新增 `_build_owner_verification_record` + `expected_owner_verification_record_path`; `load_records` 加载 owner_verifications; `find_records_for_task` 暴露 owner_verifications |
| `tools/aipos_cli/verify_bench.py` | AIPOS-274: 人话清单 `_extract_owner_verify_checklist` 优先; AIPOS-274F1: 已核验即退站(owner_approved 排除逻辑); AIPOS-274F2: summary 镜像到 data.summary |
| `tools/aipos_cli/draft_validator.py` | AIPOS-274: 新增 `OPTIONAL_OWNER_VERIFY_FIELDS`(owner_verify_checklist, owner_verify_preview) |
| `task_cards/AIPOS-274/FINALIZE-2.md` | 本卡(已存在,纳入 commit) |
| `task_cards/AIPOS-274/FINALIZE2-RETURN.md` | 本 RETURN |

## 实际模型与 token 自报

- **模型**: (由 pi 运行时决定,executor 未指定)
- **token 用量**: executor 无直接 token 计数器,以 pi 运行时账本为准

## 证据

- **commit hash**: `c10abec`
- **push 输出**: `+ f2952bd...c10abec main -> main (forced update)` (origin git@github.com:kiwiai777/lybra.git, force-with-lease 因 amend 回填证据)
- **staged files (5)**:
  - `task_cards/AIPOS-274/FINALIZE-2.md` (+13)
  - `task_cards/AIPOS-274/FINALIZE2-RETURN.md` (+38)
  - `tools/aipos_cli/draft_validator.py` (+6)
  - `tools/aipos_cli/records.py` (+74)
  - `tools/aipos_cli/verify_bench.py` (+89 -9)
- **diff 核对结论**: 三源文件所有改动均带 AIPOS-274/F1/F2 注记,无非 274 范围改动
