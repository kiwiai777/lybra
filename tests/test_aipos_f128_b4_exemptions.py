"""AIPOS-F128 — 入库护栏 B④ 读项目声明: 产物槽(交回/裁决落点)与冻结卡名下记录豁免 record_type, 预演列明豁免计数。

病根(人肉期项目接入总预演): 项目把 return_root / verdict_root 声明在 5_tasks/records 之下(人肉期形), B④ 对 records/** 新增
一律要求 record_type → 执行体 Return / 审计报告(产物, 非门记录)每次结案都被拒; 存量冻结卡(AIPOS-F122)名下的人肉期手写记录
无门出生标记, 补字段 = 伪造门记录。

件① 产物槽豁免: <return_root>/<ID>/** 与 <verdict_root>/<ID>/**(落点只读 workspace_config.project_paths)不要求 record_type;
     B① 对槽内代码文件照旧拒; 槽根直下(无 <ID>/)不豁免。
件② 冻结卡豁免: <records>/<类型>/<ID>/** 且 ID 被冻结(唯一判定 legacy_baseline.frozen_tasks); 解冻后恢复要求; 清单读不出 = 拒。
件③ 不静默: governance-commit 预演 / 正式提交 / pre-commit hook 同一判据同一文案「B④ 豁免: 产物槽 N、冻结卡 M」+ 依据, --json 同步。
件④ 夹具: 临时治理根仿人肉期布局(卡号前缀参数化, 不涉任何真实项目); 声明段缺省 = 行为不变。

靶场全部临时 git 仓(禁碰真实治理根); 母本 hook 装进临时仓 .git/hooks 走真 pre-commit。
跑法: python3 -m pytest tests/test_aipos_f128_b4_exemptions.py -q -s
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from test_aipos_f122_legacy_baseline import _card  # noqa: E402  — 人肉期卡样本唯一来源

from tools.aipos_cli import governance_commit as gc  # noqa: E402
from tools.aipos_cli.governance_guardrails import (  # noqa: E402
    EXEMPT_ARTIFACT_SLOT,
    EXEMPT_LEGACY_FROZEN,
    GuardrailDeclarationError,
    check_entries,
    format_exemptions,
    format_report,
    load_guardrail_declarations,
)
from tools.schema_constants import Verdict  # noqa: E402

HOOK_SRC = REPO_ROOT / "tools" / "hooks" / "governance-pre-commit"
WS_REL = "2_projects/sample"
ACTOR = "advisor.sample"
RETURNS = "5_tasks/records/returns"
VERDICTS = "5_tasks/records/audit_verdicts"


def _show(text: str) -> None:
    print(text, flush=True)


def _git(cwd: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-c", "core.quotepath=false", *args], cwd=str(cwd), check=check,
                          capture_output=True, text=True)


def _cli(argv: list[str], capsys) -> tuple[int, str, str]:
    from tools.aipos_cli.aipos_cli import main

    capsys.readouterr()
    rc = main(argv)
    captured = capsys.readouterr()
    return rc, captured.out, captured.err


def _write(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


class Rig:
    def __init__(self, repo: Path, ws: Path, prefix: str):
        self.repo, self.ws, self.prefix = repo, ws, prefix

    def rel(self, ws_rel: str) -> str:
        return f"{WS_REL}/{ws_rel}"


def _make_rig(tmp_path: Path, monkeypatch, capsys, *, prefix: str, absolute: bool = False,
              return_root: str = RETURNS, verdict_root: str = VERDICTS) -> Rig:
    """人肉期布局治理根(共享仓内子工作区): 交回 / 裁决落点声明在 records 之下; 冻结 <P>-1、<P>-2, <P>-9 活跃。"""
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    (tmp_path / "home").mkdir(exist_ok=True)
    monkeypatch.setenv("LYBRA_SCHEMA_DIR", str(REPO_ROOT / "schema"))
    repo = tmp_path / "repo"
    ws = repo / WS_REL
    for state in ("pending", "claimed", "completed", "blocked"):
        (ws / "5_tasks" / "queue" / state).mkdir(parents=True)
    _write(ws / "governance" / "README.md", "---\nstatus: active\n---\n# governance\n")
    (ws / "5_tasks" / "records").mkdir(parents=True)
    paths = {"return_root": str(ws / return_root) if absolute else return_root,
             "verdict_root": str(ws / verdict_root) if absolute else verdict_root}
    _write(ws / "project.json", json.dumps({"project": "sample-proj", "config_version": 1, "paths": paths}, indent=2) + "\n")
    _card(ws, "completed", f"{prefix}-1")
    _card(ws, "claimed", f"{prefix}-2")
    _card(ws, "pending", f"{prefix}-9")
    rc, out, err = _cli(["project", "freeze-legacy", "--workspace-root", str(ws), "--task-ids", f"{prefix}-1", f"{prefix}-2",
                         "--reason", "人肉期存量", "--actor", ACTOR, "--confirm"], capsys)
    assert rc == 0, out + err
    _git(tmp_path, "init", "-q", "-b", "main", str(repo))
    _git(repo, "config", "user.name", "Rig User")
    _git(repo, "config", "user.email", "rig@example.com")
    _git(repo, "add", "--", WS_REL)
    _git(repo, "commit", "-q", "-m", "rig: human-era governance workspace + legacy freeze")
    hooks = repo / ".git" / "hooks"
    hooks.mkdir(exist_ok=True)
    shutil.copy(HOOK_SRC, hooks / "pre-commit")
    os.chmod(hooks / "pre-commit", 0o755)
    return Rig(repo, ws, prefix)


@pytest.fixture(params=["DEMO", "ZETA"])
def rig(request, tmp_path, monkeypatch, capsys):
    return _make_rig(tmp_path, monkeypatch, capsys, prefix=request.param)


def _samples(r: Rig) -> dict[str, str]:
    """四类夹具 + 边界: 键 = 期望, 值 = 仓根相对路径。全部无 record_type(人肉期 / 产物形)。"""
    p = r.prefix
    files = {
        "slot_return": f"{RETURNS}/{p}-9/RETURN.md",
        "slot_return_evidence": f"{RETURNS}/{p}-9/evidence/run.log.md",
        "slot_verdict": f"{VERDICTS}/{p}-9A/AUDIT-REPORT.md",
        "frozen_claim": f"5_tasks/records/claims/{p}-1/claim_{p}-1_20260801_000000_human.md",
        "frozen_closure_lower": f"5_tasks/records/closures/{p.lower()}-2/closure-note.md",
        "active_claim": f"5_tasks/records/claims/{p}-9/claim_{p}-9_20260801_000000_human.md",
        "loose_record": "5_tasks/records/misc/notes.md",
        "slot_root_direct": f"{RETURNS}/README.md",
        "slot_code": f"{RETURNS}/{p}-9/tool.py",
    }
    for key, rel in files.items():
        body = "print('x')\n" if rel.endswith(".py") else f"# {key}: 人肉期 / 产物形, 无 record_type\n"
        _write(r.ws / rel, body)
    return {k: r.rel(v) for k, v in files.items()}


def _entries(files: dict[str, str]) -> list[tuple[str, str]]:
    return [("A", rel) for rel in files.values()]


# ===========================================================================
# 件①② 四类夹具 + 边界(check_entries 唯一实现)
# ===========================================================================

def test_items12_slot_and_frozen_exempt_others_still_rejected(rig):
    files = _samples(rig)
    decls = load_guardrail_declarations(REPO_ROOT / "schema")
    report = check_entries(rig.repo, _entries(files), decls, current_branch="main")
    text = format_report(report, decls)
    _show(f"[件①②·四类夹具 prefix={rig.prefix}]\n{text}\n{json.dumps(report.to_dict()['b4_exemptions'], ensure_ascii=False, indent=1)}")

    by_file = {v["file"]: v for v in report.violations}
    exempt = {e["file"]: e for e in report.exemptions}
    # 产物槽: Return / 证据 / 审计报告无 record_type 可过
    for key in ("slot_return", "slot_return_evidence", "slot_verdict"):
        assert files[key] in exempt and exempt[files[key]]["kind"] == EXEMPT_ARTIFACT_SLOT, key
        assert files[key] not in by_file, key
    assert exempt[files["slot_return"]]["basis"] == f"project.json paths.return_root → {WS_REL}/{RETURNS}/<ID>/ (declared)"
    assert exempt[files["slot_verdict"]]["declared_key"] == "verdict_root"
    # 冻结卡手写记录可过(卡号目录大小写不敏感, 与冻结判定同按大写)
    for key in ("frozen_claim", "frozen_closure_lower"):
        assert exempt[files[key]]["kind"] == EXEMPT_LEGACY_FROZEN and files[key] not in by_file, key
    assert exempt[files["frozen_claim"]]["basis"].startswith(f"legacy_baseline 清单 5_tasks/records/legacy_baseline/")
    # 未冻结卡手写记录仍拒; records 下非槽非冻结新文件仍拒; 槽根直下(无 <ID>/)不算槽
    for key in ("active_claim", "loose_record", "slot_root_direct"):
        assert by_file[files[key]]["check"] == "B④" and files[key] not in exempt, key
    # B① 对槽内代码文件仍拒(B④ 豁免不及 B①)
    assert by_file[files["slot_code"]]["check"] == "B①"
    assert report.to_dict()["b4_exemptions"][EXEMPT_ARTIFACT_SLOT] == 4  # 3 产物 + tool.py(B④ 免, B① 拒)
    assert report.to_dict()["b4_exemptions"][EXEMPT_LEGACY_FROZEN] == 2
    assert not report.ok
    assert "  B④ 豁免: 产物槽 4、冻结卡 2" in text


def test_item1_absolute_path_declaration(tmp_path, monkeypatch, capsys):
    """落点声明为绝对路径(人肉期项目常见形)同样按 project_paths 解析到仓内槽。"""
    r = _make_rig(tmp_path, monkeypatch, capsys, prefix="DEMO", absolute=True)
    files = _samples(r)
    decls = load_guardrail_declarations(REPO_ROOT / "schema")
    report = check_entries(r.repo, _entries(files), decls, current_branch="main")
    _show(f"[件①·绝对路径声明] {report.to_dict()['b4_exemptions'][EXEMPT_ARTIFACT_SLOT]} 产物槽豁免")
    assert {e["file"] for e in report.exemptions if e["kind"] == EXEMPT_ARTIFACT_SLOT} == {
        files["slot_return"], files["slot_return_evidence"], files["slot_verdict"], files["slot_code"]}


def test_item1_slot_outside_records_changes_nothing(tmp_path, monkeypatch, capsys):
    """lybra 形(return_root 在 task_cards/): 槽不在 records 下 → 无产物槽豁免, records 新增仍按原规则拒。"""
    r = _make_rig(tmp_path, monkeypatch, capsys, prefix="DEMO", return_root="task_cards", verdict_root="task_cards")
    rel = r.rel(f"5_tasks/records/returns/DEMO-9/RETURN.md")
    _write(r.repo / rel, "# no record_type\n")
    decls = load_guardrail_declarations(REPO_ROOT / "schema")
    report = check_entries(r.repo, [("A", rel)], decls, current_branch="main")
    _show(f"[件①·槽在 records 外]\n{format_report(report, decls)}")
    assert [v["check"] for v in report.violations] == ["B④"] and report.exemptions == []


def test_item2_unfreeze_restores_requirement(rig, capsys):
    files = _samples(rig)
    rc, out, err = _cli(["project", "freeze-legacy", "--workspace-root", str(rig.ws), "--unfreeze", f"{rig.prefix}-1",
                         "--reason", "恢复推进", "--actor", ACTOR, "--confirm"], capsys)
    assert rc == 0, out + err
    decls = load_guardrail_declarations(REPO_ROOT / "schema")
    report = check_entries(rig.repo, [("A", files["frozen_claim"]), ("A", files["frozen_closure_lower"])], decls,
                           current_branch="main")
    _show(f"[件②·解冻后]\n{format_report(report, decls)}")
    assert [v["file"] for v in report.violations] == [files["frozen_claim"]]
    assert [e["file"] for e in report.exemptions] == [files["frozen_closure_lower"]]


def test_item2_broken_manifest_fail_closed(rig):
    files = _samples(rig)
    _write(rig.ws / "5_tasks/records/legacy_baseline/zz-broken.md", "---\nrecord_type: legacy_baseline\naction: bogus\n---\n")
    decls = load_guardrail_declarations(REPO_ROOT / "schema")
    with pytest.raises(GuardrailDeclarationError, match="冻结卡豁免读不出"):
        check_entries(rig.repo, [("A", files["frozen_claim"])], decls, current_branch="main")
    # 预演: 同一模块 → FAIL(拒绝放行), 不当「无冻结」继续
    res = gc.governance_commit(rig.ws, None, ACTOR, repo_root=REPO_ROOT, dry_run=True, push=False,
                               paths=["5_tasks/records/claims"])
    _show(f"[件②·清单坏] verdict={res['verdict']} message={res['message'][:200]}")
    assert res["verdict"] == Verdict.FAIL and "冻结卡豁免读不出" in res["message"]


def test_item4_declaration_absent_behaviour_unchanged(rig, tmp_path):
    """config.schema 豁免段缺省 = 无豁免、不出声(行为与文案不变)。"""
    files = _samples(rig)
    schema_dir = tmp_path / "schema_no_exempt"
    schema_dir.mkdir()
    config = json.loads((REPO_ROOT / "schema" / "config.schema.json").read_text(encoding="utf-8"))
    del config["governance_structure"]["file_declarations"]["record_file"]["required_frontmatter_exemptions"]
    (schema_dir / "config.schema.json").write_text(json.dumps(config, ensure_ascii=False), encoding="utf-8")
    decls = load_guardrail_declarations(schema_dir)
    report = check_entries(rig.repo, _entries(files), decls, current_branch="main")
    assert decls.slot_path_keys == () and decls.legacy_frozen_exempt is False
    assert report.exemptions == [] and format_exemptions(report, decls) == []
    assert "B④ 豁免" not in format_report(report, decls)
    b4 = {v["file"] for v in report.violations if v["check"] == "B④"}
    assert b4 == set(files.values())  # 全部缺 record_type 均被 B④ 拒(.py 另被 B① 拒)


def test_item4_malformed_declaration_fail_closed(tmp_path):
    config = json.loads((REPO_ROOT / "schema" / "config.schema.json").read_text(encoding="utf-8"))
    config["governance_structure"]["file_declarations"]["record_file"]["required_frontmatter_exemptions"]["artifact_slot"][
        "project_paths_keys"] = ["finalize_mode"]
    (tmp_path / "config.schema.json").write_text(json.dumps(config, ensure_ascii=False), encoding="utf-8")
    with pytest.raises(GuardrailDeclarationError, match="artifact_slot"):
        load_guardrail_declarations(tmp_path)


# ===========================================================================
# 件③ 不静默: 预演(CLI 文本 + --json)/ 正式提交 / pre-commit hook 同一判据同一文案
# ===========================================================================

def test_item3_dry_run_formal_and_hook_list_exemption_counts(rig, capsys):
    files = _samples(rig)
    for key in ("active_claim", "loose_record", "slot_root_direct", "slot_code"):  # 只留可过的: 3 产物 + 2 冻结
        (rig.repo / files[key]).unlink()
    paths = ["5_tasks/records"]

    rc, out, err = _cli(["governance-commit", "--governance-root", str(rig.ws), "--actor", ACTOR, "--workspace-root",
                         str(REPO_ROOT), "--dry-run", "--paths", *paths], capsys)
    _show(f"[件③·预演文本] rc={rc}\n{out}{err}")
    assert rc == 0, out + err
    assert "  - B④ 豁免: 产物槽 3、冻结卡 2" in out
    assert f"产物槽 依据 project.json paths.return_root → {WS_REL}/{RETURNS}/<ID>/ (declared): 2 file(s)" in out
    assert f"产物槽 依据 project.json paths.verdict_root → {WS_REL}/{VERDICTS}/<ID>/ (declared): 1 file(s)" in out
    assert "冻结卡 依据 legacy_baseline 清单 5_tasks/records/legacy_baseline/" in out

    rc, out, err = _cli(["governance-commit", "--governance-root", str(rig.ws), "--actor", ACTOR, "--workspace-root",
                         str(REPO_ROOT), "--dry-run", "--json", "--paths", *paths], capsys)
    data = json.loads(out)
    ex = data["guardrail_report"]["b4_exemptions"]
    _show(f"[件③·预演 --json] verdict={data['verdict']} b4_exemptions={json.dumps({k: v for k, v in ex.items() if k != 'files'})}"
          f" files={len(ex['files'])}")
    assert rc == 0 and data["verdict"] == Verdict.PASS
    assert ex[EXEMPT_ARTIFACT_SLOT] == 3 and ex[EXEMPT_LEGACY_FROZEN] == 2 and len(ex["files"]) == 5
    assert all(item["basis"] for item in ex["files"])

    # 正式提交(不 push): 前置四检 + 真 hook 同判放行, 操作行同样列明豁免
    head_before = _git(rig.repo, "rev-parse", "HEAD").stdout.strip()
    res = gc.governance_commit(rig.ws, None, ACTOR, repo_root=REPO_ROOT, dry_run=False, push=False, paths=paths)
    _show(f"[件③·正式提交] verdict={res['verdict']} committed={res['committed']}\n" + "\n".join(res["operations"]))
    assert res["verdict"] == Verdict.PASS and res["committed"] is True
    assert "B④ 豁免: 产物槽 3、冻结卡 2" in "\n".join(res["operations"])
    assert res["guardrail_report"]["b4_exemptions"][EXEMPT_LEGACY_FROZEN] == 2
    assert _git(rig.repo, "rev-parse", "HEAD").stdout.strip() != head_before


def test_item3_hook_same_judgement_as_dry_run(rig):
    """真 pre-commit hook: 可过的豁免文件放行并列明计数; 混入未冻结手写记录 → 拒, 拒因段与预演逐字同源。"""
    files = _samples(rig)
    keep = [files[k] for k in ("slot_return", "slot_verdict", "frozen_claim")]
    _git(rig.repo, "add", "--", *keep)
    proc = _git(rig.repo, "commit", "-q", "-m", "exempt artifacts", check=False)
    hook_out = proc.stdout + proc.stderr
    _show(f"[件③·hook 放行] rc={proc.returncode}\n{hook_out}")
    assert proc.returncode == 0, hook_out
    assert "B④ 豁免: 产物槽 2、冻结卡 1" in hook_out

    _git(rig.repo, "add", "--", files["active_claim"], files["frozen_closure_lower"])
    proc = _git(rig.repo, "commit", "-q", "-m", "mixed", check=False)
    hook_out = proc.stdout + proc.stderr
    _show(f"[件③·hook 拒] rc={proc.returncode}\n{hook_out}")
    assert proc.returncode == 1
    assert f"  - {files['active_claim']}: New record lacks required field 'record_type'" in hook_out
    assert "B④ 豁免: 产物槽 0、冻结卡 1" in hook_out
    _git(rig.repo, "reset", "-q")
    res = gc.governance_commit(rig.ws, None, ACTOR, repo_root=REPO_ROOT, dry_run=True, push=False,
                               paths=[files["active_claim"][len(WS_REL) + 1:], files["frozen_closure_lower"][len(WS_REL) + 1:]])
    assert res["verdict"] == Verdict.BLOCK
    start = hook_out.index("B④ 豁免")
    assert hook_out[start:hook_out.index("Guardrails (", start)].strip() == \
        res["message"][res["message"].index("B④ 豁免"):res["message"].index("Guardrails (")].strip()


# ===========================================================================
# 单源: 豁免判据只在 governance_guardrails; 落点 / 冻结判定复用唯一读取口
# ===========================================================================

def test_single_source_reuse():
    src = (REPO_ROOT / "tools/aipos_cli/governance_guardrails.py").read_text(encoding="utf-8")
    assert "from tools.aipos_cli.workspace_config import project_paths" in src
    assert "from tools.aipos_cli.legacy_baseline import frozen_tasks" in src
    assert "records/returns" not in src and "audit_verdicts" not in src  # 不写死落点
    commit_src = (REPO_ROOT / "tools/aipos_cli/governance_commit.py").read_text(encoding="utf-8")
    assert "format_exemptions(report, decls)" in commit_src and "frozen_tasks" not in commit_src
