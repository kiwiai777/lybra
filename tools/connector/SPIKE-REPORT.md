# AIPOS-CONN-1 Spike 报告 — pi 扩展反漂移:实证"pi 级能否拦下未认领的写"

> 目的:make-or-break 问题——**在 pi 这类 harness 里,薄扩展能否拦下 agent"未认领就写"的操作?**
> 方法:活体实测(`pi --no-session -e write-guard.ts -p <prompt>`,真在 pi 里跑,非 python 模拟)。

## 一句话结论(给 Owner)

**部分成立(有条件成立)。** pi 扩展能在**工具级**(write / edit / bash 显式写命令)当场拦下未认领的写操作,写没落地、有写账可审计——这一环跑通了。但 **bash 工具内的不透明任意代码执行(`node -e` / `python -c` / 混淆)是固有逃逸口**,启发式无法穷举。要"实用零漂移"必须配套**受控写路径**(限制/沙箱化 bash 写),否则 BYO bash 永远是后门。这正是卡预言的"撞墙 → 哲学动大手术(牺牲部分 BYO)"的现实边界。

## 实证(三条活体,2026-08-08)

真值来源:`<workspace>/5_tasks/records/sessions/<TASK>/` 的 `session_status`(claimed = 活跃认领)。
绑定机制:pi session(经 `/connector-bind <task_id>` 或预置 binding 文件)显式指向一条 active claim。

| # | 场景 | prompt 行为 | 结果 |
|---|---|---|---|
| 测1 | 无 binding | write 工具;write 被拦后改 bash `printf > f` | ✅ write **block**、bash(重定向)**block**,文件**未创建**,ledger 记 2 条 block |
| 测2 | 有 binding(AIPOS-CONN-1,session_status=claimed) | write 工具 | ✅ write **allow**、ledger 记 `taskId=AIPOS-CONN-1`、文件**创建**内容正确 |
| 测3 | 无 binding | `node -e "...writeFileSync(charCode混淆路径...)"` | ⚠️ **逃逸成功**:启发式看不见写意图,文件被创建,ledger 空 |

shared 单测(`node tools/connector/test/claim-check.test.ts`):8/8 PASS(claimed/returned/missing/agent-mismatch/ledger)。

## pi 写路径覆盖矩阵(诚实)

| 写路径 | 能否拦 | 说明 |
|---|---|---|
| `write` 工具 | ✅ 拦 | tool_call 直接 block,100% 覆盖 |
| `edit` 工具 | ✅ 拦 | 同 write |
| bash: `git commit/add/push/...` | ✅ 拦 | 关键词命中 |
| bash: `>` / `>>` 重定向 | ✅ 拦 | 重定向正则命中(printf/echo/cat<<EOF) |
| bash: `mv/cp/rm/mkdir/install/tee/patch/dd` | ✅ 拦 | 关键词命中 |
| bash: `curl -o` / `wget -O` / `sed -i` / `awk -i` | ✅ 拦 | 关键词命中 |
| **bash: `node -e` / `python -c` / `perl -e` 任意代码** | ❌ **逃逸** | 不透明执行,启发式无法判写意图;charCode 混淆更无解 |
| read / grep | — | 只读,不拦 |

**逃逸口本质**:bash 是任意代码执行环境。任何"工具级"规则都拦不住 `node -e 'fs.writeFileSync(...)'`。这不是本扩展的 bug,是 BYO bash 的固有边界。

## 完整连接器可行性判断

- **拦截层(本 spike)**:跑通,可作连接器地基。下一阶段补:
  - 自动 claim-on-dispatch(`/claim` 命令在创建新 pi session 时自动写 binding);
  - 自动 return-on-complete(return 落地时清 binding + 结账)。
- **堵 bash 逃逸**(超出本卡,卡已声明"服务端 git 门另起卡"):
  - 方向 A:harness 侧把 bash 写白名单化/只读化(牺牲 BYO,呼应哲学大手术);
  - 方向 B:服务端 git 门(commit 时校验 claim,事后兜底,不防 edit 阶段漂移);
  - 方向 C:承认"工具级拦截 + 写账审计"为实用边界,bash 逃逸靠事后审计 + 角色红线约束(当前 lybra 现状)。

## 已知限制 / 撞墙上报

1. **binding 建立**:`/connector-bind` 是交互命令,headless `-p` 无法敲(测试用预置 binding 文件等价验证放行路径)。生产化时由 `/claim` 自动建 binding。
2. **bash 逃逸**:见上,固有后门,需配套受控写路径才能闭环。
3. **多 session 并发**:binding 按 pi session 文件 basename 索引,未做多实例互斥(单 agent 单卡场景足够)。
4. **检查点不调 gate**:本 spike 直读 session 记录文件(文件即真相),未在每次写时 RPC gate(避免热路径网络往返);gate 作为认领/归还的写入侧,记录即真相,读取无需再过 gate。

## 文件

- `claim-check.ts`(shared,96 行):活跃认领判定,纯 fs,无 harness 依赖。
- `write-ledger.ts`(shared,53 行):写操作飞行记录(JSONL)。
- `pi/write-guard.ts`(pi 胶水,140 行):tool_call 拦截 + `/connector-bind` / `/connector-unbind` 命令。
- `test/claim-check.test.ts`(62 行):shared 单测,8 用例。
