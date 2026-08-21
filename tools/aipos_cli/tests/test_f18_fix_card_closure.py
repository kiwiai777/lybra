"""AIPOS-F18 修复轮2(close 级联 + 声明驱动)tests。

覆盖 R2 七条中的代码侧验收:
- F-E-1: fix卡close(PASS族)成功路径不再 NameError(重复append已删, 警告只留except内一处)
- F-B-1: 声明(toggle)解析根=运行代码所在仓根——治理根(无schema/)不再是解析根;
  置关→不派生+出声; 还原→派生恢复(声明驱动, 非代码写死)
- F-F-1: fix_closures 派生记录按声明写入(位置模板/必填字段/门标记)
- F-G-1: 卡号演进模式改声明跟随(改 pattern → 行为跟随; 改回 → 还原)
- F-H-1: 派生复审卡不再携带 derived_from_audit_task_id(级联误触发隐患);
  复审卡正文错字已修(确认); 原卡ID提取正则一式盖全 R/R2/R3…

跑法: python3 -m pytest tools/aipos_cli/tests/test_f18_fix_card_closure.py -q
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

import tools.schema_loader as schema_loader
from tools.aipos_cli.audit_derivation import derive_audit_task_id
from tools.aipos_cli.board_adapter import close_task

ORIGINAL_ID = "AIPOS-FX-001"
FIX_ID = "AIPOS-FX-001-fix1"
AUDIT_SOURCE_ID = "AIPOS-FX-001R"  # fix卡由该审计卡的FAIL派生(既有机制)


def _write_card(root: Path, state: str, task_id: str, filename: str, extra_fm: str = "") -> None:
    card = root / "5_tasks" / "queue" / state / filename
    card.parent.mkdir(parents=True, exist_ok=True)
    card.write_text(
        "---\n"
        f"task_id: {task_id}\n"
        f"title: Fixture card {task_id}\n"
        "project: lybra\n"
        f"status: {state}\n"
        "assigned_to: exec.lybra.kiwiai-dev\n"
        "agent_instance: exec.lybra.kiwiai-dev\n"
        "context_bundle: exec.lybra.kiwiai-dev\n"
        "task_mode: code\n"
        "priority: high\n"
        "created_by: advisor.test\n"
        "needs_owner: false\n"
        "output_target: tools/\n"
        "artifact_policy: formal_write\n"
        f"claim_id: claim_{task_id}_20260821_100000_exec-lybra-kiwiai-dev\n"
        "claimed_by: exec.lybra.kiwiai-dev\n"
        "claimed_at: '2026-08-21T10:00:00Z'\n"
        f"active_session_id: session_{task_id}_20260821_100000_exec-lybra-kiwiai-dev\n"
        f"{extra_fm}"
        "---\n"
        f"# {task_id}\n",
        encoding="utf-8",
    )


def _write_return(root: Path, task_id: str, return_id: str) -> None:
    d = root / "5_tasks" / "records" / "returns" / task_id
    d.mkdir(parents=True, exist_ok=True)
    (d / f"{return_id}.md").write_text(
        "---\n"
        "record_type: return_record\n"
        f"task_id: {task_id}\n"
        f"return_id: {return_id}\n"
        "returned_at: '2026-08-21T10:30:00Z'\n"
        "---\n"
        "# Return\n",
        encoding="utf-8",
    )


def _write_verdict(root: Path, reviewed_task_id: str, verdict: str, verdict_id: str, at: str = "2026-08-21T11:00:00Z") -> None:
    d = root / "5_tasks" / "records" / "audit_verdicts" / reviewed_task_id
    d.mkdir(parents=True, exist_ok=True)
    (d / f"{verdict_id}.md").write_text(
        "---\n"
        "record_type: audit_verdict\n"
        f"verdict_id: {verdict_id}\n"
        f"reviewed_task_id: {reviewed_task_id}\n"
        f"verdict: {verdict}\n"
        f"verdict_at: '{at}'\n"
        "---\n"
        f"# verdict {verdict}\n",
        encoding="utf-8",
    )


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    """治理工作区夹具: 原卡(claimed已归还) + fix卡(claimed已归还+PASS裁决)。

    注意: 本根【没有 schema/】—— 正是 F-B-1 指出的真实门语境(治理根无schema/)。
    """
    root = tmp_path
    for state in ("pending", "claimed", "completed", "blocked"):
        (root / "5_tasks" / "queue" / state).mkdir(parents=True, exist_ok=True)
    (root / "5_tasks" / "records" / "closures").mkdir(parents=True, exist_ok=True)
    (root / "governance").mkdir(parents=True, exist_ok=True)
    (root / "stage_archive").mkdir(parents=True, exist_ok=True)
    (root / "project.json").write_text('{"project": "lybra"}\n', encoding="utf-8")

    _write_card(root, "claimed", ORIGINAL_ID, "aipos-fx-001.md")
    _write_return(root, ORIGINAL_ID, "return_fx_001")
    _write_card(
        root, "claimed", FIX_ID, "aipos-fx-001-fix1.md",
        extra_fm=f"derived_from_audit_task_id: {AUDIT_SOURCE_ID}\n",
    )
    _write_return(root, FIX_ID, "return_fx_001_fix1")
    _write_verdict(root, FIX_ID, "PASS", "verdict_fx_fix1_pass")
    return root


def _close_fix(root: Path) -> dict:
    return close_task(
        task_id=FIX_ID,
        actor="exec.lybra.kiwiai-dev",
        closure_evidence={"finalize_commit_hash": "abc1234"},
        dry_run=False,
        repo_root=root,
    )


def _copy_schema_root(tmp_path: Path, mutate=None) -> Path:
    """复制真实产品仓 schema 到临时根并按需改写(测完即弃, 不动真声明)。"""
    src = schema_loader.code_repo_schema_root() / "schema" / "transitions.schema.json"
    root = tmp_path / "code_root"
    (root / "schema").mkdir(parents=True, exist_ok=True)
    data = json.loads(src.read_text(encoding="utf-8"))
    if mutate:
        mutate(data)
    (root / "schema" / "transitions.schema.json").write_text(
        json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return root


class TestFixCardCloseCascade:
    """F-E-1 / F-A-1 / F-F-1 / F-H-1: close 级联主路径。"""

    def test_pass_family_close_derives_reaudit_and_writes_record(self, repo: Path) -> None:
        """F-E-1+F-A-1+F-F-1: PASS族close→派生复审卡+fix_closures记录, 无NameError。"""
        result = _close_fix(repo)
        assert result.get("ok") is True, result.get("governance_warnings")
        warnings = result.get("data", {}).get("governance_warnings") or []
        assert not any("派生失败" in w for w in warnings), warnings  # F-E-1: 成功路径零派生失败

        fix_res = result.get("data", {}).get("fix_derivation_result") or {}
        derived_id = fix_res.get("derived_audit_task_id")
        assert derived_id == f"{ORIGINAL_ID}R", fix_res  # 原卡无R卡→首个R

        # 派生复审卡落 pending
        reaudit_card = repo / "5_tasks" / "queue" / "pending" / "aipos-fx-001r.md"  # 小写惯例
        assert reaudit_card.is_file(), "复审卡未落 pending/"
        text = reaudit_card.read_text(encoding="utf-8")
        # F-H-1: 复审卡不再携带 fix卡标识字段(级联误触发隐患已除)
        assert "derived_from_audit_task_id:" not in text
        assert f"derived_from_fix_task: {FIX_ID}" in text
        assert "硾认" not in text and "确认没有引入新的问题" in text  # F-H-1 错字已修

        # F-F-1: fix_closures 门生记录按声明位置写入
        record_rel = fix_res.get("fix_closure_record_path") or ""
        assert record_rel.startswith(f"5_tasks/records/fix_closures/{FIX_ID}/derivation_{FIX_ID}_"), record_rel
        record = (repo / record_rel).read_text(encoding="utf-8")
        for marker in (
            "record_type: fix_closure_derivation",
            "derived_at:",
            f"fix_task_id: {FIX_ID}",
            f"source_task_id: {ORIGINAL_ID}",
            f"derived_audit_task_id: {derived_id}",
            "verdict_id: verdict_fx_fix1_pass",
        ):
            assert marker in record, marker

    def test_fail_verdict_close_no_derivation(self, repo: Path) -> None:
        """非PASS族终局→不派生不炸(只收尾)。"""
        _write_verdict(repo, FIX_ID, "FAIL", "verdict_fx_fix1_fail2", at="2026-08-21T12:00:00Z")
        result = _close_fix(repo)
        assert result.get("ok") is True
        assert result.get("data", {}).get("fix_derivation_result") is None
        assert not (repo / "5_tasks" / "queue" / "pending" / "aipos-fx-001r.md").exists()

    def test_source_id_extraction_regex(self, repo: Path) -> None:
        """F-H-1: R/R2/R3 后缀一律剥净(正则一式)。"""
        _write_verdict(repo, FIX_ID, "PASS_WITH_NOTES", "verdict_fx_fix1_pwn")
        # derived_from_audit_task_id 指向 R2 审计卡 → 原卡=AIPOS-FX-001
        fix_card = repo / "5_tasks" / "queue" / "claimed" / "aipos-fx-001-fix1.md"
        fix_card.write_text(
            fix_card.read_text(encoding="utf-8").replace(
                f"derived_from_audit_task_id: {AUDIT_SOURCE_ID}",
                "derived_from_audit_task_id: AIPOS-FX-001R2",
            ),
            encoding="utf-8",
        )
        result = _close_fix(repo)
        fix_res = result.get("data", {}).get("fix_derivation_result") or {}
        assert fix_res.get("source_task_id") == ORIGINAL_ID, fix_res


class TestToggleFromDeclaration:
    """F-B-1: 开关声明读取(解析根=代码所在仓根; 治理根无schema/不影响)。"""

    def test_toggle_off_no_derivation_then_restore(self, repo: Path, tmp_path: Path, monkeypatch) -> None:
        def _off(data):
            data["nodes"]["fix_card_closure"]["toggle"]["enabled"] = False

        schema_root = _copy_schema_root(tmp_path, _off)
        monkeypatch.setattr(schema_loader, "code_repo_schema_root", lambda: schema_root)
        result = _close_fix(repo)
        assert result.get("ok") is True
        warnings = result.get("data", {}).get("governance_warnings") or []
        assert any("fix卡复审派生已关闭" in w for w in warnings), warnings  # 置关出声
        assert not (repo / "5_tasks" / "queue" / "pending" / "aipos-fx-001r.md").exists()
        assert result.get("data", {}).get("fix_derivation_result") is None

        # 还原(声明enabled=true)→同一close语境恢复派生(另开一套新原卡+fix卡验证)
        monkeypatch.undo()
        _write_card(repo, "claimed", "AIPOS-FX-002", "aipos-fx-002.md")
        _write_return(repo, "AIPOS-FX-002", "return_fx_002")
        _write_card(
            repo, "claimed", "AIPOS-FX-002-fix1", "aipos-fx-002-fix1.md",
            extra_fm="derived_from_audit_task_id: AIPOS-FX-002R\n",
        )
        _write_return(repo, "AIPOS-FX-002-fix1", "return_fx_002_fix1")
        _write_verdict(repo, "AIPOS-FX-002-fix1", "PASS", "verdict_fx_002_fix1")
        result2 = close_task(
            task_id="AIPOS-FX-002-fix1",
            actor="exec.lybra.kiwiai-dev",
            closure_evidence={"finalize_commit_hash": "def5678"},
            dry_run=False,
            repo_root=repo,
        )
        assert (result2.get("data", {}).get("fix_derivation_result") or {}).get("derived_audit_task_id") == "AIPOS-FX-002R"

    def test_governance_root_without_schema_does_not_disable_toggle(self, repo: Path) -> None:
        """F-B-1核心: 治理根(无schema/)不再导致SchemaLoadError→静默默认;真声明生效。"""
        result = _close_fix(repo)  # 未打补丁: 解析根=真实代码仓根(仓内有schema/)
        assert result.get("ok") is True
        warnings = result.get("data", {}).get("governance_warnings") or []
        assert not any("声明读取失败" in w for w in warnings), warnings
        assert result.get("data", {}).get("fix_derivation_result") is not None


class TestNumberingFromDeclaration:
    """F-G-1: 卡号演进模式改声明跟随。"""

    def test_default_pattern_r_sequence(self, tmp_path: Path) -> None:
        assert derive_audit_task_id("AIPOS-T", None) == "AIPOS-TR"

    def test_pattern_change_follows_declaration(self, tmp_path: Path, monkeypatch) -> None:
        def _rx(data):
            data["nodes"]["fix_card_closure"]["revision_card_numbering"]["pattern"] = "<原卡ID>RX[迭代序号]"

        schema_root = _copy_schema_root(tmp_path, _rx)
        monkeypatch.setattr(schema_loader, "code_repo_schema_root", lambda: schema_root)
        # 治理根夹具(空队列) → 首个后缀 RX
        assert derive_audit_task_id("AIPOS-T", tmp_path) == "AIPOS-TRX"

        # 已占坑演进: 预置 TRX 卡 → 下一号 TRX2
        _write_card(tmp_path, "completed", "AIPOS-TRX", "aipos-trx.md")
        assert derive_audit_task_id("AIPOS-T", tmp_path) == "AIPOS-TRX2"

        # 还原声明(去掉补丁)→ 回到 R 序列
        monkeypatch.undo()
        assert derive_audit_task_id("AIPOS-T", tmp_path) == "AIPOS-TR"
