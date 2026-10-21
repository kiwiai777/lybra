"""AIPOS-F205 — 环境卡(config 等非 validation 卡)绑定固定候选与专用验证根(chris 岚图 LANTU-ENV3 实撞)。

实撞: config 卡 lane.repo=governance_root(只写治理根, outputs + 顾问复核), 要在本卡专用验证根准备固定候选 fa5097c1 的构建依赖;
验证根 run-start 候选按本卡 lane.repo 解析 = 治理根自身 → VR_CANDIDATE_INVALID。
件① 卡面: config / docs / governance 卡可选声明 validation_source_task + validation_candidate(enums.schema task_mode.candidate_binding;
    缺省 = 行为不变); 起草 / 发卡 / queue amend 同一判据; 开工提示验证根节注明绑定与「制品只写本卡验证根, 产出走 outputs」。
件② run-start 候选核对 / 源码送达按来源卡 lane.repo(workspace_config.resolve_card_repo 唯一解析)取候选仓。
件③ 恢复: 已认领卡经 `queue amend --restricted` 补两字段(card.schema restricted_amend.claimed_card_fields)。
件④ 交付契约不变: 只写治理根卡仍 outputs 契约 + 顾问复核(不变成固定候选主体卡)。

靶场 = AIPOS-F196/F197 同一靶场(tmp 治理根 + tmp 产品仓 + 第二产品仓 web6 + tmp HOME; 本机模拟目标主机 sim-mac)。
禁真门 / 真治理根 / 真工位 / 真 chris / 真 pi。验收原文经 _show 打到 stdout(RETURN 证据)。
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import test_aipos_f78_engine_agnostic as f78  # noqa: E402  — 靶场骨架唯一来源(禁第二份)
from test_aipos_f78_engine_agnostic import AUDITOR, DRIVER, EXEC, _write  # noqa: E402
from test_aipos_f73d_loop_driver import init_governance_repo  # noqa: E402
from test_aipos_f90_loop_one_stage import rig  # noqa: E402,F401  — pytest 夹具复用
from test_aipos_f196_validation_to_pi import HOST, OWNER_REF, _cli, _draft_fm, _fp, _show, _vr, vrig  # noqa: E402,F401
from test_aipos_f197_noncode_pi_completion import _claim, _run, _sha  # noqa: E402
from tools.aipos_cli import next_resolver as nr  # noqa: E402

SRC2 = "PROBE-F205-UI5"     # 来源卡: 代码卡, lane.repo = web6(第二产品仓), 产出候选
ENV = "PROBE-F205-ENV3"     # 环境卡: config, lane.repo = governance_root(只写治理根), 绑定 SRC2 的候选
WEB6 = "web6"


@pytest.fixture
def erig(vrig, tmp_path):  # noqa: F811
    """F196 靶场 + 多仓声明(产品仓 + web6)+ web6 上的来源卡候选(chris 岚图形: 来源卡 lane.repo 是具名仓, 环境卡只写治理根)。"""
    web6 = f78._init_product_repo(tmp_path / "repos" / WEB6)
    _write(web6 / "pom.xml", "<project><artifactId>admin</artifactId></project>\n")
    f78._git(web6, "add", "pom.xml")
    f78._git(web6, "commit", "-q", "-m", "ui5 candidate")
    vrig.web6 = web6
    vrig.web6_candidate = f78._git(web6, "rev-parse", "HEAD")
    vrig.web6_tree = f78._git(web6, "rev-parse", "HEAD^{tree}")
    data = json.loads((vrig.gov / "project.json").read_text(encoding="utf-8"))
    data["repos"] = {"default": "product", "items": {"product": str(vrig.code_repo), WEB6: str(web6)}}
    _write(vrig.gov / "project.json", json.dumps(data))
    f78._card(vrig.gov, SRC2, "completed", extra={"harness": "claude-code", "lane": {"repo": WEB6, "paths": ["src/"], "roles": ["executor"]}})
    return vrig


def _env_lane() -> dict:
    return {"repo": "governance_root", "paths": [f"task_cards/{ENV}/artifacts/"], "roles": ["executor"]}


def _env_card(r, queue: str = "pending", *, bind: bool = True, **extra) -> Path:
    fields = {"harness": "pi", "audit": "", "audit_by": "", "output_target": f"task_cards/{ENV}/artifacts/", "lane": _env_lane()}
    if bind:
        fields.update({"validation_source_task": SRC2, "validation_candidate": r.web6_candidate})
    fields.update(extra)
    return f78._card(r.gov, ENV, queue, assigned=EXEC, task_mode="config", extra=fields)


def _declare(r) -> str:
    rc, out, err = _vr(r.gov, "declare", "--host", HOST, "--base", r.base, "--task-id", ENV, "--instance", EXEC, "--owner-ref", OWNER_REF,
                       "--host-fingerprint", _fp(), "--actor", DRIVER, "--confirm")
    _show(f"---- validation-root declare(环境卡 {ENV}, 实例 {EXEC}, host={HOST})exit {rc} ----\n{out}{err}")
    assert rc == 0, out + err
    return f"vr-{ENV.lower()}-executor-1"


def _kickoff(gov: Path, task_id: str) -> str:
    path, _q = nr._find_task_in_queue(gov, task_id)
    view = nr.card_workstation_view(gov, task_id, nr._read_frontmatter(path))
    picked = nr.select_next_card([{**view, "task_id": task_id, "queue_state": "claimed", "kickoff_refusal": None, "card_path": str(path)}])
    kickoff = (picked["next_card"] or {}).get("kickoff")
    assert kickoff, picked["next_card_excluded"]
    return kickoff


# ===========================================================================
# 声明单源 + 唯一读取口(缺省 = 行为不变)
# ===========================================================================

def test_declaration_single_source_and_default_unchanged():
    enums = json.loads((REPO_ROOT / "schema" / "enums.schema.json").read_text(encoding="utf-8"))["enums"]["task_mode"]
    binding = enums["candidate_binding"]
    _show(f"[声明] enums.schema task_mode.candidate_binding.task_modes = {binding['task_modes']} fields_from = {binding['fields_from']} "
          f"reject_codes = {binding['reject_codes']}")
    assert binding["single_implementation"].endswith("next_resolver.card_environment_binding")
    assert nr.candidate_binding_declaration()["fields"] == nr.candidate_subject_declaration()["fields"]
    card = json.loads((REPO_ROOT / "schema" / "card.schema.json").read_text(encoding="utf-8"))
    fields = card["restricted_amend"]["claimed_card_fields"]
    _show(f"[声明] card.schema restricted_amend.claimed_card_fields = {fields}")
    assert {"validation_source_task", "validation_candidate"} <= set(fields)
    from tools.aipos_cli.board_adapter import restricted_amend_fields

    assert {"validation_source_task", "validation_candidate"} <= restricted_amend_fields()
    sha = "a" * 40
    cases = {
        "config 未声明(缺省)": ({"task_mode": "config"}, None, None),
        "config 声明": ({"task_mode": "config", "validation_source_task": "S", "validation_candidate": sha}, {"source": "S", "candidate": sha}, None),
        "governance 声明": ({"task_mode": "governance", "validation_source_task": "S", "validation_candidate": sha}, {"source": "S", "candidate": sha}, None),
        "validation(固定候选主体, 不走绑定)": ({"task_mode": "validation", "validation_source_task": "S", "validation_candidate": sha}, None, None),
        "config 只写候选": ({"task_mode": "config", "validation_candidate": sha}, None, "VALIDATION_SUBJECT_MISSING"),
        "config 候选非 40 位": ({"task_mode": "config", "validation_source_task": "S", "validation_candidate": "abc"}, None, "VALIDATION_CANDIDATE_INVALID"),
        "code 写绑定": ({"task_mode": "code", "validation_source_task": "S", "validation_candidate": sha}, None, "VALIDATION_BINDING_MODE"),
    }
    for label, (fm, want, code) in cases.items():
        try:
            got = nr.card_environment_binding(fm)
        except nr.CandidateSubjectError as exc:
            _show(f"[读取口 card_environment_binding · {label}] 拒 {exc}")
            assert exc.code == code
            continue
        _show(f"[读取口 card_environment_binding · {label}] = {got}")
        assert code is None and got == want
    # 统一读取口: 固定候选主体卡与环境卡绑定都给出卡面候选(run-start 核对同读)
    assert nr.card_bound_candidate({"task_mode": "validation", "validation_source_task": "S", "validation_candidate": sha})["kind"] == "subject"
    assert nr.card_bound_candidate({"task_mode": "config", "validation_source_task": "S", "validation_candidate": sha})["kind"] == "environment"
    assert nr.card_bound_candidate({"task_mode": "config"}) is None
    # 交付契约不变: 环境卡绑定不是固定候选主体
    assert nr.card_candidate_subject({"task_mode": "config", "validation_source_task": "S", "validation_candidate": sha}) is None


# ===========================================================================
# 件①②: 靶场复现 → 卡面绑定后 declare / run-start 通过(候选仓按来源卡解析)→ 源码送达 + src-verify → 开工提示 → 写判定
# ===========================================================================

def test_item1_item2_env_card_run_start_by_source_repo_delivery_and_kickoff(erig):
    from tools.aipos_cli import validation_root as vr

    gov = erig.gov
    _env_card(erig, bind=False)
    init_governance_repo(gov)
    _claim(gov, ENV)
    assert not nr.card_needs_worktree(gov, {**nr._read_frontmatter(nr._find_task_in_queue(gov, ENV)[0]), "task_id": ENV})

    # 开工提示: 未绑定 + 未声明 = 无验证根节(行为不变)
    kick0 = _kickoff(gov, ENV)
    assert "跨机临时验证根" not in kick0 and "产物落点: 只写治理根" in kick0

    decl = _declare(erig)
    # 复现 LANTU-ENV3: 未绑定的治理根卡, run-start 候选按本卡 lane.repo = 治理根自身 → 拒
    rc, out, err = _vr(gov, "run-start", "--id", decl, "--candidate", erig.web6_candidate, "--actor", DRIVER)
    _show(f"---- 件② 未声明绑定(行为不变)run-start 预演 exit {rc} ----\n{out}{err}")
    assert rc == 1 and "VR_CANDIDATE_INVALID" in err and f"产品仓 {gov}" in err

    # 件③ 已认领卡经 restricted amend 补绑定(正式出口); 先看坏值被拒
    path, _q = nr._find_task_in_queue(gov, ENV)
    assert "/claimed/" in str(path)
    base = ["queue", "--workspace-root", gov, "amend", "--task-id", ENV, "--actor", DRIVER, "--restricted",
            "--amendment-reason", "AIPOS-F205: 环境卡绑定来源卡固定候选(执行前置, 不改验收)"]
    bad = {"validation_source_task": SRC2, "validation_candidate": erig.candidate}  # 候选在默认产品仓, 不在来源卡仓 web6
    rc, out, err = _cli(*base, "--amendments", json.dumps(bad), "--dry-run")
    _show(f"---- 件③ 已认领卡 restricted amend 补绑定(候选不在来源卡仓)exit {rc} ----\n{out}{err}")
    assert rc == 1 and "Amendment validation failed" in out and "AIPOS-F205" in out and "VALIDATION_CANDIDATE_INVALID" in out
    good = {"validation_source_task": SRC2, "validation_candidate": erig.web6_candidate}
    rc, out, err = _cli(*base, "--amendments", json.dumps(good), "--dry-run")
    _show(f"---- 件③ 已认领卡 restricted amend 补绑定 预演 exit {rc} ----\n{out}{err}")
    assert rc == 0 and '"verdict": "PASS"' in out
    rc, out, err = _cli(*base, "--amendments", json.dumps(good))
    _show(f"---- 件③ 已认领卡 restricted amend 补绑定 确认 exit {rc} ----\n{out}{err}")
    assert rc == 0 and '"amendments_applied"' in out
    fm = nr._read_frontmatter(nr._find_task_in_queue(gov, ENV)[0])
    assert (fm["validation_source_task"], fm["validation_candidate"], fm["lane"]["repo"]) == (SRC2, erig.web6_candidate, "governance_root")

    # 绑定后: 开工提示(尚无 run)= 绑定行, 候选仓 = 来源卡仓 web6, run-start 命令带卡面候选
    kick1 = _kickoff(gov, ENV)
    _show(f"---- 件① 绑定后开工提示(有声明, 未铸 run)----\n{kick1}\n------------------------------")
    assert "本卡绑定固定候选(AIPOS-F205" in kick1 and f"来源卡 {SRC2}" in kick1 and f"`{erig.web6}`" in kick1
    assert "只写本卡验证根本活动 run 目录" in kick1 and "frontmatter outputs" in kick1 and "产物落点: 只写治理根" in kick1
    assert f"run-start --id {decl} --candidate {erig.web6_candidate}" in kick1

    # run-start: 候选 ≠ 卡面候选 = 拒; = 卡面候选 = 放行, 候选仓按来源卡 lane.repo 解析
    rc, out, err = _vr(gov, "run-start", "--id", decl, "--candidate", erig.candidate, "--actor", DRIVER)
    _show(f"---- 件② run-start 候选 ≠ 卡面候选 exit {rc} ----\n{err.strip()}")
    assert rc == 1 and "VR_CANDIDATE_INVALID" in err and "绑定固定候选的环境卡" in err
    rc, out, err = _vr(gov, "run-start", "--id", decl, "--candidate", erig.web6_candidate, "--actor", DRIVER)
    _show(f"---- 件② 绑定后 run-start 预演 exit {rc} ----\n{out}{err}")
    assert rc == 0 and f"git -C {erig.web6} archive --format=tar --prefix=src/ {erig.web6_candidate}" in out
    rc, out, err = _vr(gov, "run-start", "--id", decl, "--candidate", erig.web6_candidate, "--actor", DRIVER, "--confirm")
    _show(f"---- 件② 绑定后 run-start 确认 exit {rc} ----\n{out}{err}")
    assert rc == 0, out + err

    entry = next(e for e in vr.project_validation_roots(gov) if e["id"] == decl)
    run = vr.active_run(entry)
    src = vr.candidate_source(gov, entry, run)
    _show(f"[件② 候选来源(唯一实现 candidate_source)] {src}")
    assert src == {"card": ENV, "repo": str(erig.web6), "candidate": erig.web6_candidate, "tree": erig.web6_tree}

    # 开工提示(活动 run): 绑定行 + 目录链 / 源码送达 / 核对命令(送达从来源卡仓)
    kick2 = _kickoff(gov, ENV)
    _show(f"---- 件① 绑定后开工提示(活动 run)----\n{kick2}\n------------------------------")
    delivery = vr.source_delivery(gov, entry, run)
    assert delivery["deliver"] in kick2 and delivery["verify"] in kick2 and "本卡绑定固定候选" in kick2
    assert "尚无验证根声明" not in kick2 and "-Dmaven.repo.local=" in kick2

    # 目标主机(本机模拟): 目录链 → 送达 → 核对
    for label, command in (("init-run", vr.init_command(gov, decl)["command"]), ("送达", delivery["deliver"]), ("核对", delivery["verify"])):
        proc = _run(command)
        tail = (proc.stdout + proc.stderr).strip().splitlines()
        _show(f"[件② {label}] $ {command}\n  exit {proc.returncode}: {tail[-1] if tail else ''}")
        assert proc.returncode == 0, proc.stdout + proc.stderr
    assert proc.stdout.strip().splitlines()[-1].startswith(f"SRC_OK candidate={erig.web6_candidate} tree={erig.web6_tree}")
    run_dir = erig.base / ENV / "run-1"
    assert (run_dir / "src" / "pom.xml").is_file()

    # 写判定: 环境制品(依赖缓存 / 临时 HOME / settings)在本 run 目录 = 允许; 他卡 / 全局缓存 = 拒
    check = ["roles", "--workspace-root", gov, "write-boundary", "--instance", EXEC, "--task-id", ENV, "--host", HOST, "--run-id", "run-1",
             "--level", "mutate", "--check"]
    for target, want in ((run_dir / "m2" / "repository" / "com" / "h2database" / "h2-1.4.200.jar", 0), (run_dir / "home" / "settings.xml", 0),
                         (erig.base / "OTHER-CARD" / "run-1" / "x", 3)):
        rc, out, err = _cli(*check, target)
        _show(f"[件② 写判定 {target}] exit {rc}: {(out + err).strip().splitlines()[0] if (out + err).strip() else ''}")
        assert rc == want, out + err
    rc, out, err = _vr(gov, "declare", "--host", HOST, "--base", "/Users/kiwi/.m2/lybra", "--task-id", ENV, "--instance", EXEC,
                       "--owner-ref", OWNER_REF, "--host-fingerprint", _fp(), "--actor", DRIVER)
    _show(f"[件② 全局缓存作 base 仍拒(F190 规则不变)] exit {rc}: {err.strip()}")
    assert rc == 1 and "VR_BASE_FORBIDDEN" in err


# ===========================================================================
# 件①: 起草 / 发卡判据(候选按来源卡仓核; 拒因带出口)
# ===========================================================================

def test_item1_draft_binding_refusals(erig):
    from tools.aipos_cli.draft_writer import create_draft

    gov = erig.gov
    meta = {"task_id": "PROBE-F205-D1", "title": "环境卡", "project": "lybra", "context_bundle": "t", "task_mode": "config",
            "priority": "high", "status": "pending", "created_by": DRIVER, "needs_owner": False, "artifact_policy": "formal_write",
            "output_target": "task_cards/PROBE-F205-D1/artifacts/",
            "lane": {"repo": "governance_root", "paths": ["task_cards/PROBE-F205-D1/artifacts/"], "roles": ["executor"]},
            "validation_source_task": SRC2, "validation_candidate": erig.web6_candidate}
    ok = create_draft(gov, meta, "## Goal\n\n准备依赖\n", dry_run=True)
    fm = _draft_fm(ok)
    _show(f"[件① 起草 config 卡绑定来源卡候选] verdict={ok['verdict']} blocking={ok['blocking_reasons']} lane.repo={fm['lane']['repo']} "
          f"validation_candidate={fm.get('validation_candidate')}")
    assert ok["verdict"] != "BLOCK" and fm["validation_candidate"] == erig.web6_candidate
    assert nr.card_governance_outputs(gov, {**fm, "task_id": meta["task_id"]}) and nr.card_candidate_subject(fm) is None
    cases = {
        "候选不在来源卡仓(在本卡 / 默认仓)": ({"validation_candidate": erig.candidate}, "VALIDATION_CANDIDATE_INVALID"),
        "来源卡不在队列": ({"validation_source_task": "PROBE-NOPE-1"}, "VALIDATION_SOURCE_UNKNOWN"),
        "只写来源卡": ({"validation_candidate": None}, "VALIDATION_SUBJECT_MISSING"),
        "code 卡写绑定": ({"task_mode": "code", "output_target": "app/", "lane": {"repo": "product", "paths": ["app/"]}}, "VALIDATION_BINDING_MODE"),
    }
    for label, (change, code) in cases.items():
        res = create_draft(gov, {k: v for k, v in {**meta, **change}.items() if v is not None}, "## Goal\n\nx\n", dry_run=True)
        hit = [r for r in res["blocking_reasons"] if "AIPOS-F205" in r]
        _show(f"[件① 起草拒 {label}] verdict={res['verdict']} {hit[0] if hit else res['blocking_reasons']}")
        assert res["verdict"] == "BLOCK" and hit and code in hit[0] and "出口" in hit[0]


# ===========================================================================
# 件④: 交付契约不变(只写治理根卡 = outputs 契约 + 顾问复核), 绑定不改交付主体
# ===========================================================================

def test_item4_return_still_outputs_and_advisor_review(erig):
    from tools.aipos_cli.artifact_ingest import validate_task_artifact

    gov = erig.gov
    _env_card(erig)
    init_governance_repo(gov)
    _claim(gov, ENV)
    fm = nr._read_frontmatter(nr._find_task_in_queue(gov, ENV)[0])
    assert nr.card_governance_outputs(gov, {**fm, "task_id": ENV}) and not nr.card_needs_worktree(gov, {**fm, "task_id": ENV})
    assert nr.candidate_subject_values(gov, ENV, fm) is None and not nr.task_mode_independent_audit("config")
    slot = gov / "task_cards" / ENV
    for name in ("dependency-preparation-summary.md", "dependency-manifest.json"):
        _write(slot / "artifacts" / name, f"{name}\n")
    outputs = [f"task_cards/{ENV}/artifacts/dependency-preparation-summary.md", f"task_cards/{ENV}/artifacts/dependency-manifest.json"]
    (slot / "RETURN.md").write_text("---\noutputs:\n" + "".join(f"- {o}\n" for o in outputs) + "---\n# RETURN\n\n## 一句话结论\n依赖准备完成。\n",
                                    encoding="utf-8")
    got = validate_task_artifact(ENV, gov)
    _show(f"[件④ 交回入口(绑定候选的环境卡)] ok={got['ok']} category={got['category']} "
          f"manifest={json.dumps(got.get('outputs_manifest'), ensure_ascii=False)}")
    assert got["ok"] and got["category"] == "OK"
    manifest = got["outputs_manifest"]
    assert manifest["outputs"] == sorted(f"{_sha(gov / o)}  {o}" for o in outputs)
    # 顾问复核结案出口(只写治理根卡快车道 mark-concluded)对绑定候选的环境卡照常可用(预演)
    rc, txt, err = _cli("--workspace-root", gov, "mark-concluded", "--task-id", ENV, "--actor", DRIVER, "--report-path",
                        f"task_cards/{ENV}/RETURN.md", "--conclusion-note", "顾问复核: 产出清单逐项核对通过", "--dry-run")
    _show(f"---- 件④ 顾问复核 mark-concluded --dry-run exit {rc} ----\n{txt}{err}")
    assert rc == 0 and '"verdict": "PASS"' in txt and '"operation": "mark_concluded"' in txt
    # 带分支三键 / 候选三键交回 = 不是本卡契约(交回契约仍 outputs)
    (slot / "RETURN.md").write_text(f"---\ncommit_sha: {erig.web6_candidate}\ntree_hash: {erig.web6_tree}\nbranch: card/{SRC2}\n---\n# RETURN\n",
                                    encoding="utf-8")
    bad = validate_task_artifact(ENV, gov)
    _show(f"[件④ 以候选三键交回(非本卡契约)] ok={bad['ok']} category={bad['category']} reason={bad.get('message') or bad.get('reason')}")
    assert not bad["ok"]
    # 顾问复核出口(只写治理根卡快车道)不变
    assert not nr.task_mode_independent_audit(str(fm.get("task_mode")))
