# 部署生效验证标准 (AIPOS-369)

## 目的

确保 finalize 类任务卡在部署后,gate **真的**在运行最新代码,而不是:
- 工作树验证(import 测试)冒充 live gate 验证
- `.deploy/current` symlink 未切换但报告"已生效"
- gate 进程未重启或重启失败但静默

## 强制要求(S2: Live 端点验证)

finalize 任务的部署生效证据**必须包含**:

### 1. `lybra-deploy verify` 完整输出

```bash
$ tools/lybra-deploy verify
[lybra-deploy] Verifying deployment integrity (AIPOS-369)...

Working tree HEAD:    <full-commit-hash>
Current deployment:   <full-commit-hash>

[lybra-deploy] ✓ Symlink verification: current == HEAD

[lybra-deploy] Querying live gate via HTTP...
Live gate commit:     <full-commit-hash>
Live gate running from: /home/kiwi/projects/lybra/.deploy/releases/<timestamp-hash>

[lybra-deploy] ✓ Live gate HTTP verification: running HEAD commit

[lybra-deploy] === All checks passed ===
[lybra-deploy] Deployment is consistent: working tree, current symlink, and live gate all at <commit-hash>
```

这个输出证明了三件事:
- **S1 断言**: `.deploy/current` 的 VERSION 文件 == git HEAD (symlink 切换成功)
- **S2 HTTP 端点**: live gate 通过 `lybra_gate_version` MCP 工具报告的 commit == HEAD
- **一致性**: 工作树、部署快照、live 进程三者对齐

### 2. 禁止的伪证据

以下**不能**作为"部署生效"的证据:

❌ **工作树 import 验证**:
```bash
# 错误示例 — 这只证明工作树代码可 import,不证明 gate 在跑它
cd /home/kiwi/projects/lybra && python3 -c "import tools.mcp_server.tools"
```

❌ **只检查 symlink**:
```bash
# 不足 — symlink 可能已切换,但 gate 进程未重启
readlink .deploy/current
```

❌ **systemd 服务状态**:
```bash
# 不足 — 进程活着不等于跑的是新代码
systemctl --user is-active lybra-dev-gate.service
```

## 自动化保障

`lybra-deploy` 脚本已内置保障(AIPOS-369):

- **Step 3 后**: 强制断言 `.deploy/current` 的 git_commit == HEAD,不等则 FAIL loud + 回滚
- **Step 5**: 通过 HTTP 调用 `lybra_gate_version`,验证 live gate 报告的 commit == HEAD,不匹配则 FAIL

部署失败时,脚本会:
1. 大声报错(stderr 输出)
2. 非零退出码
3. 自动回滚到 previous release

## finalize 流程集成

在 finalize 类任务卡中,部署步骤应为:

```bash
# 1. Commit 代码
git add <精确 pathspec>
git commit -m "..."

# 2. 部署(自动验证)
tools/lybra-deploy

# 3. 验证并捕获证据
tools/lybra-deploy verify > /tmp/deploy-verification.txt

# 4. 将验证输出贴入 RETURN / FINALIZE-EVIDENCE
```

## 历史教训

- **实证(337U/337Z/364/294/367B)**: 部署后 `.deploy/current` 多次未指向最新 release,gate 跑旧快照,但执行体报"current==HEAD 已生效"——实为在工作树 PYTHONPATH 验证,非 live gate
- **336 断头教训**: 进程内 import 验证无法证明 live 服务状态

## 相关工具

- `tools/lybra-deploy deploy`: 部署 + 自动验证(内置 S1/S2 断言)
- `tools/lybra-deploy verify`: 独立验证命令,生成可粘贴证据
- `tools/lybra-deploy status`: 查看当前部署版本与 gate 状态
- MCP 工具 `lybra_gate_version`: HTTP 端点,返回 gate 运行的 git commit

## 验收标准

finalize RETURN 中的部署证据必须能回答:

1. ✅ `.deploy/current` 指向的 release 的 git_commit 是什么?
2. ✅ 这个 commit 是否等于工作树 git HEAD?
3. ✅ live gate 通过 HTTP 端点报告的 commit 是什么?
4. ✅ 三者是否一致?

只回答前 2 个 = 不足;只有全部 4 个才能证明"部署生效"。
