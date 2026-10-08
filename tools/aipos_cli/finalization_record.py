"""
AIPOS-R8B 大项B: N5 finalization 记录写入

finalization 记录必落、按 <task_id> 分目录(与其它节点一致,finalize 不部署时也必须有)。
deployment 记录可选、按 <commit> 分目录(部署跨卡,键不同是对的)。

落点: 声明 transitions.schema nodes.N5.record.location(record_locations.kinds.finalizations), 读取口 record_writer.record_dir;
落盘名 = finalize_ref = record_writer.build_runtime_id("finalization", task_id, finalized_at, actor)(AIPOS-F109 件①:
干跑预览与真写同一推导; 存量旧名 finalization_<compact>.md / finalization_<ID>_<ISO 带冒号>.md 照读, 读侧 glob finalization_*.md)。
"""
import json
import sys
from tools.aipos_cli.clock import iso_z
from pathlib import Path
from typing import Any


def _finalize_ref(task_id: str, finalized_at: str, actor: str) -> str:
    """finalize_ref = 记录 id = 落盘名(AIPOS-F109 件①): record_writer.build_runtime_id("finalization", …)。"""
    from tools.aipos_cli.record_writer import build_runtime_id

    return build_runtime_id("finalization", task_id, finalized_at, actor)


def build_finalization_record(
    *,
    task_id: str,
    actor: str,
    commit: str,
    authorization_type: str,
    authorization_ref: str,
    finalized_at: str | None = None,
    deployed: bool = False,
    deployment_record_ref: str | None = None,
    deploy_status: str | None = None,
    merge_commit: str | None = None,
    remote_ref: str | None = None,
    finalize_return_ref: str | None = None,
    post_merge_regression: dict[str, Any] | None = None,
    push_status: str | None = None,
    push_status_reason: str | None = None,
    deploy_status_reason: str | None = None,
) -> dict[str, Any]:
    """构造 finalization 记录 frontmatter。

    AIPOS-F135 件②③: push_status(值域声明 transitions N5.record.push_status.values; 值域外 = ValueError, fail-closed)与
    push_status_reason / deploy_status_reason(依据, 声明 N5.record.*.reason_field)给了就入记录; 外部 ingest 不给 = 不写(行为不变)。

    AIPOS-F118 件①: post_merge_regression = 合并后回归检查结果(声明 transitions N5.record.post_merge_regression;
    post_merge_regression.check_after_merge 产出), 给了就原样入记录——下一次合并取 merged_failures 作合并前基线。

    AIPOS-F78B 件②: finalize_mode=external 时同一 writer 由 artifact ingest 调用, 追加 remote_ref(外部 FINALIZE Return 自述的远端 ref)
    与 finalize_return_ref(FINALIZE Return 相对治理根路径); 声明 transitions artifact_ingest.finalization.record.extra_fields。

    AIPOS-F73D 前置一①: deploy_status 字段(值域声明在 transitions.schema N5.record.deploy_status)——
    finalization 记录在 merge+push 成功即落, 部署结果只记不阻记录。缺省按 deployed 布尔推导。
    AIPOS-F78 前置零③: 记录必含 merge_commit(卡分支 merge 后 main HEAD; 缺省=commit)与 finalize_ref(记录 id),
    推导核 N5→N6 读 merge_commit 填 closure_evidence.finalize_commit_hash(声明: transitions N5.record.merge_commit)。
    """
    if deploy_status is None:
        deploy_status = "deployed" if deployed else "not_attempted"
    if finalized_at is None:
        finalized_at = iso_z()
    merge_commit = str(merge_commit or commit or "").strip()
    if not merge_commit:
        raise ValueError("finalization 记录缺 merge_commit/commit(AIPOS-F78 前置零③: 三字段齐才派生 close)")
    
    record = {
        "record_type": "finalization_record",
        "operation": "finalize",
        "task_id": task_id,
        "actor": actor,
        "finalized_at": finalized_at,
        "commit": commit,
        "commit_short": commit[:8],
        "merge_commit": merge_commit,
        "finalize_ref": _finalize_ref(task_id, finalized_at, actor),
        "authorization_type": authorization_type,
        "authorization_ref": authorization_ref,
        "deployed": deployed,
        "deploy_status": deploy_status,
    }
    
    if deploy_status_reason:
        record["deploy_status_reason"] = str(deploy_status_reason).strip()
    if push_status is not None:
        allowed = _declared_push_status_values()
        if push_status not in allowed:
            raise ValueError(f"push_status={push_status!r} 不在声明值域 {allowed}(transitions N5.record.push_status.values)")
        record["push_status"] = push_status
        if push_status_reason:
            record["push_status_reason"] = str(push_status_reason).strip()
    if deployment_record_ref:
        record["deployment_record_ref"] = deployment_record_ref
    if remote_ref:
        record["remote_ref"] = str(remote_ref).strip()
    if finalize_return_ref:
        record["finalize_return_ref"] = str(finalize_return_ref).strip()
    if post_merge_regression is not None:
        record["post_merge_regression"] = post_merge_regression

    return record


def _declared_push_status_values() -> list[str]:
    """AIPOS-F135 件②: push_status 值域唯一声明 transitions N5.record.push_status.values(缺 = SchemaLoadError, fail-closed)。"""
    from tools.schema_loader import SchemaLoadError, load_schema

    decl = ((load_schema("transitions").get("nodes") or {}).get("N5") or {}).get("record", {}).get("push_status")
    values = decl.get("values") if isinstance(decl, dict) else None
    if not isinstance(values, list) or not values:
        raise SchemaLoadError("transitions.schema.json nodes.N5.record.push_status.values 未声明")
    return [str(v) for v in values]


def record_path(governance_root: Path, task_id: str, finalize_ref: str) -> Path:
    """finalization 记录路径(AIPOS-F109 件①): 声明落点目录(record_dir) / <finalize_ref>.md——与 write_records_atomic 真写同一推导。"""
    from tools.aipos_cli.record_writer import record_dir

    return record_dir(governance_root, "finalizations", task_id) / f"{finalize_ref}.md"


def existing_finalization_records(governance_root: Path, task_id: str) -> list[Path]:
    """本卡已落的 finalization 记录(新到旧, 按 mtime)——「有无 finalization 记录」唯一判据(AIPOS-F120):
    推导核 N5→N6(next_resolver)与 finalize 续跑补记录(件②)共用。读侧 glob finalization_*.md(含存量旧名)。"""
    from tools.aipos_cli.record_writer import record_dir

    directory = record_dir(governance_root, "finalizations", task_id)
    if not directory.is_dir():
        return []
    return sorted(directory.glob("finalization_*.md"), key=lambda p: p.stat().st_mtime, reverse=True)


def render_record_markdown(frontmatter: dict[str, Any]) -> str:
    """渲染 finalization 记录 Markdown.

    AIPOS-F46: 收敛到 F22B 单源 (record_writer.render_markdown).
    原实现用 yaml.dump 直接拼接, 绕过 safe_dump 单源.
    """
    from tools.aipos_cli.record_writer import render_markdown as _render_markdown_single_source

    body = f"""# Finalization Record: {frontmatter['task_id']}

- **task_id**: {frontmatter['task_id']}
- **commit**: {frontmatter['commit']}
- **merge_commit**: {frontmatter['merge_commit']}
- **finalize_ref**: {frontmatter['finalize_ref']}
- **actor**: {frontmatter['actor']}
- **finalized_at**: {frontmatter['finalized_at']}
- **authorization_type**: {frontmatter['authorization_type']}
- **authorization_ref**: {frontmatter['authorization_ref']}
- **deployed**: {frontmatter['deployed']}
- **deploy_status**: {frontmatter['deploy_status']}
"""
    for extra in ("deploy_status_reason", "push_status", "push_status_reason"):
        if frontmatter.get(extra):
            body += f"- **{extra}**: {frontmatter[extra]}\n"

    if frontmatter.get("deployment_record_ref"):
        body += f"- **deployment_record_ref**: {frontmatter['deployment_record_ref']}\n"
    for extra in ("remote_ref", "finalize_return_ref"):
        if frontmatter.get(extra):
            body += f"- **{extra}**: {frontmatter[extra]}\n"
    if isinstance(frontmatter.get("post_merge_regression"), dict):
        body += f"- **post_merge_regression**: {frontmatter['post_merge_regression'].get('summary')}\n"

    return _render_markdown_single_source(frontmatter, body)


def write_finalization_record(
    *,
    governance_root: Path,
    task_id: str,
    actor: str,
    commit: str,
    authorization_type: str,
    authorization_ref: str,
    deployed: bool = False,
    deployment_record_ref: str | None = None,
    finalized_at: str | None = None,
    dry_run: bool = False,
    deploy_status: str | None = None,
    merge_commit: str | None = None,
    remote_ref: str | None = None,
    finalize_return_ref: str | None = None,
    post_merge_regression: dict[str, Any] | None = None,
    push_status: str | None = None,
    push_status_reason: str | None = None,
    deploy_status_reason: str | None = None,
) -> dict[str, Any]:
    """写 finalization_record 到治理工作区 records。返回 {ok, path, wrote}。"""
    frontmatter = build_finalization_record(
        post_merge_regression=post_merge_regression,
        push_status=push_status,
        push_status_reason=push_status_reason,
        deploy_status_reason=deploy_status_reason,
        task_id=task_id,
        actor=actor,
        commit=commit,
        remote_ref=remote_ref,
        finalize_return_ref=finalize_return_ref,
        authorization_type=authorization_type,
        authorization_ref=authorization_ref,
        deployed=deployed,
        deployment_record_ref=deployment_record_ref,
        finalized_at=finalized_at,
        deploy_status=deploy_status,
        merge_commit=merge_commit,
    )
    path = record_path(governance_root, task_id, frontmatter["finalize_ref"])
    if dry_run:
        return {"ok": True, "path": str(path), "wrote": False, "frontmatter": frontmatter}
    
    # AIPOS-F64: 统一写入器
    from tools.aipos_cli.record_writer import write_records_atomic
    finalization_markdown = render_record_markdown(frontmatter)

    write_result = write_records_atomic(
        repo_root=governance_root,
        records=[("finalization", frontmatter["finalize_ref"], finalization_markdown, task_id)],
    )
    
    return {"ok": True, "path": write_result["paths"][0], "wrote": True, "frontmatter": frontmatter}


def main(argv: list[str] | None = None) -> int:
    """CLI 入口: python3 -m tools.aipos_cli.finalization_record <args>"""
    import argparse

    parser = argparse.ArgumentParser(description="AIPOS-R8B: write a finalization_record")
    parser.add_argument("--governance-root", required=True, help="Governance workspace root")
    parser.add_argument("--task-id", required=True, help="Task ID")
    parser.add_argument("--commit", required=True, help="Full git commit hash")
    parser.add_argument("--actor", required=True, help="Finalizing actor")
    parser.add_argument("--authorization-type", required=True, help="Authorization type (verdict_ref/dev_override)")
    parser.add_argument("--authorization-ref", required=True, help="Authorization reference")
    parser.add_argument("--deployed", action="store_true", help="Whether deployed")
    parser.add_argument("--deploy-status", help="AIPOS-F73D: deploy_status (values declared in transitions.schema N5.record.deploy_status)")
    parser.add_argument("--deployment-record-ref", help="Deployment record reference")
    parser.add_argument("--dry-run", action="store_true", help="Preview only")
    args = parser.parse_args(argv)

    result = write_finalization_record(
        governance_root=Path(args.governance_root),
        task_id=args.task_id,
        actor=args.actor,
        commit=args.commit,
        authorization_type=args.authorization_type,
        authorization_ref=args.authorization_ref,
        deployed=args.deployed,
        deployment_record_ref=args.deployment_record_ref,
        dry_run=args.dry_run,
        deploy_status=args.deploy_status,
    )
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
