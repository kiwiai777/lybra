"""AIPOS-F246 件① — 顾问简报(`lybra project headline`): 看板首页每位顾问一页纸卡的「现在」「做成」两行。

一句话: 顾问(或 Owner)用一条命令写「现在在做什么 / 最近做成了什么」, 落治理根一条只追加记录; 看板取每位顾问最新一条,
超过声明的陈旧阈值标黄, 顾问 loop status / next 末尾提示一次更新命令。命名与 F67 `lybra brief`(顾问冷启动派生简报)区分。

单一真相(卡面硬约束, 不另判):
- 声明 = verbs.schema verbs.lybra_project_headline(headline_declaration 唯一读取口, fail-closed)。
- 落点 = transitions.schema record_locations.kinds.advisor_headlines(record_writer.record_dir / record_location 唯一读取口);
  记录带 record_type(过既有入库护栏 governance_guardrails B④), 写后经 governance_commit.land_written_paths 落账(AIPOS-F158 同一包装)。
- 顾问 = advisor_updates_cli.advisor_instances(本治理根已接入、角色类 = 顾问的实例; 退役 / 作废不列)。
- 写入方: 本人 = 本治理根连接文件里有该实例的驱动方凭据(two_phase_shell_factory.signer_role_for_actor, 签发者即 --actor 的唯一口);
  Owner = 连接文件里有 owner 角色凭据(two_phase_shell_factory.load_role_token 取凭据唯一入口)。凭据只在进程内判在不在, 不输出、不落盘。
- 任何项目同一命令: 不写死项目 ID / 路径 / 实例名。
"""
from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

VERB = "lybra_project_headline"
FIELDS = ("title", "stage", "now", "done")


class HeadlineRefused(ValueError):
    """写简报被拒(零写入); code ∈ 声明 reject_codes, 消息带出口。"""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def headline_declaration() -> dict[str, Any]:
    """verbs.schema verbs.lybra_project_headline(fail-closed): 记录类 / 类型 / 落账命令 / owner_actor / statuses ∋ default_status /
    stale_hours 正数 / limits 四字段正整数 / texts / reject_codes / exit_codes 齐。"""
    from tools.schema_loader import SchemaLoadError, load_schema

    decl = (load_schema("verbs").get("verbs") or {}).get(VERB)
    keys = ("record_kind", "record_type", "landing_command", "owner_actor", "statuses", "default_status", "stale_hours", "limits", "texts",
            "reject_codes", "exit_codes")
    if not isinstance(decl, dict) or any(k not in decl for k in keys):
        raise SchemaLoadError(f"verbs.schema.json verbs.{VERB} 未声明齐 {list(keys)}")
    limits = decl["limits"]
    if (not isinstance(limits, dict) or any(not isinstance(limits.get(f), int) or isinstance(limits.get(f), bool) or limits[f] < 1
                                            for f in FIELDS)):
        raise SchemaLoadError(f"verbs.schema.json verbs.{VERB}.limits 须为 {list(FIELDS)} 各一个正整数")
    stale = decl["stale_hours"]
    if not isinstance(stale, (int, float)) or isinstance(stale, bool) or stale <= 0:
        raise SchemaLoadError(f"verbs.schema.json verbs.{VERB}.stale_hours 须为正数")
    if not isinstance(decl["statuses"], list) or decl["default_status"] not in decl["statuses"]:
        raise SchemaLoadError(f"verbs.schema.json verbs.{VERB}: default_status 须在 statuses 内")
    if any(k not in (decl["texts"] or {}) for k in ("preview", "written", "stale_hint")):
        raise SchemaLoadError(f"verbs.schema.json verbs.{VERB}.texts 须有 preview / written / stale_hint")
    if any(k not in (decl["exit_codes"] or {}) for k in ("ok", "refused", "usage", "landing_failed")):
        raise SchemaLoadError(f"verbs.schema.json verbs.{VERB}.exit_codes 须有 ok / refused / usage / landing_failed")
    return decl


def _exit(name: str) -> int:
    return int(headline_declaration()["exit_codes"][name]["code"])


def _parse_time(value: Any) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        moment = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    return moment if moment.tzinfo else moment.replace(tzinfo=timezone.utc)


# ---------------------------------------------------------------------------
# 读: 每位顾问最新一条
# ---------------------------------------------------------------------------

def _instance_records(governance_root: Path, instance_dir: Path, decl: dict[str, Any]) -> list[dict[str, Any]]:
    from tools.aipos_cli.frontmatter import FrontmatterReadError, require_frontmatter
    from tools.aipos_cli.record_writer import record_file_prefix

    out: list[dict[str, Any]] = []
    prefix = record_file_prefix(str(decl["record_kind"]))
    for path in sorted(instance_dir.glob(f"{prefix}_*.md")):
        try:
            meta, _body = require_frontmatter(path)
        except FrontmatterReadError as exc:  # 读不出 = 点名(不静默跳过, 不当无简报)
            raise ValueError(f"顾问简报记录读不出 {path}: {exc}") from exc
        if meta.get("record_type") != decl["record_type"]:
            raise ValueError(f"{path}: 不是顾问简报记录(record_type ≠ {decl['record_type']})")
        written = _parse_time(meta.get("written_at"))
        if written is None:
            raise ValueError(f"{path}: written_at 读不出({meta.get('written_at')!r})")
        out.append({**{k: meta.get(k) for k in ("agent_instance", "written_by", "status", *FIELDS)},
                    "written_at": str(meta.get("written_at")), "_at": written,
                    "record": path.resolve().relative_to(Path(governance_root).resolve()).as_posix()})
    return out


def latest_headlines(governance_root: str | Path) -> dict[str, dict[str, Any]]:
    """{实例: 最新一条简报}(按 written_at; 同刻按记录名)。目录不在 = {}; 记录读不出 / 形不合 = ValueError(点名, 不猜)。只读。"""
    from tools.aipos_cli.record_writer import record_dir

    decl = headline_declaration()
    root = Path(governance_root)
    base = record_dir(root, str(decl["record_kind"]))
    if not base.is_dir():
        return {}
    out: dict[str, dict[str, Any]] = {}
    for entry in sorted(base.iterdir()):
        if not entry.is_dir():
            continue
        records = _instance_records(root, entry, decl)
        if records:
            best = max(records, key=lambda r: (r["_at"], r["record"]))
            out[str(best.get("agent_instance") or entry.name)] = {k: v for k, v in best.items() if k != "_at"}
    return out


def headline_age_hours(record: dict[str, Any] | None, now: datetime | None = None) -> float | None:
    """简报距今小时数(无简报 / 时间读不出 = None)。"""
    from tools.aipos_cli.clock import utc_now

    moment = _parse_time((record or {}).get("written_at"))
    if moment is None:
        return None
    return max(0.0, ((now or utc_now()) - moment).total_seconds() / 3600.0)


def is_stale(record: dict[str, Any] | None, now: datetime | None = None) -> bool:
    """陈旧 = 有简报且距今 ≥ 声明 stale_hours(无简报另算「还没写」, 不叫陈旧)。"""
    age = headline_age_hours(record, now)
    return age is not None and age >= float(headline_declaration()["stale_hours"])


# ---------------------------------------------------------------------------
# 写: 两阶段 + 落账
# ---------------------------------------------------------------------------

def _writer_check(root: Path, instance: str, actor: str, decl: dict[str, Any], connection_json: Path | None) -> str:
    """写入方核验: 本人(驱动方凭据在本治理根连接文件)或 Owner(owner 角色凭据在)。返回写入方角色说明; 拒 = HeadlineRefused。"""
    from tools.aipos_cli.service_mode import connection_path

    conn = connection_path(root, connection_target=connection_json)
    owner_actor = str(decl["owner_actor"])
    if actor not in (instance, owner_actor):
        raise HeadlineRefused("not_self", f"写入方 {actor} 不是 {instance} 本人, 也不是 Owner({owner_actor}); 顾问简报只能本人或 Owner 写。"
                                          f"出口: 以 {instance} 本人写(--actor 缺省即本人), 或请 Owner 写(--actor {owner_actor})")
    if not conn.is_file():
        raise HeadlineRefused("no_credential", f"本治理根连接文件不在 {conn}, 无从核写入方凭据; 出口: 在治理根所在机运行(或 --connection-json 指本机连接文件)")
    if actor == owner_actor:
        from tools.aipos_cli.two_phase_shell_factory import load_role_token

        try:
            load_role_token(connection_json_path=str(conn), role=owner_actor)  # 取凭据唯一入口; 只判在不在, 值不出进程
        except (ValueError, OSError) as exc:
            raise HeadlineRefused("no_credential", f"连接文件 {conn} 没有可用的 Owner 凭据: {exc}") from exc
        return owner_actor
    from tools.aipos_cli.two_phase_shell_factory import signer_role_for_actor

    try:
        return signer_role_for_actor(connection_json_path=str(conn), actor=actor, repo_root=root)
    except (ValueError, OSError) as exc:  # DriverTokenError(ValueError 子类)= 该实例凭据不在本连接文件
        raise HeadlineRefused("no_credential", f"连接文件 {conn} 里没有 {actor} 的顾问凭据(只能本人写自己的简报): {exc}") from exc


def write_headline(governance_root: str | Path, *, instance: str, title: str, now_text: str, done: str, stage: str | None = None,
                   status: str | None = None, actor: str | None = None, confirm: bool = False, connection_json: str | Path | None = None,
                   moment: datetime | None = None) -> dict[str, Any]:
    """预演(confirm=False)零写入; 确认 = 写一条记录并落账。返回 {stage, instance, actor, record, fields, landing}; 拒 = HeadlineRefused。"""
    from tools.aipos_cli.advisor_updates_cli import advisor_instances
    from tools.aipos_cli.clock import file_slug, iso_z, utc_now
    from tools.aipos_cli.record_writer import record_dir, record_location, render_markdown

    decl = headline_declaration()
    root = Path(governance_root)
    name = str(instance or "").strip()
    writer = str(actor or "").strip() or name
    advisors = advisor_instances(root)
    if name not in advisors:
        raise HeadlineRefused("not_advisor", f"--instance {name or '(空)'} 不是本治理根已接入的顾问实例; 本治理根顾问: {advisors or '(无)'}")
    fields = {"title": str(title or "").strip(), "stage": str(stage or "").strip(), "now": str(now_text or "").strip(),
              "done": str(done or "").strip()}
    empty = [k for k in ("title", "now", "done") if not fields[k]]
    if empty:
        raise HeadlineRefused("empty", f"简报 {empty} 不能为空(--title / --now / --done 必填)")
    over = [f"{k} {len(v)}>{decl['limits'][k]} 字" for k, v in fields.items() if len(v) > int(decl["limits"][k])]
    if over:
        raise HeadlineRefused("too_long", f"简报超字数上限: {', '.join(over)}(上限读 verbs.schema {VERB}.limits; 写成一两句人话)")
    state = str(status or decl["default_status"])
    if state not in decl["statuses"]:
        raise HeadlineRefused("bad_status", f"--status {state} 不在声明 {decl['statuses']}")
    role = _writer_check(root, name, writer, decl, Path(connection_json).expanduser() if connection_json else None)
    at = moment or utc_now()
    kind = str(decl["record_kind"])
    filename = Path(record_location(kind)).name.format(agent_instance=name, timestamp=file_slug("compact", at))
    path = record_dir(root, kind, name) / filename
    out: dict[str, Any] = {"stage": "confirm" if confirm else "preview", "instance": name, "actor": writer, "writer_role": role,
                           "record": path.relative_to(root).as_posix(), "fields": {**fields, "status": state}, "landing": None}
    if not confirm:
        return out
    if path.exists():
        raise HeadlineRefused("exists", f"{out['record']} 已存在(同秒), 只追加不覆盖; 出口: 稍后重跑")
    meta = {"record_type": str(decl["record_type"]), "headline_id": path.stem, "agent_instance": name, "written_by": writer,
            "written_at": iso_z(at), "status": state, **fields}
    body = (f"# 顾问简报 · {fields['title']}\n\n- 现在: {fields['now']}\n- 最近做成: {fields['done']}\n"
            + (f"- 阶段: {fields['stage']}\n" if fields["stage"] else ""))
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8") as fh:  # 排他创建: 只追加不覆盖
        fh.write(render_markdown(meta, body))
    from tools.aipos_cli.governance_commit import land_written_paths

    out["landing"] = land_written_paths(root, [out["record"]], writer, command=str(decl["landing_command"]), instance=name)
    return out


# ---------------------------------------------------------------------------
# 顾问出口提示(loop status / next 末尾; 无顾问视角 / 简报新鲜 = 不显示)
# ---------------------------------------------------------------------------

def headline_hint_lines(governance_root: str | Path, actor: str | None = None) -> list[str]:
    """查看实例(advisor_updates_cli.resolve_viewer: 显式 --actor > 本治理根唯一顾问)的简报超阈值(stale_hours)= 一行更新命令提示;
    视角不定 / 新鲜 / 还没写(看板卡上已写「顾问还没写简报」)= []。读不出 = 一行告警原文(只影响显示)。"""
    from tools.aipos_cli.advisor_updates_cli import resolve_viewer
    from tools.aipos_cli.custom_roles import RoleRegistryReadError
    from tools.schema_loader import SchemaLoadError

    try:
        decl = headline_declaration()
        viewer = resolve_viewer(governance_root, actor)
        instance = viewer["instance"]
        if not instance or instance not in viewer["candidates"]:
            return []
        record = latest_headlines(governance_root).get(instance)
        fill = {"governance_root": str(governance_root), "instance": instance}
        if is_stale(record):
            return [str(decl["texts"]["stale_hint"]).format(age_hours=int(headline_age_hours(record) or 0), **fill)]
        return []
    except (SchemaLoadError, RoleRegistryReadError, ValueError, OSError) as exc:
        return [f"⚠ 顾问简报读不出: {type(exc).__name__}: {exc}"]


# ---------------------------------------------------------------------------
# CLI 薄壳
# ---------------------------------------------------------------------------

def run_headline_cli(args: Any) -> int:
    import json

    from tools.aipos_cli.aipos_cli import _find_repo_root_for_args
    from tools.schema_loader import SchemaLoadError

    if bool(getattr(args, "dry_run", False)) and bool(getattr(args, "confirm", False)):
        print("lybra project headline: --dry-run 与 --confirm 不能同时给", file=sys.stderr)
        return _exit("usage")
    try:
        root = Path(_find_repo_root_for_args(args))
        report = write_headline(root, instance=getattr(args, "instance", None) or "", title=getattr(args, "title", None) or "",
                                now_text=getattr(args, "now", None) or "", done=getattr(args, "done", None) or "",
                                stage=getattr(args, "stage", None), status=getattr(args, "status", None),
                                actor=getattr(args, "actor", None), confirm=bool(getattr(args, "confirm", False)),
                                connection_json=getattr(args, "connection_json", None))
    except HeadlineRefused as exc:
        print(f"lybra project headline: 拒({exc.code}): {exc}", file=sys.stderr)
        return _exit("refused")
    except (FileNotFoundError, SchemaLoadError, ValueError, OSError) as exc:
        print(f"lybra project headline: {type(exc).__name__}: {exc}", file=sys.stderr)
        return _exit("refused")
    landing = report.get("landing")
    texts = headline_declaration()["texts"]
    if getattr(args, "json", False):
        print(json.dumps({**report, "landing": ({k: v for k, v in landing.items() if k != "line"} if landing else None)},
                         ensure_ascii=False, indent=2))
    else:
        key = "preview" if report["stage"] == "preview" else "written"
        print(str(texts[key]).format(instance=report["instance"], record=report["record"], actor=report["actor"]))
        for k, v in report["fields"].items():
            if v:
                print(f"  {k}: {v}")
        if landing:
            print(landing["line"], file=sys.stdout if landing.get("landed") else sys.stderr)
    if landing and not landing.get("landed"):
        return int(landing.get("exit_code") or _exit("landing_failed"))
    return _exit("ok")


# AIPOS-316: Guard against direct invocation
from tools.aipos_cli._cli_entry_guard import check_direct_invocation  # noqa: E402

check_direct_invocation(__name__)
