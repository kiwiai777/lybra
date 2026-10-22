"""AIPOS-F210 — 交付主体渲染单源(F208 P0-2, gap #218): 凡点名「分支 / 候选 / 产出清单」的文字只经
next_resolver.card_report_contract + card_workstation_view 产出(交付主体三态唯一判定 next_resolver.card_delivery_subject)。

靶场 = 临时治理根(AIPOS-F78 `_make_gov` 骨架: lybra 形 + chris 形人肉期落点声明)+ 临时产品仓; 三形态卡自造:
code 卡(卡分支)/ validation 候选卡(固定候选 = 来源卡分支上的提交)/ 治理根 docs 卡(lane.repo = governance_root)。
不读真实凭据、不碰真实治理根、不对真实门写、不起 pi。

件① 卡正文工作纪律段(machine_zone)三态渲染, 与开工面(card_workstation_view, 即 /go 与 lybra kickoff 同读)同句。
件② 审计取证锚点合一(audit_derivation.render_forensic_anchor / forensic_anchor_ref): 派生审计卡正文、governance_refs、手动派审三处同读。
件③ 审计报告必填句(report_frontmatter_clause)、交回 / 裁决骨架(board_adapter._create_return_skeleton → record_writer)
    全部 = card_report_contract 三态(给值或占位)。
件④(棘轮)产品代码与模板中拼「card/<…>」/「基线...卡分支」、在文字里点名 card_branch_name(...)、渲染面绕过 card_report_contract
    直调 report_frontmatter_contract(branch_task_id=...) 的式样只减不增(基线 tests/f210_branch_wording_baseline.jsonl, 一行一条带理由)。
"""
from __future__ import annotations

import ast
import hashlib
import json
import re
import sys
from collections import Counter
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "tests"))

from ratchet_baseline import read_entries  # noqa: E402
from tools.aipos_cli.runall_discovery import repo_files  # noqa: E402
from tools.aipos_cli.workspace_config import default_test_contract, discover_test_files  # noqa: E402

import test_aipos_f78_engine_agnostic as f78  # noqa: E402  — 靶场骨架唯一来源(禁第二份)

BASELINE_PATH = Path(__file__).resolve().parent / "f210_branch_wording_baseline.jsonl"
CODE, SRC, VAL, DOC = "PROBE-F210-K1", "PROBE-F210-S1", "PROBE-F210-V1", "PROBE-F210-D1"


def _show(text: str) -> None:
    sys.__stdout__.write(text + "\n")
    sys.__stdout__.flush()


@pytest.fixture(params=["lybra", "chris"])
def rig(request, tmp_path, monkeypatch):
    """三形态卡: code 卡 / validation 固定候选卡(来源卡 SRC 卡分支上的提交)/ 只写治理根 docs 卡; 治理根两种落点声明形。"""
    from tools.schema_loader import clear_cache

    clear_cache()
    gov = f78._make_gov(tmp_path, monkeypatch, shape=request.param)
    code_repo = tmp_path / f"product-{request.param}"
    sha, tree = f78._branch_with_commit(code_repo, SRC, rel="app/feature.txt")
    f78._card(gov, SRC, "completed")
    f78._card(gov, CODE, "claimed")
    f78._card(gov, VAL, "claimed", task_mode="validation",
              extra={"validation_source_task": SRC, "validation_candidate": sha, "output_target": f"task_cards/{VAL}/artifacts/"})
    f78._card(gov, DOC, "claimed", task_mode="docs",
              extra={"output_target": f"task_cards/{DOC}/artifacts/",
                     "lane": {"repo": "governance_root", "paths": [f"task_cards/{DOC}/artifacts/"], "roles": ["executor"]}})
    return {"gov": gov, "code_repo": code_repo, "shape": request.param, "candidate": sha, "candidate_tree": tree}


def _fm(gov: Path, task_id: str) -> dict:
    from tools.aipos_cli import next_resolver as nr

    path, _q = nr._find_task_in_queue(gov, task_id)
    return {**nr._read_frontmatter(path), "task_id": task_id}


def _discipline_lines(text: str) -> dict[str, str]:
    out = {}
    for line in text.splitlines():
        m = re.match(r"^- \*\*(.+?)\*\*: (.*)$", line)
        if m:
            out[m.group(1)] = m.group(2)
    return out


def _derive(gov: Path, task_id: str) -> dict:
    from tools.aipos_cli.audit_derivation import build_derived_audit_task

    return build_derived_audit_task(source_task_id=task_id, source_metadata=_fm(gov, task_id), source_path=f"x/{task_id.lower()}.md",
                                    return_record_ref="return_fixture", artifact_refs=[], repo_root=gov)


def _manual_dispatch(gov: Path, task_id: str) -> dict:
    from tools.aipos_cli.board_adapter import _build_audit_dispatch_preview

    res = _build_audit_dispatch_preview(source_task_id=task_id, source_path=None, actor=f78.DRIVER, agent_instance=f78.DRIVER,
                                        owner_policy_ref="pol_fixture", audit_task_id=f"{task_id}M", audit_task_title=None,
                                        audit_by=f78.AUDITOR, audit_agent_instance=f78.AUDITOR, dispatch_reason="f210 fixture",
                                        repo_root=gov, dry_run=True)
    return res["data"]


def _skeleton(gov: Path, task_id: str, fm: dict | None = None) -> str:
    from tools.aipos_cli.board_adapter import _create_return_skeleton
    from tools.aipos_cli.next_resolver import card_report_path

    wrote = _create_return_skeleton(gov, task_id, card_frontmatter=fm)
    assert wrote and wrote["wrote"], wrote
    return card_report_path(gov, task_id, fm).read_text(encoding="utf-8")


def _audit_card(gov: Path, task_id: str) -> tuple[str, dict]:
    """把派生审计卡落进靶场队列(认领态), 供审计卡骨架 / 开工面读。"""
    spec = _derive(gov, task_id)
    meta = {**spec["metadata"], "status": "claimed", "claimed_by": f78.AUDITOR}
    f78._write(gov / "5_tasks" / "queue" / "claimed" / f"{spec['audit_task_id'].lower()}.md", f78._fm(meta, spec["body"]))
    return spec["audit_task_id"], meta


# ===========================================================================
# 件①②③ 三形态: 卡正文 / 派生审计卡锚点 / 手动派审锚点 / 报告必填句 / 交回与裁决骨架 —— 与开工面同源
# ===========================================================================

def test_item1_2_3_three_subject_forms_render_consistently(rig):
    from tools.aipos_cli.audit_derivation import forensic_anchor_ref, report_frontmatter_clause
    from tools.aipos_cli.machine_zone import derive_machine_zone_纪律段
    from tools.aipos_cli.next_resolver import card_report_contract, card_workstation_view, render_report_frontmatter_clause

    gov, sha = rig["gov"], rig["candidate"]
    expect_kind = {CODE: "branch", VAL: "candidate", DOC: "governance_outputs"}
    for task_id, kind in expect_kind.items():
        fm = _fm(gov, task_id)
        view = card_workstation_view(gov, task_id, fm)
        assert view["delivery_subject"]["kind"] == kind, view
        discipline = derive_machine_zone_纪律段(task_id, fm, governance_root=gov)
        spec = _derive(gov, task_id)
        body_anchor = [ln for ln in spec["body"].splitlines() if ln.startswith("- **禁 checkout") or ln.startswith("- **交付主体")]
        ref_anchor = [r for r in spec["metadata"]["governance_refs"] if "取证锚点" in str(r)]
        manual = _manual_dispatch(gov, task_id)
        manual_ref = [r for r in manual["audit_task_metadata"]["governance_refs"] if "取证锚点" in str(r)]
        manual_body_anchor = [ln for ln in manual["audit_task_markdown"].splitlines()
                              if ln.startswith("- **禁 checkout") or ln.startswith("- **交付主体")]
        clause_line = next(ln for ln in spec["body"].splitlines() if ln.startswith("- **报告落位**"))
        _show(f"==== [{rig['shape']}] {task_id} task_mode={fm['task_mode']} 交付主体={kind} ====\n"
              f"[件① 卡正文工作纪律]\n{discipline}\n"
              f"[开工面 worktree_required={view['worktree_required']} branch={view['branch']}] "
              f"worktree_refusal={(view.get('worktree_refusal') or {}).get('reason')}\n"
              f"[件② 派生审计卡正文锚点] {body_anchor}\n[件② 派生审计卡 governance_refs 锚点] {ref_anchor}\n"
              f"[件② 手动派审 governance_refs 锚点] {manual_ref}\n[件② 手动派审正文锚点] {manual_body_anchor}\n"
              f"[件③ 派生审计卡报告落位句] {clause_line}")
        lines = _discipline_lines(discipline)
        # 件②: 三处同一渲染(只差审计卡 ID 决定的报告落点)
        assert len(body_anchor) == 1 and body_anchor == manual_body_anchor, (body_anchor, manual_body_anchor)
        assert len(ref_anchor) == 1 and ref_anchor[0] == forensic_anchor_ref(gov, task_id, fm, spec["audit_task_id"])
        assert manual_ref == [forensic_anchor_ref(gov, task_id, fm, f"{task_id}M")]
        assert ref_anchor[0].split(" | 报告落点=")[0] == manual_ref[0].split(" | 报告落点=")[0]
        # 件③: 审计报告必填句 = card_report_contract(审计卡跟随被审卡)
        audit_fm = {"task_id": spec["audit_task_id"], "task_mode": "audit", "reviewed_task_id": task_id}
        clause = report_frontmatter_clause(task_id, gov, spec["audit_task_id"])
        assert clause == render_report_frontmatter_clause(card_report_contract(gov, spec["audit_task_id"], audit_fm)) and clause in clause_line
        own_branch = f"card/{task_id}"
        if kind == "branch":
            assert lines["分支"].startswith(f"`{own_branch}`") and lines["工作起点"] == "从当前 main 拉取"
            assert view["branch"] == own_branch and view["worktree_required"] is True
            assert body_anchor == [f"- **禁 checkout 卡分支**: 用 `git diff main...{own_branch}` 取证(不切换工作区)"]
            assert f"禁checkout卡分支(git diff main...{own_branch})" in ref_anchor[0]
            assert f"被审分支 {own_branch} tip" in clause
            continue
        # 非卡分支态: 任何面都不再出现本卡 card/<ID>、三点 diff
        reason = view["worktree_refusal"]["reason"]
        for label, text in (("卡正文", discipline), ("审计卡正文", spec["body"]), ("governance_refs", json.dumps(spec["metadata"]["governance_refs"],
                            ensure_ascii=False)), ("手动派审", manual["audit_task_markdown"]), ("必填句", clause)):
            assert own_branch not in text and "git diff" not in text, (label, text)
        assert lines["分支"] == reason and view["branch"] is None and view["worktree_required"] is False  # 与开工稿 no_worktree_reason 同句
        if kind == "candidate":
            assert sha in lines["工作起点"] and f"来源卡 {SRC}" in lines["工作起点"]
            assert sha in body_anchor[0] and "src-verify" in body_anchor[0] and sha in ref_anchor[0]
            assert f"= {sha}" in clause and "固定候选" in clause  # 审计报告 commit_sha 实值 = 候选
            assert f"= {sha}" in lines["报告必填"] and f"= card/{SRC}" in lines["报告必填"]  # 交回三键实值(branch = 来源卡分支名, 说明出处)
        else:
            assert "outputs" in lines["工作起点"] and "outputs" in lines["报告必填"] and "commit_sha" not in lines["报告必填"]
            assert "outputs_digest" in body_anchor[0] and "产品仓=不涉及(被审卡只写治理根)" in ref_anchor[0]
            assert "`outputs_digest`" in clause and "commit_sha" not in clause


def test_item3_return_and_verdict_skeletons_follow_report_contract(rig):
    from tools.aipos_cli.frontmatter import parse_markdown_frontmatter
    from tools.aipos_cli.next_resolver import card_report_contract

    gov, sha, tree = rig["gov"], rig["candidate"], rig["candidate_tree"]
    for task_id in (CODE, VAL, DOC):
        fm = _fm(gov, task_id)
        audit_id, audit_fm = _audit_card(gov, task_id)
        for card_id, card_fm in ((task_id, fm), (audit_id, audit_fm)):
            text = _skeleton(gov, card_id, card_fm)
            head, _body, _w = parse_markdown_frontmatter(text)
            contract = card_report_contract(gov, card_id, card_fm)
            _show(f"[件③ {rig['shape']} 骨架 {card_id}] frontmatter = {json.dumps(head, ensure_ascii=False)}")
            assert list(head) == [e["key"] for e in contract], (head, contract)
            assert all(str(v).startswith("(待填写") for v in head.values()), head  # 仍是占位(完成判据不变)
            if task_id == VAL:
                assert f"card/{VAL}" not in text and f"照抄 {sha}" in head["commit_sha"]
                assert card_id != task_id or f"照抄 {tree}" in head["tree_hash"]  # 交回三键实值; 审计报告只 commit_sha
            elif task_id == DOC:
                assert "commit_sha" not in head and f"card/{DOC}" not in text
            else:
                assert f"card/{CODE}" in head["commit_sha"]


def test_item1_live_shapes_f208_and_validation_audit_preview(rig):
    """线上形态复刻(只读夹具版): F208 形(docs + lane.repo governance_root)卡正文不写建分支; LANTU-VALID1R2 形(验证卡的审计卡)
    不要求 diff 不存在的卡分支; HBLX-24BV 形(验证卡审计报告骨架)不叫填被审卡分支 tip。"""
    from tools.aipos_cli.machine_zone import derive_machine_zone_纪律段

    gov = rig["gov"]
    doc = derive_machine_zone_纪律段(DOC, _fm(gov, DOC), governance_root=gov)
    assert "从当前 main 拉取" not in doc and f"card/{DOC}" not in doc
    audit_id, audit_fm = _audit_card(gov, VAL)
    body = (gov / "5_tasks" / "queue" / "claimed" / f"{audit_id.lower()}.md").read_text(encoding="utf-8")
    assert f"main...card/{VAL}" not in body
    skeleton = _skeleton(gov, audit_id, audit_fm)
    assert f"git rev-parse card/{VAL}" not in skeleton and f"被审分支 card/{VAL}" not in skeleton


def test_item2_governance_anchor_and_clause_share_bound_digest(rig, monkeypatch):
    """治理根产出清单态: 审计取证锚点与报告必填句给同一个交回绑定指纹(card_delivery_subject 一次取值)。"""
    from tools.aipos_cli import next_resolver as nr

    digest = "d" * 64
    monkeypatch.setattr(nr, "governance_outputs_return_binding",
                        lambda root, tid: {"kind": "governance_outputs", "outputs_digest": digest} if tid == DOC else None)
    spec = _derive(rig["gov"], DOC)
    anchor = next(ln for ln in spec["body"].splitlines() if ln.startswith("- **交付主体"))
    clause = next(ln for ln in spec["body"].splitlines() if ln.startswith("- **报告落位**"))
    _show(f"[件② 治理根态绑定指纹] {anchor}\n{clause}")
    assert f"outputs_digest `{digest}`" in anchor and f"`outputs_digest`(= {digest};" in clause


def test_item1_undecidable_subject_named_not_guessed(rig, monkeypatch):
    """交付主体判定失败 = 卡正文如实写拒因(发卡前须修正), 不回落成卡分支措辞。"""
    from tools.aipos_cli import next_resolver as nr
    from tools.aipos_cli.machine_zone import derive_machine_zone_纪律段
    from tools.schema_loader import SchemaLoadError

    def boom(*_a, **_k):
        raise SchemaLoadError("enums.schema.json enums.task_mode(fixture 断开)")

    monkeypatch.setattr(nr, "card_delivery_subject", boom)
    view = nr.card_workstation_view(rig["gov"], CODE, _fm(rig["gov"], CODE))
    assert view["delivery_subject"] is None and view["delivery_subject_refusal"]["code"] == "DELIVERY_SUBJECT_UNRESOLVED"
    monkeypatch.setattr(nr, "card_report_contract", lambda *a, **k: [])
    text = derive_machine_zone_纪律段(CODE, _fm(rig["gov"], CODE), governance_root=rig["gov"])
    _show(f"[件① 判定失败] {text.splitlines()[2]}")
    assert "交付主体不可判定(DELIVERY_SUBJECT_UNRESOLVED" in text and f"card/{CODE}" not in text


def test_declaration_single_implementation_and_renderers_exist():
    from tools.aipos_cli import audit_derivation, machine_zone, next_resolver

    decl = json.loads((REPO_ROOT / "schema" / "enums.schema.json").read_text(encoding="utf-8"))["enums"]["task_mode"]["delivery_subject"]
    assert decl["kinds"] == list(next_resolver.DELIVERY_SUBJECT_KINDS)
    modules = {"next_resolver": next_resolver, "machine_zone": machine_zone, "audit_derivation": audit_derivation}
    for ref in [decl["single_implementation"], *decl["renderers"]]:
        mod, func = ref.rsplit("/", 1)[-1].split(".", 1)
        assert callable(getattr(modules[mod], func, None)), ref
    # 渲染面不再自判 / 自拼: machine_zone 不读候选卡面、不替换分支式样; 审计派生与手动派审不再直调分支名
    mz = (REPO_ROOT / "tools/aipos_cli/machine_zone.py").read_text(encoding="utf-8")
    assert "card_candidate_subject" not in mz and 'replace("{task_id}"' not in mz and "card_workstation_view(" in mz
    ad = (REPO_ROOT / "tools/aipos_cli/audit_derivation.py").read_text(encoding="utf-8")
    ba = (REPO_ROOT / "tools/aipos_cli/board_adapter.py").read_text(encoding="utf-8")
    assert "card_branch_name" not in ad and "取证锚点(AIPOS-A1 大项C): 产品仓=" not in ba
    assert ad.count("\\u2605取证锚点(AIPOS-A1 大项C)") == 1  # 锚点一行字面全产品只此一处(forensic_anchor_ref)


# ===========================================================================
# 件④ 棘轮: 分支措辞拼接只减不增
# ===========================================================================

_CARD_LITERAL = re.compile(r"card/[{<]|\.\.\.card/")
_TEMPLATE_GLOBS = (("schema/", ".json"), ("agents/", ".md"), ("docs/", ".md"))
ALLOWED_THREE_DOT = {("tools/aipos_cli/audit_derivation.py", "render_forensic_anchor")}  # 取证锚点唯一渲染
CONTRACT_HOME = "tools/aipos_cli/next_resolver.py"  # 报告契约函数所在(report_frontmatter_contract 带 branch_task_id 的合法调用方)


def fingerprint(text: str) -> str:
    return hashlib.sha1(text.strip().encode("utf-8")).hexdigest()[:12]


def scoped_files(repo_root: Path = REPO_ROOT) -> tuple[list[str], list[str]]:
    """扫描面 = 产品仓文件清单: .py 产品源码(排除 run-all 自动发现的测试文件、conftest.py、路径含 tests/ 段, 与 F209 棘轮同一口径)
    + 模板(schema/*.json、agents/**/*.md、docs/**/*.md)。"""
    files = repo_files(repo_root)
    tests = set(discover_test_files(files, default_test_contract()))
    code, templates = [], []
    for rel in files:
        parts = rel.split("/")
        if rel.endswith(".py"):
            if rel in tests or parts[-1] == "conftest.py" or "tests" in parts[:-1]:
                continue
            code.append(rel)
        elif any(rel.startswith(prefix) and rel.endswith(ext) for prefix, ext in _TEMPLATE_GLOBS):
            templates.append(rel)
    return sorted(code), sorted(templates)


def _call_name(func: ast.AST) -> str:
    return func.id if isinstance(func, ast.Name) else func.attr if isinstance(func, ast.Attribute) else ""


def _docstring_ids(tree: ast.Module) -> set[int]:
    ids = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) and node.body:
            first = node.body[0]
            if isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant):
                ids.add(id(first.value))
    return ids


def _enclosing(tree: ast.Module) -> dict[int, str]:
    out: dict[int, str] = {}

    def visit(node: ast.AST, name: str) -> None:
        for child in ast.iter_child_nodes(node):
            child_name = child.name if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) else name
            if hasattr(child, "lineno"):
                out.setdefault(child.lineno, child_name)
            visit(child, child_name)

    visit(tree, "<module>")
    return out


def _hits_in_tree(tree: ast.Module) -> list[tuple[ast.AST, str]]:
    """五类式样(docstring 不计):
    card_literal       字面量含 card/{ 、card/< 或 ...card/(手拼卡分支名 / 三点区间);
    card_prefix_concat f-string 以 card/ 接占位(手拼卡分支名);
    three_dot_concat   f-string `{x}...{y}`(手拼「基线...卡分支」区间; 取证锚点唯一渲染 render_forensic_anchor 除外);
    branch_in_text     f-string 内直接点名 card_branch_name(...)(措辞绕过交付主体判定);
    contract_bypass    report_frontmatter_contract(..., branch_task_id=...) 在 next_resolver 契约函数之外(渲染面绕过 card_report_contract)。"""
    docs = _docstring_ids(tree)
    hits: list[tuple[ast.AST, str]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str) and id(node) not in docs and _CARD_LITERAL.search(node.value):
            hits.append((node, "card_literal"))
        elif isinstance(node, ast.JoinedStr):
            vals = node.values
            for i, part in enumerate(vals):
                prev = vals[i - 1] if i > 0 else None
                nxt = vals[i + 1] if i + 1 < len(vals) else None
                if isinstance(part, ast.Constant) and isinstance(part.value, str):
                    if part.value.endswith("card/") and isinstance(nxt, ast.FormattedValue):
                        hits.append((node, "card_prefix_concat"))
                    if isinstance(nxt, ast.FormattedValue) and ((part.value == "..." and isinstance(prev, ast.FormattedValue))
                                                                 or re.search(r"[\w/-]\.\.\.$", part.value)):
                        hits.append((node, "three_dot_concat"))
                elif isinstance(part, ast.FormattedValue) and isinstance(part.value, ast.Call) and _call_name(part.value.func) == "card_branch_name":
                    hits.append((node, "branch_in_text"))
        elif isinstance(node, ast.Call) and _call_name(node.func) == "report_frontmatter_contract" \
                and any(k.arg == "branch_task_id" for k in node.keywords):
            hits.append((node, "contract_bypass"))
    return hits


def scan_source(rel: str, text: str) -> list[dict[str, str]]:
    tree = ast.parse(text)
    lines = text.splitlines()
    funcs = _enclosing(tree)
    seen: set[tuple[int, str]] = set()
    out: list[dict[str, str]] = []
    for node, kind in sorted(_hits_in_tree(tree), key=lambda h: (h[0].lineno, h[0].col_offset, h[1])):
        func = funcs.get(node.lineno, "<module>")
        if (node.lineno, kind) in seen or (kind == "three_dot_concat" and (rel, func) in ALLOWED_THREE_DOT) \
                or (kind == "contract_bypass" and rel == CONTRACT_HOME):
            continue
        seen.add((node.lineno, kind))
        line = lines[node.lineno - 1]
        out.append({"file": rel, "func": func, "kind": kind, "fp": fingerprint(line), "text": line.strip()[:160]})
    return out


def scan_template(rel: str, text: str) -> list[dict[str, str]]:
    """模板逐行: 命中片段前后各 24 字作指纹(长描述行别处改字不致误报新增)。"""
    out = []
    for line in text.splitlines():
        for m in _CARD_LITERAL.finditer(line):
            snippet = line[max(0, m.start() - 24): m.end() + 24]
            out.append({"file": rel, "func": "<template>", "kind": "card_literal", "fp": fingerprint(snippet), "text": snippet.strip()})
    return out


def scan_repo(repo_root: Path = REPO_ROOT) -> list[dict[str, str]]:
    code, templates = scoped_files(repo_root)
    hits: list[dict[str, str]] = []
    for rel in code:  # 解码 / 语法失败 = 抛(fail-closed: 扫描面内读不了不得当作「无命中」)
        hits.extend(scan_source(rel, (repo_root / rel).read_text(encoding="utf-8")))
    for rel in templates:
        hits.extend(scan_template(rel, (repo_root / rel).read_text(encoding="utf-8")))
    return hits


def _key(entry: dict[str, object]) -> tuple[str, str, str, str]:
    return (str(entry["file"]), str(entry["func"]), str(entry["kind"]), str(entry["fp"]))


def test_ratchet_baseline_well_formed():
    for entry in read_entries(BASELINE_PATH):
        assert set(entry) == {"file", "func", "kind", "fp", "text", "reason"}, entry
        assert str(entry["reason"]).strip(), entry


def test_ratchet_branch_wording_only_through_contract_and_view_baseline_only_shrinks():
    hits = scan_repo()
    baseline = read_entries(BASELINE_PATH)
    current, allowed = Counter(_key(h) for h in hits), Counter(_key(e) for e in baseline)
    new, stale = current - allowed, allowed - current
    code, templates = scoped_files()
    _show(f"[件④ 棘轮] 扫描面 {len(code)} 个产品源文件 + {len(templates)} 个模板; 分支措辞拼接式样命中 {sum(current.values())} 处; "
          f"基线 {len(baseline)} 条")
    for hit in hits:
        _show(f"  命中 [{hit['kind']}] {hit['file']}::{hit['func']} {hit['text']}")
    assert not new, ("新增分支措辞拼接(点名分支 / 候选 / 产出清单的文字改经 next_resolver.card_report_contract + card_workstation_view, "
                     "取证锚点经 audit_derivation.render_forensic_anchor): "
                     + "; ".join(f"[{h['kind']}] {h['file']}::{h['func']} {h['text']}" for h in hits if _key(h) in new))
    assert not stale, f"基线条目已不存在(已修 / 已删)→ 从 {BASELINE_PATH.name} 删除这些行(只减不增): {sorted(stale)}"


def test_ratchet_pattern_catches_known_shapes_and_spares_benign():
    bad = '''
def render(task_id, base, branch, reviewed):
    a = f"git diff {card_base_branch()}...{card_branch_name(task_id)}"
    b = f"分支 card/{task_id}"
    c = "填 card/<被审卡ID> tip"
    d = report_frontmatter_contract("verdict", branch_task_id=reviewed)
    return a, b, c, d
'''
    kinds = [(h["kind"], h["text"]) for h in scan_source("x.py", bad)]
    assert kinds == [("branch_in_text", 'a = f"git diff {card_base_branch()}...{card_branch_name(task_id)}"'),
                     ("three_dot_concat", 'a = f"git diff {card_base_branch()}...{card_branch_name(task_id)}"'),
                     ("card_prefix_concat", 'b = f"分支 card/{task_id}"'),
                     ("card_literal", 'c = "填 card/<被审卡ID> tip"'),
                     ("contract_bypass", 'd = report_frontmatter_contract("verdict", branch_task_id=reviewed)')], kinds
    clean = '''
def ok(root, task_id, fm):
    """文档里写 card/<ID> 与 main...card/<ID> 不计。"""
    view = card_workstation_view(root, task_id, fm)
    contract = card_report_contract(root, task_id, fm)
    generic = report_frontmatter_contract("return")
    return f"{view['branch']} ... {len(contract)}", generic
'''
    assert scan_source("y.py", clean) == []
    assert [h["kind"] for h in scan_template("t.md", "建 `card/<卡ID>`; diff main...card/X")] == ["card_literal", "card_literal"]


def test_f210_fixture_discovered_by_runall():
    from tools.aipos_cli.workspace_config import is_test_file

    assert is_test_file("tests/test_aipos_f210_delivery_subject_single_source.py", default_test_contract())
