# FIX-1 卡:AIPOS-286 — 全量重落(内容被 278F2 旧读回写抹掉)

- **依据**:INCIDENT-STALE-OVERWRITE.md(278 卡目录);准绳=286 原卡 S1-S4 +
  原 RETURN 改动清单(task_cards/AIPOS-286/RETURN.md)。
- 重落:S1 runtime-status 注入主机名/IP;S2 提示词第 0 步(同机确认+连通检测,
  不通 block-and-report);S3 向导页 SSH 橙色提醒;S4 双语+8 条契约测试全绿。
- **注意**:279F1 刚重落的内容(MCP 片段/cc 一行式/QUICKSTART)是盘上现状,禁碰禁覆盖;
  铁律:改任何文件前先重读盘上当前版本。
- 回报 RETURN-FIX-1.md 到 task_cards/AIPOS-286/。
