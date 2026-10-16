# 路线图声明(roadmap.yaml)

AIPOS-F171。每个项目在治理根放一份结构化路线图: 按 lane(子项目)分组的里程碑, 每个里程碑带可核对的验收标准。卡出的时候挂到对应里程碑,
产品据此算进度、标出游离卡与偏差(`lybra roadmap status`, 见下「进度与偏差」; 看板页用同一份声明与同一推导)。

- **落点**: `project.json` 的 `paths.roadmap_file`, 缺省 `governance/roadmap.yaml`(相对治理根, 也可写绝对路径;
  改落点用 `lybra project set-paths --key roadmap_file --value <路径>`)。文件不存在 = 项目未声明路线图, 出卡不提示游离卡, 行为不变。
- **文件形的唯一声明**: `schema/config.schema.json` → `governance_structure.roadmap`(键、id 式样、值域、可改字段、拒因码)。
- **改动只经命令**(两阶段: 缺省预演, `--confirm` 才写; 写后自动生成 decision_log 指针并落账), 文件由产品序列化, 手改会在下次写入时丢注释。

## 格式

```yaml
milestones:
- id: M1                      # 项目内唯一(里程碑与标准同一命名空间), 式样 ^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$
  lane: core                  # 所属 lane(子项目)
  title: 一句人话目标
  target_date: '2026-11-30'   # 可选, YYYY-MM-DD
  status: active              # 可选: planned(缺省) | active | done | dropped
  criteria:                   # 可选
  - id: M1-C1
    statement: 可核对的一句话
    verify: auto              # auto(审计 PASS 且有运行证据) | artifact(另需产物) | owner_check(另需 Owner 抽查)
```

顶层另可有 `description`(说明文字)。不认识的键、缺必填键、id 重复、值不在值域 = `ROADMAP_INVALID`(逐条列出), 编辑命令拒写, 出卡挂接拒校验。

## 命令

```bash
lybra roadmap show [项目] [--lane <lane>] [--json]          # 只读: 里程碑 / 标准 / 各自挂接卡数 / 游离卡
lybra roadmap add-milestone [项目] --id M1 --lane core --title "一句话目标" [--target-date YYYY-MM-DD] [--status planned]
lybra roadmap add-criterion [项目] --milestone M1 --id M1-C1 --statement "可核对的一句话" --verify auto
lybra roadmap set [项目] --id M1 --field status --value done   # 可改: 里程碑 title/lane/target_date/status, 标准 statement/verify
lybra roadmap remove [项目] --id M1-C1                          # 只允许无挂接卡; 有挂接 = 拒 ROADMAP_REMOVE_LINKED
```

写命令缺省预演(打印整份文件 diff 与将生成的 decision_log 指针名, 零写入), 加 `--confirm --actor <你的实例>` 才写入、生成指针、落账。
项目名省略 = `--workspace-root` / 当前目录所在治理根声明的项目(不回落 home 级活动项目)。有挂接卡的里程碑不删, 改
`set --field status --value dropped`; id 不能改名(会让已挂接卡失联)。

## 卡挂接

卡稿 JSON 的 frontmatter 加:

```json
"milestone": "M1",
"criteria": ["M1-C1"]
```

- 出卡(`draft create` / `validate` / `publish`)校验: 引用的里程碑 / 标准不存在、标准不属所挂里程碑、只给标准不给里程碑 = 拒
  `ROADMAP_LINK_INVALID`; 项目已声明路线图而卡未挂 = 警告 `ROADMAP_UNLINKED`(游离卡, 不拒——探索 / 修复类工作允许不挂, 计入偏差);
  项目没有路线图文件 = 不校验不警告。
- 老卡回挂: `lybra queue amend --task-id <卡> --amendments '{"milestone": "M1", "criteria": ["M1-C1"]}' --amendment-reason "回挂路线图" --actor <你的实例>`;
  只改这两个字段的 amend 在 pending / claimed / blocked / completed 任一状态都允许(声明 `card.schema` `roadmap_link.amend_states`), 引用同样校验。
- 挂接判定只有一个实现(`tools/aipos_cli/roadmap.py` `card_link_status`), CLI、看板与后续偏差推导共用。

## 进度与偏差(AIPOS-F173)

```bash
lybra roadmap status [项目] [--lane <lane>] [--json]        # 只读: 里程碑进度条 / 偏差信号 / 下一步建议
```

每个里程碑的**进度** = 已满足的验收标准数 / 标准总数。一条标准**满足** = 有挂接它的卡已结案(队列 completed), 且该卡达到标准声明的
`verify` 要求:

- `auto`: 最新常规审计裁决属 PASS 类(PASS / PASS_WITH_NOTES), 补审没判 FAIL; 卡面声明了运行证据(`audit_runtime_evidence.required: true`)
  时, 裁决报告里还须有「独立运行证据」节。
- `artifact`: 另需卡面 `artifacts` 声明了该标准的产物, 且每项 `path_glob` 在卡产物落点至少匹配一个文件(见下「验收证据与 Owner 抽查」)。
- `owner_check`: 同 `artifact` 的条件, 再加 Owner 抽查确认(`lybra roadmap verify --result confirm`)。

里程碑声明的 `status` 与推导不一致时在进度行后标 ⚠: 声明 done 但标准没全满足、声明 planned 但已有挂接卡在推进、标准已全满足但还没标 done。

**偏差信号**(每条带一句人话原因与给顾问的命令建议):

| 信号 | 含义 | 严重度 |
|---|---|---|
| 游离卡占比高 `unlinked_ratio` | 近 `unlinked_window_days` 天新出的卡里没挂路线图的占比超过 `unlinked_ratio`(审计卡、已撤卡不计) | 提示 |
| 里程碑停滞 `stalled` | active 且标准未全满足的里程碑, 挂接卡最近一次推进(卡面时刻 / 门记录 / loop 运行记录)早于 `stall_days` 天前(有挂接卡的记录读不出 = 不判, 点名) | **严重** |
| 标准无卡覆盖 `uncovered` | active 里程碑下的某条标准没有任何挂接卡 | **严重** |
| 反复返工 `rework` | 同一标准的挂接卡返工轮次(卡面 `rework_rounds`)合计 ≥ `rework_rounds` | 提示 |
| 里程碑逾期 `overdue` | `target_date` 已过而里程碑未 done / dropped | **严重** |
| 路线图近期被改 `roadmap_changed` | 近 `change_window_days` 天路线图文件有变更(decision_log 指针指向路线图文件) | 提示 |

**严重**的信号同时进 Owner 待决收件箱(`lybra owner inbox`, 类别「路线图偏差」`roadmap_drift`): 写明要定什么、顾问建议(即上表建议命令)、
告诉哪个顾问(挂接卡最近的出卡人; 没卡 = 路线图最近编辑人), 可选裁决 `add_card`(补卡 / 回挂) / `replan`(调路线图) / `acknowledge`(知悉)。
同一偏差真相不变就是同一个事项(重算不重复); 裁决过不再出现; 真相变了(改了目标日、有了新推进后又停滞)= 新事项。dropped 的里程碑不出任何信号。

**阈值配置**: 治理根 `project.json` 的 `roadmap_drift` 段, 缺段 / 缺键 = 缺省(声明 `schema/config.schema.json`
`configuration_sources.project_json.schema.roadmap_drift`):

```json
"roadmap_drift": {"unlinked_window_days": 14, "unlinked_ratio": 0.3, "stall_days": 7, "rework_rounds": 2, "change_window_days": 7}
```

未知键或值不合(整数键须为 ≥1 的整数, `unlinked_ratio` 须为 0..1 的数)= `ROADMAP_DRIFT_INVALID`, 命令拒、收件箱把该项目这一类点名进
读不出列表, 不猜成缺省。`roadmap status` 输出的阈值行会标出哪些取的是缺省。

推导只有一个实现(`tools/aipos_cli/roadmap_drift.py` `roadmap_status`), CLI、Owner 收件箱与看板(`board_adapter.get_roadmap_status`)共用;
只读既有真相(路线图、卡面挂接、门记录、loop 运行记录、decision_log 指针), 不另记进度。

## 验收证据与 Owner 抽查(AIPOS-F175)

**验证状态五态**: 每条标准按已有真相推导(同一 `roadmap_status`, 不另记状态), CLI 每行标出状态与依据:

| 状态 | 什么时候 | 算满足 |
|---|---|---|
| 未验证 | 无挂接卡 / 卡未结案 / 审计未过 / 补审 FAIL / 声明运行证据而报告无证据节 / (artifact、owner_check)卡面没声明该标准的产物或文件不在落点——原因写缺什么 | 否 |
| 自动验证通过 | `auto` / `artifact` 标准满足上面全部条件(依据: 卡、裁决 id、运行证据报告、产物文件) | `auto` / `artifact` 是 |
| 待 Owner 抽查 | `owner_check` 标准产物齐且该卡还没有 Owner 结论(或上次判不符后修复卡已自动验证通过 = 待复查) | 否 |
| Owner 已确认 | 最近一条 Owner 抽查记录 = 符合(依据: 记录路径、转达人、Owner 原话) | `owner_check` 是 |
| 不符 | 最近一条 Owner 抽查记录 = 不符, 显示「修复卡 <卡号>(草稿待发布 / 队列状态)」 | 否 |

状态可追溯: `roadmap status --json` 每条标准带 `basis`(依据)与 `verifications`(该标准的全部抽查记录, 时间序)。

**产物约定**(只此一个卡面字段, 声明 `schema/card.schema.json` `card_artifacts`): 出卡时卡稿写

```json
"artifacts": [{"criterion": "C4", "path_glob": "home.png", "kind": "screenshot"},
              {"criterion": "C4", "path_glob": "steps.txt", "kind": "output"}]
```

`criterion` 须在本卡 `criteria` 里, `kind` ∈ screenshot / output / report, `path_glob` 相对**卡产物落点** `<return_root>/<卡ID>/artifacts/`
(lybra = `task_cards/<卡ID>/artifacts/`; 在执行体交回落点内, 落点随 project.json `paths.return_root`)。执行体按声明放文件并在 RETURN 引用,
审计核文件存在并在报告引用(两份章程已写)。形不合 = 出卡 / amend 拒 `CARD_ARTIFACTS_INVALID`。看板路线图页对图片(png / jpg / gif / webp)
显示缩略图(只读取图路由 `GET /owner/artifact`, 只给卡面声明且在落点内的图), 对文本(txt / log / md / json / out / csv)显示前 2000 字节摘录。

**Owner 抽查**: 标准到「待 Owner 抽查」即进 Owner 收件箱(类别「验收待 Owner 抽查」`owner_check`, 附产物路径、看板链接与「告诉顾问」话术)。
Owner 看完告诉顾问「符合」或「不符: …」, 顾问记录:

```bash
lybra roadmap verify [项目] --criterion <标准id> --result confirm|mismatch --relayed-by <转达的顾问实例|owner> \
    --owner-words "<Owner 原话>" [--note "<哪里不对>"] [--card <卡ID>]       # 缺省预演; --confirm --actor owner 写入并落账
```

写既有 `owner_verifications` 记录(`5_tasks/records/owner_verifications/<卡>/verify_<卡>_<时刻>.md`, 字段 workspace / milestone / criterion /
result / relayed_by / owner_words / note / fix_task_id; 同一秒撞名拒写, 不覆盖)。`--relayed-by` / `--owner-words` 缺 = 用法错 exit 2;
只认 `verify=owner_check` 且已到待抽查(或已有结论)的标准。**不符**同次自动起草修复卡(卡号 `<被抽查卡>-oc<N>`, 挂同一 milestone /
criteria, `owner_check_ref` = 抽查记录 id, 承继原卡执行者 / lane / 审计 / artifacts 等), 顾问 `lybra draft publish --path <草稿> --dry-run`
预演后发布; 修复卡结案审计通过、产物齐后标准回到「待 Owner 抽查」等复查。收件箱里的 owner_check 事项不用 `lybra owner decide`(会被拒并指路)。

## 看板

看板项目页的里程碑区读本文件(只读简版, `GET /api/roadmap`); 增改里程碑请告诉顾问, 由顾问用上面的命令完成(看板只读, AIPOS-F171)。

「路线图与验收」页(`GET /owner/roadmap?project=<项目>&milestone=<里程碑>`, AIPOS-F174, 只读)把 `roadmap status` 的同一推导画出来:
顶部偏差红黄条(严重 = 进 Owner 收件箱的那几类, 每条一句原因 +「告诉 <顾问实例>: …」+ 建议命令), 左栏按 lane 列里程碑(进度条 = 已满足标准数 /
标准总数、状态、目标日, 逾期标红), 右栏是选中里程碑的逐条标准: 挂接卡与队列态、最新审计裁决(可点开裁决记录)、审计报告「独立运行证据」节的
摘录(命令 / 退出码 / 日志摘要), 以及验证状态五态与依据、卡面声明产物的缩略图 / 摘录、Owner 抽查记录(转达人 + 原话)与修复卡; 待 Owner
抽查的标准给「告诉 <顾问>: 符合 / 不符」话术与 `lybra roadmap verify` 命令(AIPOS-F175)。没有路线图的项目显示「如何开始」:
告诉顾问建路线图, 顾问用上面的 `lybra roadmap add-milestone` / `add-criterion`。看板不另算任何进度: 每条标准的证据由
`roadmap_drift.card_evidence` 随推导带出(与判定满足与否同一份读取)。
