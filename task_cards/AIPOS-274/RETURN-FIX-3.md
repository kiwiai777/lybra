# RETURN-FIX-3 — AIPOS-274F3 完成回报

## 1. 一句话结论

**完成**。通过/打回按钮从 `<details>` 技术细节折叠内移至站卡 `.vb-details` 底部（`techDetails` 之外），站卡展开即常显。

## 2. 做了什么

- 定位 `vbStationCard()` 函数（`project-detail.html` ~L1660）
- 原结构：`techContent.appendChild(actions)` → actions 嵌套在 `<details>` 技术细节折叠内，需展开两层才见按钮
- 新结构：`details.appendChild(actions)`（在 `details.appendChild(techDetails)` 之后）→ actions 在站卡展开时直接可见，不依赖技术细节折叠状态
- 加注释标记 `// AIPOS-274F3: 按钮移出技术细节折叠,站卡展开即常显`

## 3. 改动清单

| 文件 | 改动性质 |
|------|----------|
| `web/board/static/project-detail.html` | L1672-1675：删除 `techContent.appendChild(actions)`；在 `details.appendChild(techDetails)` 后插入 `details.appendChild(actions)`；加 AIPOS-274F3 注释 |

工作树含其他卡（AIPOS-274F1/F2/F4 等）的未提交修改，本卡仅动上述一处。

## 4. 测试/验证结果原文

```
$ python3 -m pytest web/board/tests/test_owner_verification_e2e.py -x -q
....                                                                     [100%]
4 passed in 0.03s

$ python3 -m pytest web/board/tests/test_aipos274f2_envelope_alignment.py -x -q
.......                                                                  [100%]
7 passed in 3.55s

$ python3 -m pytest web/board/tests/ -q --deselect ...test_local_read_api.py (2 governance tests)
215 passed, 2 deselected (pre-existing failures, confirmed via git stash)
```

pre-existing 失败（`test_local_read_api.py` 2 个 governance 路由测试）经 `git stash` 验证修改前即存在，与本次无关。

## 5. 排除物 + 理由

- 未 commit/push：任务卡未授权 finalize
- 未改 CSS：`.vb-actions` 样式无需调整，已在站卡展开区域正常渲染

## 6. 异常与自作判断

无偏离。

## 7. 实际模型 + 自报 token 用量

`model=qwen3.7-plus, tokens≈15k/3k`

## 8. 待办 / 移交

- 顾问/Owner 眼验：站卡展开状态下按钮应直接可见（不展开"技术细节"即见通过/打回）
- 审计卡落点由顾问决定

下一棒:advisor 眼验 → 打开 project-detail 页面，展开任一核验站，确认按钮在技术细节折叠外常显
