"""AIPOS-F163(gap #148) — 运行证据硬门下沉到门侧裁决写入唯一口: 手敲 `lybra audit-verdict --confirm` 直提旁路堵上。

依据: F162 运行证据判据只在 `lybra artifact ingest --kind verdict`(loop 入口)核; 驱动方手敲 `lybra audit-verdict --confirm`
直提裁决不经过该判据——同一规则两个入口只守一个。

并入点(唯一实现, 禁第二份): 判据 = board_adapter.runtime_evidence_check(自 artifact_ingest 下沉), 在门侧裁决写入唯一口
board_adapter._build_audit_verdict_preview 核(audit_verdict_task 预演 / 一段式放行 / 确认重放都经此, 一切提交路径汇入);
artifact_ingest._validate_task_artifact 与推导核 next_resolver 调用同一函数(拒因码沿用 F162, loop 对拒收的处理不变)。

靶场: F90 进程内真门 + 临时治理根 / 临时产品仓(F162 夹具助手唯一来源); 两条路径都经真产品 CLI 薄壳(aipos_cli.main)
→ 两阶段薄壳 → 真门动词处理器。不读真实治理根、不连真实门、不碰真实工位。
"""
from __future__ import annotations

import shlex
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import test_aipos_f162_runtime_evidence_supplementary_audit as f162  # noqa: E402  — 证据报告/运行记录助手唯一来源
import test_aipos_f90_loop_one_stage as f90  # noqa: E402  — loop 靶场唯一来源
from test_aipos_f73d_loop_driver import init_governance_repo  # noqa: E402
from test_aipos_f90_loop_one_stage import rig  # noqa: E402,F401  — pytest fixture
from tools.aipos_cli import next_resolver as nr  # noqa: E402

CATEGORY = "RUNTIME_EVIDENCE_MISSING"


def _show(text: str) -> None:
    print(text, flush=True)


def _to_audit_await(rig, task_id: str, **extra) -> str:
    """loop 推进到审计卡已认领、等审计报告(不落报告即停); 返回审计卡号。"""
    f90._card(rig.gov, task_id, "pending", **extra)
    init_governance_repo(rig.gov)
    audit_id = f"{task_id}R"

    def watch(args, expect_ready, deploy_guard=None):
        if f"/{audit_id}/" in " ".join(args.expect):
            return 2
        f90._executor_work(rig, task_id)
        return 0 if expect_ready([]) else 3

    f162._loop(rig, task_id, watch, max_wait=2)
    assert nr._find_task_in_queue(rig.gov, audit_id)[1] == "claimed"
    return audit_id


def _full_report(rig, audit_id: str, task_id: str, verdict: str = "PASS") -> Path:
    entries = [f162._entry(rig, audit_id, f162.CMD_TESTS), f162._entry(rig, audit_id, f162.CMD_DIAG)]
    return f162._report(rig, audit_id, verdict, evidence=entries, reviewed=task_id)


def _missing_report(rig, audit_id: str, task_id: str) -> Path:
    """人肉期样本(chris 58R 形): 只在审计树跑 preflight + 核执行体原件 hash, 无「独立运行证据」节。"""
    return f162._report(rig, audit_id, "PASS_WITH_NOTES", body_extra=f162._chris_style_58r(rig, audit_id), reviewed=task_id)


def _direct_command(rig, audit_id: str, task_id: str) -> str:
    """驱动方手敲的直提命令 = 推导核给出的 shell_command 原样(`lybra audit-verdict ... --confirm`); 取命令时临时落合格报告。"""
    _full_report(rig, audit_id, task_id)
    command = str(nr.derive_next_step(audit_id, rig.gov).get("shell_command") or "")
    assert command.startswith("lybra audit-verdict ") and " --confirm" in command, command
    return command


def _run(argv: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(argv, capture_output=True, text=True)  # 靶场 in_process_run: 进程内真产品 CLI


def _ingest(rig, audit_id: str) -> subprocess.CompletedProcess:
    return _run(["lybra", "artifact", "ingest", "--task-id", audit_id, "--kind", "verdict", "--workspace-root", str(rig.gov),
                 "--connection-json", str(rig.gov / ".lybra" / "connection.json")])


def _submit(rig, path: str, audit_id: str, command: str) -> subprocess.CompletedProcess:
    if path == "ingest":
        return _ingest(rig, audit_id)
    return _run(shlex.split(command))


def _verdicts(rig, task_id: str) -> list[Path]:
    return f90._records(rig.gov, "audit_verdicts", task_id)


@pytest.mark.parametrize("path", ["ingest", "direct"])
def test_declared_card_missing_rejected_then_full_evidence_passes_on_both_paths(rig, path):
    """声明 audit_runtime_evidence 的卡: 缺证据(chris 58R 形)两条路径都被拒(同一拒因码 RUNTIME_EVIDENCE_MISSING)、零裁决记录;
    补齐证据后两条路径都入门。direct = 手敲 `lybra audit-verdict --confirm`(F162 旁路)。"""
    task_id = f162.TASK  # 运行记录助手(f162._entry)按 F162 被审卡号渲染占位; 每个参数一个独立临时根
    audit_id = _to_audit_await(rig, task_id, audit_runtime_evidence=f162.EVIDENCE)
    command = _direct_command(rig, audit_id, task_id)
    _missing_report(rig, audit_id, task_id)
    rejected = _submit(rig, path, audit_id, command)
    text = rejected.stdout + rejected.stderr
    _show(f"[F163·{path}·缺证据拒收原文] rc={rejected.returncode}\n{text}")
    assert rejected.returncode != 0, text
    assert CATEGORY in text and "缺「独立运行证据」节" in text, text
    assert not _verdicts(rig, task_id), "拒收期间零裁决记录"
    assert nr._find_task_in_queue(rig.gov, audit_id)[1] == "claimed"

    _full_report(rig, audit_id, task_id)
    passed = _submit(rig, path, audit_id, command)
    text = passed.stdout + passed.stderr
    _show(f"[F163·{path}·证据齐全通过原文] rc={passed.returncode}\n{text}")
    assert passed.returncode == 0, text
    records = _verdicts(rig, task_id)
    assert len(records) == 1, records
    verdict = nr._read_frontmatter(records[0])
    _show(f"[F163·{path}·裁决记录] {records[0].name} verdict={verdict['verdict']} audit_task_id={verdict['audit_task_id']}")
    assert verdict["verdict"] == "PASS" and verdict["audit_task_id"] == audit_id


@pytest.mark.parametrize("path", ["ingest", "direct"])
def test_undeclared_card_unchanged_on_both_paths(rig, path):
    """未声明卡: PASS 无证据节两条路径都照旧入门(行为不变)。"""
    task_id = f"PROBE-F163-UNDECL-{path.upper()}"
    audit_id = _to_audit_await(rig, task_id)
    f162._report(rig, audit_id, "PASS", reviewed=task_id)
    command = str(nr.derive_next_step(audit_id, rig.gov).get("shell_command") or "")
    assert command.startswith("lybra audit-verdict "), command
    res = _submit(rig, path, audit_id, command)
    text = res.stdout + res.stderr
    _show(f"[F163·{path}·未声明卡行为不变原文] rc={res.returncode}\n{text}")
    assert res.returncode == 0 and CATEGORY not in text, text
    assert len(_verdicts(rig, task_id)) == 1


def test_direct_submit_wrong_worktree_and_pass_override_rejected_at_gate(rig):
    """门侧判据按门收到的裁决值与提交的产物核: 报告写 FAIL(不受限)而手敲直提改成 PASS = 仍按 PASS 核运行证据并拒;
    证据工作树为被审执行卡工作树 = 同一拒因原文(判据只此一份)。"""
    task_id = f162.TASK
    audit_id = _to_audit_await(rig, task_id, audit_runtime_evidence=f162.EVIDENCE)
    command = _direct_command(rig, audit_id, task_id)
    f162._report(rig, audit_id, "FAIL", reviewed=task_id)
    res = _run(shlex.split(command))  # 推导核取命令时报告是 PASS → 命令带 --verdict PASS; 报告随后改成 FAIL 无证据
    text = res.stdout + res.stderr
    _show(f"[F163·direct·报告 FAIL 直提 PASS 原文] rc={res.returncode}\n{text}")
    assert res.returncode != 0 and CATEGORY in text, text
    _code, exec_wt = nr.card_worktree_location(rig.gov, task_id)
    f162._report(rig, audit_id, "PASS", reviewed=task_id, evidence=[
        f162._entry(rig, audit_id, f162.CMD_TESTS, worktree=str(exec_wt), excerpt=True),
        f162._entry(rig, audit_id, f162.CMD_DIAG, worktree=str(exec_wt), excerpt=True)])
    res = _run(shlex.split(command))
    text = res.stdout + res.stderr
    _show(f"[F163·direct·执行体树证据原文] rc={res.returncode}\n{text}")
    assert res.returncode != 0 and CATEGORY in text and "是被审执行卡" in text and "不是审计分配工作树" in text, text
    assert not _verdicts(rig, task_id)


def test_single_criterion_location():
    """判据只此一份: 定义在门侧(board_adapter), artifact_ingest 不再定义; 门写入口与 ingest / 推导核调用同一函数。"""
    import inspect

    from tools.aipos_cli import artifact_ingest, board_adapter

    assert hasattr(board_adapter, "runtime_evidence_check")
    assert not hasattr(artifact_ingest, "runtime_evidence_check")
    gate_src = inspect.getsource(board_adapter._build_audit_verdict_preview)
    ingest_src = inspect.getsource(artifact_ingest._validate_task_artifact)
    assert "runtime_evidence_check(" in gate_src
    assert "from tools.aipos_cli.board_adapter import runtime_evidence_check" in ingest_src
    defs = subprocess.run(["git", "grep", "-n", "def runtime_evidence_check", "--", "tools"], cwd=REPO_ROOT,
                          capture_output=True, text=True).stdout.splitlines()
    _show("[F163·判据定义处] " + " | ".join(defs))
    assert len(defs) == 1 and defs[0].startswith("tools/aipos_cli/board_adapter.py:"), defs
