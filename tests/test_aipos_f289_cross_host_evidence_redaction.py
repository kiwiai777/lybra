"""AIPOS-F289 — 跨机固定候选卡运行证据有唯一正确写法 + 凭据打码只遮值不遮键名。

依据(11-03 chris 理想 HBLX-160, 顾问只读复现): 固定候选验证卡卡面 audit_runtime_evidence = {host: <Mac>, commands: [`ssh <Mac> '…<执行 run 路径>…'`,
`ssh <Mac> 'for k in X_SDK_ID X_TOKEN X_..._PASSWORD; do … echo "$k non_empty" …'`]}, 审计两轮 PASS 都被裁决入口拒, 最终 runtime_evidence_exhausted;
loop 记录与 loop status 的拒因因含 TOKEN / PASSWORD 键名整行变成「[含凭据字样, 整行已隐去]」, 顾问看不到逐条差异。

验收(卡面 ★验收):
 ① redact_progress 只遮值不遮键名: 键名保留、10 种以上真实值形态全打码; 定位不了值的照旧整行隐去; 人读字段口径不变;
    HBLX-160 形态拒因在 loop 日志 / 运行记录 / loop status 中可见逐条差异(临时根复现);
 ② 临时治理根 + 临时跨机验证根(假 ssh 就地执行模拟目标主机, 不连真实 Mac): 开工稿 / 派审步骤按唯一写法渲染执行行 → 审计补做 → 裁决入口接受;
    远端 run 的 run-long 引用 = 拒并给出口;
 ③ 出卡预演即拒 ssh 前缀 / 写死 run 路径命令并给改写出口; 合法占位写法通过; 补审派发同一判据;
 ④ 已认领卡: 推导核判声明错给受限 amend 出口 → 受限 amend 改写通过 → 审计按新声明可过; runtime_evidence_exhausted 的 loop status 直接给出口;
 ⑥ en 新文案零 CJK。

靶场 = AIPOS-F196/F199/F200/F223 同一靶场(tmp 治理根 + tmp 产品仓 + tmp HOME; 进程内门; 目标主机 sim-mac = 本机模拟, 假 ssh 把远端命令就地交 sh);
测试夹具进程一律经 fixture_procs 助手(经 F95 / F196 靶场)。禁真门 / 真治理根 / 真工位 / 真 chris / 真 Mac / 真 pi。验收原文经 _show 打到 stdout。
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import test_aipos_f78_engine_agnostic as f78  # noqa: E402  — 靶场骨架唯一来源(禁第二份)
import test_aipos_f90_loop_one_stage as f90  # noqa: E402
import test_aipos_f162_runtime_evidence_supplementary_audit as f162  # noqa: E402
from test_aipos_f73d_loop_driver import init_governance_repo  # noqa: E402
from test_aipos_f78_engine_agnostic import AUDITOR, DRIVER, EXEC, _git  # noqa: E402
from test_aipos_f90_loop_one_stage import rig  # noqa: E402,F401  — pytest 夹具复用
from test_aipos_f131_loop_run_status import _cli as _cli_list  # noqa: E402
from test_aipos_f171b_loop_single_instance_resume import _records  # noqa: E402
from test_aipos_f196_validation_to_pi import ALIAS, HOST, SRC, VAL, _cli, _show, _validation_card, _vr, vrig  # noqa: E402,F401
from test_aipos_f199_validation_audit_run_evidence import VALR, _check, _declare, _run, _run_start  # noqa: E402
from test_aipos_f200_remote_run_evidence_fetch import (  # noqa: E402
    _declare_remote, _entry, _fake_ssh_double_dash, _fetch, _manifest, _prepare_remote_run, _set_host,
)
from test_aipos_f223_multi_host_evidence_host import _dispatch_steps, _kickoff_steps  # noqa: E402
from test_aipos_f234_in_transit_truth import _run_record  # noqa: E402
from tools.aipos_cli import next_resolver as nr  # noqa: E402
from tools.aipos_cli import validation_root as vr  # noqa: E402
from tools.aipos_cli.harness_launch import REDACT_HIDDEN_LINE, REDACT_MASK, credential_flagged, redact_progress  # noqa: E402

HIDDEN = REDACT_HIDDEN_LINE
KEYS = "APP_TOKEN APP_ADMIN_PASSWORD"  # 卡面命令里只有键名(HBLX-160 同形: X_TOKEN / X_..._PASSWORD)
CMD_FILE = "cat {src_dir}/app/feature.txt"
CMD_KEYS = 'for k in ' + KEYS + '; do grep -q "$k" {src_dir}/app/feature.txt && echo "$k non_empty" || echo "$k EMPTY"; done'
REASON = "AIPOS-F289 靶场"


# ===========================================================================
# 验收①: 只遮值不遮键名(唯一实现 harness_launch.redact_progress)
# ===========================================================================

#: 真实值形态(值均为靶场造的假值): (原文, 不得出现的值, 必须保留的键名 / 前缀)
VALUE_FORMS = [
    ("token=Vq7hR2kLm9xQ", "Vq7hR2kLm9xQ", "token="),
    ('{"password": "hunter2Zq9"}', "hunter2Zq9", '"password": '),
    ("password: 'sw0rdFish88'", "sw0rdFish88", "password: "),
    ("Authorization: Bearer abcdEFGH1234ijklMNOP", "abcdEFGH1234ijklMNOP", "Authorization: Bearer "),
    ("curl -H 'Authorization: token ghp_" + "A1b2C3d4E5f6G7h8I9j0K1l2M3n4O5p6Q7" + "' https://api.example", "A1b2C3d4E5f6G7h8I9j0K1l2M3n4O5p6Q7",
     "Authorization: token "),
    ("export APP_API_TOKEN=s3cr3tValue77", "s3cr3tValue77", "export APP_API_TOKEN="),
    ("PGPASSWORD=Sup3rS3cret psql -h db", "Sup3rS3cret", "PGPASSWORD="),
    ("deploy --password hunter22x --env prod", "hunter22x", "deploy --password "),
    ("--token=abcdef1234 --verbose", "abcdef1234", "--token="),
    ("api_key: sk-" + "abcdefghijklmnopqrstuvwxyz012345", "abcdefghijklmnopqrstuvwxyz012345", "api_key: "),
    ("x-api-key: AbCdEf123456", "AbCdEf123456", "x-api-key: "),
    ("mysql://root:t00rPass@localhost:3306/app", "t00rPass", "mysql://root:"),
    ("aws configure set key AKIA" + "ABCDEFGHIJKLMNOP", "ABCDEFGHIJKLMNOP", "aws configure set key "),
    ("client_secret=\"q8~Zs.x77\"", "q8~Zs.x77", "client_secret="),
    ("upload blob ZXlKaGJHY2lPaUpJVXpJMU5pSjkuZXlKemRXSWlPaUl4TWpNME5UWTNPRGt3SW4w done", "ZXlKaGJHY2lPaUpJVXpJMU5pSjkuZXlKemRXSWlPaUl4TWpNME5UWTNPRGt3SW4w",
     "upload blob "),
]

#: 无值可遮 = 原样(键名 / 键名清单 / 状态词 / 占位 / 计数键)
KEYS_ONLY = [
    "ssh kiwiai-mac 'for k in HBJOTA_SDK_ID HBJOTA_TOKEN HBJOTA_DEVICE_ADMIN_PASSWORD HBJOTA_DEVICE_COMMON_PASSWORD; do v=$(plutil -extract $k raw "
    "-o - /Users/x/Info.plist 2>/dev/null); [ -n \"$v\" ] && echo \"$k non_empty\" || echo \"$k EMPTY\"; done'",
    "HBJOTA_TOKEN non_empty",
    "HBJOTA_DEVICE_ADMIN_PASSWORD EMPTY",
    "cat .lybra/connection.json token",
    "token=$APP_TOKEN password: <口令> secret: ${SECRET_REF}",
    "input_tokens: 12345",
]

#: 定位不了值 = 整行隐去(旧口径安全兜底)
UNLOCATED = ["the password is hunter2", "token 是 abc123", "password=my_secret_pw", "password my_secret_pw", "Authorization: Bearer abc",
             "LYBRAENROLL1.eyJ2IjoxfQ"]


def test_acceptance1_values_masked_keys_kept():
    for text, value, keep in VALUE_FORMS:
        got = redact_progress(text)
        _show(f"[验收① 值打码] {text!r}\n    → {got!r}")
        assert value not in got and REDACT_MASK in got and keep in got, (text, got)
    assert len(VALUE_FORMS) >= 10
    for text in KEYS_ONLY:
        got = redact_progress(text)
        _show(f"[验收① 键名保留] {text[:90]!r} → 原样={got == text}")
        assert got == text
    for text in UNLOCATED:
        got = redact_progress(text)
        _show(f"[验收① 定位不了值 → 整行隐去] {text!r} → {got!r}")
        assert got == HIDDEN
    # 人读字段口径不变: 含凭据字样即拒(credential_flagged), 不随进度打码放宽
    assert credential_flagged("rotate token") and credential_flagged("HBJOTA_TOKEN non_empty") and not credential_flagged("Owner 10-21 批准 ENV3B 恢复")
    with pytest.raises(vr.ValidationRootError, match="含凭据字样"):
        vr._single_line("Owner 批准 token 轮换", "--owner-ref")


def test_acceptance1_declaration_fail_closed(monkeypatch):
    """progress_redaction 缺 = 旧口径(含凭据字样整行隐去); 键不齐 = SchemaLoadError → redact_progress 按旧口径(不放宽)。"""
    from tools.aipos_cli import credential_guard as cg
    from tools.schema_loader import SchemaLoadError

    real = nr._artifact_ingest_declaration()
    guard = {k: v for k, v in real["credential_guard"].items() if k != "progress_redaction"}
    monkeypatch.setattr(nr, "_artifact_ingest_declaration", lambda: {**real, "credential_guard": guard})
    assert cg.progress_redaction_declaration() is None
    assert redact_progress("HBJOTA_TOKEN non_empty") == HIDDEN
    broken = {**real["credential_guard"], "progress_redaction": {"always_hide": "(?i)LYBRAENROLL"}}
    monkeypatch.setattr(nr, "_artifact_ingest_declaration", lambda: {**real, "credential_guard": broken})
    with pytest.raises(SchemaLoadError, match="progress_redaction"):
        cg.progress_redaction_declaration()
    assert redact_progress("token=Vq7hR2kLm9xQ") == HIDDEN


def test_acceptance1_hblx160_reason_visible_in_log_record_status(rig):  # noqa: F811
    """HBLX-160 形态: 卡面命令只有键名(APP_TOKEN / APP_ADMIN_PASSWORD), 审计实跑命令与声明差一个词 → 拒因原文含键名与逐条差异,
    loop 日志 / 运行记录 / loop status 同文可见(不再整行隐去)。"""
    declared = "for k in " + KEYS + '; do echo "$k non_empty"; done'
    ran = "for k in APP_TOKEN APP_ADMIN_PASSWD; do echo \"$k non_empty\"; done"
    f90._card(rig.gov, f90.TASK, "pending", audit_runtime_evidence={"required": True, "commands": [declared]})
    init_governance_repo(rig.gov)

    def report(r):
        wt = f162._rendered(r, f90.AUDIT)["worktree"]
        return f162._report(r, f90.AUDIT, "PASS", evidence=[{"command": ran, "worktree": wt, "exit_code": 0,
                                                            "log_excerpt": "APP_TOKEN non_empty"}])

    res, text = f162._loop(rig, f90.TASK, f162._watch(rig, {f90.AUDIT: [report]}, []))
    line = next(ln for ln in text.splitlines() if re.match(r"^\[\d+\] runtime_evidence: ", ln))
    _show(f"---- 验收① loop 输出拒因行 ----\n{line}")
    assert HIDDEN not in line and "APP_TOKEN APP_ADMIN_PASSWORD" in line and "逐条差异" in line
    assert "声明 `APP_ADMIN_PASSWORD;` ≠ 实跑 `APP_ADMIN_PASSWD;`" in line
    detail = line.split("] ", 1)[1]
    meta = _records(rig.gov)[-1]
    steps = [s for s in meta["steps"] if s.get("detail")]
    _show(f"[验收① 运行记录该步 detail] {steps[0]['detail'][:400]}")
    assert steps and steps[0]["detail"] == detail[:len(steps[0]["detail"])] and HIDDEN not in json.dumps(meta, ensure_ascii=False)
    log = Path(meta["log_path"]).read_text(encoding="utf-8")
    assert detail in log and HIDDEN not in log
    rc, status, err = _cli_list(["loop", "status", "--task-id", f90.TASK, "--workspace-root", str(rig.gov)])
    shown = [ln for ln in status.splitlines() if "runtime_evidence:" in ln]
    _show("---- 验收① loop status(拒因行)----\n" + "\n".join(shown))
    assert shown and all(HIDDEN not in ln for ln in shown) and any("APP_ADMIN_PASSWD" in ln and "逐条差异" in ln for ln in shown)


# ===========================================================================
# 验收②: 跨机运行证据唯一写法(临时跨机验证根: 假 ssh 模拟目标主机)
# ===========================================================================

def _claimed_audit_card(gov: Path) -> None:
    f78._card(gov, VALR, "claimed", assigned=AUDITOR, task_mode="audit",
              extra={"reviewed_task_id": VAL, "claimed_by": AUDITOR, "agent_instance": AUDITOR, "harness": "pi", "audit": "", "audit_by": "",
                     "claim_id": f"claim_{VALR}_x", "claimed_at": "2026-11-03T00:00:00Z"})
    f78._claim_record(gov, VALR, AUDITOR)


@pytest.fixture
def xhost(vrig):  # noqa: F811
    """跨机靶场(F200 假 ssh 增认 `ssh -- <别名>`); 卡面由各测试先写, 再 _arm 起治理仓 + 主机别名 + 声明。"""
    _fake_ssh_double_dash(vrig)
    vrig.tree = f78._git(vrig.code_repo, "rev-parse", f"{vrig.candidate}^{{tree}}")
    vrig.exec_run = vrig.base / VAL / "run-1"  # 执行实例 run 目录(声明 path_template 渲染形; _arm 后核对)
    return vrig


def _arm(x) -> None:
    """治理仓基线 + 目标主机 sim-mac 别名(set-host)+ 执行实例 / 审计实例在同一 base 的声明(审计卡 / 验证卡均 claimed)。"""
    gov, base = x.gov, x.base
    init_governance_repo(gov)
    _set_host(gov)
    x.exec_decl = _declare_remote(gov, base, VAL, EXEC)
    x.decl = _declare_remote(gov, base, VALR, AUDITOR)
    assert Path(vr.run_directory(_entry(gov, x.exec_decl), "run-1")) == x.exec_run


def _exec_lines(steps: str) -> list[str]:
    return [m.group(1) for m in re.finditer(r"^\s+(?:执行|run): `(.+)`$", steps, re.MULTILINE)]


def _run_exec_lines(lines: list[str]) -> list[dict]:
    entries = []
    for line in lines:
        proc = _run(line)
        _show(f"[执行行] exit {proc.returncode}\n{proc.stdout}{proc.stderr}")
        assert proc.returncode == 0, proc.stdout + proc.stderr
        entry = next(ln for ln in proc.stdout.splitlines() if ln.startswith("CMD_ENTRY "))
        entries.append(json.loads(entry[len("CMD_ENTRY "):]))
    return entries


def test_acceptance2_remote_run_single_path_rendered_and_accepted(xhost):
    gov = xhost.gov
    _validation_card(xhost, queue="claimed", claimed_by=EXEC, audit_runtime_evidence={"required": True, "commands": [CMD_FILE, CMD_KEYS], "host": HOST})
    _claimed_audit_card(gov)
    _arm(xhost)
    decl = xhost.decl
    run_id = _run_start(gov, decl, xhost.candidate)
    run_dir = _prepare_remote_run(gov, decl)
    steps = _kickoff_steps(gov)
    _show(f"---- 验收② 审计开工稿「验证卡运行证据步骤」(审计 run 在目标主机 {HOST})----\n{steps}")
    lines = _exec_lines(steps)
    assert len(lines) == 2 and all(f"| ssh {ALIAS} " in ln and "python3 - run-cmd" in ln and str(run_dir) in ln for ln in lines)
    assert "不要自己给卡面命令加 ssh 前缀" in steps and "CMD_ENTRY" in steps and "evidence-fetch" in steps
    dispatch = _dispatch_steps(gov)
    assert all(ln in dispatch for ln in lines)  # 派审注入(审计卡取证锚点)同一渲染
    entries = _run_exec_lines(lines)
    _show(f"[验收② CMD_ENTRY 原样作报告条目] {json.dumps(entries, ensure_ascii=False)}")
    assert [e["exit_code"] for e in entries] == [0, 0] and all(e["log_path"].startswith(f"{run_dir}/logs/") for e in entries)
    record = (run_dir / ".lybra-vr" / "evidence-commands.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(record) == 2 and json.loads(record[1])["command"] == entries[1]["command"]
    before = _check(gov, VALR, VAL, xhost.candidate, entries)
    _show("[验收② 取回前·裁决入口拒(无副本, 出口 = 清单 + 取回)]\n  " + before["reasons"][0][:400])
    assert "审计报告落点无取回副本" in before["reasons"][0]
    assert _manifest(gov, decl).returncode == 0
    rc, out, err = _fetch(gov, decl, run_id)
    _show(f"[验收② evidence-fetch] rc={rc}\n{out}{err}")
    assert rc == 0, out + err
    got = _check(gov, VALR, VAL, xhost.candidate, entries)
    _show(f"[验收② 按唯一写法补做 → 裁决入口] {got}")
    assert got is None
    # 远端 run 的 run-long 引用(R3 写法: run-long 在治理根所在机执行)= 拒并给出口
    from test_aipos_f241_runtime_evidence_resume import _run_long_dir

    long_dir = _run_long_dir(gov / "task_cards" / VALR / "run_long", "audit-cmd1", entries[0]["command"], str(gov))
    bad = _check(gov, VALR, VAL, xhost.candidate, [{"run_long": str(long_dir), "run_id": run_id, "run_dir": str(run_dir)}, entries[1]])
    _show("[验收② 远端 run 用 run-long 引用 → 拒]\n  " + "\n  ".join(bad["reasons"][:2]))
    assert "run-long 的工作目录与日志在治理根所在机" in bad["reasons"][0] and "helper run-cmd" in bad["reasons"][0]


def test_acceptance2_local_run_renders_run_long_line(vrig):  # noqa: F811
    gov, base = vrig.gov, vrig.base
    _validation_card(vrig, queue="claimed", claimed_by=EXEC, audit_runtime_evidence={"required": True, "commands": [CMD_FILE]})
    _claimed_audit_card(gov)
    init_governance_repo(gov)
    decl = _declare(gov, base, VALR, AUDITOR)
    run_id = _run_start(gov, decl, vrig.candidate)
    steps = _kickoff_steps(gov)
    _show(f"---- 验收② 审计 run 在治理根所在机 · 开工稿 ----\n{steps}")
    run_dir = vr.run_directory(_entry(gov, decl), run_id)
    lines = _exec_lines(steps)
    assert len(lines) == 1 and f"run-long start --task-id {VALR} --name {run_id}-cmd-1 --cwd {run_dir} -- " in lines[0]
    assert "run-cmd" not in steps and '"run_long": "<该运行目录>"' in steps


# ===========================================================================
# 验收③: 出卡预演拦截(判据唯一 artifact_ingest.cross_host_evidence_problems)
# ===========================================================================

def _hblx160_commands(exec_run: Path) -> list[str]:
    return [f"ssh {ALIAS} 'cat {exec_run}/src/app/feature.txt'",
            f"ssh {ALIAS} 'for k in {KEYS}; do grep -q \"$k\" {exec_run}/src/app/feature.txt && echo \"$k non_empty\" || echo \"$k EMPTY\"; done'"]


def _draft_meta(task_id: str, candidate: str, evidence: dict) -> dict:
    return {"task_id": task_id, "title": "跨机验证卡", "project": "lybra", "context_bundle": "t", "task_mode": "validation", "priority": "high",
            "status": "pending", "created_by": DRIVER, "needs_owner": False, "output_target": f"task_cards/{task_id}/artifacts/",
            "artifact_policy": "formal_write", "validation_source_task": SRC, "validation_candidate": candidate, "audit_runtime_evidence": evidence}


def test_acceptance3_draft_refuses_ssh_prefix_and_foreign_run_path(xhost):
    from tools.aipos_cli.artifact_ingest import check_runtime_evidence_declaration
    from tools.aipos_cli.draft_writer import create_draft

    gov = xhost.gov
    _validation_card(xhost, audit_runtime_evidence={"required": True, "commands": [CMD_FILE], "host": HOST})  # 已发布的他卡(执行 run 归属)
    _arm(xhost)
    bad = {"required": True, "commands": _hblx160_commands(xhost.exec_run)}
    res = create_draft(gov, _draft_meta("PROBE-F289-V2", xhost.candidate, bad), "## Goal\n\n跨机验证\n", dry_run=True)
    reasons = [r for r in res["blocking_reasons"] if r.startswith("RUNTIME_EVIDENCE_DECLARATION_INVALID")]
    _show(f"---- 验收③ 出卡预演(HBLX-160 形态)verdict={res['verdict']} ----\n" + "\n".join(reasons))
    assert res["verdict"] == "BLOCK"
    assert sum("以 `ssh " + ALIAS + "` 开头" in r for r in reasons) == 2 and sum("写死了 run 目录" in r for r in reasons) == 2
    assert any(f"{xhost.exec_decl}: 卡 {VAL} 的 run" in r for r in reasons)
    rewrite = next(r for r in reasons if "改写建议" in r)
    assert f'"host": "{HOST}"' in rewrite and '"cat {src_dir}/app/feature.txt"' in rewrite and "ssh " not in rewrite.split(" = ", 1)[1]
    good = {"required": True, "commands": [CMD_FILE, CMD_KEYS], "host": HOST}
    res = create_draft(gov, _draft_meta("PROBE-F289-V3", xhost.candidate, good), "## Goal\n\n跨机验证\n", dry_run=True)
    _show(f"[验收③ 合法占位写法] verdict={res['verdict']} blocking={res['blocking_reasons']}")
    assert not [r for r in res["blocking_reasons"] if "RUNTIME_EVIDENCE" in r]
    # 补审派发同一判据(被审卡 = 固定候选, subject_frontmatter)
    checked = check_runtime_evidence_declaration({"required": True, "commands": [f"ssh {HOST} 'cat {xhost.exec_run}/src/x'"]}, governance_root=gov,
                                                 audit_instance=AUDITOR, card_task_id=VAL, subject_frontmatter={"task_mode": "validation"})
    _show("[验收③ 补审派发同判据]\n  " + "\n  ".join(checked["problems"]))
    assert any("以 `ssh " + HOST + "` 开头" in p for p in checked["problems"])
    # 卡分支主体(code 卡)不受影响: ssh 他用照旧
    checked = check_runtime_evidence_declaration({"required": True, "commands": [f"ssh {ALIAS} 'true'"]}, governance_root=gov,
                                                 audit_instance=None, card_frontmatter={"task_mode": "code"})
    assert not [p for p in checked["problems"] if "DECLARATION_INVALID" in p]


# ===========================================================================
# 验收④: 在途卡出路(受限 amend 改写 → 审计按新声明可过; exhausted 的 loop status 直接给出口)
# ===========================================================================

def test_acceptance4_claimed_card_rewrite_amend_then_audit_passes(xhost):
    from tools.aipos_cli.artifact_ingest import evidence_declaration_problem

    gov = xhost.gov
    bad = {"required": True, "commands": _hblx160_commands(xhost.exec_run), "host": HOST}
    _validation_card(xhost, queue="claimed", claimed_by=EXEC, audit_runtime_evidence=bad)
    _claimed_audit_card(gov)
    _arm(xhost)
    decl = xhost.decl
    audit_fm = nr._read_frontmatter(nr._find_task_in_queue(gov, VALR)[0])
    problem = evidence_declaration_problem(gov, VALR, audit_fm)
    _show(f"---- 验收④ 推导核 / 裁决入口: 声明错(不拉起审计体)----\n{problem['line']}")
    assert problem["category"] == "RUNTIME_EVIDENCE_DECLARATION_INVALID" and "--restricted" in problem["amend"]
    assert f"queue supersede --task-id {VAL} --by <新卡号>" in problem["outlet"]
    # loop 以 runtime_evidence_exhausted 结束的存量运行: loop status 下一动作直接给同一出口
    _run_record(gov, VAL, status="ended", pid=999999, fingerprint=None)
    rc, status, err = _cli_list(["loop", "status", "--task-id", VAL, "--workspace-root", str(gov)])
    hint = [ln for ln in status.splitlines() if "跨机写法规则" in ln]
    _show("---- 验收④ loop status(runtime_evidence_exhausted)----\n" + "\n".join(hint))
    assert hint and "queue amend --task-id " + VAL + " --restricted" in hint[0]
    # 照出口里的 amend 命令(去 --dry-run)执行 = 受限 amend 跨机改写等价放行
    value = problem["cross_host"]["value"]
    _show(f"[验收④ 改写后声明] {json.dumps(value, ensure_ascii=False)}")
    assert value["commands"] == [CMD_FILE, CMD_KEYS] and value["host"] == HOST
    rc, out, err = _cli("queue", "--workspace-root", gov, "amend", "--task-id", VAL, "--actor", DRIVER, "--restricted", "--amendment-reason", REASON,
                        "--amendments", json.dumps({"audit_runtime_evidence": value}, ensure_ascii=False))
    _show(f"[验收④ queue amend --restricted(跨机改写)] rc={rc}\n{out[:600]}{err}")
    assert rc == 0, out + err
    # 非等价改动(改命令其余部分)照旧拒
    worse = {**value, "commands": [CMD_FILE + " | head -1", CMD_KEYS]}
    rc, out, err = _cli("queue", "--workspace-root", gov, "amend", "--task-id", VAL, "--actor", DRIVER, "--restricted", "--amendment-reason", REASON,
                        "--amendments", json.dumps({"audit_runtime_evidence": worse}, ensure_ascii=False), "--dry-run")
    assert rc != 0 and "跨机写法改写等价" in out + err
    audit_fm = nr._read_frontmatter(nr._find_task_in_queue(gov, VALR)[0])
    assert evidence_declaration_problem(gov, VALR, audit_fm) is None
    # 审计按新声明: 开工稿执行行 → 清单 → 取回 → 裁决入口通过
    run_id = _run_start(gov, decl, xhost.candidate)
    _prepare_remote_run(gov, decl)
    entries = _run_exec_lines(_exec_lines(_kickoff_steps(gov)))
    assert _manifest(gov, decl).returncode == 0 and _fetch(gov, decl, run_id)[0] == 0
    got = _check(gov, VALR, VAL, xhost.candidate, entries)
    _show(f"[验收④ 新声明下审计补做 → 裁决入口] {got}")
    assert got is None


# ===========================================================================
# 验收⑥ + 单源
# ===========================================================================

def test_acceptance6_en_texts_zero_cjk_and_single_source():
    from tools.aipos_cli.artifact_ingest import cross_host_declaration
    from tools.aipos_cli.i18n import has_cjk

    texts = cross_host_declaration()["texts"]
    cjk = [k for k, v in texts["en"].items() if has_cjk(v)]
    _show(f"[验收⑥ cross_host en 文案 {len(texts['en'])} 条, 含 CJK: {cjk}]")
    assert cjk == [] and set(texts["en"]) == set(texts["zh"])
    src = "\n".join(p.read_text(encoding="utf-8") for p in (REPO_ROOT / "tools").rglob("*.py") if "tests" not in p.parts)
    for name in ("def redact_progress(", "def cross_host_evidence_problems(", "def cross_host_rewrite(", "def evidence_exec_command(",
                 "def _credential_matches(", "def credential_redact("):
        assert src.count(name) == 1, name
    # 合并后预载闭包不因凭据判据迁出而膨胀(harness_launch 不导入 artifact_ingest)
    from tools.aipos_cli import finalize as fz

    closure = fz._post_merge_import_closure()
    assert "tools.aipos_cli.credential_guard" in closure and "tools.aipos_cli.artifact_ingest" not in closure
