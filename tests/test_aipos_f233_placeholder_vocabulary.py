"""AIPOS-F233 — 卡面笔误不再拖到 Owner: 审计 / 验证命令占位统一词表 + 发卡规范化 + 声明错立即归顾问 + 占位改名受限 amend +
runtime_evidence_exhausted 归第三档 advisor_fix + 报告头 YAML 自由文本键冒号容错。

依据(10-25 chris 理想 HBLX-52): 分支卡 audit_runtime_evidence.commands 误用固定候选卡词表 `{src_dir}` / `{candidate}`, 分支卡审计只渲染
`{worktree}` / `{commit_sha}` / `{tree_hash}` → 审计体实跑通过仍逐字不匹配被拒收, loop 白拉起审计体两次 → runtime_evidence_exhausted → Owner 撤换。

验收逐条(原文经 _show 打到 stdout, RETURN 引用):
  ① 靶场复刻 HBLX-52 形态(分支卡声明 {src_dir}/{candidate}): 审计在审计分配工作树实跑后裁决入门(别名渲染匹配), loop 结案;
  ② draft create / publish 别名自动改写原文(发布记录 warnings 写明改写前后)+ 未知占位拒发原文;
  ③ 声明含渲染不了的占位 → loop 立即 advisor_fix 结束、审计体拉起 0 次原文;
  ④ 占位改名受限 amend 走通且返工自动放行不失原文; 非等价改动仍被拒原文;
  ⑤ runtime_evidence_exhausted 不进 Owner 收件箱原文;
  ⑥ `model: glm-5.3 (provider: kiwiai)` 报告头容错入门原文 + 非声明键同类错仍走修报告头; 开工提示报告必填行带引号规则。

靶场 = F90 进程内门替身 + 临时治理根 / 产品仓 / 临时 HOME; 构件复用 F90 / F162 / F165 / F221 / F222 / F225(禁第二份)。
禁真门 / 真治理根 / 真工位 / 起 pi; 项目名 / 实例名 / 卡号均为靶场值(产品不写死)。
"""
from __future__ import annotations

import copy
import hashlib
import json
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import test_aipos_f78_engine_agnostic as f78  # noqa: E402
import test_aipos_f90_loop_one_stage as f90  # noqa: E402  — loop 靶场唯一来源
import test_aipos_f162_runtime_evidence_supplementary_audit as f162  # noqa: E402  — 运行证据靶场构件
import test_aipos_f165_evidence_exec_check_no_new_privs as f165  # noqa: E402  — 出卡构件
import test_aipos_f218_candidate_rework_recovery as f218  # noqa: E402
import test_aipos_f221_root_ladder_fast_lane_rework as f221  # noqa: E402
import test_aipos_f222_rework_limit_arbitration as f222  # noqa: E402  — 返工上限构件
import test_aipos_f225_owner_intervention_tiers as f225  # noqa: E402  — 发布基线构件
from test_aipos_f73d_loop_driver import init_governance_repo  # noqa: E402
from test_aipos_f78_engine_agnostic import DRIVER, _fm, _git, _write  # noqa: E402
from test_aipos_f90_loop_one_stage import rig  # noqa: E402,F401  — pytest fixture
from test_aipos_f131_loop_run_status import _cli  # noqa: E402
from tools.aipos_cli import next_resolver as nr  # noqa: E402
from tools.aipos_cli.artifact_ingest import validate_task_artifact  # noqa: E402

TASK, AUDIT = f90.TASK, f90.AUDIT
# HBLX-52 原形: 分支卡声明误用固定候选词表({src_dir} / {candidate}); 靶场命令在审计分配工作树真实可跑
HBLX52_CMDS = ["git -C {src_dir} cat-file -e {candidate}",
               "python3 -B {src_dir}/tests/test_probe_f90_1.py --worktree {src_dir} --commit {candidate} --offline"]


def _show(text: str) -> None:
    print(text, flush=True)


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _real_run_entry(rig, audit_id: str, declared: str) -> dict:
    """审计体在自己的审计分配工作树按「规范占位」理解实跑一条声明命令(HBLX-52R 审计体就是这么跑的: {src_dir}→工作树, {candidate}→被审 tip)。"""
    _code, wt = nr.card_worktree_location(rig.gov, audit_id)
    tip = _git(rig.code_repo, "rev-parse", f"card/{TASK}")
    command = declared.replace("{src_dir}", str(wt)).replace("{candidate}", tip)
    proc = rig.real_run(["bash", "-c", command], capture_output=True, text=True, timeout=120, cwd=str(wt))
    log = rig.gov.parent / "audit-logs" / f"{audit_id}-{hashlib.sha1(command.encode()).hexdigest()[:8]}.log"
    _write(log, f"$ {command}\n{proc.stdout}{proc.stderr}exit={proc.returncode}\n")
    return {"command": command, "worktree": str(wt), "exit_code": proc.returncode, "log_path": str(log), "log_sha256": _sha(log)}


# ===========================================================================
# 件① 词表守卫 + 渲染唯一实现
# ===========================================================================

def test_item1_vocabulary_declared_guarded_and_aliases_render_per_subject(monkeypatch):
    import tools.schema_loader as sl
    from tools.aipos_cli.artifact_ingest import canonicalize_evidence_command, placeholder_vocabulary, render_evidence_command
    from tools.schema_loader import SchemaLoadError

    vocab = placeholder_vocabulary()
    table = {m["id"]: (m["names"], m["canonical"]) for m in vocab["meanings"]}
    _show("---- ① 占位词表(语义 → 同义占位名 / 各主体规范名)----\n" + "\n".join(f"  {k}: {v}" for k, v in table.items()))
    assert table["work_dir"] == (["worktree", "src_dir"], {"branch": "worktree", "candidate": "src_dir"})
    assert table["subject_commit"] == (["commit_sha", "candidate"], {"branch": "commit_sha", "candidate": "candidate"})
    assert table["subject_tree"] == (["tree_hash", "candidate_tree"], {"branch": "tree_hash", "candidate": "candidate_tree"})
    cmd = HBLX52_CMDS[1]
    branch = canonicalize_evidence_command(cmd, "branch")
    _show(f"[① 分支卡规范化] {cmd!r} → {branch['command']!r} pairs={branch['pairs']}")
    assert branch["command"] == "python3 -B {worktree}/tests/test_probe_f90_1.py --worktree {worktree} --commit {commit_sha} --offline"
    rendered = render_evidence_command(cmd, "branch", {"worktree": "/wt", "commit_sha": "c" * 40, "tree_hash": "t" * 40})
    assert rendered == f"python3 -B /wt/tests/test_probe_f90_1.py --worktree /wt --commit {'c' * 40} --offline"
    assert render_evidence_command("x {worktree} {commit_sha}", "candidate", {"src_dir": "/r/src", "candidate": "a" * 40}) == f"x /r/src {'a' * 40}"
    assert canonicalize_evidence_command("echo ${HOME} {{lit}} {nope}", "branch")["unknown"] == ["nope"]  # shell 写法不计

    real = sl.load_schema

    def patched(mutate):
        def load(name, *a, **k):
            data = real(name, *a, **k)
            if name != "transitions":
                return data
            data = copy.deepcopy(data)
            mutate(data["artifact_ingest"]["verdict"]["runtime_evidence"]["placeholder_vocabulary"])
            return data
        return load

    cases = {
        "同一名属两个语义": lambda v: v["meanings"][1]["names"].append("worktree"),
        "规范名不是该主体已声明占位": lambda v: v["meanings"][0]["canonical"].__setitem__("branch", "src_dir"),
        "已声明占位未入表": lambda v: v["meanings"].pop(3),
        "主体不全": lambda v: v["subjects"].pop("candidate"),
    }
    for label, mutate in cases.items():
        monkeypatch.setattr(sl, "load_schema", patched(mutate))
        with pytest.raises(SchemaLoadError) as exc:
            placeholder_vocabulary()
        _show(f"[① 词表守卫·{label}] {exc.value}")
    monkeypatch.setattr(sl, "load_schema", real)


# ===========================================================================
# 件① 验收①: 靶场复刻 HBLX-52 形态 —— 审计实跑后裁决入门(别名渲染匹配), loop 结案
# ===========================================================================

def test_acceptance1_hblx52_shape_branch_card_candidate_vocab_admitted(rig):
    f90._card(rig.gov, TASK, "pending", audit_runtime_evidence={"required": True, "commands": list(HBLX52_CMDS)})
    init_governance_repo(rig.gov)
    entries: list[dict] = []

    def audit_report(r):
        entries[:] = [_real_run_entry(r, AUDIT, c) for c in HBLX52_CMDS]
        return f162._report(r, AUDIT, "PASS", evidence=entries)

    seen: list[str] = []
    checks: dict[str, list] = {}
    res, text = f162._loop(rig, TASK, f162._watch(rig, {AUDIT: [audit_report]}, seen, checks))
    _show("---- 验收① HBLX-52 形态: 卡面声明(固定候选词表, 分支卡)----\n" + "\n".join(f"  - {c}" for c in HBLX52_CMDS))
    _show("---- 验收① 审计体在审计分配工作树实跑(运行记录)----\n" + json.dumps(entries, ensure_ascii=False, indent=1))
    _show(f"---- 验收① 裁决入口(artifact ingest 预检同一判据)---- ok={checks[AUDIT][0]['ok']} category={checks[AUDIT][0]['category']}")
    _show(f"---- 验收① lybra loop 输出 ----\n{text}")
    assert all(e["exit_code"] == 0 for e in entries), entries
    assert checks[AUDIT][0]["ok"] and checks[AUDIT][0]["category"] == "OK", checks[AUDIT][0]
    assert seen == ["executor", f"auditor:{AUDIT}"], seen  # 一次拉起即入门, 无补做
    assert res.exit_code == 0 and res.outcome == "completed", text
    verdict = nr._read_frontmatter(f90._records(rig.gov, "audit_verdicts", TASK)[0])
    _show(f"[验收① 裁决记录] verdict={verdict['verdict']} audit_task_id={verdict['audit_task_id']} verdict_id={verdict['verdict_id']}")
    assert verdict["verdict"] == "PASS"
    audit_card, _q = nr._find_task_in_queue(rig.gov, AUDIT)
    body = audit_card.read_text(encoding="utf-8")
    section = body[body.index("## 独立运行证据要求"):].split("\n\n证据节格式")[0]
    _show(f"---- 验收① 派生审计卡「独立运行证据要求」(按卡分支主体规范形展示)----\n{section}")
    assert "`python3 -B {worktree}/tests/test_probe_f90_1.py --worktree {worktree} --commit {commit_sha} --offline`" in section


# ===========================================================================
# 件② 验收②: draft create / publish 别名自动改写 + 未知占位拒发
# ===========================================================================

def test_acceptance2_draft_create_publish_rewrite_and_unknown_refused(rig, tmp_path):
    init_governance_repo(rig.gov)
    gov = rig.gov
    rc, out, err = _cli(["--workspace-root", str(gov), "draft", "create", "--from-json",
                         str(f165._draft(tmp_path, "PROBE-F233-D1", list(HBLX52_CMDS))), "--actor", DRIVER])
    _show(f"---- 验收② draft create(分支卡声明固定候选词表)rc={rc} ----\n{out}{err}")
    assert rc == 0, out + err
    assert "AIPOS-F233 占位规范化(交付主体 branch)" in out and "{src_dir} → {worktree}, {candidate} → {commit_sha}" in out
    draft = gov / "5_tasks" / "drafts" / "probe-f233-d1.md"
    assert nr._read_frontmatter(draft)["audit_runtime_evidence"]["commands"][1] == \
        "python3 -B {worktree}/tests/test_probe_f90_1.py --worktree {worktree} --commit {commit_sha} --offline"

    # 存量草稿(F233 前起草, 仍是别名): 手放 → publish 规范化并写进发布记录
    rc, out, err = _cli(["--workspace-root", str(gov), "draft", "create", "--from-json",
                         str(f165._draft(tmp_path, "PROBE-F233-D2", ["cd {worktree} && make test COMMIT={commit_sha}"])), "--actor", DRIVER])
    assert rc == 0, out + err
    d2 = gov / "5_tasks" / "drafts" / "probe-f233-d2.md"
    d2.write_text(d2.read_text(encoding="utf-8").replace("cd {worktree} && make test COMMIT={commit_sha}",
                                                         "cd {src_dir} && make test COMMIT={candidate}"), encoding="utf-8")
    rc, out, err = _cli(["--workspace-root", str(gov), "draft", "publish", "--path", "5_tasks/drafts/probe-f233-d2.md", "--actor", DRIVER])
    _show(f"---- 验收② draft publish(存量别名草稿)rc={rc} ----\n{out}{err}")
    assert rc == 0, out + err
    card, queue = nr._find_task_in_queue(gov, "PROBE-F233-D2")
    published = nr._read_frontmatter(card)["audit_runtime_evidence"]["commands"]
    record = next((gov / "5_tasks" / "records" / "publishes" / "PROBE-F233-D2").glob("*.md"))
    rec_text = record.read_text(encoding="utf-8")
    line = next(ln for ln in rec_text.splitlines() if "AIPOS-F233 占位规范化" in ln)
    _show(f"[验收② 发卡后卡面] queue={queue} commands={published}\n[验收② 发布记录 {record.name}] {line.strip()}")
    assert queue == "pending" and published == ["cd {worktree} && make test COMMIT={commit_sha}"]
    assert "改写前 `cd {src_dir} && make test COMMIT={candidate}` → 改写后 `cd {worktree} && make test COMMIT={commit_sha}`" in line

    # 未知占位(两表都无)= 起草 / 发卡拒并列出可用占位
    rc, out, err = _cli(["--workspace-root", str(gov), "draft", "create", "--from-json",
                         str(f165._draft(tmp_path, "PROBE-F233-D3", ["python3 {workdir}/run.py --commit {commit}"])), "--actor", DRIVER])
    hit = next((ln.strip() for ln in (out + err).splitlines() if "未知占位" in ln), "")
    _show(f"---- 验收② 未知占位起草 rc={rc} ----\n{hit}")
    assert rc != 0 and "RUNTIME_EVIDENCE_DECLARATION_INVALID" in hit and "{workdir} / {commit}" in hit
    assert "可用占位: 卡分支主体 {worktree}, {commit_sha}, {tree_hash}" in hit and "{worktree} ≡ {src_dir}" in hit
    assert nr._find_task_in_queue(gov, "PROBE-F233-D3")[1] is None
    d4 = gov / "5_tasks" / "drafts" / "probe-f233-d4.md"
    _write(d4, d2.read_text(encoding="utf-8").replace("PROBE-F233-D2", "PROBE-F233-D4").replace("cd {src_dir}", "cd {workdir}"))
    rc, out, err = _cli(["--workspace-root", str(gov), "draft", "publish", "--path", "5_tasks/drafts/probe-f233-d4.md", "--actor", DRIVER])
    hit = next((ln.strip() for ln in (out + err).splitlines() if "未知占位" in ln), "")
    _show(f"---- 验收② 未知占位发卡(手放草稿)rc={rc} ----\n{hit}")
    assert rc != 0 and "{workdir}" in hit and nr._find_task_in_queue(gov, "PROBE-F233-D4")[1] is None


# ===========================================================================
# 件③ 验收③: 声明含渲染不了的占位 → 立即 advisor_fix 结束, 审计体拉起 0 次
# ===========================================================================

BAD_CMDS = ["python3 -B {workdir}/tools/check.py --commit {commit_sha}"]


def test_acceptance3_unrenderable_declaration_ends_advisor_fix_zero_auditor_launch(rig):
    f90._card(rig.gov, TASK, "pending", audit_runtime_evidence={"required": True, "commands": list(BAD_CMDS)})  # 存量卡(F233 前已发布)
    init_governance_repo(rig.gov)
    seen: list[str] = []
    res, text = f162._loop(rig, TASK, f162._watch(rig, {AUDIT: []}, seen))
    _show(f"---- 验收③ lybra loop 输出(声明含 {{workdir}})----\n{text}")
    _show(f"[验收③ 等待 / 拉起轨迹] seen={seen}")
    assert seen == ["executor"], seen  # 审计体拉起 0 次
    assert res.exit_code == 4 and res.reason == "runtime_evidence_declaration_invalid", (res.reason, text)
    assert "queue amend --task-id PROBE-F90-1 --restricted" in text and "{workdir} → {worktree}" in text
    rc, out, err = _cli(["loop", "status", "--task-id", TASK, "--workspace-root", str(rig.gov)])
    _show(f"---- 验收③ lybra loop status ----\n{out}{err}")
    assert "advisor_fix(runtime_evidence_declaration_invalid)" in out and "不找 Owner、不撤换、不再拉起审计体" in out
    assert "--restricted" in out and "owner_needed" not in out.split("顾问下一动作")[1].split("\n")[0]

    # 审计报告已在(存量: F233 前审计体已交 PASS)= 裁决步同判声明错, 不让审计体补做
    f162._report(rig, AUDIT, "PASS", evidence=[{"command": "python3 -B /x/tools/check.py --commit abc", "worktree": "/x", "exit_code": 0,
                                                "log_excerpt": "ok"}])
    derived = nr.derive_next_step(AUDIT, rig.gov)
    _show(f"[验收③ 已交报告 · 推导核] action={derived['action']} triggered_by={derived['triggered_by']}\n  {derived['suggested_action']}")
    assert derived["action"]["type"] == "evidence_declaration_invalid" and derived["triggered_by"] == "advisor"
    check = validate_task_artifact(AUDIT, rig.gov)
    _show(f"[验收③ 裁决入口] category={check['category']}\n  " + "\n  ".join(check["reasons"]))
    assert check["category"] == "RUNTIME_EVIDENCE_DECLARATION_INVALID" and any("{workdir}" in r for r in check["reasons"])


# ===========================================================================
# 件④ 验收④: 占位改名受限 amend 走通且返工自动放行不失; 非等价改动仍被拒
# ===========================================================================

def _amend(gov: Path, task_id: str, value: dict, *extra: str) -> tuple[int, str, str]:
    return _cli(["queue", "--workspace-root", str(gov), "amend", "--task-id", task_id, "--actor", DRIVER, "--restricted",
                 "--amendment-reason", "只改审计命令占位(AIPOS-F233 靶场)", "--amendments",
                 json.dumps({"audit_runtime_evidence": value}, ensure_ascii=False), *extra])


def test_acceptance4_placeholder_rename_restricted_amend_keeps_auto_release(rig):
    task = "PROBE-F233-A"
    rounds = [{"round": n, "verdict_ref": f"verdict_old_{n}", "created_at": f"2026-10-0{n}T00:00:00Z", "created_by": DRIVER,
               "focus_items": ["x"]} for n in (1, 2)]
    alias = {"required": True, "commands": list(HBLX52_CMDS)}
    pending = f78._card(rig.gov, task, "pending", extra={"audit_runtime_evidence": alias})
    f225._publish_baseline(rig.gov, task, pending)  # 发布基线 = 别名声明(HBLX-52 原样发布)
    pending.unlink()
    f78._card(rig.gov, task, "claimed", extra={"claimed_by": "exec.lybra.test", "claimed_at": "'2026-10-01T00:00:00Z'",
                                               "rework_rounds": rounds, "audit_runtime_evidence": alias})
    init_governance_repo(rig.gov)

    fixed = {"required": True, "commands": ["git -C {worktree} cat-file -e {commit_sha}",
                                             "python3 -B {worktree}/tests/test_probe_f90_1.py --worktree {worktree} --commit {commit_sha} --offline"]}
    rc, out, err = _amend(rig.gov, task, fixed)
    _show(f"---- 验收④ 已认领卡受限 amend 只改占位名(别名 → 规范形)rc={rc} ----\n{out}{err}")
    assert rc == 0 and '"amendments_applied"' in out
    card = f218._card_fm(rig.gov, task)
    assert card["audit_runtime_evidence"] == fixed and card["rework_rounds"] == rounds

    # 非等价: 改命令其余部分 / 跨语义改名 = 照旧拒
    bad1 = {"required": True, "commands": [fixed["commands"][0], fixed["commands"][1].replace("--offline", "--online")]}
    rc1, out1, _ = _amend(rig.gov, task, bad1, "--dry-run")
    bad2 = {"required": True, "commands": [fixed["commands"][0].replace("{commit_sha}", "{tree_hash}"), fixed["commands"][1]]}
    rc2, out2, _ = _amend(rig.gov, task, bad2, "--dry-run")
    _show(f"---- 验收④ 非等价(改参数)rc={rc1} ----\n{out1}\n---- 验收④ 非等价(跨语义改名 commit_sha → tree_hash)rc={rc2} ----\n{out2}")
    assert rc1 == 1 and rc2 == 1 and all("实改了 ['commands']" in o for o in (out1, out2))

    # 返工自动放行: 第 3 轮仍自动追加(指纹按规范形算, 只改占位名不算范围变化)
    now = datetime.now(timezone.utc)
    vid = f222._verdict(rig.gov, task, 3, f221._iso(now + timedelta(seconds=5)))
    res = f222._queue_rework(rig.gov, task, vid, dry_run=False)
    card = f218._card_fm(rig.gov, task)
    _show("---- 验收④ 占位改名后 queue rework 第 3 轮 ----\n" + json.dumps(
        {"ok": res.get("ok"), "auto_release": (res.get("data") or {}).get("auto_release"), "round": card["rework_rounds"][-1]},
        ensure_ascii=False))
    assert res.get("ok") and card["rework_rounds"][-1]["round"] == 3 and card["rework_rounds"][-1]["auto_extension_ref"]


def test_acceptance4_unknown_placeholder_fix_equivalent_and_fingerprint(rig):
    """把渲染不了的占位改成可用占位 = 渲染等价(受限 amend 放行); 返工自动放行范围判据同读等价判据(未知 → 已知不算范围变化)。"""
    from tools.aipos_cli import queue_mutation
    from tools.aipos_cli.artifact_ingest import evidence_commands_render_equivalent

    assert evidence_commands_render_equivalent(BAD_CMDS, ["python3 -B {worktree}/tools/check.py --commit {commit_sha}"], "branch")
    assert not evidence_commands_render_equivalent(BAD_CMDS, ["python3 -B {worktree}/tools/check.py --commit {tree_hash}"], "branch")
    assert not evidence_commands_render_equivalent(["a {worktree}"], ["a {workdir}"], "branch")  # 改后含未知 = 不等价
    task = "PROBE-F233-B"
    bad = {"required": True, "commands": list(BAD_CMDS)}
    pending = f78._card(rig.gov, task, "pending", extra={"audit_runtime_evidence": bad})
    f225._publish_baseline(rig.gov, task, pending)
    pending.unlink()
    rounds = [{"round": n, "verdict_ref": f"verdict_old_{n}", "created_at": f"2026-10-0{n}T00:00:00Z", "created_by": DRIVER,
               "focus_items": ["x"]} for n in (1, 2)]
    f78._card(rig.gov, task, "claimed", extra={"claimed_by": "exec.lybra.test", "claimed_at": "'2026-10-01T00:00:00Z'",
                                               "rework_rounds": rounds, "audit_runtime_evidence": bad})
    init_governance_repo(rig.gov)
    good = {"required": True, "commands": ["python3 -B {worktree}/tools/check.py --commit {commit_sha}"]}
    rc, out, err = _amend(rig.gov, task, good)
    _show(f"---- 验收④ 渲染不了的占位改成可用占位(受限 amend)rc={rc} ----\n{out}{err}")
    assert rc == 0, out + err
    path, _q = nr._find_task_in_queue(rig.gov, task)
    meta, body = nr._read_frontmatter(path), path.read_text(encoding="utf-8").split("---", 2)[2]
    check = queue_mutation.auto_extension_check(rig.gov, task, meta, body, next_round=3, max_rounds=2, verdict_ref="v3")
    _show(f"[验收④ 返工自动放行范围判据] eligible={check['eligible']} basis={check.get('basis') or check.get('why')}")
    assert check["eligible"]
    if "audit_runtime_evidence" in queue_mutation.auto_extension_declaration()["fingerprint_fields"]:  # AIPOS-F230 起运行证据声明入指纹
        assert "只改占位名" in check["basis"]


# ===========================================================================
# 件⑤ 验收⑤: runtime_evidence_exhausted 不进 Owner 收件箱
# ===========================================================================

def test_acceptance5_runtime_evidence_exhausted_not_in_owner_inbox(rig):
    from tools.aipos_cli.owner_inbox import tier_declaration

    f90._card(rig.gov, TASK, "pending", audit_runtime_evidence=f162.EVIDENCE)
    init_governance_repo(rig.gov)
    script = {AUDIT: [lambda r, n=n: f162._report(r, AUDIT, "PASS", body_extra=f"\n第 {n} 份(仍缺运行证据)\n") for n in (1, 2, 3)]}
    res, _text = f162._loop(rig, TASK, f162._watch(rig, script, []))
    assert res.reason == "runtime_evidence_exhausted"
    spec = tier_declaration()["loop_reasons"]["runtime_evidence_exhausted"]
    _show(f"[验收⑤ 档位登记] loop_reasons.runtime_evidence_exhausted = {spec}")
    assert spec["tier"] == 3 and spec["action"] == "advisor_fix"
    rc, out, err = _cli(["loop", "status", "--task-id", TASK, "--workspace-root", str(rig.gov)])
    line = next(ln for ln in out.splitlines() if "顾问下一动作" in ln)
    _show(f"[验收⑤ loop status 顾问下一动作] {line.strip()[:400]}")
    assert "advisor_fix(runtime_evidence_exhausted) — 第三档(归顾问" in line
    rc, out, err = _cli(["owner", "inbox", "--home-root", str(rig.gov.parent), "--json"])
    items = [i for i in json.loads(out)["items"] if i.get("task_id") == TASK]
    _show(f"[验收⑤ Owner 收件箱(本卡事项)] {items}")
    assert items == []


# ===========================================================================
# 件⑥ 验收⑥: 报告头 YAML 自由文本键冒号容错 + 非声明键照旧修报告头 + 开工提示预防
# ===========================================================================

def test_acceptance6_header_yaml_free_text_tolerated_other_keys_still_fixed(rig):
    from tools.aipos_cli.next_resolver import report_header_quote_rule

    f90._card(rig.gov, TASK, "pending")
    init_governance_repo(rig.gov)

    def report_with(line: str):
        def write(r):
            tip = _git(r.code_repo, "rev-parse", f"card/{TASK}")
            report = r.gov / "task_cards" / AUDIT / "audit_report.md"
            report.parent.mkdir(parents=True, exist_ok=True)
            report.write_text(f"---\ntask_id: {AUDIT}\nreviewed_task_id: {TASK}\nverdict: PASS\ncommit_sha: {tip}\n{line}\n---\n"
                              "# 审计报告\n\n## 一句话结论\n逐条复核通过。\n", encoding="utf-8")
            f90._pi_session(r.home, r.home / "workstations" / "probe-auditor", report, f90.RUNTIME_AUDIT_MODEL)
            return report
        return write

    checks: dict[str, list] = {}
    res, text = f162._loop(rig, TASK, f162._watch(rig, {AUDIT: [report_with("model: glm-5.3 (provider: kiwiai)")]}, [], checks))
    first = checks[AUDIT][0]
    _show(f"---- 验收⑥ 报告头 `model: glm-5.3 (provider: kiwiai)`(声明的自由文本键)入门 ----\nok={first['ok']} category={first['category']} "
          f"notes={first.get('notes')}\nfrontmatter.model={first['frontmatter'].get('model')!r}")
    assert first["ok"] and first["category"] == "OK" and first["frontmatter"]["model"] == "glm-5.3 (provider: kiwiai)"
    assert first.get("notes") and "已按整行值加引号读出" in first["notes"][0] and "报告原文未改" in first["notes"][0]
    assert res.exit_code == 0 and res.outcome == "completed", text
    report = rig.gov / "task_cards" / AUDIT / "audit_report.md"
    assert "model: glm-5.3 (provider: kiwiai)\n" in report.read_text(encoding="utf-8")  # 原文未改

    other = rig.gov / "task_cards" / "PROBE-F233-X" / "audit_report.md"
    _write(other, "---\nverdict: PASS\nengine: glm-5.3 (provider: kiwiai)\n---\n# x\n")
    from tools.aipos_cli.frontmatter import FrontmatterReadError, frontmatter_repair_notes, require_frontmatter

    with pytest.raises(FrontmatterReadError) as exc:
        require_frontmatter(other)
    _show(f"[验收⑥ 非声明键同类错(照旧读不出 → F232 修报告头例行修法)] {str(exc.value)[:260]}")
    assert frontmatter_repair_notes(other.read_text(encoding="utf-8")) == []
    rule = report_header_quote_rule()
    _show(f"[验收⑥ 开工提示 / lybra kickoff 报告必填行末尾(预防)] {rule}")
    assert "值含冒号(`: `)须加引号" in rule and 'model: "glm-5.3 (provider: kiwiai)"' in rule
    lines = nr.render_report_field_lines([{"key": "commit_sha", "hint": "x", "value": None}])
    assert lines[-1] == rule


def test_acceptance6_non_declared_key_audit_report_goes_header_fix(rig):
    """非声明键同类错: 审计报告读不出 = 报告格式错(header_problem, F232 例行修法), 不容错。"""
    f90._card(rig.gov, TASK, "pending")
    init_governance_repo(rig.gov)
    outcome: dict[str, dict] = {}

    def watch(args, expect_ready, deploy_guard=None):
        if f"/{AUDIT}/" in " ".join(args.expect):
            tip = _git(rig.code_repo, "rev-parse", f"card/{TASK}")
            report = rig.gov / "task_cards" / AUDIT / "audit_report.md"
            _write(report, f"---\nreviewed_task_id: {TASK}\nverdict: PASS\ncommit_sha: {tip}\nengine: glm-5.3 (provider: kiwiai)\n---\n# x\n")
            outcome["check"] = validate_task_artifact(AUDIT, rig.gov)
            return 2
        f90._executor_work(rig)
        return 0 if expect_ready([]) else 3

    f162._loop(rig, TASK, watch, max_wait=2)
    check = outcome["check"]
    _show(f"[验收⑥ 非声明键 · 裁决入口] category={check['category']}\n  {check['reasons'][0][:400]}")
    assert check["category"] == "INGEST_FRONTMATTER_UNREADABLE" and check.get("header_problem")


def test_single_source_and_lane():
    """唯一实现: 渲染 / 规范化 / 等价判据只在 artifact_ingest 一处; 旧占位迁移提示退役(无调用方)。"""
    import ast

    src = (REPO_ROOT / "tools" / "aipos_cli" / "artifact_ingest.py").read_text(encoding="utf-8")
    funcs = {n.name for n in ast.parse(src).body if isinstance(n, ast.FunctionDef)}
    for name in ("placeholder_vocabulary", "canonicalize_evidence_command", "render_evidence_command", "normalize_runtime_evidence_declaration",
                 "evidence_commands_render_equivalent", "evidence_declaration_problem"):
        assert name in funcs, name
    assert "runtime_evidence_legacy_problems" not in funcs
    grep = subprocess.run(["git", "-C", str(REPO_ROOT), "grep", "-n", "runtime_evidence_legacy_problems(", "--", "tools"],
                          capture_output=True, text=True)
    assert grep.stdout == "", grep.stdout
    board = (REPO_ROOT / "tools" / "aipos_cli" / "board_adapter.py").read_text(encoding="utf-8")
    assert "render_evidence_command(command, \"branch\", values)" in board
    decl = json.loads((REPO_ROOT / "schema" / "transitions.schema.json").read_text(encoding="utf-8"))
    rt = decl["artifact_ingest"]["verdict"]["runtime_evidence"]
    assert "legacy_placeholders" not in rt["candidate_subject"] and "legacy_hint" not in rt["candidate_subject"]
    assert rt["loop"]["declaration_invalid_end_reason"] == "runtime_evidence_declaration_invalid"
