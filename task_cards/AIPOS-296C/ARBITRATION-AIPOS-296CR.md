# 顾问仲裁:296CR FAIL 处置(2026-08-02)——commit 时序误判第 N 例

- **F-296CR-1(P0"改动未提交")不成立**:v4 回路 commit=FINALIZE 阶段动作,return/审计时
  工作树未提交是常态与设计(先例:ARBITRATION-279R/290R;277R 明文"排除物在工作树未
  提交符合 RETURN 声明")。审计误把 finalize 门的事当 return 门缺陷。
- **技术裁决=PASS**:S1-S5 全过,140 测绿,SSE chunked/去 close 符 RFC 7230,confirm_client
  SSE 解析正规化到位。
- **F-296CR-1(P2 chunked 原始帧断言 gap)采纳**:urllib/undici 能解码即证格式正确,不阻断;
  记为 296C finalize 后的轻增强(可并入 296C 收编附带或 285 系批次,不单开卡)。
- **tailnet 直连终验**:卡允许顾问/mac 终验;本机 hairpin 不可验,留 mac 侧(用户拓扑铁律)。
- **系统性根治**:commit 时序误判已第 4-5 次(278R/279R/290R/296CR)——审计 kickoff/skill
  必须内置"v4:未提交=正常,commit 是 FINALIZE 步"一条,不再每次靠顾问仲裁。出卡见 300。
