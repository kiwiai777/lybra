# 路线图声明(roadmap.yaml)

AIPOS-F171。每个项目在治理根放一份结构化路线图: 按 lane(子项目)分组的里程碑, 每个里程碑带可核对的验收标准。卡出的时候挂到对应里程碑,
产品据此算进度、标出游离卡(后续 F172 偏差推导与 F173 看板页用同一份声明)。

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

## 看板

看板项目页的里程碑区读本文件(只读简版, `GET /api/roadmap`); 增改里程碑请告诉顾问, 由顾问用上面的命令完成(看板只读, AIPOS-F171)。
