"""AIPOS-R6S 大项B①: deployment_record — 每次 deploy 落一条机器可核的记录。

设计权威: LEDGER 2026-08-16 (deploy 是当前唯一无 records 的固化点, 已两次"未审先
deploy") + 迁移门第⑤条(固化点全通 = 每点至少一条真实机器产物)。

record_type=deployment_record (enums.schema 唯一值域源), 落点 = 声明 transitions.schema nodes.N5.deployment_record.location
(record_locations.kinds.deployments, 平铺): deployments/deployment_<compact 时间>_<commit8>.md(AIPOS-F109 件①: 干跑预览与真写同一推导;
存量旧名 deployments/<commit8>/deployment_<compact>.md、deployments/deployment_<ISO 带冒号>_<commit8>.md 保留不迁, 无读取方)。

authorization 二选一(缺授权即拒, 见 lybra-deploy 与 deploy_gate):
  - verdict_ref : audited —— finalize 传本卡 PASS 裁决 id (deployment_provenance=audited)
  - dev_override: dev_override —— 显式 --reason 必填 (deployment_provenance=dev_override)

本模块零依赖(不 import 大包), 供 lybra-deploy(shell 内 python3 -m)与 deploy_gate(Python)
共用, 一机制一实现。
"""
from __future__ import annotations

import json
import sys
from tools.aipos_cli.clock import file_slug, iso_z
from pathlib import Path
from typing import Any

PROVENANCE_AUDITED = "audited"
PROVENANCE_DEV_OVERRIDE = "dev_override"
VALID_PROVENANCE = (PROVENANCE_AUDITED, PROVENANCE_DEV_OVERRIDE)


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
    coverage: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """构建 deployment_record 字典(frontmatter + 摘要)。

    AIPOS-F130 件③: coverage = deployment_authorization.verdict_ref_deploy_coverage 结果(verdict_ref 部署时由 lybra-deploy 传
    --coverage-base 算出)→ 记录列出覆盖区间(coverage_interval)与覆盖区间的全部裁决(covering_verdicts, 多卡并集)。"""
    if authorization_type not in VALID_PROVENANCE and authorization_type not in ("verdict_ref", "dev_override"):
        raise ValueError(f"unknown authorization_type: {authorization_type}")
    commit = (commit or "").strip()
    if not commit:
        raise ValueError("commit is required")
    provenance = PROVENANCE_AUDITED if authorization_type == "verdict_ref" else PROVENANCE_DEV_OVERRIDE
    deployed_at = deployed_at or iso_z()
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
    if coverage is not None:
        frontmatter["coverage_interval"] = coverage["interval"]
        frontmatter["covering_verdicts"] = list(coverage.get("covering_verdicts") or [])
    return frontmatter


def deployment_id(commit: str, deployed_at: str) -> str:
    """部署记录 id = 落盘名(无扩展名): deployment_<compact 时间>_<commit8>(声明 N5.deployment_record.location)。"""
    return f"deployment_{file_slug('compact', deployed_at)}_{commit[:8]}"


def record_path(governance_root: Path, commit: str, deployed_at: str) -> Path:
    """部署记录路径(AIPOS-F109 件①): 声明落点目录(record_dir, 平铺) / <deployment_id>.md——与 write_records_atomic 真写同一推导。"""
    from tools.aipos_cli.record_writer import record_dir  # 延迟导入: 本模块供 lybra-deploy 以 python3 -m 轻量调用

    return record_dir(governance_root, "deployments") / f"{deployment_id(commit, deployed_at)}.md"


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
    if "coverage_interval" in frontmatter:
        body += f"- **coverage_interval**: {frontmatter['coverage_interval']}\n"
        body += f"- **covering_verdicts**: {', '.join(frontmatter['covering_verdicts']) or '(空区间)'}\n"
    # AIPOS-F87 件①: frontmatter 经单源 record_writer.render_markdown(safe_dump + 写后回读校验), 原逐行 f"{k}: {v}" 拼接退役
    # (值含 `**`/冒号/`#` 时曾可写出不可解析的记录)。字段序 = build_deployment_record 的插入序。
    from tools.aipos_cli.record_writer import render_markdown

    return render_markdown(frontmatter, "\n" + body, list(frontmatter))


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
    coverage: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """写 deployment_record 到治理工作区 records。返回 {ok, path, wrote}。"""
    frontmatter = build_deployment_record(
        commit=commit,
        actor=actor,
        authorization_type=authorization_type,
        authorization_ref=authorization_ref,
        deployed_at=deployed_at,
        runtime_directory=runtime_directory,
        coverage=coverage,
    )
    path = record_path(governance_root, commit, frontmatter["deployed_at"])
    if dry_run:
        return {"ok": True, "path": str(path), "wrote": False, "frontmatter": frontmatter}
    
    # AIPOS-F64: 统一写入器
    from tools.aipos_cli.record_writer import write_records_atomic
    deployment_markdown = render_record_markdown(frontmatter)

    write_result = write_records_atomic(
        repo_root=governance_root,
        records=[("deployment", deployment_id(commit, frontmatter["deployed_at"]), deployment_markdown, None)],
    )
    
    return {"ok": True, "path": write_result["paths"][0], "wrote": True, "frontmatter": frontmatter}


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
    # AIPOS-F130 件③: verdict_ref 部署的区间覆盖(与 finalize 完整性同一实现); lybra-deploy 传部署前的 current commit(无部署 = 空串)
    parser.add_argument("--coverage-base", default=None,
                        help="verdict_ref deploys: commit deployed before this deploy (empty = first deploy); computes interval coverage")
    parser.add_argument("--repo-root", default=None, help="Product repo root for --coverage-base (default: cwd)")
    parser.add_argument("--check-only", action="store_true",
                        help="With --verdict-ref/--coverage-base: only run the coverage authorization check (exit 2 if refused), write nothing")
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

    coverage: dict[str, Any] | None = None
    if args.check_only and (authorization_type != "verdict_ref" or args.coverage_base is None):
        print("ERROR: --check-only requires --verdict-ref and --coverage-base", file=sys.stderr)
        return 2
    if authorization_type == "verdict_ref" and args.coverage_base is not None:
        from tools.aipos_cli.deployment_authorization import verdict_ref_deploy_coverage

        coverage = verdict_ref_deploy_coverage(
            Path(args.repo_root) if args.repo_root else Path.cwd(),
            Path(args.governance_root),
            authorization_ref,
            args.coverage_base.strip() or None,
            args.commit,
        )
        if not coverage["authorized"]:
            uncovered = "".join(f"\n  {u}" for u in coverage.get("uncovered_commits") or [])
            print(f"ERROR: verdict_ref 区间覆盖授权未过 ({coverage['interval']}): {coverage['message']}{uncovered}", file=sys.stderr)
            return 2
        if args.check_only:
            print(json.dumps({"authorized": True, "interval": coverage["interval"],
                              "covering_verdicts": coverage["covering_verdicts"], "message": coverage["message"]},
                             ensure_ascii=False, indent=2, sort_keys=True))
            return 0

    result = write_deployment_record(
        governance_root=Path(args.governance_root),
        commit=args.commit,
        actor=args.actor,
        authorization_type=authorization_type,
        authorization_ref=authorization_ref,
        runtime_directory=args.runtime_directory,
        dry_run=args.dry_run,
        coverage=coverage,
    )
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
