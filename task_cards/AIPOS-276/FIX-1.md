# FIX-1 卡:AIPOS-276 — 按 276R 审计 F-* 清单修复(只修清单+回归)

- **准绳**:AUDIT-REPORT-AIPOS-276R.md 需修复段字面。
- **F-276-1(P1)**:render_publish_record() 增 warnings 参数,调用处传
  validation["warnings"],staleness warning 写入 publish 记录(frontmatter 或 body);
  测试断言:构造超龄地图→publish dry_run+confirm→记录文件内含该 warning 原文。
- **F-276-2(P2)**:真机证据——测试工作区把 project-map.md updated 改为 14 天前,
  重启本地 serve 实测板面红标渲染,证据(HTML 片段/curl 输出原文)入 RETURN。
- 红线:只修清单项+回归,不扩范围;284B 未收编文件禁碰。
- 回报 RETURN-FIX-1.md 到本目录,审计复审接棒。
