# FIX-1 卡:AIPOS-279 — Owner 三点修改 + 全量重落(279 内容被 278F2 旧读回写抹掉)

- **铁证**:Owner 会话裁定 2026-07-31 三点 + INCIDENT-STALE-OVERWRITE.md(278 卡目录)。
- **重落基线**:279 原 RETURN 改动清单为蓝本,但内容按以下三点修订后落盘:
  1. **补 Claude Code 命令行接法**:MCP 片段区加 cc CLI 一行式
     (`claude mcp add lybra --transport http <URL> --header "Authorization: Bearer <TOKEN>"`),
     并注明桌面版/命令行均可,现有示意为举例非穷举;
  2. **CLI 自举从"可选"升为标准第二步**:话术改"完整功能(含 agent watch 耳朵/
     claim 全链)需要安装 Lybra CLI——第二步就装",不再"增强能力(可选)";
  3. **Gate URL 动态取**:片段中的 URL 必须来自 serve 实配(advertise 地址,286 的
     runtime 信息源同宗),字面 127.0.0.1 仅在无 advertise 配置时 fallback 并注明
     "同机默认,跨机请用服务端广播地址";断言:配置 advertise 时片段含广播址。
- **红线(事故后)**:改任何文件前必须重读盘上当前版本,禁止凭会话旧读整文件回写;
  QUICKSTART 跨机节一并重落。
- 断言:S1-S3 各一 HTTP/内容断言+原 279 三点回归;回报 RETURN-FIX-1.md。
