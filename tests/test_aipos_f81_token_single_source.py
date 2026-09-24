"""AIPOS-F81: token 解析单源 —— 靶场夹具。

依据: 2026-09-24 重 enroll 后 connection.json 形如 [retired 旧 token, 新 token], enroll --verify 通过但
`lybra sync` 仍 401 —— loop_context.ConnectionResolver(第二套)与 pi loop-context.ts(第三套)按
instance→role 取**第一条**不认 retired。本卡把挑选收到 tools/aipos_cli/token_resolver.py::select_token_entry
一处(TS 同判据, 字段名来源 token_resolver.TOKEN_ENTRY_FIELDS)。

纪律: token 永不上屏 —— 夹具只比 sha256 指纹, 断言消息只带指纹, 不带 token 值。
"""

from __future__ import annotations

import json
import re
import secrets
import shutil
import subprocess
from pathlib import Path

import pytest

from tools.aipos_cli.token_resolver import (
    TOKEN_ENTRY_FIELDS,
    TOKEN_REENROLL_EXIT,
    TokenAllRetiredError,
    TokenNotFoundError,
    TokenResolutionError,
    get_token_for_role_and_project,
    select_token_entry,
    token_fingerprint as fp,
)
from tools.loop_context import ConnectionResolver

REPO_ROOT = Path(__file__).resolve().parents[1]
LOOP_CONTEXT_TS = REPO_ROOT / "agents" / "harness" / "pi" / "lybra-loop" / "loop-context.ts"
INSTANCE = "exec.lybra.kiwiai-dev"


def _tok(label: str) -> str:
    return f"f81-{label}-{secrets.token_hex(8)}"


def _entry(token: str, *, role: str = "executor", instance: str | None = INSTANCE, retired: bool = False,
           projects: list[str] | None = None) -> dict:
    e: dict = {"role": role, "token": token, "projects": projects or ["lybra"]}
    if instance:
        e["agent_instance"] = instance
    if retired:
        e.update(retired=True, retired_at="2026-09-24T03:00:00Z", retired_reason="superseded by re-enrollment")
    return e


def _station(tmp_path: Path, tokens: list[dict], *, role: str = "executor", instance: str = INSTANCE) -> Path:
    root = tmp_path / "station"
    lybra = root / ".lybra"
    lybra.mkdir(parents=True)
    (lybra / "role").write_text(json.dumps({"role": role, "instance": instance}), encoding="utf-8")
    (lybra / "connection.json").write_text(json.dumps({
        "config_version": 1,
        "workspace_root": str(tmp_path / "gov"),
        "mcp": {"rpc_url": "http://127.0.0.1:7118/mcp"},
        "tokens": tokens,
    }), encoding="utf-8")
    return root


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    for k in ("LYBRA_ROLE", "LYBRA_ACTOR", "LYBRA_AGENT_INSTANCE", "LYBRA_OWNER_POLICY_REF", "LYBRA_TOKEN",
              "LYBRA_WORKSPACE_ROOT", "LYBRA_GATE_URL", "LYBRA_HARNESS_ROOT"):
        monkeypatch.delenv(k, raising=False)


@pytest.fixture()
def rotated(tmp_path):
    """真实现场同形: tokens[0]=retired 旧 token, tokens[1]=新 token(同实例同角色)。"""
    old, new = _tok("old"), _tok("new")
    root = _station(tmp_path, [_entry(old, retired=True), _entry(new)])
    return root, fp(old), fp(new)


@pytest.fixture()
def all_retired(tmp_path):
    a, b = _tok("a"), _tok("b")
    root = _station(tmp_path, [_entry(a, retired=True), _entry(b, retired=True)])
    return root, {fp(a), fp(b)}, (a, b)


# ── 验收①: 靶场 [retired, new] 三类客户端取新 token ─────────────────────────────

def test_sync_client_picks_new_token(rotated):
    from tools.aipos_cli.distribution_sync import resolve_sync_context

    root, old_fp, new_fp = rotated
    ctx = resolve_sync_context(harness_root=root)
    got = fp(ctx["token"])
    print(f"sync resolve_sync_context → {got} (old={old_fp} new={new_fp})")
    assert got == new_fp, f"sync 取到 {got}, 期望新 token {new_fp}(旧={old_fp})"


def test_resolve_identity_picks_new_token(rotated):
    root, old_fp, new_fp = rotated
    ident = ConnectionResolver.resolve_identity(workspace_root=root, env={})
    got = fp(ident["token"]["value"] or "")
    print(f"resolve_identity → {got} source={ident['token']['source']}")
    assert got == new_fp, f"resolve_identity 取到 {got}, 期望 {new_fp}"
    assert ident["token"]["source"] == ".lybra/connection.json"
    assert "error" not in ident["token"]


def test_resolve_token_picks_new_token_by_instance_and_role(rotated):
    root, old_fp, new_fp = rotated
    by_inst = fp(ConnectionResolver.resolve_token(workspace_root=root, agent_instance=INSTANCE, env={}))
    by_role = fp(ConnectionResolver.resolve_token(workspace_root=root, role="executor", env={}))
    print(f"resolve_token instance → {by_inst}; role → {by_role}")
    assert by_inst == new_fp, f"instance 匹配取到 {by_inst}, 期望 {new_fp}"
    assert by_role == new_fp, f"role 匹配取到 {by_role}, 期望 {new_fp}"


def test_confirm_client_picks_new_token(rotated):
    from tools.aipos_cli.confirm_client import load_owner_token

    root, old_fp, new_fp = rotated
    got = fp(load_owner_token(connection_json=root / ".lybra" / "connection.json", role="executor"))
    print(f"confirm_client.load_owner_token → {got}")
    assert got == new_fp, f"confirm_client 取到 {got}, 期望 {new_fp}"


def test_audit_helpers_token_and_metadata_from_same_active_entry(tmp_path, monkeypatch):
    from tools.aipos_cli import audit_helpers

    old, new = _tok("aud-old"), _tok("aud-new")
    root = _station(tmp_path, [
        _entry(old, role="auditor", instance="audit.lybra.old-host", retired=True),
        _entry(new, role="auditor", instance="audit.lybra.kiwiai-dev"),
    ], role="auditor", instance="audit.lybra.kiwiai-dev")
    ctx = audit_helpers.resolve_audit_context(workspace_root=root, role="auditor")
    got = fp(ctx["token"])
    print(f"audit_helpers → {got} agent_instance={ctx['agent_instance']}")
    assert got == fp(new), f"audit_helpers 取到 {got}, 期望 {fp(new)}"
    assert ctx["agent_instance"] == "audit.lybra.kiwiai-dev"


def test_board_login_any_role_fallback_skips_retired(tmp_path):
    from tools.aipos_cli.board_login import load_role_token

    old, new = _tok("hbj-old"), _tok("hbj-new")
    conn = _station(tmp_path, [
        _entry(old, role="hbj-coder", instance="exec.chris-huibojin.mac", retired=True),
        _entry(new, role="hbj-coder", instance="exec.chris-huibojin.mac"),
    ]) / ".lybra" / "connection.json"
    tok, role = load_role_token(conn)
    print(f"board_login any-role fallback → {fp(tok)} role={role}")
    assert fp(tok) == fp(new), f"board_login 取到 {fp(tok)}, 期望 {fp(new)}"
    assert role == "hbj-coder"


def test_token_rotation_owner_token_skips_retired():
    from tools.aipos_cli.token_rotation import _owner_token_from

    old, new = _tok("own-old"), _tok("own-new")
    got = _owner_token_from({"tokens": [_entry(old, role="owner", retired=True), _entry(new, role="owner")]})
    assert got is not None and fp(got) == fp(new), f"rotation 预旋转 owner token 取到 {fp(got or '')}, 期望 {fp(new)}"
    assert _owner_token_from({"tokens": [_entry(old, role="owner", retired=True)]}) is None


def test_charter_render_token_projects_from_active_entry(tmp_path):
    from tools.aipos_cli.charter_render import workstation_identity

    root = _station(tmp_path, [
        _entry(_tok("wrong-domain"), retired=True, projects=["chris-huibojin"]),
        _entry(_tok("right-domain"), projects=["lybra"]),
    ])
    ident = workstation_identity(root)
    assert ident["token_projects"] == ["lybra"], f"token_projects={ident['token_projects']}(应取未退役条目)"


# ── 验收②: 全 retired fail-closed, 拒因带出口 ────────────────────────────────

def test_all_retired_resolve_token_fail_closed(all_retired):
    root, fps, raw = all_retired
    with pytest.raises(TokenAllRetiredError) as ei:
        ConnectionResolver.resolve_token(workspace_root=root, role="executor", agent_instance=INSTANCE,
                                         env={"LYBRA_TOKEN": _tok("env")})
    msg = str(ei.value)
    print(f"resolve_token 全 retired 拒因: {msg}")
    assert TOKEN_REENROLL_EXIT in msg and "lybra roles enroll" in msg
    assert all(f in msg for f in fps), "拒因应列指纹"
    assert not any(r in msg for r in raw), "拒因不得含 token 值"


def test_all_retired_resolve_identity_no_env_substitution(all_retired):
    root, fps, raw = all_retired
    ident = ConnectionResolver.resolve_identity(workspace_root=root, env={"LYBRA_TOKEN": _tok("env")})
    tok = ident["token"]
    print(f"resolve_identity 全 retired: value={'None' if tok['value'] is None else fp(tok['value'])} "
          f"source={tok['source']} error={tok.get('error')}")
    assert tok["value"] is None and tok["source"] == "unresolved", "全 retired 禁 env 静默顶替"
    assert TOKEN_REENROLL_EXIT in tok["error"]
    assert not any(r in tok["error"] for r in raw)


def test_all_retired_sync_fail_closed_with_exit(all_retired):
    from tools.aipos_cli.distribution_sync import resolve_sync_context

    root, fps, raw = all_retired
    with pytest.raises(ValueError) as ei:
        resolve_sync_context(harness_root=root)
    msg = str(ei.value)
    print(f"sync 全 retired 拒因: {msg}")
    assert "lybra roles enroll" in msg and not any(r in msg for r in raw)


def test_all_retired_confirm_client_fail_closed(all_retired):
    from tools.aipos_cli.confirm_client import load_owner_token

    root, fps, raw = all_retired
    with pytest.raises(TokenAllRetiredError) as ei:
        load_owner_token(connection_json=root / ".lybra" / "connection.json", role="executor")
    assert "lybra roles enroll" in str(ei.value) and not any(r in str(ei.value) for r in raw)


# ── 单源判据细则(select_token_entry) ─────────────────────────────────────────

def test_instance_match_precedes_role_match():
    other, mine_old, mine_new = _tok("other"), _tok("mine-old"), _tok("mine-new")
    tokens = [
        _entry(other, instance="exec.lybra.other-host"),
        _entry(mine_old, retired=True),
        _entry(mine_new),
    ]
    got = select_token_entry(tokens, role="executor", agent_instance=INSTANCE)
    assert fp(got["token"]) == fp(mine_new), f"instance 优先: 取到 {fp(got['token'])}, 期望 {fp(mine_new)}"


def test_instance_all_retired_falls_to_active_role_match():
    """instance 阶段无可用 → role 阶段(与旧 instance→role 次序一致, 只是多排除 retired)。"""
    role_tok, inst_old = _tok("role"), _tok("inst-old")
    got = select_token_entry([_entry(inst_old, retired=True), _entry(role_tok, instance="exec.lybra.x")],
                             role="executor", agent_instance=INSTANCE)
    assert fp(got["token"]) == fp(role_tok)


def test_no_match_is_not_found_and_env_fallback_kept(tmp_path):
    root = _station(tmp_path, [_entry(_tok("aud"), role="auditor", instance="audit.lybra.kiwiai-dev")])
    with pytest.raises(TokenNotFoundError):
        select_token_entry(json.loads((root / ".lybra" / "connection.json").read_text())["tokens"],
                           role="executor", agent_instance=INSTANCE)
    env_tok = _tok("env")
    got = ConnectionResolver.resolve_token(workspace_root=root, role="executor", env={"LYBRA_TOKEN": env_tok})
    assert fp(got) == fp(env_tok), "无命中时 env 兜底保持(声明优先级不变)"
    explicit = _tok("explicit")
    assert fp(ConnectionResolver.resolve_token(workspace_root=root, role="executor", explicit_token=explicit)) == fp(explicit)


def test_empty_token_value_is_resolution_error_not_fallback():
    with pytest.raises(TokenResolutionError) as ei:
        select_token_entry([{"role": "executor", "agent_instance": INSTANCE, "token": ""}], role="executor")
    assert not isinstance(ei.value, (TokenNotFoundError, TokenAllRetiredError))


def test_get_token_for_role_and_project_agent_instance_param(tmp_path):
    new = _tok("new")
    conn = _station(tmp_path, [_entry(_tok("old"), retired=True), _entry(new)]) / ".lybra" / "connection.json"
    assert fp(get_token_for_role_and_project(conn, None, agent_instance=INSTANCE)) == fp(new)
    assert fp(get_token_for_role_and_project(conn, "executor", "lybra", agent_instance=INSTANCE)) == fp(new)


# ── Δ: 第二/第三套挑选循环已删 + TS 字段名同一声明 ──────────────────────────────

def test_loop_context_has_no_own_selection_loop():
    src = (REPO_ROOT / "tools" / "loop_context.py").read_text(encoding="utf-8")
    assert not re.search(r"for\s+\w+\s+in\s+tokens\b", src), "loop_context.py 不得再自带 tokens 挑选循环"
    assert "select_token_entry" in src and "get_token_for_role_and_project" in src


def test_ts_loop_context_has_no_own_selection_loop():
    src = LOOP_CONTEXT_TS.read_text(encoding="utf-8")
    assert not re.search(r"for\s*\(\s*const\s+\w+\s+of\s+tokens\s*\)", src), "loop-context.ts 不得再自带 tokens 挑选循环"
    assert src.count("selectTokenEntry(tokens") == 2, "resolveToken / resolveIdentity 均须走 selectTokenEntry"


def test_ts_field_names_declared_from_python_source():
    src = LOOP_CONTEXT_TS.read_text(encoding="utf-8")
    m = re.search(r"export const TOKEN_ENTRY_FIELDS = \{(.*?)\} as const;", src, re.S)
    assert m, "loop-context.ts 缺 TOKEN_ENTRY_FIELDS"
    ts_fields = dict(re.findall(r"(\w+):\s*\"([^\"]+)\"", m.group(1)))
    assert ts_fields == TOKEN_ENTRY_FIELDS, f"TS 字段名 {ts_fields} ≠ Python 声明 {TOKEN_ENTRY_FIELDS}"
    m2 = re.search(r'export const TOKEN_REENROLL_EXIT = "([^"]+)";', src)
    assert m2 and m2.group(1) == TOKEN_REENROLL_EXIT
    assert "来源: tools/aipos_cli/token_resolver.py::TOKEN_ENTRY_FIELDS" in src


_CASES = [
    ("rotated", [{"role": "executor", "agent_instance": INSTANCE, "token": "T0", "retired": True},
                 {"role": "executor", "agent_instance": INSTANCE, "token": "T1"}], "executor", INSTANCE),
    ("instance_first", [{"role": "executor", "agent_instance": "exec.lybra.other", "token": "T0"},
                        {"role": "executor", "agent_instance": INSTANCE, "token": "T1", "retired": True},
                        {"role": "executor", "agent_instance": INSTANCE, "token": "T2"}], "executor", INSTANCE),
    ("fall_to_role", [{"role": "executor", "agent_instance": INSTANCE, "token": "T0", "retired": True},
                      {"role": "executor", "agent_instance": "exec.lybra.x", "token": "T1"}], "executor", INSTANCE),
    ("all_retired", [{"role": "executor", "agent_instance": INSTANCE, "token": "T0", "retired": True}], "executor", INSTANCE),
    ("no_match", [{"role": "auditor", "agent_instance": "audit.lybra.y", "token": "T0"}], "executor", INSTANCE),
    ("empty_value", [{"role": "executor", "agent_instance": INSTANCE, "token": ""}], "executor", None),
]


def _py_outcome(tokens, role, inst):
    try:
        return "pick:" + fp(select_token_entry(tokens, role=role, agent_instance=inst)["token"])
    except TokenAllRetiredError:
        return "TokenAllRetiredError"
    except TokenNotFoundError:
        return "TokenNotFoundError"
    except TokenResolutionError:
        return "TokenResolutionError"


def test_python_and_ts_select_same_entry_on_shared_cases(tmp_path):
    """同构: 同一组靶场喂 Python select_token_entry 与 TS selectTokenEntry, 结论(指纹/错误类)逐条相同。"""
    node = shutil.which("node")
    assert node, "node 不在 PATH(run-all 依赖 Node ≥ 22)"
    tokens_by_case = {}
    for name, toks, role, inst in _CASES:
        real = [dict(t, token=(_tok(name) if t["token"] else "")) for t in toks]
        tokens_by_case[name] = (real, role, inst)
    payload = tmp_path / "cases.json"
    payload.write_text(json.dumps({k: {"tokens": v[0], "role": v[1], "inst": v[2]} for k, v in tokens_by_case.items()}))
    script = (
        "import { selectTokenEntry, TokenAllRetiredError, TokenNotFoundError, TokenResolutionError } from "
        f"{json.dumps(LOOP_CONTEXT_TS.as_uri())};\n"
        "import { readFileSync } from 'node:fs'; import { createHash } from 'node:crypto';\n"
        "const fp = (t) => 'sha256:' + createHash('sha256').update(t, 'utf-8').digest('hex').slice(0, 12);\n"
        f"const cases = JSON.parse(readFileSync({json.dumps(str(payload))}, 'utf-8'));\n"
        "const out = {};\n"
        "for (const [k, c] of Object.entries(cases)) {\n"
        "  try { out[k] = 'pick:' + fp(selectTokenEntry(c.tokens, { role: c.role, agentInstance: c.inst }).token); }\n"
        "  catch (e) { out[k] = e instanceof TokenAllRetiredError ? 'TokenAllRetiredError' : e instanceof TokenNotFoundError ? "
        "'TokenNotFoundError' : e instanceof TokenResolutionError ? 'TokenResolutionError' : 'OTHER:' + e.constructor.name; }\n"
        "}\n"
        "console.log(JSON.stringify(out));\n"
    )
    proc = subprocess.run([node, "--input-type=module", "-e", script], capture_output=True, text=True, timeout=60)
    assert proc.returncode == 0, f"node 失败: {proc.stderr[-800:]}"
    ts_out = json.loads(proc.stdout.strip().splitlines()[-1])
    for name, (real, role, inst) in tokens_by_case.items():
        py = _py_outcome(real, role, inst)
        print(f"{name:15s} py={py:32s} ts={ts_out[name]}")
        assert py == ts_out[name], f"{name}: Python {py} ≠ TS {ts_out[name]}"
    assert _py_outcome(*tokens_by_case["rotated"]) == "pick:" + fp(tokens_by_case["rotated"][0][1]["token"])
