"""
AIPOS-R8B 大项B: N5 finalization 记录写入

finalization 记录必落、按 <task_id> 分目录(与其它节点一致,finalize 不部署时也必须有)。
deployment 记录可选、按 <commit> 分目录(部署跨卡,键不同是对的)。

落点: <governance_root>/5_tasks/records/finalizations/<task_id>/finalization_<timestamp>.md
"""
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


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
) -> dict[str, Any]:
    """构造 finalization 记录 frontmatter"""
    if finalized_at is None:
        finalized_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    
    record = {
        "record_type": "finalization_record",
        "operation": "finalize",
        "task_id": task_id,
        "actor": actor,
        "finalized_at": finalized_at,
        "commit": commit,
        "commit_short": commit[:8],
        "authorization_type": authorization_type,
        "authorization_ref": authorization_ref,
        "deployed": deployed,
    }
    
    if deployment_record_ref:
        record["deployment_record_ref"] = deployment_record_ref
    
    return record


def record_path(governance_root: Path, task_id: str, timestamp: str) -> Path:
    """计算 finalization 记录路径: 5_tasks/records/finalizations/<task_id>/finalization_<ts>.md"""
    ts_compact = timestamp.replace(":", "").replace("-", "").replace("T", "_").replace("Z", "")
    filename = f"finalization_{ts_compact}.md"
    return governance_root / "5_tasks" / "records" / "finalizations" / task_id / filename


def render_record_markdown(frontmatter: dict[str, Any]) -> str:
    """渲染 finalization 记录 Markdown.

    AIPOS-F46: 收敛到 F22B 单源 (record_writer.render_markdown).
    原实现用 yaml.dump 直接拼接, 绕过 safe_dump 单源.
    """
    from tools.aipos_cli.record_writer import render_markdown as _render_markdown_single_source

    body = f"""# Finalization Record: {frontmatter['task_id']}

- **task_id**: {frontmatter['task_id']}
- **commit**: {frontmatter['commit']}
- **actor**: {frontmatter['actor']}
- **finalized_at**: {frontmatter['finalized_at']}
- **authorization_type**: {frontmatter['authorization_type']}
- **authorization_ref**: {frontmatter['authorization_ref']}
- **deployed**: {frontmatter['deployed']}
"""

    if frontmatter.get("deployment_record_ref"):
        body += f"- **deployment_record_ref**: {frontmatter['deployment_record_ref']}\n"

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
) -> dict[str, Any]:
    """写 finalization_record 到治理工作区 records。返回 {ok, path, wrote}。"""
    frontmatter = build_finalization_record(
        task_id=task_id,
        actor=actor,
        commit=commit,
        authorization_type=authorization_type,
        authorization_ref=authorization_ref,
        deployed=deployed,
        deployment_record_ref=deployment_record_ref,
        finalized_at=finalized_at,
    )
    path = record_path(governance_root, task_id, frontmatter["finalized_at"])
    if dry_run:
        return {"ok": True, "path": str(path), "wrote": False, "frontmatter": frontmatter}
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(render_record_markdown(frontmatter), encoding="utf-8")
    return {"ok": True, "path": str(path), "wrote": True, "frontmatter": frontmatter}


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
    )
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
