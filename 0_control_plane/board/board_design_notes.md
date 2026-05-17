# AI Project OS Board Design Notes

## v0 方向：状态看板与汇报墙
AI Project OS Board 旨在提供一个读取 Git 仓库 Markdown/YAML 数据的统一可视化界面。

## 核心功能
- **任务概览**：显示当前活跃的任务卡（Task Cards）及其状态。
- **汇报流**：展示从 `4_inbox/` 整理出的最新 Agent 汇报。
- **资源监控**：显示本地/云端 Agent 的角色实例状态。
- **记忆地图**：Shared Memory 的目录结构可视化。
- **Resume 快捷键**：展示各 Agent 汇报中最后留下的继续命令。

## 技术选型 (v0)
- **数据源**：以 Git 仓库文件为唯一事实源，暂不引入外部数据库。
- **形态**：可以是一个基于 Markdown 解析的静态 Web 页面或简单的 CLI Dashboard。

## 后续演进
- 引入实时消息推送。
- 集成多渠道聊天室功能。

## v0 Board Boundary
The Board may act as a BBS, discussion board, report wall, or status board. It may display project state, but it does not replace formal project documents. v0 reads Markdown/YAML from the repository and does not introduce a complex database.
