# AIPOS-FND-12: 派工使用指南 — 根治手动挡裸干

## 问题背景

**旧方式(裸干风险)**:Owner 手动派工时直接贴任务卡的**文件路径**,执行体直接读文件就能看卡正文,绕过了 gate 认领流程:

```bash
# ❌ 旧方式:直接贴文件路径 (绕过 gate,无认领记录,账外工作)
/home/kiwi/ai-project-os/2_projects/lybra/5_tasks/queue/pending/aipos-xxx.md
```

执行体直接 `read` 就能看到卡正文,绕过了:
- gate claim 记录
- PreAuthorized 信封检查
- session 记录生成
- 能力账本计费

**后果**:账外工作,gate 无记录,无法追溯,无法回收。

## 正确方式:派工命令化

**新方式**:Owner 贴**派工命令**,执行体跑命令自动走 claim→materialize 流程:

```bash
# ✅ 新方式:派工命令 (强制经 gate 认领)
lybra dispatch AIPOS-XXX --to exec.lybra.kiwiai-dev
```

**输出**(Owner 贴给执行体的):
```bash
lybra agent materialize --task-id AIPOS-XXX --actor exec.lybra.kiwiai-dev \
  --owner-policy-ref pol_lybra_dev_8 --gate-url http://127.0.0.1:7118 \
  --connection-json /path/to/.lybra/connection.json --material-root ~/.lybra/work
```

执行体跑这个命令,**强制**走完:
1. **claim** (gate 记录,信封检查)
2. **拉正文** (经认证信道)
3. **本地材料化** (`~/.lybra/work/<task-id>/card.md`)
4. **干活** (读本地卡)
5. **交回** (`lybra agent pushback`,gate 记录)

## 使用示例

### Owner 派工(生成命令)

```bash
# 基础用法
lybra dispatch AIPOS-FND-12 --to exec.lybra.kiwiai-dev

# 指定 workspace(自动解析 gate/connection.json)
lybra dispatch AIPOS-FND-12 --to exec.lybra.kiwiai-dev \
  --workspace-root ~/ai-project-os/2_projects/lybra

# JSON 输出(含 usage_hint)
lybra dispatch AIPOS-FND-12 --to exec.lybra.kiwiai-dev --json

# 显式指定 gate/policy
lybra dispatch AIPOS-FND-12 --to exec.lybra.kiwiai-dev \
  --gate-url http://127.0.0.1:7118 \
  --owner-policy-ref pol_custom \
  --connection-json ~/.lybra/connection.json
```

### 执行体领卡并干活

执行体收到 Owner 的派工命令后:

```bash
# 1. 认领并材料化(自动 claim,拉正文到本地)
lybra agent materialize --task-id AIPOS-XXX --actor exec.me \
  --owner-policy-ref pol_lybra_dev_8 --gate-url http://127.0.0.1:7118 \
  --connection-json ~/.lybra/connection.json --material-root ~/.lybra/work

# 输出:
#   材料化成功,卡正文已落地:
#     ~/.lybra/work/AIPOS-XXX/card.md
#   完成后写交回正文到:
#     ~/.lybra/work/AIPOS-XXX/RETURN.md

# 2. 读本地卡并干活
cat ~/.lybra/work/AIPOS-XXX/card.md  # 按卡内要求实现

# 3. 写交回正文
echo "完成情况..." > ~/.lybra/work/AIPOS-XXX/RETURN.md

# 4. 交回(pushback 读本地 RETURN.md 并推回 gate)
lybra agent pushback --task-id AIPOS-XXX --actor exec.me \
  --owner-policy-ref pol_lybra_dev_8 --gate-url http://127.0.0.1:7118 \
  --connection-json ~/.lybra/connection.json
```

## 验收保证

### 1. 执行体不经认领拿不到卡正文

**直接读队列文件**:能看到 frontmatter,但拿不到 `claim_id`/`active_session_id`(这些由 gate 在 claim 时动态生成),**无法走完交回流程**。

**materialize 内部流程**:
```python
# agent_materialize.py:materialize()
claim = client.call_tool("lybra_queue_claim_dry_run", ...)
if not claim.get("ok"):
    return {"ok": False, "phase": "claim", ...}  # claim 失败,不拉正文

# claim 成功后才拉正文
preview = client.call_tool("lybra_task_preview", {"include_body": True})
body_markdown = preview["data"]["body_markdown"]
# 落本地
write(material_dir / "card.md", body_markdown)
```

**claim 失败场景**:
- 任务不在 PreAuthorized 信封内
- 信封已达 max_tasks 上限
- 信封已过期
- 任务已被别人认领
- actor 不匹配 assigned_to/agent_instance

### 2. 认领成功→材料化→干活→交回,全有 gate 记录

- **claim 记录**: `5_tasks/records/claims/<claim_id>.json`
- **session 记录**: `5_tasks/records/task_sessions/<task_id>/<session_id>.json`
- **return 记录**: `5_tasks/records/returns/<return_id>.json`
- **能力账本**: `actual_model` 自报,喂计费

### 3. harness 无关(pi/cc/codex 都走同一套)

materialize/pushback 是**读写本地文件**的纯命令行工具,任何能跑 bash 的 agent harness 都能用:
- Pi coding agent
- Claude Code
- Cursor Composer
- 任何能执行 shell 命令的 agent

## 迁移指南(Owner)

**旧习惯**:
```bash
# ❌ 贴文件路径
cd ~/ai-project-os/2_projects/lybra
请执行这张卡: 5_tasks/queue/pending/aipos-xxx.md
```

**新习惯**:
```bash
# ✅ 生成并贴派工命令
lybra dispatch AIPOS-XXX --to exec.lybra.kiwiai-dev
# 把输出的 `lybra agent materialize ...` 命令贴给执行体
```

**为什么必须改**:
- 旧方式绕过 gate,无认领记录,无法追溯
- 新方式强制经 gate,claim 失败(信封外/已被认领)立即拦截
- gate 侧 366 withhold body 只在"经连接器取 body"时生效,手动读文件绕不过

## 参考

- **卡**: `AIPOS-FND-12` (本卡)
- **材料化**: `AIPOS-363` (claim→拉正文→本地落卡)
- **交回**: `AIPOS-363 S2` (pushback→gate 记录)
- **信封**: `AIPOS-366` (PreAuthorized 自治,withhold body)
- **集成测试**: `tools/aipos_cli/test_dispatch_integration.py`
