"""AIPOS-F151 — 接入族两阶段与码不出进程(gap #106 / #92 / #107 / #121)。

件① `lybra roles enroll-code` 两阶段: 缺省预演(门动词 lybra_enroll_code_dry_run, 显示将签的角色 / 实例 / 治理根 / 有效期, 不出码),
    --confirm 才 dry_run → lybra_enroll_code_confirm 出码。两阶段旗标 = verbs.schema two_phase_protocol.roles_writers, 注册与
    project_json_writers 同一包装 aipos_cli._project_json_two_phase_flags(family=…)。
件② `lybra roles enroll-deliver`(唯一实现 enroll_deliver.enroll_deliver): 签码 + 接入一体, 码只在本进程内(同机直调 enroll_client.enroll;
    跨机经 ssh stdin, 见 test_aipos_f113); --governance-root 必给; 缺省预演零写入。向导 / 文档改用此命令, 不再示例 `--code <码>`。
件③ enroll 预检: 换码前按码内签发声明(enrollment.SELF_CONTAINED_DECLARED_KEYS)判覆盖信封(唯一判定 workstation_wiring.
    enroll_envelope_requirement), 推导不出即拒且不消耗码、不改凭据; 旧码(无声明)换码后复核也先于写 connection.json。
件④ `lybra roles register / remove` 两阶段: 缺省预演(将建 / 删的 token 引用与角色类), --confirm 才写。

靶场: 临时 home + 已建项目(夹具自签 owner 凭据, 覆盖 executor 的信封) + 本检出起的临时门子进程(随机端口; 构件唯一来源
tools/test_aipos_r2_enroll.py); 不触生产门 / 真实治理根 / 真实工位。注册码与凭据只断言「不出现」, 输出只打印指纹。
"""
from __future__ import annotations

import ast
import contextlib
import hashlib
import io
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from tools.test_aipos_r2_enroll import _free_port, _seed_project, _start_gate, _stop_gate  # noqa: E402  — 临时门靶场构件唯一来源

INSTANCE = "exec.proj-r2.f151host"
CODE_RE = re.compile(r"LYBRAENROLL1\.[A-Za-z0-9_-]+")
PREVIEW = "预览(未执行; 加 --confirm 执行)"


def _show(line: str) -> None:
    print(CODE_RE.sub("LYBRAENROLL1.<redacted>", line), flush=True)


def _cli(*argv: str) -> tuple[int, str, str]:
    from tools.aipos_cli.aipos_cli import main

    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        try:
            rc = main(list(argv))
        except SystemExit as exc:
            rc = exc.code if isinstance(exc.code, int) else 1
    return int(rc or 0), out.getvalue(), err.getvalue()


def _md5(path: Path) -> str:
    return hashlib.md5(path.read_bytes()).hexdigest() if path.exists() else "(absent)"


def _registry_snapshot(home: Path) -> dict[str, str]:
    """门注册码库(各工作区 .lybra/enrollments.json)全文快照: 预演前后相等 = 未签码。"""
    return {str(p): p.read_text(encoding="utf-8") for p in sorted(home.rglob("enrollments.json"))}


@pytest.fixture
def gate_range(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    home = tmp_path / "home"
    user_home = tmp_path / "user-home"
    user_home.mkdir()
    port = _free_port()
    gate_url = f"http://127.0.0.1:{port}"
    project, owner_token = _seed_project(home, gate_url)
    env = {k: v for k, v in os.environ.items() if not k.startswith(("LYBRA_", "AIPOS_"))}
    env.update({"PYTHONPATH": str(REPO_ROOT), "HOME": str(user_home), "LYBRA_HOME_ROOT": str(home)})
    monkeypatch.setenv("HOME", str(user_home))
    monkeypatch.setenv("LYBRA_HOME_ROOT", str(home))
    monkeypatch.delenv("LYBRA_GATE_URL", raising=False)
    gate = _start_gate(home, port, env, tmp_path / "gate.log")
    try:
        yield type("Range", (), {"tmp": tmp_path, "home": home, "project": project, "owner_token": owner_token, "gate_url": gate_url})
    finally:
        _stop_gate(gate)


# ===========================================================================
# 件① enroll-code: 缺省预演不出码, --confirm 才出码
# ===========================================================================

def test_item1_enroll_code_preview_issues_nothing_confirm_issues(gate_range):
    r = gate_range
    base = ("roles", "--workspace-root", str(r.project), "enroll-code", "--role", "executor", "--instance", INSTANCE,
            "--governance-root", str(r.project), "--gate-url", r.gate_url, "--ttl", "3600", "--owner-authorization-ref", "f151-test")
    before = _registry_snapshot(r.home)
    for flags in ([], ["--dry-run"]):
        rc, out, err = _cli(*base, *flags)
        _show(f"[件①·enroll-code {' '.join(flags) or '(缺省)'}] rc={rc}\n{out}{err}")
        assert rc == 0 and f"roles enroll-code executor: {PREVIEW}" in out, out + err
        assert f"Instance: {INSTANCE}" in out and f"Governance root: {r.project}" in out and "有效期: 3600 秒" in out
        assert not CODE_RE.search(out + err), "预演不得出码"
        assert _registry_snapshot(r.home) == before, "预演零写入: 门注册码库未新增记录"
    rc, out, err = _cli(*base, "--json")
    payload = json.loads(out)
    assert rc == 0 and payload["dry_run"] is True and payload["issued"] is False and "self_contained_code" not in payload
    assert payload["preview"]["role"] == "executor" and payload["preview"]["ttl_seconds"] == 3600
    rc, out, err = _cli(*base, "--confirm", "--json")
    payload = json.loads(out)
    _show(f"[件①·enroll-code --confirm] rc={rc} code_id={payload.get('code_id')} fingerprint={payload.get('fingerprint')}")
    assert rc == 0 and payload["phase"] == "confirm" and CODE_RE.fullmatch(payload["self_contained_code"])
    after = _registry_snapshot(r.home)
    assert after != before and payload["code_id"] in "".join(after.values()), "--confirm 才在门注册码库落码记录"
    # 件③ 码内签发声明(非秘密): 兑换方换码前据此预检
    from tools.aipos_cli.enrollment import decode_self_contained_code

    decl = decode_self_contained_code(payload["self_contained_code"])
    _show(f"[件①·码内签发声明] role={decl['role']} instance={decl['instance']} role_class={decl['role_class']}")
    assert (decl["role"], decl["instance"], decl["role_class"]) == ("executor", INSTANCE, "executor")
    rc, _out, err = _cli(*base, "--dry-run", "--confirm")
    assert rc == 2 and "not allowed with argument" in err


# ===========================================================================
# 件② enroll-deliver 同机全流程: 码不出现于输出 / argv(ps)/ 文件 / 审计日志
# ===========================================================================

def _proc_cmdlines() -> list[str]:
    """ps 等价: /proc/*/cmdline(本 pid 命名空间内全部进程的 argv)。"""
    lines: list[str] = []
    for entry in Path("/proc").iterdir():
        if entry.name.isdigit():
            try:
                lines.append((entry / "cmdline").read_bytes().replace(b"\0", b" ").decode("utf-8", "replace"))
            except OSError:
                continue
    return lines


def test_item2_enroll_deliver_same_machine_code_never_leaves_process(gate_range, monkeypatch):
    from tools.aipos_cli import enroll_client
    from tools.aipos_cli.enrollment import decode_self_contained_code, enrollment_trail_path

    r = gate_range
    ws = r.tmp / "ws-exec"
    base = ("roles", "--workspace-root", str(r.project), "enroll-deliver", "--role", "executor", "--instance", INSTANCE,
            "--governance-root", str(r.project), "--workspace", str(ws), "--gate-url", r.gate_url, "--ttl", "3600",
            "--owner-authorization-ref", "pol_r2_exec_1")
    before = _registry_snapshot(r.home)
    rc, out, err = _cli(*base)
    _show(f"[件②·enroll-deliver 预演] rc={rc}\n{out}{err}")
    assert rc == 0 and f"roles enroll-deliver executor: {PREVIEW}" in out and "覆盖信封 pol_r2_exec_1" in out
    assert not ws.exists() and _registry_snapshot(r.home) == before, "预演零写入、不签码"

    seen: dict[str, object] = {}
    real_enroll = enroll_client.enroll

    def spy_enroll(**kwargs):  # 码在本进程内交给 enroll 的那一刻: 全部进程 argv 中不得有码
        code = kwargs["code"]
        inner = decode_self_contained_code(code)["code"]
        procs = _proc_cmdlines()
        ps = shutil.which("ps")
        ps_out = subprocess.run([ps, "-eo", "args"], capture_output=True, text=True).stdout if ps else ""
        seen.update(code=code, inner=inner, procs=len(procs), ps_lines=len(ps_out.splitlines()),
                    hit=any(code in p or inner in p for p in procs) or code in ps_out or inner in ps_out)
        return real_enroll(**kwargs)

    spawned: list[object] = []
    real_popen = subprocess.Popen

    class RecordingPopen(real_popen):  # 本进程派生的每个子进程 argv(含 git / ps)
        def __init__(self, args, *a, **k):
            spawned.append(args)
            super().__init__(args, *a, **k)

    monkeypatch.setattr(enroll_client, "enroll", spy_enroll)
    monkeypatch.setattr(subprocess, "Popen", RecordingPopen)
    rc, out, err = _cli(*base, "--confirm", "--json")
    monkeypatch.setattr(subprocess, "Popen", real_popen)
    _show(f"[件②·enroll-deliver --confirm] rc={rc}\n{out}{err}")
    assert rc == 0, err
    code, inner = str(seen["code"]), str(seen["inner"])
    result = json.loads(out)
    _show(f"[件②·ps 断言] 码交给 enroll 时扫描 /proc 进程 {seen['procs']} 个、ps 输出 {seen['ps_lines']} 行: 含码={seen['hit']}; "
          f"本进程派生子进程 {len(spawned)} 个")
    assert seen["hit"] is False and seen["procs"] > 0
    assert all(code not in str(a) and inner not in str(a) for a in spawned), "码进了子进程 argv"
    assert code not in out + err and inner not in out + err and not CODE_RE.search(out + err), "码出现在输出里"
    assert result["phase"] == "confirm" and result["enrollment"]["code_id"] and result["enroll"]["landed"] is True
    assert result["enroll"]["verify"]["ok"] is True and result["envelope"]["policy_id"] == "pol_r2_exec_1"
    # 落盘: 工位凭据 + role(owner_policy_ref 由信封推导)
    conn = json.loads((ws / ".lybra" / "connection.json").read_text(encoding="utf-8"))
    role = json.loads((ws / ".lybra" / "role").read_text(encoding="utf-8"))
    assert [t["agent_instance"] for t in conn["tokens"] if not t.get("retired")] == [INSTANCE]
    assert role["instance"] == INSTANCE and role["owner_policy_ref"] == "pol_r2_exec_1"
    # 文件: 自包含码不在任何文件; 内层码只在门注册码库(enrollments.json, 门的码真相)
    holders_sc, holders_inner = [], []
    for path in r.tmp.rglob("*"):
        if path.is_file() and not path.is_symlink():
            data = path.read_bytes()
            if code.encode() in data:
                holders_sc.append(str(path))
            if inner.encode() in data:
                holders_inner.append(str(path))
    _show(f"[件②·文件扫描] 含自包含码: {holders_sc or '无'}; 含内层码: {[Path(p).relative_to(r.tmp).as_posix() for p in holders_inner]}")
    assert holders_sc == [] and holders_inner and all(p.endswith("/.lybra/enrollments.json") for p in holders_inner)
    # 审计日志: 本项目 enrollment_log 有 create/use/land(code_id 对得上), 无码原文
    trail = enrollment_trail_path(r.project).read_text(encoding="utf-8")
    events = [ln.split()[2] for ln in trail.splitlines() if ln.startswith("- ") and f"instance={INSTANCE}" in ln]
    _show(f"[件②·审计日志 {enrollment_trail_path(r.project).name}] {INSTANCE} 事件: {events}")
    assert events.count("create") == 1 and events.count("use") == 1 and events.count("land") == 1
    assert result["enrollment"]["code_id"] in trail and code not in trail and inner not in trail


# ===========================================================================
# 件③ 覆盖信封预检: 推导不出即拒, 码不消耗、凭据不改
# ===========================================================================

def _gov_without_envelope(tmp: Path) -> Path:
    gov = tmp / "home" / "nopol"
    (gov / "5_tasks" / "queue" / "pending").mkdir(parents=True)
    (gov / "project.json").write_text(json.dumps({"project": "nopol", "config_version": 1}), encoding="utf-8")
    (gov / ".lybra").mkdir()
    return gov


def _workstation_with_creds(tmp: Path) -> Path:
    ws = tmp / "ws-existing"
    (ws / ".lybra").mkdir(parents=True)
    (ws / ".lybra" / "connection.json").write_text(json.dumps({"config_version": 1, "tokens": [
        {"role": "executor", "agent_instance": "exec.nopol.prior", "token": "prior-fixture-not-a-secret", "fingerprint": "fp-prior"}]}),
        encoding="utf-8")
    return ws


def test_item3_precheck_refuses_before_exchange_code_not_consumed(tmp_path, monkeypatch):
    from tools.aipos_cli import enroll_client
    from tools.aipos_cli.enrollment import _load_enrollments, issue_self_contained_code

    gov = _gov_without_envelope(tmp_path)
    issued = issue_self_contained_code(gov, role="executor", instance="exec.nopol.f151host", gate_url="http://127.0.0.1:1",
                                       governance_root=str(gov), by="dec_f151", reason="F151 precheck fixture")
    ws = _workstation_with_creds(tmp_path)
    before = (_md5(ws / ".lybra" / "connection.json"), sorted(p.name for p in (ws / ".lybra").iterdir()))

    def must_not_exchange(*_a, **_k):
        raise AssertionError("预检拒之前不得调兑换(码不消耗)")

    monkeypatch.setattr(enroll_client, "exchange_enrollment_code", must_not_exchange)
    monkeypatch.setattr(enroll_client, "land_enrollment_code", must_not_exchange)
    with pytest.raises(RuntimeError) as exc:
        enroll_client.enroll(code=issued["self_contained_code"], gate_url="", workspace_root=ws)
    msg = str(exc.value)
    _show(f"[件③·换码前预检拒]\n{msg}")
    assert msg.startswith("ENROLL_ENVELOPE_MISSING") and "未换码(注册码仍可用)、未改凭据" in msg
    assert "lybra envelope mint --workspace-root" in msg and "--agent-or-role exec.nopol.f151host" in msg and "--dry-run" in msg
    record = _load_enrollments(gov)[issued["code_id"]]
    after = (_md5(ws / ".lybra" / "connection.json"), sorted(p.name for p in (ws / ".lybra").iterdir()))
    _show(f"[件③·码状态] {issued['code_id']} status={record['status']} used_at={record['used_at']}; 凭据文件 md5 前后 {before[0]} / {after[0]}")
    assert record["status"] == "pending" and record["used_at"] is None
    assert after == before and not (ws / ".lybra" / "role").exists()


def test_item3_legacy_code_post_exchange_check_precedes_credential_write(tmp_path, monkeypatch):
    """旧码(无签发声明)无法换码前预检: 换码后复核先于写 connection.json(原「先写凭据后推导」= 半成品)。"""
    from tools.aipos_cli import enroll_client
    from tools.aipos_cli.enrollment import encode_self_contained_code

    gov = _gov_without_envelope(tmp_path)
    ws = _workstation_with_creds(tmp_path)
    before = _md5(ws / ".lybra" / "connection.json")
    legacy = encode_self_contained_code(gate_url="http://127.0.0.1:1", governance_root=str(gov), transport_token="stub", code="LEGACYINNER")
    exchanged = {"ok": True, "token_entry": {"role": "executor", "agent_instance": "exec.nopol.legacy", "token": "new-fixture-not-a-secret",
                                             "fingerprint": "fp-new", "scopes": ["executor"], "role_class": "executor"}}
    monkeypatch.setattr(enroll_client, "exchange_enrollment_code", lambda *a, **k: exchanged)

    def must_not_land(*_a, **_k):
        raise AssertionError("拒之前不得 land")

    monkeypatch.setattr(enroll_client, "land_enrollment_code", must_not_land)
    with pytest.raises(RuntimeError) as exc:
        enroll_client.enroll(code=legacy, gate_url="", workspace_root=ws)
    msg = str(exc.value)
    _show(f"[件③·旧码换码后复核拒]\n{msg}")
    assert msg.startswith("ENROLL_ENVELOPE_MISSING") and "码已兑换但未落地(grace 窗口内同码可重试)、未改凭据" in msg
    assert _md5(ws / ".lybra" / "connection.json") == before and not (ws / ".lybra" / "role").exists()


def test_item3_enroll_deliver_refuses_before_issuing(gate_range):
    r = gate_range
    ws = r.tmp / "ws-audit"
    before = _registry_snapshot(r.home)
    for flags in ([], ["--confirm"]):
        rc, out, err = _cli("roles", "--workspace-root", str(r.project), "enroll-deliver", "--role", "auditor", "--instance",
                            "audit.proj-r2.f151host", "--governance-root", str(r.project), "--workspace", str(ws),
                            "--owner-authorization-ref", "f151-test", *flags)
        _show(f"[件③·enroll-deliver 无覆盖信封 {' '.join(flags) or '(缺省)'}] rc={rc}\n{err}")
        assert rc == 1 and "ENROLL_ENVELOPE_MISSING" in err and "未签码、未改凭据" in err
    assert _registry_snapshot(r.home) == before and not ws.exists()


def test_item3_requirement_not_for_governance_seat_or_subagent(tmp_path):
    from tools.aipos_cli.workstation_wiring import enroll_envelope_requirement

    gov = _gov_without_envelope(tmp_path)
    seat = enroll_envelope_requirement(gov, role="advisor", role_class="advisor", agent_instance="advisor.nopol.h")
    sub = enroll_envelope_requirement(gov, role="executor", role_class="executor", agent_instance="exec.nopol.h", executor_mode="subagent")
    work = enroll_envelope_requirement(gov, role="executor", role_class="executor", agent_instance="exec.nopol.h")
    _show(f"[件③·判定] advisor={seat['required']}/{bool(seat['refusal'])} subagent={sub['required']}/{bool(sub['refusal'])} "
          f"workstation={work['required']}/{bool(work['refusal'])}")
    assert (seat["required"], seat["refusal"]) == (False, None) and (sub["required"], sub["refusal"]) == (False, None)
    assert work["required"] is True and work["refusal"].startswith("ENROLL_ENVELOPE_MISSING")


# ===========================================================================
# 件④ roles register / remove: 缺省预演零写入, --confirm 才写
# ===========================================================================

def test_item4_register_remove_preview_zero_write(tmp_path, monkeypatch):
    from tools.aipos_cli.custom_roles import register_custom_role

    monkeypatch.setenv("HOME", str(tmp_path / "user-home"))
    gov = tmp_path / "home" / "rolesprobe"
    (gov / "5_tasks" / "queue" / "pending").mkdir(parents=True)
    (gov / "project.json").write_text(json.dumps({"project": "rolesprobe", "config_version": 1}), encoding="utf-8")
    (gov / ".lybra").mkdir()
    conn = gov / ".lybra" / "connection.json"
    conn.write_text(json.dumps({"config_version": 1, "workspace_root": str(gov), "tokens": [
        {"role": "executor", "agent_instance": "exec.rolesprobe.h", "token": "exec-fixture-not-a-secret", "token_ref": "svc-executor",
         "fingerprint": "sha256:execfp"}]}), encoding="utf-8")
    log = gov / "governance" / "custom_roles_log.md"

    def files() -> list[str]:
        return sorted(str(p.relative_to(gov)) for p in gov.rglob("*") if p.is_file())

    snap = (_md5(conn), files())
    rc, out, err = _cli("roles", "--workspace-root", str(gov), "register", "hbj-coder", "--class", "executor",
                        "--owner-authorization-ref", "dec_f151")
    _show(f"[件④·register 预演] rc={rc}\n{out}{err}")
    assert rc == 0 and f"roles register hbj-coder: {PREVIEW}" in out and "新建 token 引用 svc-hbj-coder" in out and "类 executor" in out
    assert (_md5(conn), files()) == snap and not log.exists()
    rc, out, _err = _cli("roles", "--workspace-root", str(gov), "register", "hbj-coder", "--class", "executor",
                         "--owner-authorization-ref", "dec_f151", "--json")
    payload = json.loads(out)
    assert payload["dry_run"] is True and payload["written"] is False and payload["plan"]["action"] == "create"
    assert "token" not in json.dumps(payload["plan"]).replace("token_ref", "")

    register_custom_role(gov, "hbj-coder", "executor", by="dec_f151")  # 写入口(门动词 lybra_roles_register 所调的同一实现)
    fp = next(t["fingerprint"] for t in json.loads(conn.read_text(encoding="utf-8"))["tokens"] if t["role"] == "hbj-coder")
    snap = (_md5(conn), files())
    rc, out, err = _cli("roles", "--workspace-root", str(gov), "remove", "hbj-coder", "--owner-authorization-ref", "dec_f151")
    _show(f"[件④·remove 预演] rc={rc}\n{out}{err}")
    assert rc == 0 and f"roles remove hbj-coder: {PREVIEW}" in out and f"删 token 引用 svc-hbj-coder(指纹 {fp})" in out
    assert "类 executor" in out and (_md5(conn), files()) == snap

    rc, out, err = _cli("roles", "--workspace-root", str(gov), "remove", "--instance", "exec.rolesprobe.h",
                        "--owner-authorization-ref", "dec_f151")
    _show(f"[件④·remove --instance 预演] rc={rc}\n{out}{err}")
    assert rc == 0 and "删 token 引用 svc-executor(指纹 sha256:execfp) 角色 executor 类 executor" in out
    assert (_md5(conn), files()) == snap, "预演零写入(不备份、不写删除记录)"

    rc, out, err = _cli("roles", "--workspace-root", str(gov), "remove", "hbj-coder", "--owner-authorization-ref", "dec_f151", "--confirm")
    _show(f"[件④·remove --confirm] rc={rc}\n{out}{err}")
    assert rc == 0 and "Removed custom role 'hbj-coder'" in out
    assert all(t["role"] != "hbj-coder" for t in json.loads(conn.read_text(encoding="utf-8"))["tokens"])
    for argv in (("register", "x-role", "--class", "executor"), ("remove", "x-role"), ("enroll-code", "--role", "executor")):
        rc, _out, err = _cli("roles", *argv, "--dry-run", "--confirm")
        assert rc == 2 and "not allowed with argument" in err, argv


# ===========================================================================
# ② 向导 / 文档无 `--code <码>` 兑换示例(gap #92)
# ===========================================================================

def test_item2_wizard_and_docs_have_no_code_on_command_line(tmp_path):
    from tools.aipos_cli.onboarding import format_guide_text, generate_onboarding_guide

    shapes = {
        "default": dict(project_name="probe-f151"),
        "codex-subagent": dict(project_name="probe-f151", advisor_harness="codex", advisor_host="mac-probe", executor_mode="subagent"),
        "chris-shape": dict(project_name="chris-x", home_root=str(tmp_path / "h o"), advisor_dir="~/chris session",
                            workspace_dir="~/chris exec", auditor_dir="~/chris audit"),
    }
    for name, kw in shapes.items():
        guide = generate_onboarding_guide(**kw)
        text = format_guide_text(guide) + json.dumps(guide, ensure_ascii=False)
        delivers = [ln for st in guide["steps"] for ln in str(st["command"]).splitlines() if "enroll-deliver" in ln and not ln.startswith("#")]
        _show(f"[②·向导 {name}] enroll-deliver 行 {len(delivers)} 条; '--code' 出现 {text.count('--code')} 次; 'enroll-code' 出现 {text.count('enroll-code')} 次")
        assert "--code" not in text and "enroll-code" not in text and len(delivers) == 6
    pattern = r"roles enroll --code|--code '|--code \"|--code <"
    hits = subprocess.run(["git", "grep", "-nE", pattern, "--", "docs", "tools/aipos_cli/README.md"], cwd=REPO_ROOT,
                          capture_output=True, text=True)
    _show(f"[②·文档] git grep -nE {shlex.quote(pattern)} -- docs tools/aipos_cli/README.md → rc={hits.returncode} 命中={hits.stdout.strip() or 0}")
    assert hits.returncode == 1 and hits.stdout == ""
    outside = subprocess.run(["git", "grep", "-nE", pattern, "--", "agents", "tools/AIPOS-R2-ENROLL-GUIDE.md"], cwd=REPO_ROOT,
                             capture_output=True, text=True).stdout.strip().splitlines()
    _show(f"[②·车道外(本卡不改, 只报告)] {len(outside)} 处: " + " | ".join(outside))


# ===========================================================================
# 不变量(防回潮)
# ===========================================================================

def test_invariants_single_implementations():
    from tools.schema_loader import load_schema

    cli_src = (REPO_ROOT / "tools/aipos_cli/aipos_cli.py").read_text(encoding="utf-8")
    tree = ast.parse(cli_src)
    roles_flag_calls = {n.args[1].value for n in ast.walk(tree) if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
                        and n.func.id == "_project_json_two_phase_flags"
                        and any(k.arg == "family" and getattr(k.value, "value", None) == "roles_writers" for k in n.keywords)}
    declared = load_schema("verbs")["two_phase_protocol"]["roles_writers"]["commands"]
    _show(f"[不变量] roles_writers 声明 {declared}; 同一包装注册 {sorted(roles_flag_calls)}")
    assert roles_flag_calls == set(declared) == {"enroll-code", "enroll-deliver", "register", "remove"}
    from tools.aipos_cli.aipos_cli import build_parser

    parser = build_parser()
    for argv in (["roles", "enroll-code", "--role", "executor"], ["roles", "register", "x", "--class", "executor"], ["roles", "remove", "x"],
                 ["roles", "enroll-deliver", "--role", "executor", "--governance-root", "g", "--workspace", "w",
                  "--owner-authorization-ref", "p"]):
        args = parser.parse_args(argv)
        assert (args.dry_run, args.confirm) == (False, False), argv  # 缺省 = 预演

    # 门签码两动词: tools/aipos_cli 内只由 enroll_deliver.gate_enroll_code 调(enroll-code 与 enroll-deliver 共用)
    callers = subprocess.run(["git", "grep", "-nE", r"call_tool\((\"lybra_enroll_code_|ENROLL_CODE_)", "--", "tools/aipos_cli"],
                             cwd=REPO_ROOT, capture_output=True, text=True).stdout.strip().splitlines()
    _show("[不变量] 签码门调用点:\n" + "\n".join(callers))
    assert callers and all(line.startswith("tools/aipos_cli/enroll_deliver.py:") for line in callers)
    # enroll-deliver 不再有第二份 enroll 实现(本地发码 / bootstrap 兑换 / 手写 connection.json / role / 分发)
    deliver_src = (REPO_ROOT / "tools/aipos_cli/enroll_deliver.py").read_text(encoding="utf-8")
    for banned in ("create_enrollment_code", "exchange_enrollment_code", "write_connection_json", "write_role_file",
                   "distribute_to_harness", "os.open(", "LYBRA_WORKSPACE_ROOT"):
        assert banned not in deliver_src, banned
    assert "enroll_preflight(" in deliver_src and "from tools.aipos_cli.enroll_client import enroll" in deliver_src
    # 覆盖信封判定唯一实现: enroll_client 只经 enroll_envelope_requirement(换码前 / 换码后同一函数), 不直接推导
    client_src = (REPO_ROOT / "tools/aipos_cli/enroll_client.py").read_text(encoding="utf-8")
    called = [n.func.id for n in ast.walk(ast.parse(client_src)) if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)]
    _show(f"[不变量] enroll_client 调用: enroll_envelope_requirement×{called.count('enroll_envelope_requirement')} "
          f"enroll_preflight×{called.count('enroll_preflight')} derive_effective_owner_policy_ref×{called.count('derive_effective_owner_policy_ref')}")
    assert "derive_effective_owner_policy_ref" not in called
    assert called.count("enroll_envelope_requirement") == 2 and called.count("enroll_preflight") == 1
    defs = subprocess.run(["git", "grep", "-n", "def enroll_envelope_requirement\\|def enroll_preflight", "--", "tools"], cwd=REPO_ROOT,
                          capture_output=True, text=True).stdout.strip().splitlines()
    assert [d.split(":")[0] for d in defs] == ["tools/aipos_cli/enroll_client.py", "tools/aipos_cli/workstation_wiring.py"], defs
    # 拒因码登记于声明
    codes = load_schema("verbs")["two_phase_protocol"]["roles_writers"]["reject_codes"]
    assert {"ENROLL_ENVELOPE_MISSING", "ENROLL_CODE_DECLARATION_MISMATCH"} <= set(codes)
