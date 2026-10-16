"""AIPOS-F167 — 产品内更新通知(核心, 唯一实现): 卡面顾问须知 → 部署时汇总更新说明 → 未读判定与提示渲染。

一句话: 产品每次上线自动把给顾问的须知送到顾问面前, Owner 不再转述。

件① 声明: 卡面唯一字段 advisor_notice(card.schema fields.advisor_notice; 解析 parse_advisor_notice, 出卡校验 notice_problems ——
      形不合 = BLOCK, 改顾问/审计/执行可见面的卡缺字段 = 警告, 判据 behavior_change_paths)。
件② 汇总: 唯一口 = finalize 部署成功写 finalization 记录之后(finalize._ensure_finalization_record → compile_on_deploy), 把本卡须知与回填
      清单中尚未汇总的条目汇成一份产品侧更新说明(<产品仓>/.deploy/<store.subdir>/update_*.json; 声明 verbs.schema lybra_updates.store)。
件③ 送达(只读部分): 库定位 store_dir_for_running_code(部署快照 / --dev-tree 才读, 卡工作树测试不读真实库)、已读集合 acked_update_ids
      (治理根 records advisor_update_acks/<实例>/, 只追加)、未读判定 unread_updates、提示渲染 banner_lines。写已读标记与 CLI 薄壳在
      advisor_updates_cli(本模块只依赖标准库 + 合并后预载闭包内的模块: finalize 合并后本进程调用 compile_on_deploy)。
"""
from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path
from typing import Any

VERB = "lybra_updates"
NOTICE_FIELD = "advisor_notice"


class AdvisorNoticeError(ValueError):
    """卡面 / 回填清单的 advisor_notice 形不合(消息 = 原因, 不含出口; 出口由调用方按声明模板补)。"""


class AdvisorUpdatesError(RuntimeError):
    """更新说明库 / 已读标记读不出或形不合(fail-closed, 消息带路径与原因)。"""


# ---------------------------------------------------------------------------
# 声明(唯一读取口)
# ---------------------------------------------------------------------------

def updates_declaration() -> dict[str, Any]:
    """verbs.schema verbs.lybra_updates(store / compile / ack / banner / exit_codes)。缺键 = SchemaLoadError(fail-closed)。"""
    from tools.schema_loader import SchemaLoadError, load_schema

    decl = (load_schema("verbs").get("verbs") or {}).get(VERB)
    need = {"store": ("subdir", "filename", "timestamp_style", "record_type", "readable_modes", "record_keys", "entry_keys"),
            "compile": ("backfill_source",), "ack": ("record_kind", "record_type", "landing_command"),
            "banner": ("audience", "line", "commands_line", "point", "points_limit", "more", "list_command", "ack_command",
                       "unknown_viewer", "unreadable")}
    if not isinstance(decl, dict) or not all(isinstance(decl.get(k), dict) and all(f in decl[k] for f in fields)
                                             for k, fields in need.items()) or not isinstance(decl.get("exit_codes"), dict):
        raise SchemaLoadError(f"verbs.schema.json verbs.{VERB}(store / compile / ack / banner / exit_codes)未声明齐")
    return decl


def notice_declaration() -> dict[str, Any]:
    """card.schema fields.advisor_notice(audiences / behavior_change{surfaces, warning, invalid})。缺键 = SchemaLoadError。"""
    from tools.schema_loader import SchemaLoadError, load_schema

    decl = (load_schema("card").get("fields") or {}).get(NOTICE_FIELD)
    change = decl.get("behavior_change") if isinstance(decl, dict) else None
    if not isinstance(decl, dict) or not isinstance(decl.get("audiences"), list) or not decl["audiences"] \
            or not isinstance(change, dict) or not isinstance(change.get("surfaces"), list) \
            or not all(isinstance(change.get(k), str) and change[k] for k in ("warning", "invalid")):
        raise SchemaLoadError(f"card.schema.json fields.{NOTICE_FIELD}(audiences / behavior_change.surfaces·warning·invalid)未声明齐")
    return decl


# ---------------------------------------------------------------------------
# 件① 卡面须知: 解析 + 出卡校验
# ---------------------------------------------------------------------------

def _one_line(value: Any, what: str) -> str:
    text = value.strip() if isinstance(value, str) else ""
    if not text or "\n" in text:
        raise AdvisorNoticeError(f"{what} 须为非空单行串(得 {value!r})")
    return text


def parse_advisor_notice(value: Any) -> dict[str, Any]:
    """advisor_notice 唯一解析(卡面与回填清单同读): → {audience: [...], summary, actions: [...]}; 形不合 = AdvisorNoticeError。"""
    audiences = [str(a) for a in notice_declaration()["audiences"]]
    if not isinstance(value, dict):
        raise AdvisorNoticeError(f"advisor_notice 须为对象(得 {type(value).__name__})")
    unknown = sorted(set(value) - {"audience", "summary", "actions"})
    if unknown:
        raise AdvisorNoticeError(f"advisor_notice 含未声明的键 {unknown}")
    audience = value.get("audience")
    if isinstance(audience, str):
        audience = [audience]
    if not isinstance(audience, list) or not audience or not all(isinstance(a, str) and a in audiences for a in audience):
        raise AdvisorNoticeError(f"audience 须为非空列表且每项 ∈ {audiences}(得 {value.get('audience')!r})")
    actions = value.get("actions", [])
    if not isinstance(actions, list):
        raise AdvisorNoticeError(f"actions 须为列表(得 {type(actions).__name__})")
    return {"audience": list(dict.fromkeys(audience)), "summary": _one_line(value.get("summary"), "summary"),
            "actions": [_one_line(a, f"actions[{i}]") for i, a in enumerate(actions)]}


def _git_common_dir(path: str | Path) -> Path | None:
    try:
        out = subprocess.run(["git", "-C", str(path), "rev-parse", "--path-format=absolute", "--git-common-dir"],
                             capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.SubprocessError):
        return None
    if out.returncode != 0 or not out.stdout.strip():
        return None
    return Path(os.path.realpath(out.stdout.strip()))


def product_repo_root(code_root: Path | None = None) -> Path:
    """运行代码所属产品仓(主检出): 部署链接 deploy_runtime.default_deploy_current 的部署目录所在目录(与 tools/lybra-deploy 同一推导)。"""
    from tools.aipos_cli.deploy_runtime import default_deploy_current

    return default_deploy_current(code_root).parent.parent


def _lane_paths(metadata: dict[str, Any]) -> list[str]:
    lane = metadata.get("lane") if isinstance(metadata.get("lane"), dict) else {}
    paths = lane.get("paths")
    if isinstance(paths, list) and paths:
        return [str(p).strip() for p in paths if str(p).strip()]
    target = metadata.get("output_target")
    return [p.strip() for p in str(target or "").split(",") if p.strip()]


def behavior_change_paths(metadata: dict[str, Any], governance_root: str | Path, *, code_root: Path | None = None) -> list[str]:
    """卡改动 Lybra 产品的顾问/审计/执行可见面 = 返回命中的 lane 路径(空 = 不判为行为变更卡)。判据声明 card.schema
    fields.advisor_notice.behavior_change: 卡的仓(按卡取仓唯一解析 workspace_config.resolve_card_repo: lane.repo 仓名 / 路径 →
    project.json 仓清单)与运行代码属同一产品仓(git common dir 相同)且 lane 路径与 surfaces 前缀重叠。
    仓解析不出(CardRepoUnresolved, 由出卡其他校验报)/ 不是 git 仓 / 是别的仓 = 空(其他项目业务卡不警告)。"""
    from tools.aipos_cli.workspace_config import CardRepoUnresolved, resolve_card_repo

    try:
        repo = resolve_card_repo(Path(governance_root), metadata)
    except CardRepoUnresolved:
        return []
    mine = _git_common_dir(product_repo_root(code_root))
    theirs = _git_common_dir(repo)
    if mine is None or theirs is None or mine != theirs:
        return []
    surfaces = [str(s).strip().rstrip("/") + "/" for s in notice_declaration()["behavior_change"]["surfaces"] if str(s).strip()]
    hits = []
    for path in _lane_paths(metadata):
        norm = path.lstrip("./").rstrip("/") + "/"
        if any(norm.startswith(s) or s.startswith(norm) for s in surfaces):
            hits.append(path)
    return hits


def notice_problems(metadata: dict[str, Any], governance_root: str | Path, *,
                    code_root: Path | None = None) -> tuple[list[str], list[str]]:
    """出卡校验(draft create / validate / publish 同经 draft_validator.validate_draft_metadata): (拒因, 警告)。
    有 advisor_notice = 解析, 形不合 = 拒 ADVISOR_NOTICE_INVALID; 缺 = 行为变更卡警告 ADVISOR_NOTICE_MISSING(不拒)。"""
    decl = notice_declaration()
    value = metadata.get(NOTICE_FIELD)
    if value not in (None, "", {}, []):
        try:
            parse_advisor_notice(value)
        except AdvisorNoticeError as exc:
            return [decl["behavior_change"]["invalid"].format(reason=exc, audiences=", ".join(decl["audiences"]))], []
        return [], []
    hits = behavior_change_paths(metadata, governance_root, code_root=code_root)
    if not hits:
        return [], []
    return [], [decl["behavior_change"]["warning"].format(paths=", ".join(hits))]


# ---------------------------------------------------------------------------
# 件② 更新说明库: 定位 / 读 / 部署后汇总
# ---------------------------------------------------------------------------

def store_dir_for_product_repo(product_repo: str | Path) -> Path:
    """被部署产品仓的更新说明库(<产品仓>/<部署目录名>/<store.subdir>; 部署目录名与 tools/lybra-deploy / deploy_runtime 同名)。"""
    from tools.aipos_cli.deploy_runtime import DEPLOY_DIRNAME

    return Path(product_repo) / DEPLOY_DIRNAME / str(updates_declaration()["store"]["subdir"])


def store_dir_for_running_code(running: Any = None) -> dict[str, Any]:
    """本进程运行代码可读的更新说明库: {available, store, mode, reason}。部署快照 / --dev-tree = 所属产品仓部署目录下的库;
    卡工作树 / python -m(mode=worktree)= 不可用(store.readable_modes 声明), 测试与夹具因此读不到真实库。"""
    from tools.aipos_cli.deploy_runtime import default_deploy_current, running_code

    run = running if running is not None else running_code()
    modes = [str(m) for m in updates_declaration()["store"]["readable_modes"]]
    if run.mode not in modes:
        return {"available": False, "store": None, "mode": run.mode,
                "reason": f"运行代码 mode={run.mode}(卡工作树 / python -m)不读更新说明库; 只在 {'/'.join(modes)} 模式读(经 lybra 入口)"}
    deploy_dir = Path(run.deploy_dir) if run.deploy_dir is not None else default_deploy_current(run.code_root).parent
    return {"available": True, "store": deploy_dir / str(updates_declaration()["store"]["subdir"]), "mode": run.mode, "reason": ""}


def _check_record(record: Any, path: Path) -> dict[str, Any]:
    store = updates_declaration()["store"]
    if not isinstance(record, dict) or record.get("record_type") != store["record_type"]:
        raise AdvisorUpdatesError(f"{path}: 不是更新说明记录(record_type ≠ {store['record_type']})")
    missing = [k for k in store["record_keys"] if k not in record]
    entries = record.get("entries")
    if missing or not isinstance(entries, list):
        raise AdvisorUpdatesError(f"{path}: 缺键 {missing or ['entries(须为列表)']}")
    for i, entry in enumerate(entries):
        lack = [k for k in store["entry_keys"] if not isinstance(entry, dict) or k not in entry]
        if lack:
            raise AdvisorUpdatesError(f"{path}: entries[{i}] 缺键 {lack}")
    if str(record["update_id"]) != path.stem:
        raise AdvisorUpdatesError(f"{path}: update_id={record['update_id']!r} ≠ 文件名 {path.stem!r}")
    return record


def load_updates(store: Path | None) -> list[dict[str, Any]]:
    """库内全部更新说明(按 deployed_at, update_id 升序)。库目录不在 = []; 任一文件读不出 / 形不合 = AdvisorUpdatesError(不静默跳过)。"""
    if store is None or not Path(store).is_dir():
        return []
    filename = str(updates_declaration()["store"]["filename"])
    pattern = filename.split("{", 1)[0] + "*" + Path(filename).suffix  # 落盘名式样的静态前缀(声明 store.filename)
    out = []
    for path in sorted(Path(store).glob(pattern)):
        try:
            record = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise AdvisorUpdatesError(f"{path}: 读不出({type(exc).__name__}: {exc})") from exc
        out.append(_check_record(record, path))
    return sorted(out, key=lambda r: (str(r["deployed_at"]), str(r["update_id"])))


def _backfill_entries(product_repo: Path) -> list[dict[str, Any]]:
    rel = str(updates_declaration()["compile"]["backfill_source"])
    path = Path(product_repo) / rel
    if not path.is_file():
        return []
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise AdvisorNoticeError(f"回填清单 {rel} 读不出({type(exc).__name__}: {exc})") from exc
    rows = doc.get("entries") if isinstance(doc, dict) else None
    if not isinstance(rows, list):
        raise AdvisorNoticeError(f"回填清单 {rel} 缺 entries 列表")
    out = []
    for i, row in enumerate(rows):
        task_id = str((row or {}).get("task_id") or "").strip() if isinstance(row, dict) else ""
        if not task_id:
            raise AdvisorNoticeError(f"回填清单 {rel} entries[{i}] 缺 task_id")
        try:
            notice = parse_advisor_notice({k: v for k, v in row.items() if k != "task_id"})
        except AdvisorNoticeError as exc:
            raise AdvisorNoticeError(f"回填清单 {rel} entries[{i}]({task_id}): {exc}") from exc
        out.append({"task_id": task_id, **notice, "source": "backfill"})
    return out


def _card_notice(governance_root: Path, task_id: str) -> dict[str, Any] | None:
    from tools.aipos_cli.frontmatter import parse_markdown_frontmatter
    from tools.aipos_cli.task_loader import find_task_card

    card, _state = find_task_card(governance_root, task_id)  # 全部队列目录(值域读 schema 投影, 不手写子集)
    if card is None:
        return None
    meta, _body, _warnings = parse_markdown_frontmatter(card.read_text(encoding="utf-8"))
    value = meta.get(NOTICE_FIELD)
    if value in (None, "", {}, []):
        return None
    try:
        return {"task_id": task_id, **parse_advisor_notice(value), "source": "card"}
    except AdvisorNoticeError as exc:
        raise AdvisorNoticeError(f"卡 {task_id} 的 advisor_notice: {exc}") from exc


def compile_on_deploy(workspace_root: str | Path, governance_root: str | Path, task_id: str, *,
                      merge_commit: str | None = None) -> dict[str, Any]:
    """部署成功后汇总更新说明(唯一实现; 唯一调用口 finalize._ensure_finalization_record, deploy_status=deployed)。

    条目 = 本卡卡面 advisor_notice + 回填清单中库内尚无的 task_id(去重键 task_id, 幂等)。无新条目 = 不写。
    返回 {written: 路径|None, update_id, entries, line}; 声明 / 卡面 / 回填 / 库读不出 = 异常上抛(调用方出声, 不回滚部署)。"""
    from tools.aipos_cli.clock import file_slug, iso_z
    from tools.aipos_cli.deploy_runtime import CURRENT_LINKNAME, VERSION_FILENAME
    from tools.aipos_cli.gate_drift import read_version_commit
    from tools.aipos_cli.workspace_config import read_project_json

    decl = updates_declaration()
    product = Path(workspace_root)
    gov = Path(governance_root)
    store = store_dir_for_product_repo(product)
    emitted = {str(e.get("task_id")) for r in load_updates(store) for e in r["entries"]}
    fresh: list[dict[str, Any]] = []
    own = _card_notice(gov, task_id)
    if own is not None and own["task_id"] not in emitted:
        fresh.append(own)
    for row in _backfill_entries(product):
        if row["task_id"] not in emitted and row["task_id"] not in {f["task_id"] for f in fresh}:
            fresh.append(row)
    if not fresh:
        why = "本卡卡面无 advisor_notice" if own is None else f"本卡 {task_id} 的须知已汇总过"
        return {"written": None, "update_id": None, "entries": [],
                "line": f"更新说明: 无新条目({why}; 回填清单无未汇总条目), 未生成"}
    current = store.parent / CURRENT_LINKNAME  # 被部署产品仓的部署链接(与库同一部署目录)
    commit = read_version_commit(current / VERSION_FILENAME) or str(merge_commit or "").strip()
    if not commit:
        raise AdvisorUpdatesError(f"{current / VERSION_FILENAME} 无 git_commit 且未给合并提交, 生效版本不可知")
    release = Path(os.path.realpath(current)).name if current.exists() else commit[:12]
    now = iso_z()
    stem = Path(str(decl["store"]["filename"]).format(timestamp=file_slug(str(decl["store"]["timestamp_style"]), now),
                                                      commit12=commit[:12])).stem
    target = store / f"{stem}.json"
    if target.exists():
        raise AdvisorUpdatesError(f"{target} 已存在(同秒同提交), 只追加不覆盖; 出口: 稍后重跑同一条 finalize --deploy")
    record = {"record_type": decl["store"]["record_type"], "update_id": stem, "deploy_commit": commit, "release": release,
              "deployed_at": now, "source_project": str(read_project_json(gov).get("project") or gov.name),
              "source_task_id": task_id, "entries": fresh}
    store.mkdir(parents=True, exist_ok=True)
    tmp = target.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(record, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, target)
    ids = ", ".join(e["task_id"] for e in fresh)
    return {"written": str(target), "update_id": stem, "entries": fresh,
            "line": f"✓ 更新说明已汇总: {target}({len(fresh)} 条: {ids}; 生效版本 {release} / {commit[:12]})"}


# ---------------------------------------------------------------------------
# 件③ 已读集合 / 未读判定 / 提示渲染(只读)
# ---------------------------------------------------------------------------

def acked_update_ids(governance_root: str | Path, instance: str) -> set[str]:
    """该实例在本治理根的已读集合 = records advisor_update_acks/<实例>/ 全部记录 acked_update_ids 的并集。读不出 = AdvisorUpdatesError。"""
    from tools.aipos_cli.frontmatter import parse_markdown_frontmatter
    from tools.aipos_cli.record_writer import record_dir

    ack = updates_declaration()["ack"]
    directory = record_dir(Path(governance_root), str(ack["record_kind"]), instance)
    out: set[str] = set()
    if not directory.is_dir():
        return out
    for path in sorted(directory.glob("*.md")):
        try:
            meta, _body, _warnings = parse_markdown_frontmatter(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError) as exc:
            raise AdvisorUpdatesError(f"{path}: 读不出({type(exc).__name__}: {exc})") from exc
        ids = meta.get("acked_update_ids")
        if meta.get("record_type") != ack["record_type"] or not isinstance(ids, list):
            raise AdvisorUpdatesError(f"{path}: 不是已读标记记录(record_type ≠ {ack['record_type']} 或缺 acked_update_ids 列表)")
        out |= {str(i) for i in ids}
    return out


def audience_entries(record: dict[str, Any], audience: str | None = None) -> list[dict[str, Any]]:
    who = audience or str(updates_declaration()["banner"]["audience"])
    return [e for e in record["entries"] if who in (e.get("audience") or [])]


def unread_updates(updates: list[dict[str, Any]], acked: set[str]) -> list[dict[str, Any]]:
    """未读 = 含提示受众(banner.audience)条目且 update_id 不在已读集合的更新说明(按部署时刻升序)。"""
    return [r for r in updates if audience_entries(r) and str(r["update_id"]) not in acked]


def version_label(record: dict[str, Any]) -> str:
    return f"{record['release']}({str(record['deploy_commit'])[:12]})"


def banner_lines(unread: list[dict[str, Any]], *, governance_root: str | Path, instance: str | None) -> list[str]:
    """「Lybra 有 N 条未读更新」提示(文案全读 verbs.schema lybra_updates.banner); 无未读 = []。"""
    if not unread:
        return []
    banner = updates_declaration()["banner"]
    entries = [e for r in unread for e in audience_entries(r)]
    limit = int(banner["points_limit"])
    points = "; ".join(str(banner["point"]).format(task_id=e["task_id"], summary=e["summary"]) for e in entries[:limit])
    if len(entries) > limit:
        points += " " + str(banner["more"]).format(more=len(entries) - limit)
    inst = instance or str(banner["unknown_viewer"])
    fill = {"governance_root": str(governance_root), "instance": inst}
    return [str(banner["line"]).format(count=len(entries), since=version_label(unread[0]), points=points),
            str(banner["commands_line"]).format(list_command=str(banner["list_command"]).format(**fill),
                                                ack_command=str(banner["ack_command"]).format(**fill))]


# AIPOS-316: Guard against direct invocation
from tools.aipos_cli._cli_entry_guard import check_direct_invocation  # noqa: E402
check_direct_invocation(__name__)
