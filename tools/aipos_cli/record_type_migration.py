"""AIPOS-F158 件②(gap #130): `lybra migrate record-type` —— 治理根里滞留未入库、缺记录类型字段(record_type)的记录补齐迁移。

病根: F147 起入库护栏 B④ 对 records/** 新增文件查必填 record_type(config.schema file_declarations.record_file); 早于
AIPOS-F89 件③d 的审计报告快照(records/audit_verdicts/<ID>/report_snapshots/)是报告原文直拷、无记录头, 被 B④ 拒入, 长期滞留未跟踪。

判据与推导(全读声明, 禁写死项目 ID / 路径 / 卡号前缀):
  - 候选 = 治理根 records 目录(config.schema governance_structure.paths, 与护栏同读 governance_guardrails.load_guardrail_declarations)
    下的「将新增」文件(git 未跟踪 / 已暂存新增, 清单读 governance_commit.collect_commit_manifest), 经护栏同一判据
    governance_guardrails.check_entries 判出 B④ 拒入且缺必填字段者——护栏放行(含产物槽 / 冻结卡豁免)的不碰。
  - 类型 = 按文件路径匹配声明的记录落点: transitions artifact_ingest.verdict.report_snapshot(审计报告快照)与
    record_locations.kinds(各记录类); 唯一命中才算推导出, 取值须 ∈ config.schema file_declarations.record_file.value_enums 所指枚举。
  - 改写 = 只对声明了可复原记录头的类做(report_snapshot.header_fields: 记录头 + 报告原文逐字节, 记录头字段取自引用该快照的裁决记录,
    且裁决记录 report_snapshot_sha256 须等于文件字节 sha256 —— 证明文件就是报告原文), 构造经 board_adapter.render_report_snapshot
    (与门写快照同一实现)。其余记录类缺 record_type = 门出生标记缺失, 补字段即伪造门记录: 列出推导出的类型但不改。
  - 推导不出(无落点命中 / 多义 / 裁决记录对不上 / 取值不在枚举)= 列出原因, 不改。

两阶段(verbs.schema two_phase_protocol.records_migration_writers): 缺省预演(零写入), --confirm 写入并经 N6 同一提交口落账
(governance_commit.land_written_paths, 提交范围 = 本次改写的文件)。
"""
from __future__ import annotations

import hashlib
import os
import re
import subprocess
import tempfile
from pathlib import Path
from typing import Any

#: 本迁移在落账声明(verbs.schema lybra_governance_commit.post_write_landing.commands)里的命令键
LANDING_COMMAND = "migrate_record_type"

DERIVED = "derived"            # 推导出且将改写
TYPED_NOT_REBUILDABLE = "typed_not_rebuildable"  # 推导出类型, 但该类无可复原记录头声明: 列出不改
UNDERIVABLE = "underivable"    # 推导不出: 列出不改


class MigrationError(RuntimeError):
    """迁移前提不成立(治理根不在 git 仓 / 声明不可读): 拒因 + 出口。"""

    def __init__(self, message: str, next_step: str | None = None) -> None:
        super().__init__(message)
        self.next_step = next_step


def _template_regex(template: str) -> re.Pattern[str]:
    """落点模板(`a/{x}/b_{y}.md`)→ 正则: 占位 = 单个路径段内的非空串(重复占位须同值)。"""
    out: list[str] = []
    seen: set[str] = set()
    pos = 0
    for m in re.finditer(r"\{([A-Za-z_]+)\}", template):
        out.append(re.escape(template[pos:m.start()]))
        name = m.group(1)
        out.append(f"(?P={name})" if name in seen else f"(?P<{name}>[^/]+)")
        seen.add(name)
        pos = m.end()
    out.append(re.escape(template[pos:]))
    return re.compile("^" + "".join(out) + "$")


def declared_record_locations(governance_root: Path) -> list[dict[str, Any]]:
    """声明的记录落点(治理根相对模板)与其记录类型: 审计报告快照(可复原记录头)+ record_locations 各记录类。"""
    from tools.aipos_cli.board_adapter import report_snapshot_declaration
    from tools.aipos_cli.next_resolver import _resolve_governance_path_with_relative
    from tools.aipos_cli.record_writer import _record_kinds, record_location

    root = Path(governance_root)
    snap = report_snapshot_declaration()
    base = _resolve_governance_path_with_relative(str(snap.get("relative_to") or "records"), root)
    snap_template = f"{base.relative_to(root).as_posix().rstrip('/')}/{snap['location']}"
    locations = [{"kind": "report_snapshot", "template": snap_template, "pattern": _template_regex(snap_template),
                  "record_types": [str(snap["record_type"])], "rebuild": "report_snapshot"}]
    for kind, entry in sorted(_record_kinds().items()):
        template = record_location(kind)
        locations.append({"kind": kind, "template": template, "pattern": _template_regex(template),
                          "record_types": [str(t) for t in (entry.get("record_types") or [])], "rebuild": None})
    return locations


def _derive_snapshot(root: Path, rel: str, match: re.Match[str], raw: bytes) -> dict[str, Any]:
    """审计报告快照: 记录头字段取自引用本快照的裁决记录; 裁决记录 report_snapshot_sha256 须 = 文件字节 sha256。"""
    from tools.aipos_cli.board_adapter import render_report_snapshot, unwrap_report_snapshot
    from tools.aipos_cli.frontmatter import parse_markdown_frontmatter
    from tools.aipos_cli.record_writer import audit_verdict_record_path

    reviewed, verdict_id = match.group("reviewed_task_id"), match.group("verdict_id")
    try:
        verdict_path = audit_verdict_record_path(root, reviewed, verdict_id)
    except ValueError as exc:
        return {"status": UNDERIVABLE, "reason": f"裁决记录路径不可解析: {exc}"}
    if not verdict_path.is_file():
        return {"status": UNDERIVABLE, "reason": f"引用本快照的裁决记录不存在: {verdict_path.relative_to(root).as_posix()}"}
    meta, _body, _warnings = parse_markdown_frontmatter(verdict_path.read_text(encoding="utf-8"))
    sha = hashlib.sha256(raw).hexdigest()
    verdict_rel = verdict_path.relative_to(root).as_posix()
    if str(meta.get("report_snapshot_ref") or "") != rel:
        return {"status": UNDERIVABLE, "reason": f"裁决记录 {verdict_rel} 的 report_snapshot_ref={meta.get('report_snapshot_ref')!r} 不指向本文件"}
    if str(meta.get("report_snapshot_sha256") or "") != sha:
        return {"status": UNDERIVABLE, "reason": f"文件 sha256 {sha[:12]} ≠ 裁决记录 {verdict_rel} 的 report_snapshot_sha256 "
                                                 f"{str(meta.get('report_snapshot_sha256') or '')[:12]}(文件不是入门时的报告原文)"}
    missing = [k for k in ("audit_task_id", "report_source_ref") if not str(meta.get(k) or "").strip()]
    if missing:
        return {"status": UNDERIVABLE, "reason": f"裁决记录 {verdict_rel} 缺 {missing}, 记录头推导不出"}
    if str(meta.get("verdict_id") or verdict_id) != verdict_id:
        return {"status": UNDERIVABLE, "reason": f"裁决记录 {verdict_rel} 的 verdict_id={meta.get('verdict_id')!r} 与快照文件名不符"}
    content, header_sha = render_report_snapshot(raw, verdict_id=verdict_id, reviewed_task_id=reviewed,
                                                 audit_task_id=str(meta["audit_task_id"]), source_ref=str(meta["report_source_ref"]))
    if unwrap_report_snapshot(content).encode("utf-8") != raw or header_sha != sha:  # 复原自检: 去记录头 = 原文逐字节
        return {"status": UNDERIVABLE, "reason": "记录头包装后复原自检不一致(非 UTF-8 原文?)"}
    return {"status": DERIVED, "content": content, "basis": f"裁决记录 {verdict_rel}(report_snapshot_ref 指向本文件, sha256 {sha[:12]} 一致)"}


def plan_record_type_migration(governance_root: Path | str) -> dict[str, Any]:
    """只读: 列出缺 record_type 而被 B④ 拒入的记录、推导出的类型与处置。不写盘、不碰 git 索引。"""
    from tools.aipos_cli.governance_commit import collect_commit_manifest, non_git_exit_command
    from tools.aipos_cli.governance_guardrails import (
        CHECK_B4,
        GuardrailDeclarationError,
        check_entries,
        git_current_branch,
        git_toplevel,
        load_guardrail_declarations,
        manifest_entries,
        record_b4_problems,
    )

    root = Path(governance_root).expanduser().resolve()
    try:
        git_root = git_toplevel(root)
    except subprocess.CalledProcessError as exc:
        raise MigrationError(f"治理根不在 git 工作树内: {root}({(exc.stderr or '').strip()[:200]})", non_git_exit_command(root)) from exc
    try:
        decls = load_guardrail_declarations()
    except GuardrailDeclarationError as exc:
        raise MigrationError(f"护栏声明不可读(fail-closed): {exc}") from exc
    records_rel = decls.records_rel
    if not (root / records_rel).is_dir():
        return {"governance_root": str(root), "records": records_rel, "items": [], "counts": _counts([])}
    manifest = collect_commit_manifest(root, [records_rel])
    adds = {"added", "untracked_selected"}
    entries = manifest_entries({"files": [f for f in manifest["files"] if f["status"] in adds]})
    report = check_entries(git_root, entries, decls, current_branch=git_current_branch(git_root)) if entries else None
    b4_files = sorted({v["file"] for v in (report.violations if report else []) if v["check"] == CHECK_B4})
    ws_prefix = manifest["ws_prefix"]
    locations = declared_record_locations(root)
    items: list[dict[str, Any]] = []
    for repo_path in b4_files:
        rel = repo_path[len(ws_prefix):] if ws_prefix and repo_path.startswith(ws_prefix) else repo_path
        raw = (git_root / repo_path).read_bytes()
        missing, _bad_values = record_b4_problems(raw.decode("utf-8", errors="replace"), decls)
        item: dict[str, Any] = {"path": rel, "missing_fields": missing, "record_type": None, "kind": None}
        if not missing:
            item.update(status=UNDERIVABLE, reason="字段在而取值不在声明枚举内(B④ value_enums), 不属缺字段迁移")
            items.append(item)
            continue
        hits = [loc for loc in locations if loc["pattern"].match(rel)]
        if len(hits) != 1:
            item.update(status=UNDERIVABLE, reason=("路径不匹配任何声明的记录落点" if not hits else
                                                    f"路径同时匹配多个记录落点 {[h['kind'] for h in hits]}(多义)"))
            items.append(item)
            continue
        loc = hits[0]
        item["kind"] = loc["kind"]
        types = loc["record_types"]
        item["record_type"] = types[0] if len(types) == 1 else None
        if loc["rebuild"] != "report_snapshot":
            item.update(status=TYPED_NOT_REBUILDABLE if len(types) == 1 else UNDERIVABLE,
                        reason=(f"落点 {loc['template']} 对应记录类 {loc['kind']}(类型 {'/'.join(types)}), 该类无可复原记录头声明: "
                                "缺 record_type = 门出生标记缺失, 补字段即伪造门记录, 不改(存量冻结卡走 project freeze-legacy)"))
            items.append(item)
            continue
        derived = _derive_snapshot(root, rel, loc["pattern"].match(rel), raw)
        if derived["status"] == DERIVED:
            # 改写后的内容须过护栏 B④ 同一判据(必填字段齐 + 取值 ∈ 声明枚举), 否则不改
            still_missing, bad_values = record_b4_problems(derived["content"], decls)
            if still_missing or bad_values:
                derived = {"status": UNDERIVABLE, "reason": f"改写后仍不过 B④: 缺 {still_missing} / 取值非法 {bad_values}"}
        item.update(derived)
        items.append(item)
    return {"governance_root": str(root), "records": records_rel, "items": items, "counts": _counts(items)}


def _counts(items: list[dict[str, Any]]) -> dict[str, int]:
    counts = {DERIVED: 0, TYPED_NOT_REBUILDABLE: 0, UNDERIVABLE: 0}
    for item in items:
        counts[item["status"]] += 1
    counts["total"] = len(items)
    return counts


def _atomic_write(path: Path, content: str) -> None:
    fd, tmp = tempfile.mkstemp(prefix=f".{path.name}.", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as handle:
            handle.write(content)
        os.replace(tmp, path)
    except OSError:
        Path(tmp).unlink(missing_ok=True)
        raise


def run_record_type_migration(governance_root: Path | str, *, actor: str | None, confirm: bool, push: bool = True) -> dict[str, Any]:
    """预演(confirm=False): plan 原样返回, 零写入。确认: 改写 derived 项后经 land_written_paths 落账(提交范围 = 改写的文件)。"""
    plan = plan_record_type_migration(governance_root)
    plan.update(confirm=bool(confirm), written=[], landing=None)
    if not confirm:
        return plan
    if not str(actor or "").strip():
        raise MigrationError("--confirm 须给 --actor(落账提交身份)")
    root = Path(plan["governance_root"])
    for item in plan["items"]:
        if item["status"] == DERIVED:
            _atomic_write(root / item["path"], item["content"])
            plan["written"].append(item["path"])
    if plan["written"]:
        from tools.aipos_cli.governance_commit import land_written_paths

        plan["landing"] = land_written_paths(root, plan["written"], str(actor), command=LANDING_COMMAND, push=push,
                                             count=len(plan["written"]))
    return plan
