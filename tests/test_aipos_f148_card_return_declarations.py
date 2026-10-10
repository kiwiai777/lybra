"""AIPOS-F148 — 卡与交回判据进声明(gap #123 / #112 / #114)验收夹具。

靶场 = pytest 临时目录下自造的治理根 + 产品仓(复用 F73D / F78 / F78C / F132 夹具, 禁第二份靶场); 不读写任何真实治理根。

件① 多 lane 必写 lane.repo: 项目声明了多个仓(repos.items ≥ 2)时, 卡稿缺 lane.repo 发卡拒(LANE_REPO_REQUIRED, 列仓名);
    单仓项目不变; 判据唯一实现 draft_writer.multi_repo_lane_refusal, 只在 F143 发卡核身份同一入口 publish_identity_refusals 判拒;
    起草 / 存量重生成不替卡写 repos.default。
件② 交回就绪判据声明化: transitions artifact_ingest.return.summary_source(缺省 =「一句话结论」节)+ 项目 project.json
    return_summary_source(写入口 `lybra project set-return-summary`); _check_return_artifact / 推导核交回步 / artifact ingest 同一解析
    next_resolver.extract_return_summary_text; Return 已交(必填 frontmatter 已填)而摘要来源都取不到 = artifact_invalid, loop exit 4 明示缺什么。
件③ 门侧授权须完整实例名: custom_roles.authorize_instance_class 要求合 roles.schema naming.template 的完整实例名
    (INSTANCE_NOT_CANONICAL); 裸角色名拒; 与提交身份的绑定沿用既有门链(MCP actor == agent_instance / 审计卡 claimed_by)。
"""
from __future__ import annotations

import contextlib
import io
import json
import re
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from test_aipos_f73d_loop_driver import DRIVER, EXEC, GateDouble, _claim_record, _policy, _write, init_governance_repo  # noqa: E402
from test_aipos_f78_engine_agnostic import _card  # noqa: E402
from test_aipos_f78c_card_repo import _dual_gov, _executor_commits_in_worktree, _single_gov  # noqa: E402
from test_aipos_f132_custom_role_gate import (  # noqa: E402,F401  — gov 夹具与 F132 靶场唯一来源
    AUDIT,
    CLAIM_ID,
    HBJ_AUDITOR,
    PROJECT,
    RETURN_ID,
    REVIEWED,
    SESSION_ID,
    SUBJECT,
    _reviewed_and_derived_audit,
    _verdict,
    gov,
)

TASK = "AIPOS-F148X"


def _show(label: str, payload) -> None:
    text = payload if isinstance(payload, str) else json.dumps(payload, ensure_ascii=False, default=str)
    print(f"[{label}] {text}")


def _cli(*argv: str) -> tuple[int, str, str]:
    from tools.aipos_cli.aipos_cli import main

    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        try:
            rc = main(list(argv))
        except SystemExit as exc:
            rc = exc.code if isinstance(exc.code, int) else 2
    return int(rc or 0), out.getvalue(), err.getvalue()


# ---------------------------------------------------------------------------
# 件① 多仓项目发卡必写 lane.repo
# ---------------------------------------------------------------------------

def _draft_meta(task_id: str, lane: dict | None) -> dict:
    meta = {"task_id": task_id, "title": f"{task_id} t", "project": "lybra", "assigned_to": EXEC, "context_bundle": "t",
            "task_mode": "code", "priority": "high", "status": "pending", "created_by": DRIVER, "needs_owner": False,
            "output_target": "tools/aipos_cli/, tests/", "artifact_policy": "formal_write"}
    if lane is not None:
        meta["lane"] = lane
    return meta


def _publish(gov_root: Path, task_id: str, lane: dict | None) -> tuple[dict, dict]:
    from tools.aipos_cli.draft_writer import create_draft, publish_draft

    created = create_draft(gov_root, _draft_meta(task_id, lane), "## Goal\n\n多仓发卡靶场。\n", dry_run=False)
    assert created["verdict"] != "BLOCK", created
    return created, publish_draft(gov_root, created["target_path"], dry_run=True)


def test_item1_dual_repo_publish_without_lane_repo_refused_lists_repo_names(tmp_path, monkeypatch):
    from tools.aipos_cli.frontmatter import parse_markdown_frontmatter

    gov_root, repos = _dual_gov(tmp_path, monkeypatch)
    # 卡稿无 lane(由 output_target 派生 paths): 起草不替卡写 repos.default, 只出声
    created, published = _publish(gov_root, "AIPOS-F148A", None)
    draft_meta, _body, _w = parse_markdown_frontmatter((gov_root / created["target_path"]).read_text(encoding="utf-8"))
    _show("件①·双仓起草(卡稿无 lane) 草稿 lane", draft_meta.get("lane"))
    assert "repo" not in (draft_meta.get("lane") or {}), draft_meta.get("lane")
    assert any(w.startswith("LANE_REPO_REQUIRED") for w in created["warnings"]), created["warnings"]
    refusals = [r for r in published["blocking_reasons"] if r.startswith("LANE_REPO_REQUIRED")]
    _show("件①·双仓发卡缺 lane.repo 拒因原文", refusals)
    assert published["verdict"] == "BLOCK" and len(refusals) == 1, published["blocking_reasons"]
    assert "['a', 'b']" in refusals[0] and "repos.default(a)" in refusals[0] and "lane.repo: <a | b>" in refusals[0]
    # 卡稿有 lane 但无 repo 同拒
    _created2, published2 = _publish(gov_root, "AIPOS-F148B", {"paths": ["tools/aipos_cli/"], "roles": ["executor"]})
    assert [r for r in published2["blocking_reasons"] if r.startswith("LANE_REPO_REQUIRED")], published2["blocking_reasons"]
    # 写了 lane.repo(仓名)= 通过本判据, 发卡不拒
    _created3, published3 = _publish(gov_root, "AIPOS-F148C", {"repo": "b", "paths": ["tools/aipos_cli/"], "roles": ["executor"]})
    _show("件①·双仓发卡 lane.repo=b", {"verdict": published3["verdict"], "blocking_reasons": published3["blocking_reasons"]})
    assert published3["verdict"] != "BLOCK", published3["blocking_reasons"]
    assert published3["validation"]["published_frontmatter"]["lane"]["repo"] == "b"


def test_item1_single_repo_publish_unchanged(tmp_path, monkeypatch):
    from tools.aipos_cli.draft_writer import multi_repo_lane_refusal

    single, repo = _single_gov(tmp_path, monkeypatch)
    _created, published = _publish(single, "AIPOS-F148S", None)
    _show("件①·单仓发卡(卡稿无 lane)", {"verdict": published["verdict"], "blocking_reasons": published["blocking_reasons"],
                                        "lane": published["validation"]["published_frontmatter"].get("lane")})
    assert published["verdict"] != "BLOCK", published["blocking_reasons"]
    assert published["validation"]["published_frontmatter"]["lane"]["repo"] == str(repo)  # 缺省派生 code_repo, 与 F78C 同
    # repos 段只 1 项 = 单仓, 不适用
    _write(single / "project.json", json.dumps({"project": "lybra", "repos": {"default": "only", "items": {"only": str(repo)}}}))
    assert multi_repo_lane_refusal(single, {"task_id": "X"}) == ""
    # 裸治理根(无 project.json)不适用
    bare = tmp_path / "bare"
    bare.mkdir()
    assert multi_repo_lane_refusal(bare, {"task_id": "X"}) == ""


def test_item1_regen_does_not_write_default_repo_for_multi_repo(tmp_path, monkeypatch, capsys):
    from tools.aipos_cli.draft_writer import multi_repo_lane_refusal

    gov_root, _repos = _dual_gov(tmp_path, monkeypatch)
    refusal = multi_repo_lane_refusal(gov_root, {"task_id": "X", "lane": {"paths": ["tools/"]}})
    assert refusal.startswith("LANE_REPO_REQUIRED")
    assert multi_repo_lane_refusal(gov_root, {"task_id": "X", "lane": {"repo": "a"}}) == ""
    # 仓清单自身不一致 = fail-closed 拒因原文
    _write(gov_root / "project.json", json.dumps({"project": "lybra", "repos": {"default": "zz", "items": {"a": "/x", "b": "/y"}}}))
    assert multi_repo_lane_refusal(gov_root, {"task_id": "X"}).startswith("REPOS_CONFLICT")


# ---------------------------------------------------------------------------
# 件② 交回就绪判据声明化
# ---------------------------------------------------------------------------

def _claimed_with_commit(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Path, str, str]:
    from tools.aipos_cli.next_resolver import _ensure_worktree

    gov_root, _repo = _single_gov(tmp_path, monkeypatch)
    _card(gov_root, TASK, "claimed")
    _claim_record(gov_root, TASK, EXEC)
    wt = _ensure_worktree(gov_root, TASK)
    assert wt["ok"], wt
    sha, tree = _executor_commits_in_worktree(Path(wt["worktree_path"]), TASK)
    return gov_root, sha, tree


def _return_doc(sha: str, tree: str, *, summary_fm: str | None = None, section: bool = False) -> str:
    lines = ["---", f"commit_sha: {sha}", f"tree_hash: {tree}", f"branch: card/{TASK}"]
    if summary_fm is not None:
        lines.append(f"result_summary: {summary_fm}")
    lines += ["---", "", f"# Return — {TASK}", ""]
    if section:
        lines += ["## 一句话结论", "完成本卡(模板节)。", ""]
    lines += ["## 改动", "- tools/aipos_cli/x.py", ""]
    return "\n".join(lines)


def test_item2_default_template_and_skeleton_behavior_unchanged(tmp_path, monkeypatch):
    from tools.aipos_cli.next_resolver import extract_return_summary_text
    from tools.aipos_cli.record_writer import build_return_skeleton_markdown
    from tools.aipos_cli.workspace_config import return_summary_source, return_summary_source_default

    gov_root, sha, tree = _claimed_with_commit(tmp_path, monkeypatch)
    source = return_summary_source(gov_root)
    _show("件②·未声明项目的摘要来源", source)
    assert source == {**return_summary_source_default(), "declared": False}
    assert source["frontmatter_keys"] == [] and source["section_markers"] == ["一句话结论", "## 一句话"]
    # lybra 现行模板 / 人肉期常见写法: 与 F148 前写死判据逐字等价
    samples = {
        "template": "# RETURN\n\n## 一句话结论\n**完成** 三件并入。\n\n## 改动\n- x\n",
        "short_heading": "# R\n\n## 一句话\n完成。\n",
        "skeleton": build_return_skeleton_markdown(TASK),
        "none": "# R\n\n## 改动\n- x\n",
    }
    got = {k: (extract_return_summary_text(v), extract_return_summary_text(v, source)) for k, v in samples.items()}
    _show("件②·缺省解析(无 source / 项目 source)", got)
    assert got["template"] == ("完成** 三件并入", "完成** 三件并入")
    assert got["short_heading"] == ("完成", "完成")
    assert got["skeleton"] == (None, None) and got["none"] == (None, None)
    # 认领骨架(必填 frontmatter 仍占位)= 执行体还在写: 照旧等待(不判 artifact_invalid)
    from tools.aipos_cli.next_resolver import _check_return_artifact, derive_next_step

    _write(gov_root / "task_cards" / TASK / "RETURN.md", build_return_skeleton_markdown(TASK))
    step = derive_next_step(TASK, gov_root)
    _show("件②·骨架推导", {k: step.get(k) for k in ("derivable", "action", "missing_records")})
    assert _check_return_artifact(gov_root, TASK) is False
    assert (step.get("action") or {}).get("type") != "artifact_invalid"
    # 模板节写实 = 就绪, result_summary 取节内容
    _write(gov_root / "task_cards" / TASK / "RETURN.md", _return_doc(sha, tree, section=True))
    step = derive_next_step(TASK, gov_root)
    assert _check_return_artifact(gov_root, TASK) is True and step["derivable"], step
    assert "完成本卡(模板节)" in json.dumps(step, ensure_ascii=False)


def test_item2_declared_result_summary_frontmatter_is_ready(tmp_path, monkeypatch):
    from tools.aipos_cli.artifact_ingest import validate_task_artifact
    from tools.aipos_cli.next_resolver import _check_return_artifact, derive_next_step

    gov_root, sha, tree = _claimed_with_commit(tmp_path, monkeypatch)
    _write(gov_root / "task_cards" / TASK / "RETURN.md", _return_doc(sha, tree, summary_fm="人肉期格式交回, 三件完成"))
    # 未声明: frontmatter result_summary 不算(缺省行为不变), 且已交 → artifact_invalid 明示缺什么(不空等)
    assert _check_return_artifact(gov_root, TASK) is False
    before = derive_next_step(TASK, gov_root)
    _show("件②·未声明 result_summary 时推导", {k: before.get(k) for k in ("derivable", "action", "missing_records")})
    assert before["action"]["type"] == "artifact_invalid" and "Return 摘要缺" in before["missing_records"][0]
    # 经产品写入口声明(预演零写入 → --confirm 写入)
    pj = gov_root / "project.json"
    original = pj.read_text(encoding="utf-8")
    rc, out, err = _cli("project", "set-return-summary", "--workspace-root", str(gov_root), "--frontmatter-key", "result_summary")
    _show(f"件②·set-return-summary 预演 rc={rc}", out + err)
    assert rc == 0 and "预览" in out and '"return_summary_source"' in out and pj.read_text(encoding="utf-8") == original
    rc, out, err = _cli("project", "set-return-summary", "--workspace-root", str(gov_root), "--frontmatter-key", "result_summary", "--confirm")
    _show(f"件②·set-return-summary 确认 rc={rc}", out + err)
    assert rc == 0 and "已写入" in out
    assert json.loads(pj.read_text(encoding="utf-8"))["return_summary_source"] == {"frontmatter_keys": ["result_summary"]}
    # 声明后: 就绪判据 / artifact ingest / 推导核交回步同认
    assert _check_return_artifact(gov_root, TASK) is True
    chk = validate_task_artifact(TASK, gov_root)
    _show("件②·声明后 artifact ingest 校验", {k: chk.get(k) for k in ("ok", "category", "reasons")})
    assert chk["ok"] and chk["category"] == "OK", chk
    step = derive_next_step(TASK, gov_root)
    _show("件②·声明后推导", {k: step.get(k) for k in ("derivable", "verb", "command")})
    assert step["derivable"] and "人肉期格式交回, 三件完成" in json.dumps(step, ensure_ascii=False)
    # 模板节仍认(section_markers 未覆盖 = 取缺省)
    _write(gov_root / "task_cards" / TASK / "RETURN.md", _return_doc(sha, tree, section=True))
    assert _check_return_artifact(gov_root, TASK) is True


def test_item2_both_missing_loop_states_what_is_missing(tmp_path, monkeypatch):
    from tools.aipos_cli.artifact_ingest import validate_task_artifact
    from tools.aipos_cli.loop_driver import run_loop
    from tools.aipos_cli.workspace_config import set_return_summary_source

    gov_root, sha, tree = _claimed_with_commit(tmp_path, monkeypatch)
    _policy(gov_root)
    init_governance_repo(gov_root)
    set_return_summary_source(gov_root, frontmatter_keys=["result_summary"], dry_run=False)
    _write(gov_root / "task_cards" / TASK / "RETURN.md", _return_doc(sha, tree, summary_fm="(待填写 一句话)"))
    buf = io.StringIO()
    gate = GateDouble(gov_root)
    res = run_loop(TASK, gov_root, actor=DRIVER, out=buf, execute=gate, interval=0.02, max_wait=3, max_steps=5)
    _show("件②·都缺时 loop 输出原文", buf.getvalue())
    assert res.exit_code == 4 and res.reason == "artifact_invalid" and gate.calls == [], (res, gate.calls)
    text = buf.getvalue()
    assert "Return 摘要缺" in text and "frontmatter 键 ['result_summary'] 均缺或为占位" in text
    assert "['一句话结论', '## 一句话']" in text and "补法" in text and "set-return-summary" in text
    # artifact ingest 同一文案(同一解析)
    chk = validate_task_artifact(TASK, gov_root)
    _show("件②·都缺时 ingest 拒因", chk["reasons"])
    assert chk["category"] == "INGEST_SUMMARY_MISSING" and "frontmatter 键 ['result_summary'] 均缺或为占位" in chk["reasons"][0]


def test_item2_invalid_declaration_fail_closed(tmp_path, monkeypatch):
    from tools.aipos_cli.next_resolver import derive_next_step
    from tools.aipos_cli.workspace_config import ReturnSummarySourceError, return_summary_source

    gov_root, sha, tree = _claimed_with_commit(tmp_path, monkeypatch)
    rc, _out, err = _cli("project", "set-return-summary", "--workspace-root", str(gov_root))
    assert rc == 1 and "RETURN_SUMMARY_SOURCE_INVALID" in err
    data = json.loads((gov_root / "project.json").read_text(encoding="utf-8"))
    for bad in ({"frontmatter_keys": "result_summary"}, {"unknown": []}, {"frontmatter_keys": [], "section_markers": []}, []):
        with pytest.raises(ReturnSummarySourceError):
            return_summary_source(gov_root, project={**data, "return_summary_source": bad})
    _write(gov_root / "project.json", json.dumps({**data, "return_summary_source": {"frontmatter_keys": ["a\nb"]}}))
    _write(gov_root / "task_cards" / TASK / "RETURN.md", _return_doc(sha, tree, section=True))
    step = derive_next_step(TASK, gov_root)
    _show("件②·声明不合时推导", {k: step.get(k) for k in ("action", "missing_records")})
    assert step["action"]["type"] == "artifact_invalid" and "RETURN_SUMMARY_SOURCE_INVALID" in step["missing_records"][0]


# ---------------------------------------------------------------------------
# 件③ 门侧授权须完整实例名
# ---------------------------------------------------------------------------

def test_item3_bare_role_name_verdict_refused_full_instance_passes(gov: Path):
    from tools.aipos_cli.custom_roles import authorize_instance_class

    # 裸角色名 auditor 作审计卡认领者(#114 形): 角色名解析得 auditor 类, 但门侧授权拒
    _reviewed_and_derived_audit(gov, "auditor")
    bare = _verdict(gov, "auditor")
    _show("件③·裸角色名 auditor 提交裁决", {k: bare.get(k) for k in ("verdict", "blocking_reasons")})
    assert bare["verdict"] == "BLOCK"
    reason = " ".join(bare["blocking_reasons"])
    assert reason.startswith("ROLE_VIOLATION") and "INSTANCE_NOT_CANONICAL" in reason and "裸角色名" in reason
    assert "{prefix}.{project}.{host}" in reason
    assert bare["data"]["role_resolution"]["reject_code"] == "INSTANCE_NOT_CANONICAL"
    assert not list((gov / "5_tasks/records/audit_verdicts").rglob("*.md"))  # 零写入
    # 段数不合(存量两段式)同拒
    for name in ("audit.test", "hbj-auditor.chris-fx", "audit..h"):
        res = authorize_instance_class(name, "auditor", gov)
        assert res["ok"] is False and res["reject_code"] == "INSTANCE_NOT_CANONICAL", (name, res)
    # 完整实例名通过(dry-run 预览 + confirm 落裁决记录)
    _reviewed_and_derived_audit(gov, HBJ_AUDITOR)
    ok = _verdict(gov, HBJ_AUDITOR)
    _show("件③·完整实例名提交裁决", {k: ok.get(k) for k in ("verdict", "blocking_reasons")})
    assert ok["verdict"] != "BLOCK", ok.get("blocking_reasons")
    landed = _verdict(gov, HBJ_AUDITOR, dry_run=False)
    assert landed["verdict"] != "BLOCK" and HBJ_AUDITOR in (gov / str(landed["data"]["audit_verdict_record_path"])).read_text(encoding="utf-8")


def test_item3_every_builtin_bare_role_refused_and_binding_chain(gov: Path, monkeypatch: pytest.MonkeyPatch):
    """不变量: roles.schema 每个内建角色名作裸 agent_instance 一律拒; 完整实例名须与审计卡认领实例(门认领时落定)一致,
    MCP 层 actor 须等于 agent_instance 解析出的规范实例——门侧既有绑定链的断言。"""
    from tools.aipos_cli.custom_roles import _builtin_role_names, authorize_instance_class
    from tools.mcp_server import tools as gate

    for role in sorted(_builtin_role_names()):
        res = authorize_instance_class(role, role, gov)
        assert res["ok"] is False and res["reject_code"] == "INSTANCE_NOT_CANONICAL", (role, res)
    _reviewed_and_derived_audit(gov, HBJ_AUDITOR)
    # 另一完整审计类实例(非本卡认领者)= 拒: 审计卡 claimed_by 绑定
    other = f"audit.{PROJECT}.otherhost"
    resp = _verdict(gov, other)
    _show("件③·非认领实例提交裁决", resp.get("blocking_reasons"))
    assert resp["verdict"] == "BLOCK" and "Task is claimed by another actor" in " ".join(resp["blocking_reasons"])
    # MCP 门动词: actor ≠ agent_instance = INSTANCE_MISMATCH; agent_instance 裸角色名 = 拒
    monkeypatch.setattr(gate, "_capability_has_scope", lambda scope: True)

    def call(actor: str, instance: str) -> dict:
        res = gate.lybra_audit_verdict_dry_run({
            "audit_task_id": AUDIT, "reviewed_task_id": REVIEWED, "actor": actor, "agent_instance": instance,
            "autonomy_mode": "Supervised", "owner_policy_ref": "pol_chris_fx_audit_1", "audit_claim_id": CLAIM_ID,
            "audit_session_id": SESSION_ID, "reviewed_return_record_ref": RETURN_ID, "verdict": "PASS",
            "findings_summary": "靶场", "artifact_subject": SUBJECT, "workspace_root": str(gov)})
        return json.loads(res["content"][0]["text"]) if "content" in res else res

    mismatch = call(other, HBJ_AUDITOR)
    _show("件③·MCP actor≠agent_instance", mismatch)
    assert "INSTANCE_MISMATCH" in json.dumps(mismatch, ensure_ascii=False)
    bare = call("auditor", "auditor")
    _show("件③·MCP 裸角色名", bare)
    assert bare.get("verdict") == "BLOCK" or bare.get("ok") is False
    assert "ROLE_VIOLATION" in json.dumps(bare, ensure_ascii=False) or "INSTANCE" in json.dumps(bare, ensure_ascii=False)
    ok = call(HBJ_AUDITOR, HBJ_AUDITOR)
    assert ok.get("verdict") != "BLOCK" and "ROLE_VIOLATION" not in json.dumps(ok, ensure_ascii=False), ok


# ---------------------------------------------------------------------------
# 棘轮: 单一实现 / 声明单源 / 禁写死节名
# ---------------------------------------------------------------------------

def _product_py() -> dict[str, str]:
    out = {}
    for base in ("tools/aipos_cli", "tools/mcp_server"):
        for path in sorted((REPO_ROOT / base).glob("*.py")):
            out[str(path.relative_to(REPO_ROOT))] = path.read_text(encoding="utf-8")
    return out


def test_ratchet_single_implementations_and_declarations():
    from tools.schema_loader import load_schema

    code = _product_py()
    # 件①: 判拒码只在唯一判据函数里出现, 判据只在 draft_writer 调用(发卡入口 + 起草 / 重生成不写 default)
    holders = {f for f, t in code.items() if '"LANE_REPO_REQUIRED' in t or "f\"LANE_REPO_REQUIRED" in t}
    assert holders == {"tools/aipos_cli/draft_writer.py"}, holders
    callers = {f for f, t in code.items() if "multi_repo_lane_refusal(" in t}
    assert callers == {"tools/aipos_cli/draft_writer.py"}, callers
    dw = code["tools/aipos_cli/draft_writer.py"]
    assert dw.count("def multi_repo_lane_refusal(") == 1
    body = dw.split("def publish_identity_refusals(")[1].split("\ndef ")[0]
    assert "multi_repo_lane_refusal(" in body
    # 件②: 写死节名匹配退役(原 `"一句话结论" in line`), 解析唯一 extract_return_summary_text, 读取口唯一 return_summary_source
    for f, t in code.items():
        assert not re.search(r"""["']一句话结论["']\s+in\s+line""", t), f
        assert not re.search(r"""["']## 一句话["']\s+in\s+line""", t), f
    defs = {f for f, t in code.items() if "def extract_return_summary_text(" in t}
    assert defs == {"tools/aipos_cli/next_resolver.py"}
    readers = {f for f, t in code.items() if re.search(r"""\.get\(["']return_summary_source["']\)""", t)}
    assert readers == {"tools/aipos_cli/workspace_config.py"}, readers
    ingest = code["tools/aipos_cli/artifact_ingest.py"]
    assert "extract_return_summary_text(content, summary_source)" in ingest and "return_summary_source(workspace_root)" in ingest
    decl = load_schema("transitions")["artifact_ingest"]["return"]["summary_source"]
    assert decl["frontmatter_keys"] == [] and decl["section_markers"] == ["一句话结论", "## 一句话"]
    pj = load_schema("config")["configuration_sources"]["project_json"]["schema"]["return_summary_source"]
    assert pj["default_from"] == "transitions.artifact_ingest.return.summary_source" and "default" not in pj
    assert "LANE_REPO_REQUIRED" in load_schema("config")["configuration_sources"]["project_json"]["schema"]["repos"]["reject_codes"]
    assert load_schema("verbs")["two_phase_protocol"]["project_json_writers"]["commands"][-1] == "set-return-summary"
    # 件③: 实例名判据唯一(段切分只经 parse_instance_name), authorize_instance_class 必经它
    cr = code["tools/aipos_cli/custom_roles.py"]
    auth = cr.split("def authorize_instance_class(")[1].split("\ndef ")[0]
    assert "instance_name_canonical_refusal(" in auth
    assert {f for f, t in code.items() if "def instance_name_canonical_refusal(" in t} == {"tools/aipos_cli/custom_roles.py"}
    assert load_schema("roles")["naming"]["gate_authorization"]["reject_code"] == "INSTANCE_NOT_CANONICAL"
    # fail-closed: 新增代码段无 except Exception
    for f, marker_from, marker_to in (
        ("tools/aipos_cli/draft_writer.py", "def multi_repo_lane_refusal(", "def publish_identity_refusals("),
        ("tools/aipos_cli/workspace_config.py", "RETURN_SUMMARY_SOURCE_KEYS = ", "class ProjectTargetError("),
        ("tools/aipos_cli/custom_roles.py", "def gate_authorization_declaration(", "def is_custom_role("),
        ("tools/aipos_cli/next_resolver.py", "def return_summary_status(", "def _check_verdict_artifact("),
    ):
        seg = code[f].split(marker_from)[1].split(marker_to)[0]
        assert "except Exception" not in seg, f
