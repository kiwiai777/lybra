# FIX-2 卡:AIPOS-274 — 信封层错位:total_tasks 在顶层 summary,前端读 data.summary

- **执行角色**: lybra-executor(pi,经队列卡 AIPOS-274F2 派入)
- **铁证(顾问 raw 探针)**: /api/owner-truth 响应 total_tasks 位于顶层 /summary(=105),
  data.* 下无 summary → 前端(向导让位/计数)读 data.summary 得空 → 向导仍误现;
  验证台 stations 疑同层错位([]).
- **修法(字面)**: 后端把 owner_truth 的 summary **镜像进 data.summary**(顶层保留兼容);
  验证台响应同查同修(stations 应在前端所读层级);**契约测试改打 HTTP 层断言
  data.summary.total_tasks 与 data.stations**(函数层断言不作数——本回归即函数绿路由盲);
  前端不改(以后端对齐前端为准)。
- 验收:S1 顾问 raw 探针:data.summary.total_tasks=105 且待验站含 274;S2 lybra-dev 页
  向导消失;S3 HTTP 层契约测试入套全绿;S4 零回归。回报 RETURN-FIX-2.md。
