#!/usr/bin/env python3
"""AIPOS-F54 夹具 — 新工位一条命令配齐(enroll 落"可启动最小集")。

先红后绿(真跑两版模块, 同一把刀):
  红 = git main 版 enroll_client(修复前): 空目录 enroll 后只有 .lybra(无 .pi/无 AGENTS.md/
      role 无 owner_policy_ref/connection 无 lybra_bin/无目录创建出声)
  绿 = HEAD 版: 空目录 + 一条 enroll → 可启动最小集全部就绪

验收覆盖: ①接线红绿 ②幂等seed_only ③目录自建出声 ④401分类 ⑤第三项目 ⑨⑩policy推导
⑪sync校正 ⑭lybra_bin悬空 ⑮缺项点名 ⑯结构规格 ⑰按角色类skills ⑱无角色名硬编码。
⑧⑫(chris 两工位真机 /lybra on)与⑬端到端真门启动 = owner_verify, 本夹具只验 Python 侧前置。

跑法: python3 tests/test_aipos_f54.py (经 run-all.sh 常驻)
"""
import importlib.util
import json
import subprocess
import sys
import tempfile
import urllib.error
from pathlib import Path
from unittest import mock

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))


# ---------------------------------------------------------------------------
# 前置: 从 git main 取修复前 enroll_client(红线侧真源码)
# ---------------------------------------------------------------------------

def _prefix_rev() -> str:
    """红侧基线 = 接线引入提交的父(防时间性地雷: main 合入修复后 main: 自爆)。"""
    out = subprocess.run(
        ["git", "-C", str(REPO), "log", "main", "--diff-filter=A", "--format=%H", "-1",
         "--", "tools/aipos_cli/workstation_wiring.py"],
        capture_output=True, text=True).stdout.strip()
    return f"{out}^" if out else "main"


def _load_prefix_module():
    src = subprocess.run(
        ["git", "-C", str(REPO), "show", f"{_prefix_rev()}:tools/aipos_cli/enroll_client.py"],
        capture_output=True, text=True, check=True,
    ).stdout
    tmp = Path(tempfile.mkdtemp(prefix="f54-prefix-")) / "enroll_client_prefix.py"
    tmp.write_text(src, encoding="utf-8")
    spec = importlib.util.spec_from_file_location("enroll_client_prefix", tmp)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


PREFIX = _load_prefix_module()
from tools.aipos_cli import enroll_client as CURRENT  # noqa: E402
from tools.aipos_cli.distribution_sync import _correct_owner_policy_ref  # noqa: E402
from tools.aipos_cli.workstation_wiring import (  # noqa: E402
    LOOP_WRAPPER_TS,
    SETTINGS_TEMPLATE,
    derive_effective_owner_policy_ref,
    load_role_skills,
    minimum_bootable_set_items,
    verify_minimum_bootable_set,
)


def _stub_token_entry(role: str, instance: str, role_class: str | None = None) -> dict:
    entry = {
        "role": role,
        "agent_instance": instance,
        "token": "stub-token-value",
        "fingerprint": "sha256:stub",
        "scopes": ["queue_claim", "queue_return", "task_progress"],
    }
    if role_class:
        entry["role_class"] = role_class
    return entry


def _patch_module(mod, token_entry: dict):
    """给模块打 exchange/land 桩(不触真门)。"""
    return (
        mock.patch.object(mod, "exchange_enrollment_code", return_value={"ok": True, "token_entry": token_entry}),
        mock.patch.object(mod, "land_enrollment_code", return_value=True),
    )


def _fake_governance(tmp: Path, policy_id: str = "pol_probe_exec_1", covers: str = "executor", name: str = "gov") -> Path:
    """造治理根: 5_tasks/policies 下一份生效 PreAuthorized 信封。"""
    gov = tmp / name
    (gov / "5_tasks" / "policies").mkdir(parents=True, exist_ok=True)
    (gov / "5_tasks" / "policies" / f"{policy_id}.md").write_text(
        "---\n"
        "record_type: owner_autonomy_policy\n"
        f"policy_id: {policy_id}\n"
        "mode: PreAuthorized\n"
        "status: active\n"
        "approved_by_owner: true\n"
        "owner_approval_ref: dec_probe\n"
        "active_from: '2020-01-01T00:00:00Z'\n"
        "expires_at: '2099-01-01T00:00:00Z'\n"
        f"agent_or_role: {covers}\n"
        "task_selector_task_mode: ''\n"
        "task_selector_project: ''\n"
        "task_selector_task_ids: []\n"
        "max_tasks: 50\n"
        "---\n"
        f"# Owner Autonomy Policy: {policy_id}\n",
        encoding="utf-8",
    )
    return gov


def _self_contained_code(gov_root: Path) -> str:
    from tools.aipos_cli.enrollment import encode_self_contained_code

    return encode_self_contained_code(
        gate_url="http://127.0.0.1:7999",
        governance_root=str(gov_root),
        transport_token="stub-transport",
        code="STUBINNER",
    )


def _enroll(mod, ws: Path, gov: Path, token_entry: dict) -> dict:
    # 环境自足: 裸仓/worktree 无 .deploy 部署时补 stub bin(真仓部署存在则零动作)
    stub = REPO / ".deploy" / "current" / "bin" / "lybra"
    made = not stub.exists()
    if made:
        stub.parent.mkdir(parents=True, exist_ok=True)
        stub.write_text("#!/bin/sh\n", encoding="utf-8")
    try:
        code = _self_contained_code(gov)
        p1, p2 = _patch_module(mod, token_entry)
        with p1, p2:
            return mod.enroll(code=code, gate_url="", workspace_root=ws)
    finally:
        if made:
            stub.unlink(missing_ok=True)


def _fresh_ws(tmp: Path, name: str = "ws") -> Path:
    return tmp / name


PASS = 0


def ok(label: str, cond: bool, detail: str = ""):
    global PASS
    if not cond:
        raise AssertionError(f"[FAIL] {label}" + (f" — {detail}" if detail else ""))
    PASS += 1
    print(f"  ✓ {label}" + (f" ({detail})" if detail else ""))


# ---------------------------------------------------------------------------

def test_1_red_prefix_empty_enroll():
    """验收①红: 修复前空目录 enroll → 只有 .lybra, 无任何 pi 接线。"""
    tmp = Path(tempfile.mkdtemp(prefix="f54-red-"))
    gov = _fake_governance(tmp)
    ws = _fresh_ws(tmp)
    r = _enroll(PREFIX, ws, gov, _stub_token_entry("executor", "exec.probe.kiwiai-dev"))
    ok("红: enroll ok(修复前也能落 .lybra)", r["ok"] is True)
    ok("红: 无 .pi/ 接线", not (ws / ".pi").exists())
    ok("红: 无 AGENTS.md", not (ws / "AGENTS.md").exists())
    role = json.loads((ws / ".lybra" / "role").read_text())
    ok("红: role 无 owner_policy_ref", "owner_policy_ref" not in role)
    conn = json.loads((ws / ".lybra" / "connection.json").read_text())
    ok("红: connection 无 lybra_bin", "lybra_bin" not in conn)
    ok("红: 无目录创建出声字段", "created_workspace_dir" not in r)
    ok("红: 无最小集校验", "minimum_bootable_set" not in r)
    print(f"  [RED ls -a 修复前] {[p.name for p in ws.iterdir()]}")


def test_2_green_full_wiring():
    """验收①⑨⑮⑯绿: 空目录一条 enroll → 可启动最小集齐 + 结构逐项符合规格。"""
    tmp = Path(tempfile.mkdtemp(prefix="f54-green-"))
    gov = _fake_governance(tmp)
    ws = _fresh_ws(tmp)
    r = _enroll(CURRENT, ws, gov, _stub_token_entry("executor", "exec.probe.kiwiai-dev"))
    ok("绿: enroll ok", r["ok"] is True)
    print(f"  [GREEN ls -a 修复后] {sorted(p.name for p in ws.iterdir())}")
    ok("绿: .pi/ 就位", (ws / ".pi").is_dir())
    ok("绿: AGENTS.md 就位", (ws / "AGENTS.md").is_file())
    # ⑯ 结构规格逐项
    settings = json.loads((ws / ".pi" / "settings.json").read_text())
    ok("⑯ settings 内容=规格", settings == SETTINGS_TEMPLATE)
    ok("⑯ settings 禁 defaultModel", "defaultModel" not in settings)
    ok("⑯ settings 禁 extensions 数组", "extensions" not in settings)
    claim = ws / ".pi" / "extensions" / "claim.ts"
    ok("⑯ claim.ts 是软链", claim.is_symlink())
    ok("⑯ claim.ts 软链目标正确",
       "../../.." in Path(claim.readlink()).as_posix() and "_shared/extensions/claim.ts" in claim.readlink().as_posix(),
       claim.readlink().as_posix())
    loop = ws / ".pi" / "extensions" / "lybra-loop.ts"
    ok("⑯ lybra-loop.ts 是真实文件(非软链)", loop.is_file() and not loop.is_symlink())
    ok("⑯ lybra-loop.ts 为转发文件", "export { default }" in loop.read_text() and "_distributed/extensions/lybra-loop" in loop.read_text())
    # ⑨ owner_policy_ref
    role = json.loads((ws / ".lybra" / "role").read_text())
    ok("⑨ role 含推导的 owner_policy_ref", role.get("owner_policy_ref") == "pol_probe_exec_1", str(role))
    ok("⑨ policy_derivation 报告匹配", (r.get("policy_derivation") or {}).get("policy_id") == "pol_probe_exec_1")
    # ③ lybra_bin
    conn = json.loads((ws / ".lybra" / "connection.json").read_text())
    ok("⑭ connection 含 lybra_bin", bool(conn.get("lybra_bin")))
    ok("connection 含 governance_root", conn.get("governance_root") == str(gov))
    # ⑮ 最小集校验
    mbs = r.get("minimum_bootable_set") or {}
    ok("⑮ 可启动最小集全绿", mbs.get("ok") is True, f"missing={mbs.get('missing')}")
    ok("⑮ 校验项数=声明项数", len(mbs.get("checks") or []) == len(minimum_bootable_set_items()))
    # ⑬ 前置: 目录创建出声
    ok("③ created_workspace_dir=True 出声", r.get("created_workspace_dir") is True)


def test_3_skills_by_role_class():
    """验收⑰: executor 类与 auditor 类 skills 集合分别正确(auditor 无 finalize-slice、有 audit-independent-evidence)。"""
    tmp = Path(tempfile.mkdtemp(prefix="f54-skills-"))
    gov = _fake_governance(tmp, covers="executor")
    _fake_governance(tmp, policy_id="pol_probe_audit_1", covers="auditor", name="gov")
    ws_exec = _fresh_ws(tmp, "ws-exec")
    _enroll(CURRENT, ws_exec, gov, _stub_token_entry("executor", "exec.probe"))
    ws_audit = _fresh_ws(tmp, "ws-audit")
    _enroll(CURRENT, ws_audit, gov, _stub_token_entry("auditor", "audit.probe"))
    exec_skills = sorted(p.name for p in (ws_exec / ".pi" / "skills").iterdir())
    audit_skills = sorted(p.name for p in (ws_audit / ".pi" / "skills").iterdir())
    ok("⑰ executor 类 5 技能", exec_skills == sorted(
        ["block-and-report", "chunked-io", "finalize-slice", "task-closure-loop", "write-return"]), str(exec_skills))
    ok("⑰ auditor 类含 audit-independent-evidence", "audit-independent-evidence" in audit_skills)
    ok("⑰ auditor 类无 finalize-slice", "finalize-slice" not in audit_skills)
    ok("⑰ skills 逐项软链", all((ws_exec / ".pi" / "skills" / s).is_symlink() for s in exec_skills))
    ok("⑰ 声明源可查(roles.schema tool_package)",
       load_role_skills("executor") is not None and load_role_skills("auditor") is not None)


def test_4_custom_role_by_class():
    """验收⑤: 第三项目自定义角色(probe-xyz-coder/hbj 式)按 role_class 取 executor 集合, 项目无关。"""
    tmp = Path(tempfile.mkdtemp(prefix="f54-custom-"))
    gov = _fake_governance(tmp, covers="probe-xyz-coder")
    ws = _fresh_ws(tmp)
    r = _enroll(CURRENT, ws, gov, _stub_token_entry("probe-xyz-coder", "xyz.probe.otherproj", role_class="executor"))
    ok("⑤ 自定义角色 enroll ok", r["ok"] is True)
    ok("⑤ 接线按 role_class=executor 落齐", (ws / ".pi" / "extensions" / "lybra-loop.ts").is_file())
    skills = sorted(p.name for p in (ws / ".pi" / "skills").iterdir())
    ok("⑤ skills=executor 集合", "finalize-slice" in skills and "audit-independent-evidence" not in skills)
    role = json.loads((ws / ".lybra" / "role").read_text())
    ok("⑤ 信封按角色名覆盖推导", role.get("owner_policy_ref") == "pol_probe_exec_1")


def test_5_idempotent_seed_only():
    """验收②: 重复 enroll 不覆盖已存在的用户定制(seed_only 负夹具)。"""
    tmp = Path(tempfile.mkdtemp(prefix="f54-idem-"))
    gov = _fake_governance(tmp)
    ws = _fresh_ws(tmp)
    _enroll(CURRENT, ws, gov, _stub_token_entry("executor", "exec.probe"))
    custom = {"defaultProvider": "my-own-provider", "customKey": True}
    (ws / ".pi" / "settings.json").write_text(json.dumps(custom))
    custom_charter = "# 我的定制章程\n"
    (ws / "AGENTS.md").write_text(custom_charter)
    r2 = _enroll(CURRENT, ws, gov, _stub_token_entry("executor", "exec.probe"))
    ok("② settings 用户定制保留", json.loads((ws / ".pi" / "settings.json").read_text()) == custom)
    ok("② AGENTS.md 用户定制保留", (ws / "AGENTS.md").read_text() == custom_charter)
    ok("② 重复 enroll 仍 ok", r2["ok"] is True)


def test_6_auto_create_nested_dir():
    """验收③: 目标目录不存在时自动创建并出声(先红后绿)。"""
    tmp = Path(tempfile.mkdtemp(prefix="f54-mkdir-"))
    gov = _fake_governance(tmp)
    nested = tmp / "deep" / "nested" / "ws"
    # 红: 修复前无出声字段
    r_pre = _enroll(PREFIX, nested, gov, _stub_token_entry("executor", "exec.probe"))
    ok("红: 修复前目录也建了但无出声字段", nested.is_dir() and "created_workspace_dir" not in r_pre)
    nested2 = tmp / "deep2" / "nested" / "ws"
    r_cur = _enroll(CURRENT, nested2, gov, _stub_token_entry("executor", "exec.probe"))
    ok("③ 嵌套目录自动创建", nested2.is_dir())
    ok("③ 创建出声 created_workspace_dir=True", r_cur.get("created_workspace_dir") is True)


def test_7_no_policy_error_guidance():
    """验收⑩: 无生效信封 → 报错带路(executor/auditor 硬错; advisor 类仅告警)。"""
    tmp = Path(tempfile.mkdtemp(prefix="f54-nopol-"))
    gov = _fake_governance(tmp, covers="someone-else")  # 信封不覆盖本角色
    ws = _fresh_ws(tmp)
    try:
        _enroll(CURRENT, ws, gov, _stub_token_entry("executor", "exec.probe"))
        raise AssertionError("[FAIL] ⑩ executor 无信封应报错")
    except RuntimeError as e:
        msg = str(e)
        ok("⑩ executor 无信封报错", "owner_policy_ref" in msg)
        ok("⑩ 报错带路(铸信封指引)", "铸信封" in msg or "owner_autonomy_policy" in msg)
    # advisor 类: 仅告警不阻断
    ws2 = _fresh_ws(tmp, "ws2")
    gov2 = _fake_governance(tmp, policy_id="pol_probe_adv_1", covers="someone-else", name="gov2")
    r = _enroll(CURRENT, ws2, gov2, _stub_token_entry("advisor", "adv.probe"))
    ok("⑩ advisor 类无信封不阻断", r["ok"] is True)
    ok("⑩ advisor 类带告警", bool((r.get("policy_derivation") or {}).get("warning")))


def test_8_transport_401_classification():
    """验收④: 运输凭证失效分类明确且带重签出口。"""
    tmp = Path(tempfile.mkdtemp(prefix="f54-401-"))
    sc_code = _self_contained_code(_fake_governance(tmp))

    def _raise_401(req, timeout=None):
        raise urllib.error.HTTPError(req.full_url, 401, "Unauthorized", {}, None)  # type: ignore[arg-type]

    with mock.patch.object(CURRENT.urllib.request, "build_opener") as bo:
        opener = mock.MagicMock()
        opener.open.side_effect = _raise_401
        bo.return_value = opener
        try:
            CURRENT.exchange_enrollment_code("http://127.0.0.1:7999", sc_code, None)
            raise AssertionError("[FAIL] ④ 401 应报错")
        except RuntimeError as e:
            msg = str(e)
            ok("④ 401 分类=运输凭证失效", "运输凭证失效" in msg)
            ok("④ 带'请顾问重签'出口", "重签" in msg and "enroll-code" in msg)
    # 业务错误分类: 码已消费
    body = {"ok": False, "error_code": "CODE_ALREADY_USED",
            "message": "Enrollment code is already used (单次码, 已消费).",
            "suggested_next_action": ""}
    resp = {"result": {"structuredContent": body}}
    import io

    def _ok_resp(req, timeout=None):
        return io.BytesIO(json.dumps(resp).encode())

    with mock.patch.object(CURRENT.urllib.request, "build_opener") as bo:
        opener = mock.MagicMock()
        opener.open.side_effect = _ok_resp
        bo.return_value = opener
        try:
            CURRENT.exchange_enrollment_code("http://127.0.0.1:7999", sc_code, None)
            raise AssertionError("[FAIL] ④ 码已消费应报错")
        except RuntimeError as e:
            msg = str(e)
            ok("④ 码已消费分类", "码已消费" in msg)
            ok("④ 业务错也带重签出口", "重签" in msg)


def test_9_sync_policy_correction():
    """验收⑪: 信封更替后 sync 产品路径校正 role#owner_policy_ref(不手写文件)。"""
    tmp = Path(tempfile.mkdtemp(prefix="f54-sync-"))
    gov = _fake_governance(tmp, policy_id="pol_v1", covers="executor")
    ws = _fresh_ws(tmp)
    (ws / ".lybra").mkdir(parents=True)
    (ws / ".lybra" / "role").write_text(json.dumps(
        {"role": "executor", "instance": "exec.probe", "owner_policy_ref": "pol_v0_old"}))
    (ws / ".lybra" / "connection.json").write_text(json.dumps(
        {"mcp": {"rpc_url": "http://x/mcp"}, "governance_root": str(gov)}))
    out = _correct_owner_policy_ref(ws, "executor")
    role = json.loads((ws / ".lybra" / "role").read_text())
    ok("⑪ 信封更替后键被校正", role.get("owner_policy_ref") == "pol_v1", str(out))
    ok("⑪ 校正报告含 from/to", (out.get("updated") or {}).get("from") == "pol_v0_old")
    # 治理根不可读 → 非致命
    (ws / ".lybra" / "connection.json").write_text(json.dumps(
        {"mcp": {"rpc_url": "http://x/mcp"}, "governance_root": str(tmp / "nope")}))
    out2 = _correct_owner_policy_ref(ws, "executor")
    ok("⑪ 推导不出非致命(保留现值)", out2.get("updated") is None)


def test_10_dangling_bin_and_missing_named():
    """验收⑭⑮: lybra_bin 悬空=缺项; 缺项逐项点名。"""
    tmp = Path(tempfile.mkdtemp(prefix="f54-mbs-"))
    gov = _fake_governance(tmp)
    ws = _fresh_ws(tmp)
    _enroll(CURRENT, ws, gov, _stub_token_entry("executor", "exec.probe"))
    conn_p = ws / ".lybra" / "connection.json"
    conn = json.loads(conn_p.read_text())
    conn["lybra_bin"] = "/nonexistent/path/bin/lybra"
    conn_p.write_text(json.dumps(conn))
    r = verify_minimum_bootable_set(ws)
    ok("⑭ 悬空 lybra_bin 被点名", "connection.json#lybra_bin" in r["missing"], str(r["missing"]))
    # 删 skills 目录 → 逐项点名
    import shutil

    shutil.rmtree(ws / ".pi" / "skills")
    r2 = verify_minimum_bootable_set(ws)
    ok("⑮ 缺 skills 目录逐项点名", ".pi/skills/" in r2["missing"], str(r2["missing"]))
    ok("⑮ 缺项清单完整(2 项)", len(r2["missing"]) == 2)


def test_11_no_hardcoded_roles():
    """验收⑱: 映射在声明可查, 代码无角色名硬编码。"""
    src = (REPO / "tools" / "aipos_cli" / "workstation_wiring.py").read_text(encoding="utf-8")
    for banned in ["hbj-coder", "hbj-auditor", "chris", "kiwiai-dev", "lybra-executor", "probe-xyz"]:
        ok(f"⑱ 代码无 {banned} 硬编码", banned not in src)
    skills = load_role_skills("executor")
    ok("⑱ roles.schema 声明可查", isinstance(skills, list) and len(skills) == 5)


def test_12_derive_priority_instance_over_role():
    """⑨补充: 实例精确匹配信封优先于角色级信封。"""
    tmp = Path(tempfile.mkdtemp(prefix="f54-prio-"))
    gov = _fake_governance(tmp, policy_id="pol_role_level", covers="executor")
    _fake_governance(tmp, policy_id="pol_instance_level", covers="exec.probe.kiwiai-dev", name="gov")
    pid, _ = derive_effective_owner_policy_ref(gov, role="executor", agent_instance="exec.probe.kiwiai-dev")
    ok("⑨ 实例级信封优先", pid == "pol_instance_level", pid)


if __name__ == "__main__":
    for fn in [
        test_1_red_prefix_empty_enroll,
        test_2_green_full_wiring,
        test_3_skills_by_role_class,
        test_4_custom_role_by_class,
        test_5_idempotent_seed_only,
        test_6_auto_create_nested_dir,
        test_7_no_policy_error_guidance,
        test_8_transport_401_classification,
        test_9_sync_policy_correction,
        test_10_dangling_bin_and_missing_named,
        test_11_no_hardcoded_roles,
        test_12_derive_priority_instance_over_role,
    ]:
        print(f"\n== {fn.__name__} ==")
        fn()
    print(f"\n✓ AIPOS-F54 夹具全绿 ({PASS} assertions)")
