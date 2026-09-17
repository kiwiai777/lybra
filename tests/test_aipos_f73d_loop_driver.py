"""AIPOS-F73D — `lybra loop` 顾问侧驱动器夹具(靶场分根: tmp 治理根, schema 从产品根读)。

覆盖(对应卡面验收 ①–⑦ + 前置一/二/三):
①  靶场全生命周期: claimed 卡 → 夹具在 loop 等待时落 RETURN.md → return → 派审 → (夹具落审计报告含 verdict)
   → 裁决 → finalize → close → exit 0, 记录链完整(门侧效果由夹具 gate double 落记录; 推导核/哨兵/解析夹具全真)
②  门拒 exit 2 透传拒因原文
③  等待超时 exit 3(输出等的是哪份产物); --max-steps 用尽 exit 3
④  无信封 exit 5 带 `lybra envelope mint` 申领出口; 不可推导 exit 4 带 missing_records; 派生命令解析失败 exit 4 禁执行
⑤  退出码/参数默认/允许动词集合声明在 verbs.schema lybra_loop 一处, 代码只读
⑥  grep: 无 sleep 自旋 / 无第二推导核 / 无 board_adapter 直调 / token 零出现
前置一① finalize merge+push 成功即落 finalization 记录(deploy_status=deploy_failed), 声明在 transitions N5.record.deploy_status
前置一② 推导核派生 finalize 命令带 --actor, --workspace-root=产品仓根, --governance-root=治理根; 模板 argparse 夹具
前置二  `queue claim --confirm` 经 CLI 入口真正走到该分支: 无 ImportError, required_role_class 按卡 assigned_to 与注册表派生(禁尾字母启发式)
前置三  worktree 落点 = 产品仓根/.worktrees/<ID>(config.schema 声明), 不在治理根下
"""
from __future__ import annotations

import io
import json
import os
import re
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from tools.aipos_cli import loop_driver  # noqa: E402
from tools.aipos_cli.loop_driver import (  # noqa: E402
    check_command_parses,
    exit_code_for,
    load_loop_contract,
    run_loop,
)
from tools.aipos_cli.next_resolver import (  # noqa: E402
    _build_finalize_command,
    _ensure_worktree,
    derive_next_step,
)
from tools.aipos_cli.record_writer import build_return_skeleton_markdown  # noqa: E402

TASK = "AIPOS-F73DT"
AUDIT = f"{TASK}R"
EXEC = "exec.lybra.test"
AUDITOR = "audit.lybra.test"
DRIVER = "advisor.lybra.test"
POLICY = "pol_lybra_loop_t"


# ---------------------------------------------------------------------------
# 靶场
# ---------------------------------------------------------------------------

def _ts() -> str:
    return time.strftime("%Y%m%d_%H%M%S", time.gmtime())


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _fm(meta: dict, body: str = "") -> str:
    lines = ["---"]
    for k, v in meta.items():
        if isinstance(v, list):
            lines.append(f"{k}: {json.dumps(v)}")
        elif isinstance(v, bool):
            lines.append(f"{k}: {'true' if v else 'false'}")
        else:
            lines.append(f"{k}: {v}")
    lines.append("---")
    return "\n".join(lines) + "\n" + body


@pytest.fixture
def gov(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """tmp 治理根(队列/记录/task_cards/policies), 产品仓根声明在 project.json(前置一②/三)。"""
    monkeypatch.delenv("LYBRA_CONNECTION_JSON", raising=False)
    monkeypatch.delenv("AIPOS_WORKSPACE_ROOT", raising=False)
    root = tmp_path / "gov"
    for sub in ("pending", "claimed", "completed", "blocked"):
        (root / "5_tasks" / "queue" / sub).mkdir(parents=True)
    for sub in ("claims", "returns", "audit_dispatches", "audit_verdicts", "finalizations", "closures", "events"):
        (root / "5_tasks" / "records" / sub).mkdir(parents=True)
    (root / "5_tasks" / "policies").mkdir()
    (root / "task_cards").mkdir()
    code_repo = tmp_path / "product"
    code_repo.mkdir()
    _write(root / "project.json", json.dumps({"project": "lybra", "code_repo": str(code_repo), "config_version": 1}))
    return root


def _card(gov: Path, task_id: str, queue: str, *, assigned: str, task_mode: str = "code", extra: dict | None = None) -> Path:
    meta = {"task_id": task_id, "title": f"{task_id} test", "project": "lybra", "task_mode": task_mode,
            "assigned_to": assigned, "agent_instance": assigned, "status": queue, "audit": "required",
            "audit_by": AUDITOR}
    meta.update(extra or {})
    path = gov / "5_tasks" / "queue" / queue / f"{task_id.lower()}.md"
    _write(path, _fm(meta, f"# {task_id}\n"))
    return path


def _claim_record(gov: Path, task_id: str, agent: str) -> None:
    _write(gov / "5_tasks" / "records" / "claims" / task_id / f"claim_{task_id}_{_ts()}_{agent}.md",
           _fm({"record_type": "claim_record", "event_type": "claim", "claim_id": f"claim_{task_id}_x", "task_id": task_id,
                "agent_instance": agent, "actor": agent, "owner_policy_ref": POLICY, "claimed_at": "2026-09-16T00:00:00Z"}))


def _policy(gov: Path, *, agent_or_role: str = DRIVER, expires: str = "2999-01-01T00:00:00Z", task_mode: str = "code") -> None:
    from tools.aipos_cli.autonomy_policy import build_autonomy_policy_markdown

    _write(gov / "5_tasks" / "policies" / f"{POLICY}.md", build_autonomy_policy_markdown(
        policy_id=POLICY, agent_or_role=agent_or_role, active_from="2026-01-01T00:00:00Z", expires_at=expires,
        max_tasks=20, owner_approval_ref="test-envelope", task_selector_task_mode=task_mode))


def _substantive_return(task_id: str) -> str:
    return f"# RETURN — {task_id}\n\n## 一句话结论\n完成。\n\n## 改动清单\n- x\n"


class GateDouble:
    """门侧效果替身: 只按推导核派生的动作落记录(与 transitions 声明同形), 不含任何推导/判断。"""

    def __init__(self, gov: Path, *, reject: dict | None = None):
        self.gov = gov
        self.calls: list[tuple[str, str]] = []
        self.reject = reject or {}

    def __call__(self, derivation: dict, workspace_root: Path, connection_json=None) -> dict:
        from tools.aipos_cli.next_resolver import _action_type_for_command

        cmd = derivation["command"]
        action = _action_type_for_command(cmd)
        card = derivation["task_id"]
        self.calls.append((action, card))
        if action in self.reject:
            return {"ok": False, "action_type": action, "message": f"{action} 失败: 1", "command": cmd, "exit_code": 1,
                    "output": self.reject[action]}
        rec = self.gov / "5_tasks" / "records"
        q = self.gov / "5_tasks" / "queue"
        if action == "return":
            _write(rec / "returns" / card / f"return_{card}_{_ts()}_{EXEC}.md",
                   _fm({"record_type": "return", "event_type": "return", "return_id": f"return_{card}_x", "task_id": card,
                        "agent_instance": EXEC, "returned_at": "2026-09-16T01:00:00Z", "return_status": "returned"}))
        elif action == "dispatch":
            audit = f"{card}R"
            _write(rec / "audit_dispatches" / audit / f"dispatch_{audit}_{_ts()}_owner-dispatch.md",
                   _fm({"record_type": "audit_dispatch", "dispatch_id": f"dispatch_{audit}_x", "reviewed_task_id": card,
                        "audit_task_id": audit, "dispatched_at": "2026-09-16T02:00:00Z"}))
            _card(self.gov, audit, "pending", assigned=AUDITOR, task_mode="audit", extra={"reviewed_task_id": card})
        elif action == "claim":
            src = q / "pending" / f"{card.lower()}.md"
            dst = q / "claimed" / f"{card.lower()}.md"
            dst.write_text(src.read_text(encoding="utf-8").replace("status: pending", "status: claimed"), encoding="utf-8")
            src.unlink()
            _claim_record(self.gov, card, AUDITOR)
            _write(self.gov / "task_cards" / card / "RETURN.md", build_return_skeleton_markdown(card))  # N1 骨架
        elif action == "verdict":
            reviewed = card[:-1]
            _write(rec / "audit_verdicts" / reviewed / f"verdict_{reviewed}_{_ts()}_{AUDITOR}.md",
                   _fm({"record_type": "audit_verdict_record", "verdict_id": f"verdict_{reviewed}_x", "verdict": "PASS",
                        "reviewed_task_id": reviewed, "auditor_instance": AUDITOR, "verdict_at": "2026-09-16T03:00:00Z"}))
            src = q / "claimed" / f"{card.lower()}.md"
            (q / "completed" / f"{card.lower()}.md").write_text(src.read_text(encoding="utf-8"), encoding="utf-8")
            src.unlink()
        elif action == "finalize":
            _write(rec / "finalizations" / card / f"finalization_{_ts()}.md",
                   _fm({"record_type": "finalization_record", "task_id": card, "commit": "a" * 40, "commit_hash": "a" * 40,
                        "finalized_at": "2026-09-16T04:00:00Z", "deploy_status": "deployed"}))
        elif action == "close":
            _write(rec / "closures" / card / f"closure_{card}_{_ts()}_advisor.md",
                   _fm({"record_type": "closure", "task_id": card, "actor": "advisor", "closed_at": "2026-09-16T05:00:00Z"}))
            src = q / "claimed" / f"{card.lower()}.md"
            (q / "completed" / f"{card.lower()}.md").write_text(src.read_text(encoding="utf-8"), encoding="utf-8")
            src.unlink()
        else:
            raise AssertionError(f"unexpected action {action}: {cmd}")
        return {"ok": True, "action_type": action, "message": f"{action} 成功", "command": cmd, "exit_code": 0, "output": "ok"}


# ---------------------------------------------------------------------------
# ⑤ 声明一处
# ---------------------------------------------------------------------------

def test_f73d_item2_exit_codes_declared_in_verbs_schema_single_place():
    verbs = json.loads((REPO_ROOT / "schema" / "verbs.schema.json").read_text(encoding="utf-8"))["verbs"]
    loop = verbs["lybra_loop"]
    assert loop["cli_command"] == "lybra loop"
    codes = {k: v["code"] for k, v in loop["exit_codes"].items()}
    assert codes == {"completed": 0, "gate_rejected": 2, "wait_timeout": 3, "not_derivable": 4, "no_envelope": 5}
    props = loop["parameters"]["properties"]
    assert props["max_steps"]["default"] == 20
    assert props["max_wait"]["default"] == 1800
    assert set(loop["envelope"]["allowed_verbs"]) == {"claim", "return", "dispatch", "verdict", "finalize", "close"}
    contract = load_loop_contract()
    assert {k: exit_code_for(contract, k) for k in codes} == codes
    # 代码里不写死退出码: loop_driver 只经 exit_code_for 取码
    src = (REPO_ROOT / "tools" / "aipos_cli" / "loop_driver.py").read_text(encoding="utf-8")
    assert re.search(r"exit_code\s*=\s*[2345]\b", src) is None
    assert 'sys.exit(' not in src


def test_f73d_item2_cli_loop_parses_and_defaults_come_from_schema():
    from tools.aipos_cli.aipos_cli import build_parser

    args = build_parser().parse_args(["loop", "--task-id", TASK])
    assert args.command == "loop" and args.task_id == TASK
    assert args.max_steps is None and args.max_wait is None and args.interval is None  # 缺省读声明, argparse 不写死


# ---------------------------------------------------------------------------
# ⑥ grep 红线
# ---------------------------------------------------------------------------

def test_f73d_item6_grep_no_sleep_spin_no_second_core_no_token():
    src = (REPO_ROOT / "tools" / "aipos_cli" / "loop_driver.py").read_text(encoding="utf-8")
    assert "time.sleep" not in src and "sleep(" not in src, "等待一律经 agent watch, 禁 sleep 自旋"
    assert "import time" not in src
    assert "def derive_next_step" not in src and "def scan_project" not in src, "禁第二推导核"
    assert "from tools.aipos_cli.next_resolver import" in src
    assert "run_fs_watch" in src and "def snapshot" not in src and "def check_expect_patterns" not in src, "禁第二哨兵"
    assert "tools.aipos_cli.board_adapter" not in src and "tools.aipos_cli.queue_mutation" not in src, "禁直调 board_adapter/queue_mutation"
    assert "load_owner_token" not in src and "get_token_for_role" not in src and "Bearer" not in src, "token 零出现"
    assert "except Exception" not in src


# ---------------------------------------------------------------------------
# ④ 无信封 exit 5
# ---------------------------------------------------------------------------

def test_f73d_item3_no_envelope_exit5_with_mint_hint(gov: Path):
    _card(gov, TASK, "claimed", assigned=EXEC)
    _claim_record(gov, TASK, EXEC)
    out = io.StringIO()
    res = run_loop(TASK, gov, actor=DRIVER, out=out, execute=GateDouble(gov), interval=0.01, max_wait=0.05)
    assert res.exit_code == 5 and res.outcome == "no_envelope"
    assert "lybra envelope mint" in res.suggested_action
    assert f"--agent-or-role {DRIVER}" in res.suggested_action and "--task-mode code" in res.suggested_action
    assert "5_tasks/policies/ 下没有任何信封" in res.message
    assert "exit 5" in out.getvalue()


def test_f73d_item3_expired_envelope_exit5_names_reason(gov: Path):
    _card(gov, TASK, "claimed", assigned=EXEC)
    _claim_record(gov, TASK, EXEC)
    _policy(gov, expires="2026-01-02T00:00:00Z")
    res = run_loop(TASK, gov, actor=DRIVER, out=io.StringIO(), execute=GateDouble(gov), interval=0.01, max_wait=0.05)
    assert res.exit_code == 5
    assert f"{POLICY}: policy has expired" in res.message


def test_f73d_item3_cli_entry_returns_declared_exit5(gov: Path, capsys):
    from tools.aipos_cli.aipos_cli import main

    _card(gov, TASK, "claimed", assigned=EXEC)
    rc = main(["loop", "--task-id", TASK, "--workspace-root", str(gov), "--actor", DRIVER, "--json"])
    assert rc == 5
    payload = json.loads(capsys.readouterr().out)
    assert payload["outcome"] == "no_envelope" and payload["exit_code"] == 5
    assert "lybra envelope mint" in payload["suggested_action"]


# ---------------------------------------------------------------------------
# ① 靶场全生命周期 exit 0
# ---------------------------------------------------------------------------

def test_f73d_item1_full_lifecycle_claimed_to_completed(gov: Path):
    _card(gov, TASK, "claimed", assigned=EXEC)
    _claim_record(gov, TASK, EXEC)
    _policy(gov)
    # N1 骨架: claim 时门建的 RETURN.md 不算交回(推导核判 await)
    _write(gov / "task_cards" / TASK / "RETURN.md", build_return_skeleton_markdown(TASK))
    gate = GateDouble(gov)
    out = io.StringIO()

    def agents() -> None:
        # 执行体: loop 等待期间落实质 RETURN.md
        time.sleep(0.15)
        _write(gov / "task_cards" / TASK / "RETURN.md", _substantive_return(TASK))
        # 审计体: 等审计卡被 loop 认领(骨架出现)后落 audit_report.md(N4 候选②, RETURN.md 仍是骨架)
        deadline = time.time() + 5
        while time.time() < deadline and not (gov / "task_cards" / AUDIT / "RETURN.md").exists():
            time.sleep(0.02)
        time.sleep(0.15)
        _write(gov / "task_cards" / AUDIT / "audit_report.md",
               _fm({"task_id": AUDIT, "reviewed_task_id": TASK, "verdict": "PASS", "actor": AUDITOR, "agent_instance": AUDITOR},
                   "# audit\n\n## 一句话结论\nPASS\n"))

    t = threading.Thread(target=agents, daemon=True)
    t.start()
    res = run_loop(TASK, gov, actor=DRIVER, out=out, execute=gate, interval=0.03, max_wait=8, max_steps=20)
    t.join(timeout=10)
    text = out.getvalue()
    assert res.exit_code == 0, text
    assert res.outcome == "completed" and res.envelope == POLICY
    # 步序: 等执行体 → return → dispatch → claim 审计卡 → 等审计体 → verdict → finalize → close → done
    assert [c[0] for c in gate.calls] == ["return", "dispatch", "claim", "verdict", "finalize", "close"], gate.calls
    kinds = [(s.kind, s.action_type or s.card) for s in res.steps]
    assert kinds[0] == ("wait", TASK) and kinds[-1] == ("done", "done") or kinds[-1][0] == "done"
    assert [s.kind for s in res.steps].count("wait") == 2
    # 记录链完整
    rec = gov / "5_tasks" / "records"
    for sub, card in (("returns", TASK), ("audit_dispatches", AUDIT), ("audit_verdicts", TASK), ("finalizations", TASK), ("closures", TASK)):
        assert list((rec / sub / card).glob("*.md")), f"missing record {sub}/{card}"
    assert (gov / "5_tasks" / "queue" / "completed" / f"{TASK.lower()}.md").is_file()
    assert (gov / "5_tasks" / "queue" / "completed" / f"{AUDIT.lower()}.md").is_file()
    # 派生命令原文: finalize 带 --actor 与两根; token 零出现
    fin = [s for s in res.steps if s.action_type == "finalize"][0]
    assert f"--actor {DRIVER}" in fin.command or "--actor advisor" in fin.command
    assert f"--workspace-root {json.loads((gov / 'project.json').read_text())['code_repo']}" in fin.command
    assert f"--governance-root {gov}" in fin.command
    assert "token" not in text.lower().replace("token 永不上屏", "")
    # 治理根下没有被建成工作树
    assert not (gov / "card").exists() and not (gov / ".worktrees").exists()


# ---------------------------------------------------------------------------
# ② 门拒 exit 2
# ---------------------------------------------------------------------------

def test_f73d_item2_gate_rejection_exit2_passes_reason_through(gov: Path):
    _card(gov, TASK, "claimed", assigned=EXEC)
    _claim_record(gov, TASK, EXEC)
    _policy(gov)
    _write(gov / "task_cards" / TASK / "RETURN.md", _substantive_return(TASK))
    reason = "ENVELOPE_SELECTOR_TASK_ID_MISMATCH: 任务 ID 不在信封的 task_selector.task_ids 列表中"
    gate = GateDouble(gov, reject={"return": reason})
    out = io.StringIO()
    res = run_loop(TASK, gov, actor=DRIVER, out=out, execute=gate, interval=0.01, max_wait=0.5, max_steps=5)
    assert res.exit_code == 2 and res.outcome == "gate_rejected"
    assert reason in res.message and reason in out.getvalue()
    assert gate.calls == [("return", TASK)], "门拒不重试"


# ---------------------------------------------------------------------------
# ③ 等待超时 exit 3
# ---------------------------------------------------------------------------

def test_f73d_item2_wait_timeout_exit3_names_artifact(gov: Path):
    _card(gov, TASK, "claimed", assigned=EXEC)
    _claim_record(gov, TASK, EXEC)
    _policy(gov)
    _write(gov / "task_cards" / TASK / "RETURN.md", build_return_skeleton_markdown(TASK))  # 骨架 ≠ 产物
    out = io.StringIO()
    t0 = time.monotonic()
    res = run_loop(TASK, gov, actor=DRIVER, out=out, execute=GateDouble(gov), interval=0.02, max_wait=0.2, max_steps=5)
    assert res.exit_code == 3 and res.outcome == "wait_timeout"
    assert f"task_cards/{TASK}/RETURN.md" in res.message
    assert time.monotonic() - t0 < 5
    assert res.steps[-1].kind == "wait" and res.steps[-1].ok is False


def test_f73d_item2_max_steps_exhausted_exit3(gov: Path, monkeypatch: pytest.MonkeyPatch):
    _card(gov, TASK, "claimed", assigned=EXEC)
    _claim_record(gov, TASK, EXEC)
    _policy(gov)
    _write(gov / "task_cards" / TASK / "RETURN.md", _substantive_return(TASK))

    def noop_execute(derivation, workspace_root, connection_json=None):  # 门"成功"但记录不落 → 推导原地踏步
        return {"ok": True, "action_type": "return", "message": "ok", "command": derivation["command"], "exit_code": 0, "output": ""}

    res = run_loop(TASK, gov, actor=DRIVER, out=io.StringIO(), execute=noop_execute, interval=0.01, max_wait=0.1, max_steps=3)
    assert res.exit_code == 3 and "--max-steps 3 用尽" in res.message
    assert len(res.steps) == 3


# ---------------------------------------------------------------------------
# ④ 不可推导 / 解析失败 exit 4
# ---------------------------------------------------------------------------

def test_f73d_item2_not_derivable_exit4_with_missing_records(gov: Path):
    _card(gov, TASK, "blocked", assigned=EXEC)
    _policy(gov)
    res = run_loop(TASK, gov, actor=DRIVER, out=io.StringIO(), execute=GateDouble(gov), interval=0.01, max_wait=0.1)
    assert res.exit_code == 4 and res.outcome == "not_derivable"
    assert res.missing_records and "blocked" in res.message


def test_f73d_pre1_item3_unparseable_derived_command_exit4_never_executed(gov: Path):
    _card(gov, TASK, "claimed", assigned=EXEC)
    _policy(gov)
    executed: list[str] = []

    def bad_derive(task_id: str, root: Path) -> dict:
        return {"task_id": task_id, "derivable": True, "current_node": "audit_verdict", "current_state": "claimed",
                "triggered_by": "advisor", "command": f"lybra finalize --task-id {task_id} --push --deploy",  # 缺 --actor
                "verb": "lybra_finalize", "missing_records": [], "suggested_action": "", "notes": ""}

    def spy(derivation, root, conn=None):
        executed.append(derivation["command"])
        return {"ok": True}

    res = run_loop(TASK, gov, actor=DRIVER, out=io.StringIO(), derive=bad_derive, execute=spy, interval=0.01, max_wait=0.1)
    assert res.exit_code == 4 and "解析失败" in res.message and "--actor" in res.message
    assert executed == []


def test_f73d_pre1_item3_check_command_parses_uses_aipos_cli_argparse():
    ok, err = check_command_parses("lybra queue close --task-id X --actor a --closure-evidence '{\"verdict_ref\": \"v\"}'")
    assert ok and err == ""
    ok, err = check_command_parses("lybra queue close --task-id X --actor a --closure-evidence '{}' --confirm")
    assert not ok and "unrecognized arguments: --confirm" in err
    ok, err = check_command_parses("git push")
    assert not ok and "lybra" in err


# ---------------------------------------------------------------------------
# 前置一② finalize 模板 + parser 夹具
# ---------------------------------------------------------------------------

def test_f73d_pre1_item2_finalize_template_parses_with_actor_and_two_roots(tmp_path: Path):
    cmd = _build_finalize_command(task_id=TASK, actor=DRIVER, code_repo_root=tmp_path / "product", governance_root=tmp_path / "gov")
    ok, err = check_command_parses(cmd)
    assert ok, err
    from tools.aipos_cli.aipos_cli import build_parser
    import shlex

    args = build_parser().parse_args(shlex.split(cmd)[1:])
    assert args.actor == DRIVER and args.push and args.deploy
    assert args.workspace_root == str(tmp_path / "product") and args.governance_root == str(tmp_path / "gov")


def test_f73d_pre1_item2_derived_finalize_uses_project_json_code_repo(gov: Path):
    _card(gov, TASK, "claimed", assigned=EXEC)
    _claim_record(gov, TASK, EXEC)
    rec = gov / "5_tasks" / "records"
    _write(rec / "returns" / TASK / f"return_{TASK}_x_{EXEC}.md", _fm({"record_type": "return", "task_id": TASK, "return_id": "r"}))
    _write(rec / "audit_dispatches" / AUDIT / "dispatch_x.md", _fm({"record_type": "audit_dispatch", "dispatch_id": "d", "reviewed_task_id": TASK}))
    _write(rec / "audit_verdicts" / TASK / "verdict_x.md", _fm({"record_type": "audit_verdict_record", "verdict_id": "verdict_x", "verdict": "PASS", "verdict_at": "2026-09-16T03:00:00Z"}))
    d = derive_next_step(TASK, gov)
    assert d["derivable"] and d["verb"] == "lybra_finalize"
    code_repo = json.loads((gov / "project.json").read_text())["code_repo"]
    assert f"--workspace-root {code_repo}" in d["command"] and f"--governance-root {gov}" in d["command"]
    assert "--actor advisor" in d["command"]  # 无 .lybra/role → 驱动方缺省 advisor
    assert str(gov) not in d["command"].split("--workspace-root")[1].split()[0], "治理根不得当产品仓"


# ---------------------------------------------------------------------------
# 前置一① finalization 记录: merge+push 成功即落, 部署失败带 deploy_status
# ---------------------------------------------------------------------------

def test_f73d_pre1_item1_finalization_record_written_when_deploy_fails_after_push(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    from unittest.mock import MagicMock

    from tools.aipos_cli import finalize as fz

    gov = tmp_path / "gov"
    product = tmp_path / "product"
    gov.mkdir()
    product.mkdir()
    sha = "b" * 40
    monkeypatch.setattr(fz, "_report_frontmatter_verdict_for_display", lambda *a, **k: {"report_path": None, "report_verdict": None})
    monkeypatch.setattr(fz, "_load_branch_integration", lambda root: {"branch_pattern": "card/{task_id}"})
    monkeypatch.setattr(fz, "_git_rev_parse_head", lambda root: sha)
    monkeypatch.setattr(fz, "_git_branch_exists", lambda root, b: False)
    monkeypatch.setattr(fz, "check_task_can_finalize", lambda *a, **k: {"can_finalize": True, "reason": "ok", "verdict_id": "verdict_x", "verdict": "PASS"})
    monkeypatch.setattr(fz, "check_stage_archive_gate", lambda *a, **k: {"passed": True, "message": "ok"})
    monkeypatch.setattr(fz, "_check_deployment_integrity", lambda *a, **k: {"integrity_ok": True, "message": "ok"})
    monkeypatch.setattr(fz, "_ensure_on_main_branch", lambda *a, **k: None)
    monkeypatch.setattr("tools.aipos_cli.deploy_gate.check_deployment_branch", lambda *a, **k: {"on_required_branch": True, "message": "main"})
    monkeypatch.setattr(fz, "_integrate_card_branch", lambda **k: {"blocked": False, "action": "merged", "message": "merged"})
    monkeypatch.setattr(fz, "_git_status_clean", lambda root: True)
    monkeypatch.setattr(fz, "_git_local_origin_synced", lambda root: False)  # 有待 push
    monkeypatch.setattr(fz, "_read_deploy_current", lambda root: {"current_commit": None, "provenance": None, "authorization_ref": None})
    pushed = []

    def fake_run(cmd, **kw):
        pushed.append(cmd)
        return MagicMock(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(fz.subprocess, "run", fake_run)
    monkeypatch.setattr("tools.aipos_cli.deploy_gate.invoke_lybra_deploy",
                        lambda *a, **k: {"success": False, "stdout": "", "stderr": "F70 coverage check refused", "returncode": 1})

    result = fz.finalize_task(TASK, DRIVER, product, governance_root=gov, push=True, deploy=True)
    assert result["verdict"] == "FAIL" and result["pushed"] is True and result["deployed"] is False
    assert ["git", "push"] in pushed
    records = list((gov / "5_tasks" / "records" / "finalizations" / TASK).glob("finalization_*.md"))
    assert len(records) == 1, result["operations"]
    text = records[0].read_text(encoding="utf-8")
    assert "record_type: finalization_record" in text and "deploy_status: deploy_failed" in text and f"commit: {sha}" in text
    # 推导核 N5→N6: finalization 记录存在 → close(链不断)
    assert any("deploy_status=deploy_failed" in op for op in result["operations"])


def test_f73d_pre1_item1_deploy_status_declared_once_in_transitions():
    t = json.loads((REPO_ROOT / "schema" / "transitions.schema.json").read_text(encoding="utf-8"))
    decl = t["nodes"]["N5"]["record"]["deploy_status"]
    assert decl["values"] == ["deployed", "deploy_failed", "skipped", "not_attempted"]
    src = (REPO_ROOT / "tools" / "aipos_cli" / "finalize.py").read_text(encoding="utf-8")
    assert src.count('deploy_status="deploy_failed"') == 7, "每条 push/merge 成功后的部署失败出口都落记录"


# ---------------------------------------------------------------------------
# 前置二 CLI claim 路径经 bin 入口(先红后绿: main 上同命令 ImportError)
# ---------------------------------------------------------------------------

def _claim_via_cli(gov: Path, task_id: str, conn: Path) -> subprocess.CompletedProcess:
    env = {k: v for k, v in os.environ.items() if k not in ("LYBRA_CONNECTION_JSON", "AIPOS_WORKSPACE_ROOT")}
    env["PYTHONPATH"] = str(REPO_ROOT)
    return subprocess.run(
        [sys.executable, "-m", "tools.aipos_cli.aipos_cli", "--workspace-root", str(gov), "queue", "claim",
         "--task-id", task_id, "--actor", EXEC, "--agent-instance", EXEC, "--confirm", "--connection-json", str(conn)],
        cwd=str(REPO_ROOT), env=env, capture_output=True, text=True, timeout=60,
    )


def _fake_connection(tmp_path: Path, roles: list[str]) -> Path:
    conn = tmp_path / "connection.json"
    conn.write_text(json.dumps({
        "mcp": {"rpc_url": "http://127.0.0.1:1/mcp"},
        "tokens": [{"role": r, "role_class": r, "token": f"fixture-not-a-secret-{r}", "scopes": []} for r in roles],
    }), encoding="utf-8")
    return conn


def test_f73d_pre2_cli_claim_confirm_reaches_branch_and_derives_class_from_registry(gov: Path, tmp_path: Path):
    # 卡 ID 以 R 结尾但指派给执行体 → 注册表判 executor(禁尾字母启发式); 连接文件只有 auditor → 报错点名 executor
    _card(gov, "AIPOS-TESTR", "pending", assigned=EXEC)
    conn = _fake_connection(tmp_path, ["auditor"])
    proc = _claim_via_cli(gov, "AIPOS-TESTR", conn)
    assert "ImportError" not in proc.stderr and "load_task_by_id" not in proc.stderr, proc.stderr
    assert proc.returncode == 1
    assert "No role with class 'executor'" in proc.stderr, proc.stderr
    # 审计体卡(ID 不以 R 结尾)→ auditor; 连接文件有 auditor → 过角色解析, 走到门连接(靶场无门 → 连接失败出声)
    _card(gov, "AIPOS-TESTX", "pending", assigned=AUDITOR)
    proc2 = _claim_via_cli(gov, "AIPOS-TESTX", conn)
    assert "ImportError" not in proc2.stderr
    assert "No role with class" not in proc2.stderr, proc2.stderr
    assert "gate client init failed" in proc2.stderr or "cannot load" in proc2.stderr, proc2.stderr
    assert "fixture-not-a-secret" not in proc2.stdout + proc2.stderr, "token 永不上屏"


# ---------------------------------------------------------------------------
# 前置三 worktree 落点
# ---------------------------------------------------------------------------

def test_f73d_pre3_worktree_lands_under_declared_code_repo_worktrees(gov: Path, tmp_path: Path):
    code_repo = Path(json.loads((gov / "project.json").read_text())["code_repo"])
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=code_repo, check=True)
    subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "--allow-empty", "-m", "init"], cwd=code_repo, check=True)
    res = _ensure_worktree(gov, TASK)
    assert res["ok"], res["message"]
    cfg = json.loads((REPO_ROOT / "schema" / "config.schema.json").read_text(encoding="utf-8"))
    template = cfg["configuration_sources"]["workspace_config"]["schema"]["worktree_root"]["default"]
    expected = Path(template.replace("{code_repo}", str(code_repo))) / TASK
    assert Path(res["worktree_path"]) == expected
    assert expected.is_dir() and not (gov / "card").exists()
    branches = subprocess.run(["git", "branch", "--list", f"card/{TASK}"], cwd=code_repo, capture_output=True, text=True).stdout
    assert f"card/{TASK}" in branches
    assert ".worktrees/" in (REPO_ROOT / ".gitignore").read_text(encoding="utf-8")
    res2 = _ensure_worktree(gov, TASK)
    assert res2["ok"] and "already exists" in res2["message"]


def test_f73d_pre3_worktree_fail_closed_without_code_repo(tmp_path: Path):
    gov = tmp_path / "gov2"
    (gov / "5_tasks" / "queue" / "pending").mkdir(parents=True)
    res = _ensure_worktree(gov, TASK)
    assert res["ok"] is False and "code_repo" in res["message"]


# ---------------------------------------------------------------------------
# 推导核就绪判据(骨架不算 / 审计报告候选读 N4 声明)
# ---------------------------------------------------------------------------

def test_f73d_return_skeleton_is_not_a_return_artifact(gov: Path):
    _card(gov, TASK, "claimed", assigned=EXEC)
    _claim_record(gov, TASK, EXEC)
    _write(gov / "task_cards" / TASK / "RETURN.md", build_return_skeleton_markdown(TASK))
    d = derive_next_step(TASK, gov)
    assert d["derivable"] is False and "RETURN.md 工作产物" in d["missing_records"]
    _write(gov / "task_cards" / TASK / "RETURN.md", _substantive_return(TASK))
    d = derive_next_step(TASK, gov)
    assert d["derivable"] and "queue return" in d["command"] and '--result-summary "完成"' in d["command"]


def test_f73d_audit_report_candidates_from_n4_declaration(gov: Path):
    _card(gov, TASK, "claimed", assigned=EXEC)
    _card(gov, AUDIT, "claimed", assigned=AUDITOR, task_mode="audit", extra={"reviewed_task_id": TASK})
    _claim_record(gov, AUDIT, AUDITOR)
    _write(gov / "task_cards" / AUDIT / "RETURN.md", build_return_skeleton_markdown(AUDIT))
    assert derive_next_step(AUDIT, gov)["derivable"] is False
    _write(gov / "task_cards" / AUDIT / "audit_report.md", _fm({"reviewed_task_id": TASK, "verdict": "PASS_WITH_NOTES", "actor": AUDITOR}, "# a\n"))
    d = derive_next_step(AUDIT, gov)
    assert d["derivable"] and "lybra audit-verdict" in d["command"] and "--verdict PASS_WITH_NOTES" in d["command"]
    assert check_command_parses(d["command"])[0]


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
