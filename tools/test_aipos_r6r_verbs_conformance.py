#!/usr/bin/env python3
"""AIPOS-R6R Conformance 测试 (Python 侧) —— 锁定 verbs.schema.json 的动词契约。

与 TS 测试 (agents/pi/lybra-loop/tests/verbs-conformance.test.ts) 读同一份 schema,
断言同一份预期契约(verb 名 / 必填参数 / 两阶段语义 / 关键参数 shape)。
若 schema 漂移(缺动词、改错参数名、两阶段语义变), 两侧同时失败 —— 契约单一源。

跑法: `python3 tools/test_aipos_r6r_verbs_conformance.py`
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tools.schema_loader import load_schema

# 连接器依赖的预期契约(AIPOS-R6R)。与 TS 侧同源同断言。
EXPECTED: dict[str, dict] = {
    "lybra_queue_list": {"phase": "single", "required": []},
    "lybra_task_preview": {"phase": "single", "required": []},
    "lybra_return_content": {"phase": "single", "required": ["task_id"]},
    "lybra_queue_claim_dry_run": {
        "phase": "dry_run",
        "confirm": "lybra_queue_claim_confirm",
        "required": ["actor", "agent_instance", "autonomy_mode", "owner_policy_ref"],
    },
    "lybra_queue_claim_confirm": {
        "phase": "confirm",
        "required": ["dry_run_token", "actor", "agent_instance", "owner_policy_ref", "owner_confirmation_token"],
    },
    "lybra_queue_return_dry_run": {
        "phase": "dry_run",
        "confirm": "lybra_queue_return_confirm",
        "required": ["actor", "agent_instance", "autonomy_mode", "owner_policy_ref"],
    },
    "lybra_queue_return_confirm": {
        "phase": "confirm",
        "required": ["dry_run_token", "actor", "agent_instance", "owner_policy_ref", "owner_confirmation_token"],
    },
    "lybra_queue_close_dry_run": {
        "phase": "dry_run",
        "confirm": "lybra_queue_close_confirm",
        "required": ["task_id", "actor", "closure_evidence"],
    },
    "lybra_queue_close_confirm": {
        "phase": "confirm",
        "required": ["task_id", "actor", "closure_evidence"],
    },
    "lybra_task_progress": {"phase": "single", "required": ["task_id", "event_type", "actor"]},
    "lybra_bench_audit_submit_dry_run": {"phase": "dry_run", "required": ["task_id", "actor", "conclusion"]},
}


def main() -> int:
    checks: list[tuple[str, bool]] = []

    def check(name: str, ok: bool) -> None:
        checks.append((name, ok))

    try:
        schema = load_schema("verbs")
    except Exception as e:  # noqa: BLE001
        check(f"load verbs schema ({e})", False)
        schema = None

    if schema is not None:
        verbs = schema.get("verbs", {})
        for name, exp in EXPECTED.items():
            verb = verbs.get(name)
            check(f"verb 存在: {name}", bool(verb))
            if not verb:
                continue
            check(f"verb phase 对: {name} ({exp['phase']})", verb.get("phase") == exp["phase"])
            if "confirm" in exp:
                check(f"verb 两阶段配对: {name} → {exp['confirm']}", verb.get("confirm_verb") == exp["confirm"])
            required = sorted(verb.get("parameters", {}).get("required", []))
            check(
                f"verb 必填参数对: {name}",
                required == sorted(exp["required"]),
            )

        close_evidence = (
            verbs.get("lybra_queue_close_dry_run", {})
            .get("parameters", {})
            .get("properties", {})
            .get("closure_evidence")
        )
        check("closure_evidence 是 object(非 string)", (close_evidence or {}).get("type") == "object")
        check(
            "closure_evidence 含 finalize_commit_hash",
            (close_evidence or {}).get("properties", {}).get("finalize_commit_hash", {}).get("type") == "string",
        )

        close_confirm_props = (
            verbs.get("lybra_queue_close_confirm", {}).get("parameters", {}).get("properties", {})
        )
        check("close_confirm 无 dry_run_token 参数", "dry_run_token" not in close_confirm_props)

        task_progress_props = (
            verbs.get("lybra_task_progress", {}).get("parameters", {}).get("properties", {})
        )
        check("task_progress 用 event_type(非 status)", "event_type" in task_progress_props)

        check("claim confirm_via=dry_run_token", verbs.get("lybra_queue_claim_dry_run", {}).get("confirm_via") == "dry_run_token")
        check("close confirm_via=replay_args", verbs.get("lybra_queue_close_dry_run", {}).get("confirm_via") == "replay_args")

    for name, ok in checks:
        print(f"{'PASS' if ok else 'FAIL'}  {name}")
    failures = sum(1 for _, ok in checks if not ok)
    print(f"\n{'ALL %d PASS' % len(checks) if failures == 0 else '%d/%d FAILED' % (failures, len(checks))}")
    return 0 if failures == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
