"""AIPOS-F248 件① — 收件箱事项推荐(`lybra owner recommend`): 每件「需要你」带推荐选项与一句理由, 看板推荐按钮主色实心标「推荐」。

一句话: 顾问(该事项转达对象)或 Owner 用一条命令给事项写 {choice, reason}, 落该事项治理根一条只追加记录; 事项真相一变(事项哈希不等 /
事项消失 = 新事项 ID)推荐自动失效。无顾问推荐 = 按类别声明的产品缺省(授权将尽 / 用尽 = 按原范围续期; 验收抽查无缺省); 都无 = 不标推荐。

单一真相(卡面硬约束, 不另判):
- 声明 = verbs.schema verbs.lybra_owner_recommend(recommend_declaration 唯一读取口, fail-closed)。
- 落点 = transitions.schema record_locations.kinds.owner_recommendations(record_writer.record_dir / record_location 唯一读取口);
  记录带 record_type(过既有入库护栏), 写后经 governance_commit.land_written_paths 落账(AIPOS-F158 同一包装)。
- 事项 = owner_inbox.find_items / collect_inbox(同一数据); 事项哈希 = owner_inbox.item_hash(决策记录 evidence_hash / 看板参数摘要同一构造)。
- 写入方核验 = advisor_headline.writer_credential_role(F246 顾问简报同一实现)。
- 产品缺省的续期参数只读既有推导: 信封 = autonomy_policy.envelope_overview; 项目授权 = autonomy_policy.authorization_overview。
- 任何项目同一命令: 不写死项目 ID / 路径 / 实例名。
"""
from __future__ import annotations

import math
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

VERB = "lybra_owner_recommend"
_KEYS = ("record_kind", "record_type", "landing_command", "owner_actor", "reason_max_chars", "hash_basis", "actions", "renew_days_cap",
         "defaults", "texts", "reject_codes", "exit_codes")
_ACTIONS = ("decide", "verify", "renew_envelope", "extend_authorization", "sign_grant")
_APPLIES = {"envelope": "renew_envelope", "authorization": "extend_authorization", "grant_request": "sign_grant"}
_TEXTS = ("preview", "written", "inbox_line", "by_advisor", "by_default")
_EXITS = ("ok", "refused", "usage", "landing_failed", "not_found")


class RecommendRefused(ValueError):
    """写推荐被拒(零写入); code ∈ 声明 reject_codes, 消息带出口。"""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def recommend_declaration() -> dict[str, Any]:
    """verbs.schema verbs.lybra_owner_recommend(fail-closed): 键齐; reason_max_chars 正整数; actions 恰为已实现的四种; defaults 每条
    category ∈ 收件箱类别、applies_to ∈ {envelope, authorization, grant_request} 且 action 与之对应、choice ∈ 收件箱选项、reason 非空; texts / exit_codes 齐。"""
    from tools.aipos_cli.owner_inbox import inbox_declaration
    from tools.schema_loader import SchemaLoadError, load_schema

    decl = (load_schema("verbs").get("verbs") or {}).get(VERB)
    where = f"verbs.schema.json verbs.{VERB}"
    if not isinstance(decl, dict) or any(k not in decl for k in _KEYS):
        raise SchemaLoadError(f"{where} 未声明齐 {list(_KEYS)}")
    for key in ("reason_max_chars", "renew_days_cap"):
        if not isinstance(decl[key], int) or isinstance(decl[key], bool) or decl[key] < 1:
            raise SchemaLoadError(f"{where}.{key} 须为正整数")
    from tools.aipos_cli.owner_inbox import _CANONICAL_KEYS

    if not isinstance(decl["hash_basis"], list) or not decl["hash_basis"] or not set(decl["hash_basis"]) <= set(_CANONICAL_KEYS):
        raise SchemaLoadError(f"{where}.hash_basis 须为事项规范键 {list(_CANONICAL_KEYS)} 的非空子集")
    if sorted(decl["actions"]) != sorted(_ACTIONS):
        raise SchemaLoadError(f"{where}.actions 须恰为 {list(_ACTIONS)}")
    inbox = inbox_declaration()
    for name, spec in (decl["defaults"] or {}).items():
        if (not isinstance(spec, dict) or spec.get("category") not in inbox["categories"] or spec.get("applies_to") not in _APPLIES
                or spec.get("action") != _APPLIES[spec["applies_to"]] or spec.get("choice") not in inbox["options"]
                or not str(spec.get("reason") or "").strip()):
            raise SchemaLoadError(f"{where}.defaults.{name} 须有 category(收件箱类别)/ applies_to({'|'.join(_APPLIES)})/ 对应 action / "
                                  "choice(收件箱选项)/ 非空 reason")
    if any(not isinstance((decl["texts"] or {}).get(k), str) for k in _TEXTS):
        raise SchemaLoadError(f"{where}.texts 须有 {list(_TEXTS)}")
    if any(k not in (decl["exit_codes"] or {}) for k in _EXITS):
        raise SchemaLoadError(f"{where}.exit_codes 须有 {list(_EXITS)}")
    return decl


def _exit(name: str) -> int:
    return int(recommend_declaration()["exit_codes"][name]["code"])


def _parse_time(value: Any) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        moment = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    return moment if moment.tzinfo else moment.replace(tzinfo=timezone.utc)


def _action_for(item: dict[str, Any]) -> str:
    """顾问推荐的一键动作: 验收抽查类(board.decide.verify.categories)= verify, 其余 = decide(F226 同一写路由)。"""
    from tools.aipos_cli.owner_inbox import board_decide_declaration

    return "verify" if item["category"] in board_decide_declaration()["verify"]["categories"] else "decide"


# ---------------------------------------------------------------------------
# 读: 事项的有效推荐(顾问 / Owner 记录优先, 其次产品缺省)
# ---------------------------------------------------------------------------

def recommendation_index(governance_root: str | Path) -> set[str]:
    """本治理根有推荐记录的事项 ID 集合(记录类根目录一次列举; 目录不在 = 空集)。collect_project_inbox 每根算一次, 免逐项探目录。"""
    from tools.aipos_cli.record_writer import record_dir

    base = record_dir(Path(governance_root), str(recommend_declaration()["record_kind"]))
    if not base.is_dir():
        return set()
    return {entry.name for entry in base.iterdir() if entry.is_dir()}


def _item_records(root: Path, item_id: str, decl: dict[str, Any]) -> list[dict[str, Any]]:
    from tools.aipos_cli.frontmatter import FrontmatterReadError, require_frontmatter
    from tools.aipos_cli.record_writer import record_dir, record_file_prefix

    kind = str(decl["record_kind"])
    out: list[dict[str, Any]] = []
    for path in sorted(record_dir(root, kind, item_id).glob(f"{record_file_prefix(kind)}_*.md")):
        try:
            meta, _body = require_frontmatter(path)
        except FrontmatterReadError as exc:  # 读不出 = 点名(不静默当无推荐)
            raise ValueError(f"推荐记录读不出 {path}: {exc}") from exc
        if meta.get("record_type") != decl["record_type"]:
            raise ValueError(f"{path}: 不是推荐记录(record_type ≠ {decl['record_type']})")
        out.append({**meta, "_at": _parse_time(meta.get("written_at")), "record": path.resolve().relative_to(root.resolve()).as_posix()})
    return out


def recommendation_for(governance_root: str | Path, item: dict[str, Any], *, index: set[str] | None = None) -> dict[str, Any] | None:
    """事项的推荐(唯一读取口): 该事项最新一条记录且其 item_hash = 现在的事项哈希、choice 仍是事项选项 = 顾问 / Owner 推荐; 否则产品缺省
    (default_recommendation); 都无 = None。返回 {choice, label, reason, source(advisor|default), by, action, record, params?, days?, max_tasks?}。
    顾问推荐的选项恰是缺省的选项(如续签)= 动作与参数取缺省(一键即续), 理由与推荐人取顾问的。记录读不出 = ValueError(调用方点名)。"""
    from tools.aipos_cli.owner_inbox import item_hash

    decl = recommend_declaration()
    root = Path(governance_root)
    fallback = default_recommendation(root, item, decl)
    iid = str(item["item_id"])
    if index is None or iid in index:
        records = [r for r in _item_records(root, iid, decl) if r["_at"] is not None]
        records.sort(key=lambda r: (r["_at"], r["record"]))
        latest = records[-1] if records else None
        labels = {o["key"]: o["label"] for o in item.get("options") or []}
        if latest and str(latest.get("item_hash") or "") == item_hash(item, decl["hash_basis"]) and str(latest.get("choice") or "") in labels:
            choice = str(latest["choice"])
            out = {"choice": choice, "label": labels[choice], "reason": str(latest.get("reason") or ""), "source": "advisor",
                   "by": str(latest.get("written_by") or "") or None, "action": _action_for(item), "record": latest["record"]}
            if fallback and fallback["choice"] == choice:
                out.update({k: fallback[k] for k in ("action", "params", "days", "max_tasks") if k in fallback})
            return out
    return fallback


def default_recommendation(governance_root: Path, item: dict[str, Any], decl: dict[str, Any] | None = None) -> dict[str, Any] | None:
    """按类别声明的产品缺省(defaults): 信封将尽 = 按原范围续签(天数 = 原有效期天数, 张数 = 原额度; 须为当前在用且可续); 项目授权将尽 =
    按原范围扩大(到期 = 现在 + 原有效期天数, 总卡数 = 已用 + 原总卡数); 授权用尽 / 不覆盖(带 grant 建议的授权请求)= 按建议签发
    (请求体 owner_inbox.grant_request_payload, 看板「待授权请求」卡同一构造)。其余类别无缺省 = None。参数即看板一键签发请求体
    (F172 写例外①同一实现: mode renew / authorization extend / new; request_item = 本事项 ID)。"""
    from tools.aipos_cli.clock import iso_z, utc_now
    from tools.aipos_cli.owner_inbox import grant_request_payload, inbox_declaration

    decl = decl or recommend_declaration()
    facts = item.get("facts") or {}
    applies = ("grant_request" if isinstance(item.get("grant"), dict) else "authorization" if "authorization_revision" in facts
               else "envelope")
    spec = next((s for s in (decl["defaults"] or {}).values() if s["category"] == item["category"] and s["applies_to"] == applies), None)
    if spec is None:
        return None
    now = utc_now().replace(microsecond=0)
    if applies == "grant_request":
        params = grant_request_payload(item)
        grant = item["grant"]
        block = grant.get("authorization") if isinstance(grant.get("authorization"), dict) else {}
        count = int(grant.get("max_tasks") or block.get("max_tasks") or 0)
        until = _parse_time(grant.get("expires_at") or block.get("expires_at"))
        days = max(1, math.ceil((until - now).total_seconds() / 86400)) if until and until > now else None
        values = {"days": days or "?", "max_tasks": count or "?", "date": str(grant.get("expires_at") or block.get("expires_at") or "?")[:10]}
    else:
        if not item.get("policy_id"):
            return None
        root = Path(governance_root)
        pid = str(item["policy_id"])
        if applies == "authorization":
            from tools.aipos_cli.autonomy_policy import authorization_overview

            row = next((r for r in authorization_overview(root) if str(r.get("authorization_id")) == pid), None)
        else:
            from tools.aipos_cli.autonomy_policy import envelope_overview

            row = next((r for r in envelope_overview(root)["envelopes"] if r["policy_id"] == pid), None)
            if row is not None and (not row["current"] or not row.get("next_policy_id")):
                row = None  # 历史 / 已接替的信封不续签(envelope_board.normalize_mint_params 同一前置)
        start, end = _parse_time((row or {}).get("active_from")), _parse_time((row or {}).get("expires_at"))
        count = int((row or {}).get("max_tasks") or 0)
        if row is None or start is None or end is None or end <= start or count < 1:
            return None
        days: int | None = min(int(decl["renew_days_cap"]), max(1, math.ceil((end - start).total_seconds() / 86400)))
        expires = iso_z(now + timedelta(days=int(days or 0)))
        if applies == "authorization":
            params = {"workspace": item["project"], "mode": "authorization", "action": "extend",
                      "max_tasks": int(row.get("used") or 0) + count, "expires_at": expires, "request_item": item["item_id"]}
            if end >= now + timedelta(days=int(days or 0)):  # 现到期已晚于续期目标 = 不动到期(扩大授权不缩短), 只加张数
                params.pop("expires_at")
                days, expires = None, iso_z(end)
        else:
            params = {"workspace": item["project"], "mode": "renew", "template_policy_id": pid, "max_tasks": count, "expires_at": expires,
                      "request_item": item["item_id"]}
        values = {"days": days or "?", "max_tasks": count, "date": expires[:10]}
    label = str(spec.get("label") or (inbox_declaration()["options"].get(spec["choice"]) or {}).get("label") or spec["choice"])
    return {"choice": str(spec["choice"]), "label": label, "reason": str(spec["reason"]).format(**values), "source": "default", "by": None,
            "action": str(spec["action"]), "record": None, "params": params, "days": values["days"] if values["days"] != "?" else None,
            "max_tasks": values["max_tasks"]}


def recommendation_line(item: dict[str, Any]) -> str | None:
    """CLI 收件箱一行(owner_inbox.item_lines 调; 无推荐 = None)。"""
    rec = item.get("recommended")
    if not isinstance(rec, dict):
        return None
    texts = recommend_declaration()["texts"]
    by = (texts["by_advisor"].format(by=rec.get("by") or "?") if rec.get("source") == "advisor" else texts["by_default"])
    return texts["inbox_line"].format(label=rec.get("label") or rec.get("choice"), reason=rec.get("reason") or "", by=by)


# ---------------------------------------------------------------------------
# 写: 两阶段 + 落账
# ---------------------------------------------------------------------------

def write_recommendation(item_id: str, *, choice: str, reason: str, actor: str | None = None, home_root: str | Path | None = None,
                         confirm: bool = False, connection_json: str | Path | None = None, moment: datetime | None = None) -> dict[str, Any]:
    """预演(confirm=False)零写入; 确认 = 写一条只追加记录并落账。返回 {stage, item_id, project, subject, choice, label, reason, actor,
    writer_role, record, landing}; 事项不在 = owner_inbox.InboxItemNotFound; 拒 = RecommendRefused(code ∈ reject_codes)。"""
    from tools.aipos_cli.advisor_headline import writer_credential_role
    from tools.aipos_cli.clock import file_slug, iso_z, utc_now
    from tools.aipos_cli.owner_inbox import find_items, item_hash
    from tools.aipos_cli.record_writer import record_dir, record_location, render_markdown

    decl = recommend_declaration()
    item = find_items([str(item_id or "").strip()], home_root=home_root)[0][0]
    root = Path(item["governance_root"])
    labels = {o["key"]: o["label"] for o in item.get("options") or []}
    key = str(choice or "").strip()
    if key not in labels:
        raise RecommendRefused("bad_choice", f"--choice {key or '(空)'} 不是事项 {item['item_id']} 的选项; 可选: "
                                             + " / ".join(f"{k}={v}" for k, v in labels.items()))
    text = str(reason or "").strip()
    if not text:
        raise RecommendRefused("empty", "--reason 不能为空: 写一句人话理由(看板推荐按钮旁原样显示)")
    if "\n" in text or "\r" in text or len(text) > int(decl["reason_max_chars"]):
        got = "多行" if "\n" in text or "\r" in text else f"{len(text)} 字"
        raise RecommendRefused("too_long", f"--reason 须为单行且 ≤ {decl['reason_max_chars']} 字(得 {got}); 写成一句人话")
    relay = str(item.get("relay_to") or "").strip()
    owner_actor = str(decl["owner_actor"])
    writer = str(actor or "").strip() or relay
    if not writer:
        raise RecommendRefused("not_writer", f"事项 {item['item_id']} 没有可判定的转达顾问; 出口: 请 Owner 写(--actor {owner_actor})")
    role = writer_credential_role(root, instance=relay or owner_actor, actor=writer, owner_actor=owner_actor,
                                  connection_json=Path(connection_json).expanduser() if connection_json else None, refused=RecommendRefused,
                                  what=f"事项 {item['item_id']} 的推荐")
    at = moment or utc_now()
    kind = str(decl["record_kind"])
    filename = Path(record_location(kind)).name.format(item_id=item["item_id"], timestamp=file_slug("compact", at))
    path = record_dir(root, kind, item["item_id"]) / filename
    rel = path.relative_to(root).as_posix()
    out: dict[str, Any] = {"stage": "confirm" if confirm else "preview", "item_id": item["item_id"], "project": item["project"],
                           "subject": item["subject"], "choice": key, "label": labels[key], "reason": text, "actor": writer,
                           "writer_role": role, "record": rel, "landing": None}
    if not confirm:
        return out
    if path.exists():
        raise RecommendRefused("exists", f"{rel} 已存在(同秒), 只追加不覆盖; 出口: 稍后重跑")
    meta = {"record_type": str(decl["record_type"]), "recommendation_id": path.stem, "item_id": item["item_id"], "project": item["project"],
            "category": item["category"], "subject": item["subject"], "task_id": item.get("task_id"), "policy_id": item.get("policy_id"),
            "choice": key, "reason": text, "relay_to": relay or None, "written_by": writer, "written_at": iso_z(at),
            "item_hash": item_hash(item, decl["hash_basis"])}
    body = f"# 收件箱事项推荐 · {item['item_id']}\n\n- 事项: {item['project']} / {item['subject']}\n- 推荐: {labels[key]}({key})\n- 理由: {text}\n"
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8") as fh:  # 排他创建: 只追加不覆盖
        fh.write(render_markdown(meta, body))
    from tools.aipos_cli.governance_commit import land_written_paths

    out["landing"] = land_written_paths(root, [rel], writer, command=str(decl["landing_command"]), item_id=item["item_id"])
    return out


# ---------------------------------------------------------------------------
# CLI 薄壳
# ---------------------------------------------------------------------------

def run_recommend_cli(args: Any) -> int:
    import json

    from tools.aipos_cli.owner_inbox import InboxItemNotFound
    from tools.schema_loader import SchemaLoadError

    if bool(getattr(args, "dry_run", False)) and bool(getattr(args, "confirm", False)):
        print("lybra owner recommend: --dry-run 与 --confirm 不能同时给", file=sys.stderr)
        return _exit("usage")
    try:
        report = write_recommendation(getattr(args, "item", None) or "", choice=getattr(args, "choice", None) or "",
                                      reason=getattr(args, "reason", None) or "", actor=getattr(args, "actor", None),
                                      home_root=getattr(args, "home_root", None), confirm=bool(getattr(args, "confirm", False)),
                                      connection_json=getattr(args, "connection_json", None))
    except InboxItemNotFound as exc:
        print(f"lybra owner recommend: {exc}", file=sys.stderr)
        return _exit("not_found")
    except RecommendRefused as exc:
        print(f"lybra owner recommend: 拒({exc.code}): {exc}", file=sys.stderr)
        return _exit("refused")
    except (FileNotFoundError, SchemaLoadError, ValueError, OSError) as exc:
        print(f"lybra owner recommend: {type(exc).__name__}: {exc}", file=sys.stderr)
        return _exit("refused")
    landing = report.get("landing")
    texts = recommend_declaration()["texts"]
    if getattr(args, "json", False):
        print(json.dumps({**report, "landing": ({k: v for k, v in landing.items() if k != "line"} if landing else None)},
                         ensure_ascii=False, indent=2))
    else:
        print(str(texts["preview" if report["stage"] == "preview" else "written"]).format(**report))
        if landing:
            print(landing["line"], file=sys.stdout if landing.get("landed") else sys.stderr)
    if landing and not landing.get("landed"):
        return int(landing.get("exit_code") or _exit("landing_failed"))
    return _exit("ok")


# AIPOS-316: Guard against direct invocation
from tools.aipos_cli._cli_entry_guard import check_direct_invocation  # noqa: E402

check_direct_invocation(__name__)
