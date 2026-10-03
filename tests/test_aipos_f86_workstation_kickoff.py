"""AIPOS-F86 — pi 工位开工面三件夹具(靶场 = F73D/F78/F78C/F80 既有靶场, 禁第二份)。

件① 开工面单源: `lybra my-tasks --actor <实例> --json` 对每张 claimed 卡输出 card_path / worktree_path / worktree_exists /
    worktree_refusal / report_path / report_refusal; 工作树 = next_resolver.card_worktree_location(与 claim 建树
    _ensure_worktree、card render 同一函数), 报告落点 = card_report_path(执行卡 return_root + artifact_ingest.return 候选;
    审计卡 = F66B 件③ audit_report_artifact_path, 与 audit_derivation.render_audit_report_location 同一读取口)。
    三类卡(lybra 形单仓 / F78C 双仓 lane.repo 卡 / 审计卡)字段 = claim 实际建树路径; 未建树 / 不可推导 = 明确拒因, 从不空串;
    my-tasks JSON 交给 go.ts 的 planGo(真 node 跑)→ kickoff 文本含该 worktree_path / report_path。
件② 三份章程母本「单一真相源」指向现存 governance/COMMANDS.md § 0.5(= config.schema governance_docs 声明名 = 硬规矩提取器
    同一行), grep ADVISOR-COMMANDS 零命中, charter_render 渲染后无残留。
件③ distribution.schema minimum_bootable_set 说明去 /lybra sync、/lybra on, 改为零门路径(enroll → lybra sync → /go);
    f57 TS 夹具不再写死 ./bin/lybra, 走与 f20 同口径的部署解析(resolveLybraBin → PATH)。
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from test_aipos_f73d_loop_driver import AUDITOR, EXEC, _write  # noqa: E402  — 靶场唯一来源
from test_aipos_f78_engine_agnostic import _card, _make_gov  # noqa: E402
from test_aipos_f78c_card_repo import _dual_gov, _single_gov  # noqa: E402
from test_aipos_f80_zero_gate_charter import _identity, _make_gov as _charter_gov  # noqa: E402
from tools.aipos_cli.audit_derivation import render_audit_report_location  # noqa: E402
from tools.aipos_cli.card_render import build_intent_model  # noqa: E402
from tools.aipos_cli.charter_render import charter_render_context, render_charter  # noqa: E402
from tools.aipos_cli.next_resolver import _ensure_worktree, _return_artifact_path  # noqa: E402

GO_TS = REPO_ROOT / "agents" / "harness" / "pi" / "_shared" / "extensions" / "go.ts"
VIEW_KEYS = ("card_path", "worktree_path", "worktree_exists", "worktree_refusal", "report_path", "report_refusal")
GATE_TEXT_RE = re.compile(r"lybra next|next --run|lybra_\w+")
ROLES = ("executor", "auditor", "advisor")


def _my_tasks(gov: Path, actor: str, capsys: pytest.CaptureFixture) -> dict:
    from tools.aipos_cli.aipos_cli import main

    capsys.readouterr()
    rc = main(["--workspace-root", str(gov), "my-tasks", "--actor", actor, "--json"])
    out = capsys.readouterr().out
    assert rc == 0, out
    return json.loads(out)


def _task(data: dict, task_id: str) -> dict:
    hits = [t for t in data["tasks"] if t["task_id"] == task_id]
    assert len(hits) == 1, [t["task_id"] for t in data["tasks"]]
    return hits[0]


def _show(msg: str) -> None:
    """夹具证据输出走真 stdout(capsys 只截 my-tasks 那一次 CLI 输出, 不吞证据行)。"""
    sys.__stdout__.write(msg + "\n")
    sys.__stdout__.flush()


def _print_view(label: str, task: dict) -> None:
    _show(f"[{label}] " + json.dumps({k: task.get(k) for k in ("task_id", *VIEW_KEYS)}, ensure_ascii=False))


def _no_empty_strings(task: dict) -> None:
    for key in VIEW_KEYS:
        assert task.get(key) != "", f"{key} 输出空串(须为值或 null + 拒因字段)"


def _plan_go(my_tasks_json: dict, tmp_path: Path) -> dict:
    """真 node 跑 go.ts 的 planGo(工位侧只读函数)——给定 my-tasks JSON 取开工结果。node 缺 = 失败(不跳过)。"""
    node = shutil.which("node")
    assert node, "node 不在 PATH: go.ts 夹具需 Node ≥ 22(与 run-all TS 夹具同前置)"
    data_file = tmp_path / "my-tasks.json"
    data_file.write_text(json.dumps(my_tasks_json, ensure_ascii=False), encoding="utf-8")
    script = (
        f"import {{ planGo }} from {json.dumps(GO_TS.as_uri())};\n"
        "import { readFileSync } from 'node:fs';\n"
        f"process.stdout.write(JSON.stringify(planGo(JSON.parse(readFileSync({json.dumps(str(data_file))}, 'utf-8')))));\n"
    )
    proc = subprocess.run([node, "--input-type=module", "-e", script], capture_output=True, text=True, timeout=60)
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout)


# ===========================================================================
# 件① 开工面单源
# ===========================================================================

def test_item1_lybra_single_repo_fields_equal_claim_built_tree(tmp_path, monkeypatch, capsys):
    """lybra 形单仓(project.json code_repo): 建树前 = WORKTREE_NOT_CREATED 拒因(路径已推导, 非空串);
    claim 建树(_ensure_worktree)后 my-tasks worktree_path = 实际建树路径, report_path = 执行卡声明位; go planGo kickoff 含两者原值。"""
    gov, repo = _single_gov(tmp_path, monkeypatch)
    task_id = "AIPOS-F86A"
    _card(gov, task_id, "claimed")
    _card(gov, "AIPOS-F86P", "pending")

    before = _task(_my_tasks(gov, EXEC, capsys), task_id)
    _print_view("单仓·建树前", before)
    _no_empty_strings(before)
    assert before["worktree_exists"] is False and before["worktree_path"] == str(repo / ".worktrees" / task_id)
    assert before["worktree_refusal"]["code"] == "WORKTREE_NOT_CREATED"
    assert not GATE_TEXT_RE.search(before["worktree_refusal"]["reason"].replace(before["worktree_path"], "<wt>")), before["worktree_refusal"]

    built = _ensure_worktree(gov, task_id)
    assert built["ok"], built
    data = _my_tasks(gov, EXEC, capsys)
    after = _task(data, task_id)
    _print_view("单仓·建树后", after)
    _show(f"[单仓] claim 实际建树路径: {built['worktree_path']}")
    assert after["worktree_path"] == built["worktree_path"] and Path(after["worktree_path"]).is_dir()
    assert after["worktree_exists"] is True and after["worktree_refusal"] is None
    assert after["report_path"] == str(_return_artifact_path(gov, task_id)) == str(gov / "task_cards" / task_id / "RETURN.md")
    assert after["report_refusal"] is None
    assert after["card_path"] == str((gov / "5_tasks" / "queue" / "claimed" / f"{task_id.lower()}.md").resolve())
    # 非 claimed 卡不附开工面字段(只对 claimed 卡给)
    assert "worktree_path" not in _task(data, "AIPOS-F86P")
    # card render(同一函数)与 my-tasks 一致
    model = build_intent_model(task_id, gov)
    assert model["worktree"] == after["worktree_path"] and model["return_path"] == after["report_path"]

    plan = _plan_go(data, tmp_path)
    _show("[单仓] go planGo kickoff 原文:\n" + plan.get("kickoff", json.dumps(plan, ensure_ascii=False)))
    assert plan["kind"] == "kickoff" and plan["taskId"] == task_id
    assert f"工作树路径: {after['worktree_path']}" in plan["kickoff"]
    assert f"报告落点: {after['report_path']}" in plan["kickoff"]
    assert f"任务卡路径: {after['card_path']}" in plan["kickoff"]


def test_item1_dual_repo_lane_repo_card_lands_on_declared_repo(tmp_path, monkeypatch, capsys):
    """F78C 双仓: lane.repo=b 的卡 → my-tasks worktree_path = repo-b/.worktrees/<ID>(= claim 实际建树), 不落默认仓 a、不落治理根。"""
    gov, repos = _dual_gov(tmp_path, monkeypatch)
    task_id = "AIPOS-F86B"
    _card(gov, task_id, "claimed", extra={"lane": {"repo": "b", "paths": ["tools/aipos_cli/", "tests/"], "roles": ["executor"]}})
    built = _ensure_worktree(gov, task_id)
    assert built["ok"], built
    data = _my_tasks(gov, EXEC, capsys)
    view = _task(data, task_id)
    _print_view("双仓·lane.repo=b", view)
    _show(f"[双仓] claim 实际建树路径: {built['worktree_path']}")
    _no_empty_strings(view)
    assert view["worktree_path"] == built["worktree_path"] == str(repos["b"] / ".worktrees" / task_id)
    assert view["worktree_exists"] is True and view["worktree_refusal"] is None
    assert not str(view["worktree_path"]).startswith(str(repos["a"])) and not str(view["worktree_path"]).startswith(str(gov))
    assert view["report_path"] == str(gov / "task_cards" / task_id / "RETURN.md")
    plan = _plan_go(data, tmp_path)
    assert plan["kind"] == "kickoff" and f"工作树路径: {view['worktree_path']}" in plan["kickoff"]


def test_item1_audit_card_report_path_is_audit_location(tmp_path, monkeypatch, capsys):
    """审计卡(task_mode=audit, 审计卡 ID 目录): my-tasks(--actor 审计实例)worktree_path = claim 实际建树,
    report_path = render_audit_report_location(F66B 件③)= <verdict_root>/<审计卡ID>/RETURN.md, 不是被审卡目录。"""
    gov, repo = _single_gov(tmp_path, monkeypatch)
    audit_id = "AIPOS-F86AR"
    _card(gov, audit_id, "claimed", assigned=AUDITOR, task_mode="audit", extra={"reviewed_task_id": "AIPOS-F86A"})
    built = _ensure_worktree(gov, audit_id)
    assert built["ok"], built
    data = _my_tasks(gov, AUDITOR, capsys)
    view = _task(data, audit_id)
    _print_view("审计卡", view)
    _show(f"[审计卡] claim 实际建树路径: {built['worktree_path']}")
    _no_empty_strings(view)
    assert view["worktree_path"] == built["worktree_path"] == str(repo / ".worktrees" / audit_id)
    assert view["report_path"] == render_audit_report_location(gov, audit_id) == str(gov / "task_cards" / audit_id / "RETURN.md")
    assert "AIPOS-F86A/" not in view["report_path"]
    model = build_intent_model(audit_id, gov)
    assert model["return_path"] == view["report_path"] and model["worktree"] == view["worktree_path"]
    plan = _plan_go(data, tmp_path)
    assert plan["kind"] == "kickoff" and f"报告落点: {view['report_path']}" in plan["kickoff"]


def test_item1_chris_shape_report_follows_declared_return_root(tmp_path, monkeypatch, capsys):
    """换项目: chris 形 paths.return_root=5_tasks/records/returns → report_path 随声明, 不写死 task_cards。"""
    gov = _make_gov(tmp_path, monkeypatch, shape="chris")
    task_id = "HBJ-F86C"
    _card(gov, task_id, "claimed")
    view = _task(_my_tasks(gov, EXEC, capsys), task_id)
    _print_view("chris 形", view)
    assert view["report_path"] == str(gov / "5_tasks" / "records" / "returns" / task_id / "RETURN.md")
    assert view["worktree_path"] == str(tmp_path / "product-chris" / ".worktrees" / task_id)


def test_item1_unresolvable_repo_gives_refusal_not_empty_string(tmp_path, monkeypatch, capsys):
    """不可推导(lane.repo 不在仓清单)→ worktree_path=null + worktree_refusal.code=LANE_REPO_UNDECLARED(resolve_card_repo 原拒因);
    planGo 转述拒因, 零门动词。"""
    gov, _repos = _dual_gov(tmp_path, monkeypatch)
    task_id = "AIPOS-F86U"
    _card(gov, task_id, "claimed", extra={"lane": {"repo": "zzz", "paths": ["tools/"], "roles": ["executor"]}})
    data = _my_tasks(gov, EXEC, capsys)
    view = _task(data, task_id)
    _print_view("不可推导", view)
    _no_empty_strings(view)
    assert view["worktree_path"] is None and view["worktree_exists"] is False
    assert view["worktree_refusal"]["code"] == "LANE_REPO_UNDECLARED" and "zzz" in view["worktree_refusal"]["reason"]
    assert view["report_path"] == str(gov / "task_cards" / task_id / "RETURN.md")  # 报告落点与仓无关, 照常推导
    plan = _plan_go(data, tmp_path)
    _show("[不可推导] go planGo 原文: " + json.dumps(plan, ensure_ascii=False))
    assert plan["kind"] == "refused" and "LANE_REPO_UNDECLARED" in plan["message"] and "工作树不可推导" in plan["message"]
    assert not GATE_TEXT_RE.search(plan["message"].replace(str(tmp_path), "<tmp>"))


def test_item1_go_ts_reads_only_product_fields():
    """go.ts 静态: 无 <治理根>/card/<ID> 与 task_cards/<ID> 自拼, 无 lybra next, 经 my-tasks 取数(TS 夹具 f86-go-kickoff 同断言)。"""
    src = GO_TS.read_text(encoding="utf-8")
    assert '"card"' not in src and "task_cards" not in src
    assert not re.search(r"path\.join\(\s*workspaceRoot", src)
    assert "lybra next" not in src and "next --run" not in src
    assert '"my-tasks"' in src and "worktree_path" in src and "report_path" in src


# ===========================================================================
# 件② 章程指向更正
# ===========================================================================

HARD_RULES_LINE_SUFFIX = "。修改该来源 → 章程与派审注入同步跟随。"


def test_item2_charters_point_at_declared_commands_manual():
    """三份母本 grep ADVISOR-COMMANDS 零命中; 「单一真相源」行 = 占位 {{hard_rules_source}}(AIPOS-F89 件② M17: 硬规矩来源读项目
    声明 project.json paths.hard_rules_source, 不再写死治理文档名) = 硬规矩提取器(hard_rules_extractor)生成的同一行形。"""
    line = "> **单一真相源**: {{hard_rules_source}}" + HARD_RULES_LINE_SUFFIX
    extractor_src = (REPO_ROOT / "tools" / "aipos_cli" / "hard_rules_extractor.py").read_text(encoding="utf-8")
    assert 'f"> **单一真相源**: {hard_rules_source_ref(gov_root)}' + HARD_RULES_LINE_SUFFIX + '"' in extractor_src
    for role in ROLES:
        text = (REPO_ROOT / "agents" / "roles" / role / "AGENTS.md").read_text(encoding="utf-8")
        assert "ADVISOR-COMMANDS" not in text and "COMMANDS.md" not in text, role
        assert text.count(line) == 1, role
    advisor = (REPO_ROOT / "agents" / "roles" / "advisor" / "AGENTS.md").read_text(encoding="utf-8")
    assert "项目命令手册(如有, 治理文档名由项目自定)中残留的手搓片段标注为**已退役**" in advisor


def test_item2_rendered_charters_have_no_residue(tmp_path, monkeypatch):
    """charter_render 渲染三份(lybra 形上下文)后: 无 ADVISOR-COMMANDS、无未替换占位; 「单一真相源」= 项目声明
    paths.hard_rules_source § 0.5(声明了)/ 母本自带(未声明, AIPOS-F89 件② M17)。"""
    from tools.aipos_cli.hard_rules_extractor import UNDECLARED_REF

    gov = _charter_gov(tmp_path, monkeypatch, shape="lybra")
    prefixes = {"executor": "exec", "auditor": "audit", "advisor": "advisor"}
    decl = json.loads((gov / "project.json").read_text(encoding="utf-8"))
    for declared in (False, True):
        if declared:
            decl["paths"]["hard_rules_source"] = "governance/COMMANDS.md"
            (gov / "project.json").write_text(json.dumps(decl), encoding="utf-8")
        for role in ROLES:
            harness = tmp_path / "pi" / f"lybra-{role}-{int(declared)}"
            harness.mkdir(parents=True)
            ctx = charter_render_context(gov, identity=_identity(harness, role, f"{prefixes[role]}.lybra.hostl", "lybra"),
                                         product_commit="deadbeef")
            rendered = render_charter((REPO_ROOT / "agents" / "roles" / role / "AGENTS.md").read_text(encoding="utf-8"), ctx)
            hits = [ln for ln in rendered.splitlines() if "单一真相源" in ln]
            _show(f"[{role} 渲染后 单一真相源 行 declared={declared}] " + " | ".join(hits))
            assert "ADVISOR-COMMANDS" not in rendered and "{{" not in rendered
            expected = "governance/COMMANDS.md § 0.5" if declared else UNDECLARED_REF
            assert f"> **单一真相源**: {expected}{HARD_RULES_LINE_SUFFIX}" in rendered
            if not declared:
                assert "COMMANDS.md" not in rendered


# ===========================================================================
# 件③ 残留更正
# ===========================================================================

RETIRED_PATTERNS = (r"\blybra on\b", r"(?<![\w.])/lybra\b")


def test_item3_minimum_bootable_set_describes_zero_gate_path():
    decl = json.loads((REPO_ROOT / "schema" / "distribution.schema.json").read_text(encoding="utf-8"))
    mbs = decl["minimum_bootable_set"]
    texts = [mbs["description"]] + [str(item.get("description") or "") for item in mbs["items"]]
    for text in texts:
        for pat in RETIRED_PATTERNS:
            assert not re.search(pat, text), (pat, text)
    desc = mbs["description"]
    _show("[minimum_bootable_set.description] " + desc)
    assert desc.index("lybra roles enroll") < desc.index("lybra sync") < desc.index("/go")
    # /go 由分发声明的扩展提供(斜杠命令 ∈ 分发声明)
    assert any(str(d.get("source", {}).get("path", "")).endswith("extensions/go.ts") for d in decl["distributions"])
    lybra_bin = next(i for i in mbs["items"] if i["name"] == "connection.json#lybra_bin")
    _show("[connection.json#lybra_bin.description] " + lybra_bin["description"])
    assert "/go" in lybra_bin["description"] and "my-tasks" in lybra_bin["description"]


def test_item3_fixtures_registered_in_runall():
    """AIPOS-F91: f57 TS 夹具(测 lybra-loop/loop-context 引导)随 lybra-loop 扩展退役删除, 其 bin 解析断言随删;
    仍在用的 go.ts 夹具迁至 tests/ts/, run-all 位置读唯一声明 RUNALL_RELATIVE_PATH。"""
    from tools.aipos_cli.board_adapter import RUNALL_RELATIVE_PATH

    run_all = (REPO_ROOT / RUNALL_RELATIVE_PATH).read_text(encoding="utf-8")
    for name in ("tests/ts/f86-go-kickoff.test.ts", "tests/test_aipos_f86_workstation_kickoff.py"):
        assert name in run_all, name
