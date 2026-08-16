"""AIPOS-R6S 大项B①: deployment_record — 每次 deploy 落一条机器可核的记录。

设计权威: HAZARD-LEDGER 2026-08-16 (deploy 是当前唯一无 records 的固化点, 已两次"未审先
deploy") + 迁移门第⑤条(固化点全通 = 每点至少一条真实机器产物)。

record_type=deployment_record (enums.schema 唯一值域源), 落点
<governance_root>/5_tasks/records/deployments/<commit_short>/deployment_<timestamp>.md。

authorization 二选一(缺授权即拒, 见 lybra-deploy 与 deploy_gate):
  - verdict_ref : audited —— finalize 传本卡 PASS 裁决 id (deployment_provenance=audited)
  - dev_override: dev_override —— 显式 --reason 必填 (deployment_provenance=dev_override)

本模块零依赖(不 import 大包), 供 lybra-deploy(shell 内 python3 -m)与 deploy_gate(Python)
共用, 一机制一实现。
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

PROVENANCE_AUDITED = "audited"
PROVENANCE_DEV_OVERRIDE = "dev_override"
VALID_PROVENANCE = (PROVENANCE_AUDITED, PROVENANCE_DEV_OVERRIDE)


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def resolve_authorization(
    *,
    verdict_ref: str | None,
    dev_override: bool,
    reason: str | None,
) -> tuple[str, str] | tuple[None, None]:
    """解析授权 → (authorization_type, authorization_ref)。缺授权 → (None, None)。

    authorization_type ∈ {verdict_ref, dev_override}。
    判据(卡面大项B②): 仅 verdict_ref 或 dev_override(须显式 --reason), 缺授权即拒。
    """
    verdict_ref = (verdict_ref or "").strip() or None
    reason = (reason or "").strip() or None
    if verdict_ref:
        return ("verdict_ref", verdict_ref)
    if dev_override:
        if not reason:
            return (None, None)  # dev_override 缺 reason → 缺授权
        return ("dev_override", reason)
    return (None, None)


def build_deployment_record(
    *,
    commit: str,
    actor: str,
    authorization_type: str,
    authorization_ref: str,
    deployed_at: str | None = None,
    runtime_directory: str | None = None,
) -> dict[str, Any]:
    """构建 deployment_record 字典(frontmatter + 摘要)。"""
    if authorization_type not in VALID_PROVENANCE and authorization_type not in ("verdict_ref", "dev_override"):
        raise ValueError(f"unknown authorization_type: {authorization_type}")
    commit = (commit or "").strip()
    if not commit:
        raise ValueError("commit is required")
    provenance = PROVENANCE_AUDITED if authorization_type == "verdict_ref" else PROVENANCE_DEV_OVERRIDE
    deployed_at = deployed_at or _utc_now()
    frontmatter: dict[str, Any] = {
        "record_type": "deployment_record",
        "operation": "deploy",
        "commit": commit,
        "commit_short": commit[:8],
        "actor": actor or "(unknown)",
        "deployed_at": deployed_at,
        "authorization_type": authorization_type,
        "authorization_ref": authorization_ref,
        "deployment_provenance": provenance,
    }
    if authorization_type == "dev_override":
        frontmatter["dev_override_reason"] = authorization_ref
    if runtime_directory:
        frontmatter["runtime_directory"] = runtime_directory
    return frontmatter


def record_path(governance_root: Path, commit: str, deployed_at: str) -> Path:
    """落点: <governance_root>/5_tasks/records/deployments/<commit_short>/deployment_<ts>.md"""
    ts = deployed_at.replace(":", "").replace("-", "").replace("T", "_").replace("Z", "")[:15]
    return (
        governance_root / "5_tasks" / "records" / "deployments" / commit[:8] / f"deployment_{ts}.md"
    )


def render_record_markdown(frontmatter: dict[str, Any]) -> str:
    body = (
        f"# Deployment Record: {frontmatter['commit_short']}\n\n"
        f"- **commit**: {frontmatter['commit']}\n"
        f"- **actor**: {frontmatter['actor']}\n"
        f"- **deployed_at**: {frontmatter['deployed_at']}\n"
        f"- **authorization_type**: {frontmatter['authorization_type']}\n"
        f"- **authorization_ref**: {frontmatter['authorization_ref']}\n"
        f"- **deployment_provenance**: {frontmatter['deployment_provenance']}\n"
    )
    if frontmatter.get("dev_override_reason"):
        body += f"- **dev_override_reason**: {frontmatter['dev_override_reason']}\n"
    if frontmatter.get("runtime_directory"):
        body += f"- **runtime_directory**: {frontmatter['runtime_directory']}\n"
    fm_lines = ["---"]
    for k, v in frontmatter.items():
        if isinstance(v, str):
            fm_lines.append(f"{k}: {v}")
        else:
            fm_lines.append(f"{k}: {json.dumps(v)}")
    fm_lines.append("---")
    return "\n".join(fm_lines) + "\n\n" + body + "\n"


def write_deployment_record(
    *,
    governance_root: Path,
    commit: str,
    actor: str,
    authorization_type: str,
    authorization_ref: str,
    deployed_at: str | None = None,
    runtime_directory: str | None = None,
    dry_run: bool = False,
) -> dict[str, Any]:
    """写 deployment_record 到治理工作区 records。返回 {ok, path, wrote}。"""
    frontmatter = build_deployment_record(
        commit=commit,
        actor=actor,
        authorization_type=authorization_type,
        authorization_ref=authorization_ref,
        deployed_at=deployed_at,
        runtime_directory=runtime_directory,
    )
    path = record_path(governance_root, commit, frontmatter["deployed_at"])
    if dry_run:
        return {"ok": True, "path": str(path), "wrote": False, "frontmatter": frontmatter}
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(render_record_markdown(frontmatter), encoding="utf-8")
    return {"ok": True, "path": str(path), "wrote": True, "frontmatter": frontmatter}


def main(argv: list[str] | None = None) -> int:
    """CLI 入口: python3 -m tools.aipos_cli.deployment_record <args>

    供 lybra-deploy(shell)在部署成功后调用, 落一条部署记录。
    """
    import argparse

    parser = argparse.ArgumentParser(description="AIPOS-R6S: write a deployment_record")
    parser.add_argument("--governance-root", required=True, help="Governance workspace root")
    parser.add_argument("--commit", required=True, help="Full git commit hash")
    parser.add_argument("--actor", default="(unknown)", help="Deploying actor")
    parser.add_argument("--verdict-ref", help="PASS verdict id authorizing this deploy (audited)")
    parser.add_argument("--dev-override", action="store_true", help="Deploy without audit (requires --reason)")
    parser.add_argument("--reason", help="Reason (required for dev-override)")
    parser.add_argument("--runtime-directory", help="Deployment runtime directory")
    parser.add_argument("--dry-run", action="store_true", help="Preview only")
    args = parser.parse_args(argv)

    authorization_type, authorization_ref = resolve_authorization(
        verdict_ref=args.verdict_ref,
        dev_override=args.dev_override,
        reason=args.reason,
    )
    if authorization_type is None:
        print(
            "ERROR: deploy requires authorization: --verdict-ref <id> or --dev-override --reason <text>",
            file=sys.stderr,
        )
        return 2

    result = write_deployment_record(
        governance_root=Path(args.governance_root),
        commit=args.commit,
        actor=args.actor,
        authorization_type=authorization_type,
        authorization_ref=authorization_ref,
        runtime_directory=args.runtime_directory,
        dry_run=args.dry_run,
    )
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
