# 中英双语只读盘点(i18n inventory)

> 状态: **只读盘点文档**(P4 第一张卡的设计输入), 不含任何代码改动。行号与命令口径 = 本仓当前分支;
> 所有数字附「复算命令」(均在本仓根执行), 所有结论附 `文件:行` 出处。总量与早期粗算有出入处,
> 以本文复算口径为准, 差异已标注。

## 0. 总量复算(先对齐粗算)

| 口径 | 数字 | 复算命令 |
|---|---|---|
| tools/aipos_cli 非测试 .py 文件数 | 131 | `find tools/aipos_cli -name '*.py' ! -name 'test_*' ! -path '*/tests/*' \| wc -l` |
| 其中含中文的文件 | 119 | 同上命令加 `\| xargs grep -lP '[\x{4e00}-\x{9fff}]' \| wc -l` |
| tools/aipos_cli 非测试 .py 含中行数 | 18,488 | `find …(同上) -print0 \| xargs -0 grep -cP '[\x{4e00}-\x{9fff}]' \| awk -F: '{s+=$2} END{print s}'` |
| tools/ 全部非测试 .py 含中行拆分(157 个文件) | 字符串字面量 7,155 / docstring 7,770 / 注释 4,450 行 | 见 §1.5 AST 拆分脚本 |
| schema/*.schema.json 含中行数 | 3,944(7 个文件) | `grep -cP '[\x{4e00}-\x{9fff}]' schema/*.schema.json \| awk -F: '{s+=$2} END{print s}'` |
| web/board 非测试 py/js/html 含中行数 | 1,306(17 个文件) | `find web/board -type f \( -name '*.py' -o -name '*.js' -o -name '*.html' \) ! -path '*/tests/*' ! -name 'test_*' -print0 \| xargs -0 grep -cP '[\x{4e00}-\x{9fff}]' \| awk -F: '{s+=$2} END{print s}'` |
| 测试 .py 含中行数 | 20,791(433 个文件) | `find . -name 'test_*.py' ! -path './.git/*' -print0 \| xargs -0 grep -cP '[\x{4e00}-\x{9fff}]' \| awk -F: '{s+=$2} END{print s}'` |
| 断言行含中文的测试代码行 | 4,288 | `find tests tools web task_cards -name 'test_*.py' -print0 \| xargs -0 grep -nP 'assert.*[\x{4e00}-\x{9fff}]' \| wc -l` |

与粗算的差异: 早期口径「~15,000 行」只数了 tools/aipos_cli 的含中行(实际 18,488, 差异主要来自 2026-10 以后新增的
board/loop 模块); schema 「~4,000」与复算 3,944 相符; web/board 「~1,400」与复算 1,306 同量级。

## 1. 按受众分面的出声点

四张受众面: ① Owner 面(外部用户第一跑最先撞到) ② 顾问面(驱动 loop 的 CLI 与门交互) ③ 执行/审计面
(开工稿、章程、技能、工位渲染物) ④ 开发面(注释/docstring/日志, **不须翻译**)。同一文件可能同时服务多面
(如 `aipos_cli.py` 是 Owner 命令与顾问命令共用的 CLI 壳), 归属以其主要受众为准并在表内注明。

> 表内单文件含中行数的统一复算口径: `grep -cP '[\x{4e00}-\x{9fff}]' <文件>`(全文含中行); 「字符串含中 N 行」
> 指 §1.5 AST 拆分下该文件的字符串字面量行数(不含 docstring/注释)。

### 1.1 Owner 面

| 出声点 | 机制 | 条数(口径见下) | 出处 |
|---|---|---|---|
| `lybra setup check` 安装自检 | 文案集中在 `config.schema.json setup_check` 声明(labels / pi.install / exit_*, 共 7 items); 代码只做模板渲染 | schema 声明 ~20 键; 代码内字符串 38 行 | `schema/config.schema.json`(setup_check 节); `tools/aipos_cli/setup_check.py`(字符串含中 38 行) |
| `lybra onboarding guide` 十一步接入向导 | **全部硬编码在代码 f-string**, 无 schema texts 声明(verbs.schema 无 lybra_onboarding 条目); 步号唯一声明 `STEP`(第 0 步 preflight + 11 步) | 字符串含中 345 行 | `tools/aipos_cli/onboarding.py:72`(STEP 声明), 全文件字符串含中 345 行 |
| `lybra project new` 脚手架 | 代码内模板 + config.schema 声明混用 | 字符串含中 217 行 | `tools/aipos_cli/workspace_config.py` |
| `lybra owner inbox / home / next-step / recommend / decide` | verbs.schema `lybra_owner_inbox`(22 键) + `relay/texts`(**已双语** zh/en 各 15) + 代码内模板 | schema 22+15 键; 代码字符串 126 行 | `schema/verbs.schema.json`(/verbs/lybra_owner_inbox); `tools/aipos_cli/owner_inbox.py:1699`(relay_texts, docstring 明言「CLI 用缺省语言」) |
| 看板(board) | **唯一成体系的 i18n 双语通道**(见 §2.1): 服务端 `_I18N_TEMPLATES` + 客户端 `i18n.js` | 服务端 573 键 ×2(zh/en, AST 复算); 客户端 268 键 ×2 | `web/board/app.py:681`(_I18N_TEMPLATES), `web/board/static/i18n.js:24-666` |
| 看板登录页 | **硬编码中文、无 data-i18n 标签**(登录流程整体被守卫测试豁免) | login.html 可见中文 34 行(非注释行复算) | `web/board/static/login.html:6,13,20,22`; 豁免声明 `web/board/tests/test_aipos288_cjk_source_guard.py:35` |
| 部署 / serve 运行时 | 代码内中文消息(状态、PATH 固化提示) | 字符串含中 44 行 | `tools/aipos_cli/deploy_runtime.py` |
| 面向外部用户的文档 | **已是双语成对文件**(README/QUICKSTART 各 en + zh-CN 两份, 互链); 产品页 `docs/index.html` 内置客户端 EN/中文 切换(data-lang 按钮 + body.lang-zh 类) | README 180/167 行, QUICKSTART 342/301 行(en/zh); index.html 37 含中行 | `README.md:18`(语言互链行), `QUICKSTART.md:8`, `docs/index.html:120,320-338`(切换机制) |

Owner 面小结: **看板已双语, 面向用户的文档已双语, 但 CLI 侧(setup check / onboarding guide / project new /
owner inbox)全部单语中文**, 其中 onboarding guide 是外部用户第一跑的必经入口。

### 1.2 顾问面

| 出声点 | 机制 | 条数 | 出处 |
|---|---|---|---|
| CLI 壳(所有 `lybra` 子命令的参数 help、错误、横幅) | argparse help 字符串硬编码; 部分错误文案集中在 verbs.schema 各动词 `errors` 码表 | 字符串含中 616 行(aipos_cli.py 全文件含中 1,206) | `tools/aipos_cli/aipos_cli.py` |
| `lybra next / next --run / loop`(推导与执行输出) | 代码内中文模板(next_resolver 是唯一推导核), 部分话术读 verbs.schema texts | next_resolver 567 + loop_driver 324 + loop_run_record 314 行 | `tools/aipos_cli/next_resolver.py`, `tools/aipos_cli/loop_driver.py`, `tools/aipos_cli/loop_run_record.py` |
| `lybra kickoff`(顾问派工提示) | verbs.schema `lybra_kickoff.texts` 单语扁平键 | 71 键(+validation_root_texts 31 键) | `schema/verbs.schema.json`(/verbs/lybra_kickoff) |
| 门交互 CLI 壳(`lybra confirm / gate / queue / records`) | 拒因主要来自 transitions.schema guards(error_message 42 处 + next_step 34 处)与门服务端; CLI 侧渲染 | board_adapter 字符串 336 行 | `tools/aipos_cli/board_adapter.py`; `schema/transitions.schema.json`(guards.*.error_message / next_step) |
| 门服务端(信封、认领、审计裁决拒因) | 代码内中文错误信封(`_error_result` / `_queue_claim_error` 等) + schema guards 文案 | tools.py 含中 343 行 + http_sse.py 47 行 | `tools/mcp_server/tools.py`(信封额度预警文案示例 ~line 2383) |
| verbs.schema 其余单语 texts | 集中声明、单语中文(见 §2.4 总表) | 31 块 501 键; errors 码表 5 块 84 处 message 全中文 | `schema/verbs.schema.json` |
| 顾问技能手册(随工位分发) | Markdown 母本, 单语 | agents/skills/* 10 个文件 1,067 含中行, 最大 advisor-commands 644 行 | `agents/skills/advisor-commands/SKILL.md` 等(agents/ 下全部 .md = 17 文件 1,592 含中行, 含章程母本) |

### 1.3 执行 / 审计面

| 出声点 | 机制 | 条数 | 出处 |
|---|---|---|---|
| 开工稿(`/go` 后产品给 executor/auditor 的提示) | verbs.schema `lybra_my_tasks.kickoff.template / template_no_worktree / template_governance_root` **单语中文模板** | 3 模板 + report_field_line 等配套键 | `schema/verbs.schema.json:2442-2445` |
| 章程(AGENTS.md 母本与渲染物) | Markdown 母本单语 + charter_render 代码渲染 | 母本 advisor 238 / executor 79 / auditor 117 含中行; charter_render.py 字符串 103 行 | `agents/roles/{advisor,executor,auditor}/AGENTS.md`; `tools/aipos_cli/charter_render.py` |
| 验证根 / run-long 话术(开工稿内嵌) | 代码内模板 | validation_root.py 字符串 328 行 | `tools/aipos_cli/validation_root.py` |
| 交回契约拒因(门侧 artifact_ingest) | transitions.schema `artifact_ingest.*.texts`(return 拒因、credential_guard 等) 单语 | artifact_ingest.py 字符串 166 行 + schema 21 块 183 键 | `tools/aipos_cli/artifact_ingest.py`; `schema/transitions.schema.json` |
| 注册码交付文案 | 代码内唯一渲染 + 门 enroll_code 动词说明 | enrollment.py 字符串 103 行 | `tools/aipos_cli/enrollment.py`(AIPOS-F93 件②) |

### 1.4 开发面(不须翻译)

docstring 7,770 行 + 注释 4,450 行(合计 12,220, 占 tools/ 全部非测试 py 含中行 19,375 的 63%),
按业界惯例不翻译; 翻译范围只应覆盖「用户可见字符串字面量」(7,155 行, 其中相当部分是开发面日志/异常
内部消息, 真正面向受众的出声点见上三节的条数)。

### 1.5 AST 拆分口径(复算脚本)

字面量/docstring/注释三分类的统计基于 Python ast 模块(docstring 行归属 = `ast.get_docstring` 节点行区间;
字符串行 = `ast.Constant` 字符串节点行区间; 其余含中行 = 注释)。核心逻辑(可粘回复算):

```python
import ast, re
from pathlib import Path
cjk = re.compile(r'[\u4e00-\u9fff]')
files = [p for p in Path('.').glob('tools/**/*.py')
         if 'test' not in p.name and '/tests/' not in str(p)]
s_total = d_total = c_total = 0
for p in files:
    src = p.read_text(encoding='utf-8'); tree = ast.parse(src)
    doc = {ln for n in ast.walk(tree) if isinstance(n,(ast.Module,ast.FunctionDef,ast.AsyncFunctionDef,ast.ClassDef))
           and (d:=ast.get_docstring(n,clean=False)) and (e:=n.body[0])
           for ln in range(e.lineno, getattr(e,'end_lineno',e.lineno)+1)}
    strs = {ln for n in ast.walk(tree) if isinstance(n,ast.Constant) and isinstance(n.value,str)
            for ln in range(n.lineno, getattr(n,'end_lineno',n.lineno)+1)}
    for i,l in enumerate(src.splitlines(),1):
        if not cjk.search(l): continue
        if i in doc: d_total+=1
        elif i in strs: s_total+=1
        else: c_total+=1
print(s_total, d_total, c_total)
```

## 2. 已有机制盘点

### 2.1 看板 i18n 单通道(已集中, 双语, 有守卫)

- 服务端: `web/board/app.py:668-681` — AIPOS-286 FIX-2 建立的服务端 i18n 生成通道, 约束写在注释里:
  **模板键必须 zh/en 同时存在, 缺 en 键 = 测试失败(不是静默回退)**; 取用一律经
  `_generate_text(template_key, locale, **vars)`(`web/board/app.py:2053`), 未支持的 locale 回退 zh
  (`web/board/app.py:2077`)。字典 `_I18N_TEMPLATES`(`web/board/app.py:681`)zh/en 各 573 键(AST 复算)。
- 客户端: `web/board/static/i18n.js` — 顶部红线注释声明「漏译不可能」纪律, zh(24-343 行)/en(345-666 行)
  各 268 键。
- 守卫三件:
  - `web/board/tests/test_aipos288_cjk_source_guard.py` — 产品界面代码禁裸中文字面量, 豁免 = i18n 字典 +
    `// i18n-exempt: <理由>` 行标记 + 文件级豁免(login.html / auth-chrome.js, 登录流程在范围外);
  - `tests/test_aipos_f214_guard_ratchets.py:365`(棘轮 R11)— web/board **字符串字面量**含 CJK 只减不增,
    基线 `tests/f214_cjk_literal_baseline.jsonl` 当前 **0 条**(= 新增界面中文零容忍, 与注释无关);
  - `web/board/tests/i18n-bilingual-stability.test.js` / `i18n-api-contract.test.js` — 双语键稳定性契约。
- 同一数据双端: 看板三张只读页 / 信封页 / 新建项目页的文案「走看板 i18n 单通道
  web/board/app.py _I18N_TEMPLATES(zh / en 同键)」(verbs.schema board_readonly_pages /
  board_owner_envelopes / 新建项目写例外 declarations 原文)。

### 2.2 F4 守卫严重级声明(已集中, 单语)

`schema/transitions.schema.json:23` `severity_semantics`: auto_recoverable → warn 级、needs_human/bug →
error 级; 全文件 `error_message` 声明 42 处、`next_step`(含 audience 字段指向顾问/Owner 的下一步动作)34 处。
守卫文案**单源在 schema**, 门与 CLI 都从声明渲染——这是「文案声明化」在拒因面的现有范本。

### 2.3 F13 出口函数 voice()(已退役的历史机制)

F13 的 voice()/flushVoiceBuffer() 只存在于 TS 时代的 loop 扩展, 已随 AIPOS-F91 件②老扩展整体退役
(复算: `git log --oneline -S flushVoiceBuffer -- extensions/` 最后一个触碰提交即退役提交)。**今天 CLI/门侧
没有统一出声管道**: 出声 = 各模块 return 的中文串 + `aipos_cli.py` 内散布的 print(直接 print 调用中含中文
仅 367 行(复算: `find tools -name '*.py' ! -name 'test_*' ! -path '*/tests/*' -print0 | xargs -0 grep -nP
  '^\s*(print|sys\.stdout\.write|sys\.stderr\.write)\(.*[\x{4e00}-\x{9fff}]' | wc -l`), 但「模块返回中文串由 CLI 打印」的模式覆盖 7,155 行字符串字面量)。

### 2.4 schema *texts* 声明总表(集中声明; 双语仅 3 块)

复算(遍历各 schema JSON 中键名为 texts/carry_texts/*_texts 的块, 双语 = 值为 {zh:{…},en:{…}} 两层表):

| schema | 单语扁平键块 | 单语键数 | 双语块 |
|---|---|---|---|
| verbs.schema | 31 块 | 501 键 | 3 块: board_owner_envelopes/tabs/texts(13×2), lybra_owner_inbox/relay/texts(15×2), lybra_project_workstation_plan/texts(121×2) |
| transitions.schema | 21 块 | 183 键 | 0 |
| enums.schema | 1 块 | 10 键 | 0 |
| config.schema | 2 块 | 12 键 | 0 |
| distribution.schema | 1 块 | 21 键 | 0 |

另: verbs.schema `errors` 码表 5 块共 84 处 `message` 全中文; 双语块的语言表读取有现成范本
`tools/aipos_cli/workstation_plan.py:117-122`(`texts(lang)`: 未知语言回退 locale.default)。

### 2.5 集中 vs 散落结论

- **已集中且双语**: 看板(app.py _I18N_TEMPLATES + i18n.js)、verbs.schema 3 块双语 texts、
  README/QUICKSTART 文档对、docs/index.html 客户端切换。
- **已集中但单语**: schema texts 56 块 ~727 键、errors 码表 84 处、transitions guards(error_message 42 /
  next_step 34)、config.schema setup_check 文案、章程/技能/开工稿模板。
- **散落**: onboarding.py(345 行向导文案, 无声明)、aipos_cli.py help 与错误(616)、next_resolver(567)、
  validation_root(328)、loop_run_record(314)、deploy_runtime(44) 等代码内字符串; login.html(文件级豁免)。

### 2.6 同一句话多处重复(跨文件 ≥2 处, 取样)

复算口径: 非测试 py 中长度 ≥10 字符、单行、无 `{}` 占位、含中文的字符串字面量, 跨 ≥2 文件重复共 **77 条**
(脚本 = §1.5 的遍历骨架 + `collections.defaultdict(set)` 按值收集后取 len(fs)≥2)。样例:

1. `须有 int status 与非空 message` — 5 个文件(board_enroll_code.py / board_project_new.py / envelope_board.py 等)
2. `.texts 未声明齐` — 4 个文件(history_search.py / loop_run_record.py / queue_mutations 相关等)
3. `queue 目录中找不到任务卡 {task_id}` — 4 个文件(artifact_ingest.py / card_render.py / loop_driver.py 等)
4. `⚠ 落账未完成(写入已生效, 勿重复执行写命令):` — 3 个文件(board_adapter.py / envelope_mint.py / loop_run_record.py)
5. `已存在(同秒), 只追加不覆盖; 出口: 稍后重跑` — 3 个文件(advisor_headline.py / advisor_updates_cli.py / owner_*)

> 含义: 翻译前先做一轮**同句归并**(77 条候选), 否则同一句要译多遍且易漂移; 同文件内散落(如
> `aipos_cli.py` 内同一错误模板出现 9 次)不在此列但同理。

## 3. 语言选择现状与候选

### 3.1 现状(唯一机制 = 看板)

- **唯一全局语言声明**: `schema/verbs.schema.json:804-810` `board_readonly_pages.locale` —
  `param=lang, default=zh, supported=[zh,en], storage_key=lybra_lang`(浏览器 localStorage 持久化)。
  消费点: `web/board/readonly_pages.py:39-54`(查询参数 → 页面语言)、`web/board/app.py:2053-2077`
  (_generate_text 回退链)。
- **CLI 侧仅两个语言入口**, 都复用看板声明:
  - `lybra project workstation-plan --lang`(help 原文: "Text language (board locale.supported; default
    board locale.default)")— `tools/aipos_cli/aipos_cli.py:3416`;
  - owner_inbox 的 `relay_texts(lang)` / `choice_script(lang)`(双语 texts 已备, 但 **CLI 调用不传 lang =
    恒用缺省 zh**; docstring 原文「CLI 用缺省语言」)— `tools/aipos_cli/owner_inbox.py:1699-1705`。
- **无**全局环境变量、**无** home 级配置键、**无** project.json 语言字段、**无**每实例(注册码/工位)语言属性。

### 3.2 候选(只列事实与取舍, 不替 Owner 决策)

| 候选 | 落点 | 事实依据 | 取舍 |
|---|---|---|---|
| A. home 级配置 | `<home 根>/.lybra/` 或 home 级配置文件新增 `language` 键 | home 根已是项目注册与凭据所在(`owner_inbox.home_projects` 枚举) | 一次设置全局生效; 但 CLI 命令跨项目执行, 单一用户多项目混语言时不够细; 与 storage_key=lybra_lang(浏览器侧)需明确优先序 |
| B. project.json 每项目 | `project.json` 新增顶层键(config.schema 现无 language 节) | 项目级声明是本仓强纪律(schema 声明驱动实现) | 最贴合「一个项目一个受众群」; 但 onboarding guide / setup check 发生在 project.json 创建**之前**, 覆盖不到第一跑 |
| C. 环境变量(如 LYBRA_LANG) | 进程环境 | bin/lybra 是 Node 启动器转 `python -m tools.aipos_cli.aipos_cli` | 对 CI/脚本与一次性命令最顺手、实现面最小; 但不持久、不进治理记录, 工位(agent 会话)需要每次注入 |
| D. 每实例(注册码/enrollment/工位) | enrollment 声明或工位 .lybra | 顾问/执行/审计实例已各自有 enrollment 身份 | 才能做到「同一项目, Owner 看 en、顾问看 zh」的分工; 但语言是「读者属性」不是「实例属性」, 顾问替 Owner 跑命令时语义含混 |
| E. 既有看板 locale 声明升格全局 | verbs.schema locale 声明被 CLI 侧复用(现状 workstation-plan --lang 已如此) | `workstation_plan.texts(lang)` 已示范「CLI 读看板声明」 | 零新声明; 但语义上把 CLI 语言绑死在「看板只读页」的声明段下, 命名易误读 |

## 4. 建议分期(每期给出文件与条数口径、依赖、风险)

> 前提共识(来自 §1.4/§2): 只译「用户可见字符串」, 开发面(docstring/注释 12,220 行)不译;
> 散落文案先声明化(texts 化)再翻译, 不在代码里就地嵌 en 串。

- **第 0 期(机制预备, 零翻译)**: 定盘语言选择键(§3.2 择一, Owner 拍板)并把 verbs.schema locale 缺省源
  与新键的关系声明化; 给 schema texts 定双语结构标准(zh/en 两层表, 范本 =
  `lybra_project_workstation_plan/texts` + `workstation_plan.py:117` 读取器); 做同句归并(§2.6 的 77 条
  重复先合并为单点); 测试断言解耦启动(见风险节)。产出 = 声明 + 判据, 无文案变化。
- **第 1 期(Owner 面, 外部用户第一印象)**: onboarding.py 345 行(向导文案声明化 + 双语)、setup_check
  (schema 声明补 en, ~20 键)、owner_inbox 其余单语 texts(22 键)+ relay_texts 的 CLI 侧接 lang、
  deploy_runtime.py 44 行、login.html 决策(纳入通道或维持豁免)。README/QUICKSTART/看板主体已双语,
  本期零成本。依赖: 第 0 期的语言键与 texts 结构标准。
- **第 2 期(顾问面)**: verbs.schema 31 块单语 texts(501 键)+ errors 码表 84 处补 en; next_resolver 567 /
  loop_driver 324 / loop_run_record 314 / board_adapter 336 行声明化后双语; 门侧 transitions guards
  (error_message 42 / next_step 34)与 mcp_server/tools.py 343 行。依赖: 第 0 期; 风险面最大(见下)。
- **第 3 期(执行/审计面)**: 开工稿三模板(verbs.schema lybra_my_tasks.kickoff)+ lybra_kickoff texts
  (71+31 键)、validation_root.py 328 行、artifact_ingest 拒因(183 键中归属 transitions 的部分)、
  章程/技能是否双语(**未决问题 3**, 因渲染物随母本分发, 单语母本 + 译文副本会引入漂移)。
- **第 4 期(收尾与棘轮固化)**: 登录页/残留散点、把 R11 棘轮从 web/board 推广到 tools 侧
  (零基线只减不增)、文档对同步纪律(README/QUICKSTART 互链行)复核。

**贯穿风险: 测试断言中文字面 4,288 行**(口径见 §0 表), 改文案 = 成片改断言。前十名文件(复算:
`find tests tools web task_cards -name 'test_*.py' -print0 | xargs -0 grep -cP 'assert.*[\x{4e00}-\x{9fff}]' |
awk -F: '$2>0' | sort -t: -k2 -rn | head`): tools/lybra_tui/tests/test_tui_app.py(58)、
tests/test_aipos_f176_project_upgrade.py(49)、test_aipos_f170_inbox_cleanup.py(43)、
test_aipos_f231_stop_pause_failure_visible.py(42)、test_aipos_f175_verification_owner_check.py(42)、
test_aipos_f174_board_readonly_pages.py(42)、test_aipos_f232_cleanup_sweep.py(41)、
test_aipos_f126_runall_isolation.py(41)、test_aipos_f66b_project_scoped_distribution.py(38)、
test_aipos_f168_owner_inbox.py(38)。建议第 0 期起断言改为**键或码**而非中文串(4,288 行中相当部分断的是
schema texts 键, 改造面可控); 不解耦则每期都要同步改数十个测试文件。

## 5. 未决问题(需 Owner/顾问拍板, 本卡不下结论)

1. **受众优先序**: 先 Owner 面(对外第一印象)还是先顾问面(内部效率), 决定第 1/2 期先后。
2. **语言选择键落点**: §3.2 的 A–E 五候选择一(或组合, 如「project.json 缺省 + CLI 参数覆盖」);
   与看板 storage_key 的优先序也需一并声明。
3. **章程/技能/开工稿要不要翻**: 渲染物随母本原样分发(技能不渲染, skills 声明原文), 双语意味着双母本
   或译文层, 治理成本高; 也可以只译开工稿(机器模板)不译章程(人写治理文本)。
4. **schema texts 单语→双语的迁移语义**: 缺 en 键 = 拒(看板 _I18N_TEMPLATES 纪律)还是回退 zh(
   workstation_plan.texts 现状)? 两范本并存, 需统一。
5. **测试断言解耦范围**: 4,288 行断言随翻译全改, 还是只改「断言用户可见文案」的子集?
6. **login.html**: 纳入看板 i18n 通道(移除文件级豁免)或维持「登录流程单语」的既定范围豁免。
