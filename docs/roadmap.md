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
- `artifact` / `owner_check`: 产物核对与 Owner 抽查归 F175(验收证据与抽查), 在此之前这类标准即使卡已结案也显示「未满足: 待 F175 …」, 不臆断。

里程碑声明的 `status` 与推导不一致时在进度行后标 ⚠: 声明 done 但标准没全满足、声明 planned 但已有挂接卡在推进、标准已全满足但还没标 done。

**偏差信号**(每条带一句人话原因与给顾问的命令建议):

| 信号 | 含义 | 严重度 |
|---|---|---|
| 游离卡占比高 `unlinked_ratio` | 近 `unlinked_window_days` 天新出的卡里没挂路线图的占比超过 `unlinked_ratio`(审计卡、已撤卡不计) | 提示 |
| 里程碑停滞 `stalled` | active 里程碑的挂接卡最近一次推进(卡面时刻 / 门记录 / loop 运行记录)早于 `stall_days` 天前 | **严重** |
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

## 看板

看板项目页的里程碑区读本文件(只读简版, `GET /api/roadmap`); 增改里程碑请告诉顾问, 由顾问用上面的命令完成(看板只读, AIPOS-F171)。
