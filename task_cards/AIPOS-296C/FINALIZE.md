# FINALIZE 卡:296C(SSE chunked+confirm_client)+296B(内容协商)联合收编推产品仓
- 授权链:296CR PASS_WITH_NOTES(仲裁 commit 时序误判)+四端口 chunked 实测+本机真 Claude Code
  localhost ✔;tailnet 直连留 mac 终验(owner_verify 待 mac)。296B 的 http_sse 内容协商
  与 296C 的 chunked 在同一工作树,合并收编。
- 范围(精确 pathspec):tools/mcp_server/http_sse.py, tools/mcp_server/tests/test_http_sse_transport.py,
  tools/aipos_cli/confirm_client.py, tools/aipos_cli/tests/test_confirm_client.py,
  task_cards/AIPOS-296B/ task_cards/AIPOS-296C/(-f)
- 步骤:写前重读→git status 核对→diff 抽查(全 SSE/内容协商/confirm_client 范围)→
  commit(注明 296B+296C+仲裁出处)→push→FINALIZE-RETURN.md。禁 add -A。
