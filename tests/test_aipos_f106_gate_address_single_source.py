"""AIPOS-F106: 门地址、端口与连接声明单源(碎片化族 B-a: M1 / M3 / M15 / M2)。

件① 门地址单源: 门基址一律经 confirm_client.resolve_gate_base_url(委托 loop_context.ConnectionResolver.resolve_gate_url),
     MCP 端点↔门基址换算只在 confirm_client.gate_base_url / gate_rpc_url; 门客户端凭据角色偏好序只在
     config.schema identity_resolution.keys.token.gate_client_role_preference 声明(token_resolver 读)。
件② 端口单源: 7117/7118 只在 config.schema 声明(ports.board_default / ports.gate_default; urls.gate_local 端口段锁一致),
     原 mcp_server_default 并入 gate_default; 车道内产品代码零端口字面。
件③ configuration_sources 收口: connection / role 两节只声明文件键形并指向 identity_resolution, 删 policy 节与过期字段;
     环境变量全名单在 config.schema environment_variables 声明 = 产品代码使用名单; 工作区根 env 统一 LYBRA_WORKSPACE_ROOT,
     旧名 AIPOS_WORKSPACE_ROOT 只在 workspace_config.workspace_root_from_env 一个兼容读取点识别并告警。
件④ .lybra 读取单源: .lybra/role 只经 charter_render.workstation_identity / ConnectionResolver.resolve_role|resolve_identity 读;
     connection.json 定位经 service_mode.connection_path, .lybra 判定经 ConnectionResolver.discover_lybra_dir。
"""
from __future__ import annotations

import io
import json
import re
import subprocess
import threading
from contextlib import redirect_stderr, redirect_stdout
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
LANE_PRODUCT_DIRS = ("tools/aipos_cli/", "tools/mcp_server/", "web/")
EXCLUDED_DIR_PARTS = {"tests", "test", "fixtures", "__tests__", "playwright"}


def _config() -> dict:
    return json.loads((REPO_ROOT / "schema" / "config.schema.json").read_text(encoding="utf-8"))


def _product_files() -> list[str]:
    """产品文件集(与 F87 棘轮同口径): git ls-files 中 .py/.ts/.js/.mjs/.cjs/.sh 与带 shebang 的无后缀脚本, 去测试目录与测试文件。"""
    out = subprocess.run(["git", "ls-files", "-z"], cwd=REPO_ROOT, capture_output=True, text=True, check=True).stdout
    files: list[str] = []
    for rel in out.split("\0"):
        if not rel:
            continue
        path = Path(rel)
        if any(part in EXCLUDED_DIR_PARTS for part in path.parts[:-1]):
            continue
        name = path.name
        if name.startswith(("test_", "test-")) or name.endswith((".test.ts", ".test.js", "_test.py")) or name == "conftest.py":
            continue
        full = REPO_ROOT / rel
        if not full.is_file():
            continue
        if path.suffix in {".py", ".ts", ".js", ".mjs", ".cjs", ".sh"} or (
            path.suffix == "" and full.read_text(encoding="utf-8", errors="replace").startswith("#!")
        ):
            files.append(rel)
    return sorted(files)


def _lane_python_files() -> list[str]:
    return [f for f in _product_files() if f.endswith(".py") and f.startswith(LANE_PRODUCT_DIRS)]


def _grep(files: list[str], pattern: re.Pattern[str]) -> list[str]:
    hits: list[str] = []
    for rel in files:
        for no, line in enumerate((REPO_ROOT / rel).read_text(encoding="utf-8", errors="replace").splitlines(), 1):
            if pattern.search(line):
                hits.append(f"{rel}:{no}: {line.strip()}")
    return hits


# ---------------------------------------------------------------------------
# 件① 门地址单源
# ---------------------------------------------------------------------------

MCP_STRIP_RE = re.compile(
    r"""len\(\s*["']/mcp["']\s*\)|\.replace\(\s*["']/mcp["']|removesuffix\(\s*["']/mcp["']|rstrip\(\s*["']/mcp["']"""
    r"""|endswith\(\s*["']/mcp["']\s*\)|rpc_url\[:-\d+\]"""
)


def test_item1_no_local_mcp_strip_in_lane_product_code():
    """验收①: 车道内产品代码零「各自剥 /mcp」(rpc_url[:-len("/mcp")] / [:-4] / replace / removesuffix / rstrip / endswith 判定)。"""
    hits = _grep(_lane_python_files(), MCP_STRIP_RE)
    assert hits == [], "门地址须经 confirm_client.gate_base_url / resolve_gate_base_url 换算, 禁各自剥 /mcp:\n" + "\n".join(hits)


def test_item1_token_role_preference_declared_once_and_read():
    """件①: 门客户端凭据角色偏好序只在 config.schema 声明一次, 代码零写死角色元组, 读取口返回声明值。"""
    from tools.aipos_cli.token_resolver import gate_client_role_preference

    declared = _config()["identity_resolution"]["keys"]["token"]["gate_client_role_preference"]["roles"]
    assert list(gate_client_role_preference()) == declared
    literal = re.compile(r"""\(\s*["']advisor["']\s*,\s*["']planner["']\s*,\s*["']owner["']\s*\)""")
    hits = _grep([f for f in _product_files() if f.endswith(".py")], literal)
    assert hits == [], "角色偏好序写死:\n" + "\n".join(hits)


def test_item1_gate_base_url_conversion_is_suffix_only_and_inverse():
    from tools.aipos_cli.confirm_client import gate_base_url, gate_rpc_url

    assert gate_base_url("http://h:1/mcp") == "http://h:1"
    assert gate_base_url("http://h:1/mcp/") == "http://h:1"
    assert gate_base_url("http://h:1") == "http://h:1"
    # 原 rstrip("/mcp") 按字符集剥: 主机名以 m/c/p 结尾时会误削(camp → ca)
    assert gate_base_url("http://camp") == "http://camp"
    assert gate_base_url("http://mcp.example/mcp") == "http://mcp.example"
    assert gate_rpc_url("http://h:1") == "http://h:1/mcp"
    assert gate_rpc_url("http://h:1/mcp") == "http://h:1/mcp"  # 幂等


def test_item1_resolve_gate_base_url_delegates_to_connection_resolver(monkeypatch, tmp_path):
    """resolve_gate_base_url 只换算, 门地址解析委托 ConnectionResolver.resolve_gate_url(唯一实现)。"""
    from tools.aipos_cli import confirm_client
    from tools.loop_context import ConnectionResolver

    seen: list[dict] = []

    def fake(**kwargs):
        seen.append(kwargs)
        return "http://delegated:9/mcp"

    monkeypatch.setattr(ConnectionResolver, "resolve_gate_url", staticmethod(fake))
    assert confirm_client.resolve_gate_base_url(workspace_root=tmp_path, env={}) == "http://delegated:9"
    assert seen and seen[0]["workspace_root"] == tmp_path and seen[0]["explicit_url"] is None


def test_item1_resolve_gate_base_url_layers_and_fail_closed(tmp_path):
    from tools.aipos_cli.confirm_client import GateAddressError, resolve_gate_base_url
    from tools.schema_loader import get_config_default_gate_url

    ws = tmp_path / "ws"
    (ws / ".lybra").mkdir(parents=True)
    (ws / ".lybra" / "connection.json").write_text(json.dumps({"mcp": {"rpc_url": "http://ws-gate:1/mcp"}}), encoding="utf-8")
    assert resolve_gate_base_url(workspace_root=ws, env={}) == "http://ws-gate:1"
    # 显式 > 工位声明
    assert resolve_gate_base_url(workspace_root=ws, explicit_url="http://x:2/mcp", env={}) == "http://x:2"
    # 工位声明 > env(env 仅兜底)
    assert resolve_gate_base_url(workspace_root=ws, env={"LYBRA_GATE_URL": "http://env:3"}) == "http://ws-gate:1"
    # 无声明: env 兜底 → schema 缺省
    empty = tmp_path / "empty"
    empty.mkdir()
    assert resolve_gate_base_url(workspace_root=empty, env={"LYBRA_GATE_URL": "http://env:3/mcp"}) == "http://env:3"
    assert resolve_gate_base_url(workspace_root=empty, env={}) == get_config_default_gate_url()
    # 显式凭据文件层: 声明即用; 未声明 + require_declared = 拒; 文件坏 = 拒
    conn = tmp_path / "c.json"
    conn.write_text(json.dumps({"mcp": {"rpc_url": "http://file:4/mcp"}}), encoding="utf-8")
    assert resolve_gate_base_url(connection_json=conn, require_declared=True) == "http://file:4"
    conn.write_text(json.dumps({"tokens": []}), encoding="utf-8")
    with pytest.raises(GateAddressError):
        resolve_gate_base_url(connection_json=conn, require_declared=True)
    conn.write_text("{not json", encoding="utf-8")
    with pytest.raises(GateAddressError):
        resolve_gate_base_url(connection_json=conn)


def _write_conn(path: Path, tokens: list[dict], rpc_url: str | None) -> Path:
    data: dict = {"config_version": 1, "workspace_root": str(path.parent), "tokens": tokens}
    if rpc_url:
        data["mcp"] = {"rpc_url": rpc_url}
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


def test_item1_load_gate_client_token_follows_declared_order(tmp_path):
    from tools.aipos_cli.confirm_client import load_gate_client_token

    conn = _write_conn(tmp_path / "c.json", [
        {"role": "owner", "token": "tok-owner"},
        {"role": "advisor", "token": "tok-adv-old", "retired": True},
        {"role": "planner", "token": "tok-planner"},
    ], "http://h:1/mcp")
    assert load_gate_client_token(conn) == ("planner", "tok-planner")  # advisor 全 retired → 下一个声明角色
    _write_conn(conn, [{"role": "executor", "token": "tok-exec"}], "http://h:1/mcp")
    with pytest.raises(ValueError, match="no usable token"):
        load_gate_client_token(conn)


class _Gate(BaseHTTPRequestHandler):
    seen: dict = {"paths": [], "bearers": [], "calls": []}

    def log_message(self, *a):  # 静音
        pass

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)).decode("utf-8"))
        _Gate.seen["paths"].append(self.path)
        _Gate.seen["bearers"].append(str(self.headers.get("Authorization") or ""))
        name = (body.get("params") or {}).get("name") or body.get("method")
        _Gate.seen["calls"].append(name)
        payload = {"ok": True, "enrollments": [], "registry_root": "/stub", "governance_root_filter": "x"}
        result = {"protocolVersion": "2025-03-26"} if body.get("method") == "initialize" else {"structuredContent": payload}
        out = json.dumps({"jsonrpc": "2.0", "id": body.get("id"), "result": result}).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(out)))
        self.end_headers()
        self.wfile.write(out)


def _governance_ws(tmp_path: Path) -> Path:
    ws = tmp_path / "gov"
    (ws / "5_tasks" / "queue" / "pending").mkdir(parents=True)
    (ws / "project.json").write_text(json.dumps({"project": "f106probe"}), encoding="utf-8")
    return ws


def _run_cli(argv: list[str]) -> tuple[int, str, str]:
    from tools.aipos_cli.aipos_cli import main

    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        rc = main(argv)
    return rc, out.getvalue(), err.getvalue()


def test_item1_cli_thin_shell_uses_single_base_url_and_declared_token_order(tmp_path, monkeypatch):
    """roles enroll-list 薄壳: 门基址经唯一推导口(只打一次 /mcp, 原 MCP 端点不重复拼), 凭据按声明序(无 advisor → planner)。"""
    monkeypatch.delenv("LYBRA_GATE_URL", raising=False)
    server = ThreadingHTTPServer(("127.0.0.1", 0), _Gate)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        _Gate.seen = {"paths": [], "bearers": [], "calls": []}
        ws = _governance_ws(tmp_path)
        conn = _write_conn(tmp_path / "gate-conn.json", [
            {"role": "owner", "token": "tok-owner"}, {"role": "planner", "token": "tok-planner"},
        ], f"http://127.0.0.1:{server.server_address[1]}/mcp")
        rc, out, err = _run_cli(["roles", "--workspace-root", str(ws), "--connection-json", str(conn), "enroll-list", "--json"])
        assert rc == 0, err
        assert _Gate.seen["paths"] and set(_Gate.seen["paths"]) == {"/mcp"}, _Gate.seen["paths"]
        assert set(_Gate.seen["bearers"]) == {"Bearer tok-planner"}
        assert "lybra_roles_enroll_list" in _Gate.seen["calls"]
    finally:
        server.shutdown()
        server.server_close()


def test_item1_cli_thin_shell_fail_closed_without_declared_rpc_url(tmp_path, monkeypatch):
    """凭据文件未声明 mcp.rpc_url = 拒(不落 env / schema 缺省偷偷连门)。"""
    monkeypatch.setenv("LYBRA_GATE_URL", "http://127.0.0.1:9/should-not-be-used")
    ws = _governance_ws(tmp_path)
    conn = _write_conn(tmp_path / "gate-conn.json", [{"role": "advisor", "token": "tok-adv"}], None)
    rc, _out, err = _run_cli(["roles", "--workspace-root", str(ws), "--connection-json", str(conn), "enroll-list"])
    assert rc == 1
    assert "mcp.rpc_url" in err


# ---------------------------------------------------------------------------
# 件② 端口单源
# ---------------------------------------------------------------------------

PORT_RE = re.compile(r"(?<!\d)711[78](?!\d)")


def test_item2_no_port_literal_in_lane_product_code():
    """验收②: 车道内产品代码(tools/aipos_cli, tools/mcp_server, web 含静态资源)零端口字面。"""
    files = [f for f in _product_files() if f.startswith(LANE_PRODUCT_DIRS)]
    out = subprocess.run(["git", "ls-files", "web/"], cwd=REPO_ROOT, capture_output=True, text=True, check=True).stdout.split()
    files += [f for f in out if f.endswith((".html", ".css", ".json")) and "/tests/" not in f and f not in files]
    hits = _grep(sorted(set(files)), PORT_RE)
    assert hits == [], "端口须经 schema_loader.get_config_port / workspace_config 常量读 config.schema:\n" + "\n".join(hits)


def test_item2_config_schema_port_declared_once():
    cfg = _config()
    ports = cfg["ports"]
    assert "mcp_server_default" not in ports, "门端口与 MCP 端口是同一事实, 只留 gate_default"
    text = (REPO_ROOT / "schema" / "config.schema.json").read_text(encoding="utf-8")
    port_lines = [ln.strip() for ln in text.splitlines() if PORT_RE.search(ln)]
    assert port_lines == [
        f'"board_default": {ports["board_default"]},',
        f'"gate_default": {ports["gate_default"]},',
        f'"gate_local": "{cfg["urls"]["gate_local"]}"',
    ], port_lines
    # urls.gate_local(schema_loader.get_config_default_gate_url 原样读, 车道外)端口段须与 ports.gate_default 一致
    from urllib.parse import urlparse

    assert urlparse(cfg["urls"]["gate_local"]).port == ports["gate_default"]
    assert "gate_tailscale_pattern" not in cfg["urls"]


def test_item2_code_port_constants_read_declaration():
    from tools.aipos_cli import workspace_config
    from tools.schema_loader import get_config_port

    assert workspace_config.DEFAULT_BOARD_PORT == get_config_port("board_default")
    assert workspace_config.DEFAULT_MCP_PORT == get_config_port("gate_default")
    src = (REPO_ROOT / "web" / "board" / "app.py").read_text(encoding="utf-8")
    assert 'add_argument("--port", type=int, default=DEFAULT_BOARD_PORT)' in src


# ---------------------------------------------------------------------------
# 件③ configuration_sources ↔ identity_resolution 无矛盾; env 名单声明 = 代码使用
# ---------------------------------------------------------------------------

def _schema_field_declared(schema: dict, dotted: str) -> bool:
    node: dict | None = schema
    for part in dotted.split("."):
        if not isinstance(node, dict) or part not in node:
            return False
        spec = node[part]
        node = spec.get("schema") if isinstance(spec, dict) else None
    return True


def test_item3_configuration_sources_consistent_with_identity_resolution():
    """验收③: configuration_sources 与 identity_resolution 无矛盾(逐来源层核对文件/字段)。"""
    cfg = _config()
    sources = cfg["configuration_sources"]
    ident = cfg["identity_resolution"]
    assert "policy" not in sources, ".lybra/policy.json 不存在, policy 节须删"
    by_file = {spec["source_file"]: (name, spec) for name, spec in sources.items() if isinstance(spec, dict) and "source_file" in spec}
    assert sources["role"]["source_file"] == ".lybra/role"
    assert sources["connection"]["source_file"] == ".lybra/connection.json"
    for name in ("connection", "role"):
        assert sources[name]["resolution"] == "identity_resolution"
    assert "gate_url" not in sources["connection"]["schema"] and "project" not in sources["connection"]["schema"]
    assert "credential_token" not in sources["role"]["schema"] and "agent_instance" not in sources["role"]["schema"]
    legacy = set(sources["role"]["legacy_plain_text_files"])
    checked = 0
    for key, spec in ident["keys"].items():
        for src in spec["sources"]:
            if src["layer"] != "workstation":
                continue
            if src["file"] in legacy:
                checked += 1
                continue
            assert src["file"] in by_file, f"identity_resolution.{key} 读 {src['file']}, configuration_sources 未声明该文件"
            field = re.match(r"[A-Za-z_][\w.]*", src["field"]).group(0)
            assert _schema_field_declared(by_file[src["file"]][1]["schema"], field), (
                f"identity_resolution.{key} 读 {src['file']}#{field}, configuration_sources.{by_file[src['file']][0]} 未声明"
            )
            checked += 1
    assert checked >= 8
    env_decl = cfg["environment_variables"]
    assert "override" not in env_decl["precedence"].lower()
    ident_env = {src["var"] for spec in ident["keys"].values() for src in spec["sources"] if src["layer"] == "env"}
    authority_env = set(next(s for s in cfg["lybra_dir_authority"]["sources"] if s["name"] == "env")["holds"])
    assert ident_env | authority_env <= set(env_decl["variables"])
    for var, spec in env_decl["variables"].items():
        if spec.get("identity_key"):
            assert any(src.get("var") == var for src in ident["keys"][spec["identity_key"]]["sources"]), var


def test_item3_connection_validator_reads_declared_required_keys():
    """铸全校验只读 configuration_sources.connection 的 required(无代码内第二份键表)。"""
    from tools.aipos_cli.enroll_client import validate_connection_complete

    schema = _config()["configuration_sources"]["connection"]["schema"]

    def leaves(node: dict, prefix: str = "") -> list[str]:
        out: list[str] = []
        for key, spec in node.items():
            if not spec.get("required"):
                continue
            sub = spec.get("schema")
            if isinstance(sub, dict) and any(s.get("required") for s in sub.values()):
                out += leaves(sub, f"{prefix}{key}.")
            else:
                out.append(f"{prefix}{key}")
        return out

    assert validate_connection_complete({}) == leaves(schema)
    assert validate_connection_complete(
        {"config_version": 1, "workspace_root": "/w", "mcp": {"rpc_url": "http://h/mcp"}, "tokens": []}
    ) == []


ENV_PY_RE = re.compile(r"""["']((?:LYBRA|AIPOS)_[A-Z0-9_]*[A-Z0-9])["']""")
ENV_SH_RE = re.compile(r"""\$\{?((?:LYBRA|AIPOS)_[A-Z0-9_]*[A-Z0-9])|(?:^|[\s;])(?:export\s+)?((?:LYBRA|AIPOS)_[A-Z0-9_]*[A-Z0-9])=""", re.M)
ENV_JS_RE = re.compile(r"""process\.env\.((?:LYBRA|AIPOS)_[A-Z0-9_]*[A-Z0-9])|process\.env\[\s*["']((?:LYBRA|AIPOS)_[A-Z0-9_]*[A-Z0-9])""")


def _env_names_used() -> dict[str, set[str]]:
    used: dict[str, set[str]] = {}
    for rel in _product_files():
        text = (REPO_ROOT / rel).read_text(encoding="utf-8", errors="replace")
        names: set[str] = set()
        if rel.endswith(".py"):
            # 引号内的 LYBRA_/AIPOS_ 名 = env 名; 同名的 Python 标识符定义(LYBRA_GREEN = ...)不是 env
            idents = set(re.findall(r"^\s*((?:LYBRA|AIPOS)_[A-Z0-9_]+)\s*=", text, re.M))
            names = {m.group(1) for m in ENV_PY_RE.finditer(text)} - idents
        else:
            regexes = []
            if rel.endswith((".ts", ".js", ".mjs", ".cjs")) or (text.startswith("#!") and "node" in text.splitlines()[0]):
                regexes.append(ENV_JS_RE)
            if rel.endswith(".sh") or (text.startswith("#!") and "node" not in text.splitlines()[0]):
                regexes.append(ENV_SH_RE)
            for rx in regexes:
                names |= {next(g for g in m.groups() if g) for m in rx.finditer(text)}
        for name in names:
            used.setdefault(name, set()).add(rel)
    return used


def test_item3_env_names_declared_equals_used():
    """验收④: config.schema environment_variables 声明的 env 名单 = 产品代码使用的名单(多一个少一个都红)。"""
    used = _env_names_used()
    declared = set(_config()["environment_variables"]["variables"])
    undeclared = {k: sorted(v) for k, v in used.items() if k not in declared}
    unused = sorted(declared - set(used))
    assert not undeclared, f"代码使用但未声明: {undeclared}"
    assert not unused, f"声明但代码未使用(声明须删): {unused}"


def test_item3_single_workspace_root_env_name_with_one_compat_point(monkeypatch, capsys):
    """工作区根 env 统一为 LYBRA_WORKSPACE_ROOT; 旧名只在 workspace_config 一个兼容读取点出现并告警。"""
    decl = _config()["environment_variables"]["variables"]
    assert decl["AIPOS_WORKSPACE_ROOT"]["deprecated_alias_of"] == "LYBRA_WORKSPACE_ROOT"
    legacy_files = sorted(f for f in _product_files()
                          if "AIPOS_WORKSPACE_ROOT" in (REPO_ROOT / f).read_text(encoding="utf-8", errors="replace"))
    assert legacy_files == ["tools/aipos_cli/workspace_config.py"], legacy_files

    from tools.aipos_cli import workspace_config

    monkeypatch.setattr(workspace_config, "_LEGACY_WORKSPACE_ROOT_WARNED", set())
    assert workspace_config.workspace_root_from_env({}) == (None, None)
    assert workspace_config.workspace_root_from_env({"LYBRA_WORKSPACE_ROOT": "/new"}) == ("/new", "LYBRA_WORKSPACE_ROOT")
    assert capsys.readouterr().err == ""
    assert workspace_config.workspace_root_from_env({"AIPOS_WORKSPACE_ROOT": "/old"}) == ("/old", "AIPOS_WORKSPACE_ROOT")
    assert "已废弃" in capsys.readouterr().err
    both = {"LYBRA_WORKSPACE_ROOT": "/new", "AIPOS_WORKSPACE_ROOT": "/other"}
    assert workspace_config.workspace_root_from_env(both) == ("/new", "LYBRA_WORKSPACE_ROOT")
    assert "忽略" in capsys.readouterr().err


def test_item3_serve_child_env_carries_only_new_name():
    src = (REPO_ROOT / "tools" / "aipos_cli" / "service_mode.py").read_text(encoding="utf-8")
    assert "env.pop(LEGACY_WORKSPACE_ROOT_ENV, None)" in src and "env[WORKSPACE_ROOT_ENV] = str(child_workspace_root)" in src


# ---------------------------------------------------------------------------
# 件④ .lybra 读取单源
# ---------------------------------------------------------------------------

ROLE_READ_RE = re.compile(r"""/\s*["']role["']|["']\.lybra/role["']\s*\)|Path\(\s*["']\.lybra/role["']""")
# 唯一实现: charter_render(WORKSTATION_ROLE_FILE)、loop_context.ConnectionResolver(车道外)、enroll_client.write_role_file(唯一写入器)
ROLE_ALLOWED = {"tools/loop_context.py", "tools/aipos_cli/enroll_client.py"}


def test_item4_no_direct_role_file_access_outside_unique_impl():
    """验收⑤: .lybra/role 直接读取 grep 零(除唯一实现 charter_render.workstation_identity / ConnectionResolver)。"""
    hits = [h for h in _grep([f for f in _product_files() if f.endswith(".py")], ROLE_READ_RE)
            if h.split(":", 1)[0] not in ROLE_ALLOWED]
    assert hits == [], "\n".join(hits)
    writer = (REPO_ROOT / "tools" / "aipos_cli" / "enroll_client.py").read_text(encoding="utf-8")
    role_lines = [ln for ln in writer.splitlines() if ROLE_READ_RE.search(ln)]
    assert len(role_lines) == 1 and "role_file = lybra_dir" in role_lines[0], role_lines  # 只在 write_role_file 内
    used = _grep(_lane_python_files(), re.compile(r"WORKSTATION_ROLE_FILE"))
    assert {h.split(":", 1)[0] for h in used} == {"tools/aipos_cli/charter_render.py"}


def test_item4_no_connection_json_join_in_lane():
    """connection.json / config.json 的 .lybra 路径一律经既有原语(service_mode.connection_path / CONFIG_RELATIVE_PATH)。"""
    join = re.compile(r"""["']\.lybra["']\s*/\s*["'](connection\.json|role|config\.json)["']""")
    assert _grep(_lane_python_files(), join) == []


def _workstation(root: Path, role_data: dict | str, conn: dict | None = None) -> Path:
    (root / ".lybra").mkdir(parents=True)
    (root / ".lybra" / "role").write_text(role_data if isinstance(role_data, str) else json.dumps(role_data), encoding="utf-8")
    if conn is not None:
        (root / ".lybra" / "connection.json").write_text(json.dumps(conn), encoding="utf-8")
    return root


def test_item4_workstation_identity_is_the_reader(tmp_path):
    from tools.aipos_cli.charter_render import (
        WorkstationIdentityError,
        is_enrolled_workstation,
        workstation_identity,
        workstation_role_file,
    )
    from tools.aipos_cli.distribution_sync import discover_workstations, workstation_harness

    assert workstation_role_file(tmp_path) is None and not is_enrolled_workstation(tmp_path)
    harness_dir = tmp_path / "cc"
    ws = _workstation(tmp_path / "w1", {"role": "executor", "instance": "exec.f106probe.host1",
                                        "harness": {"kind": "claude-code", "dir": str(harness_dir)}},
                      {"governance_root": "/gov", "mcp": {"rpc_url": "http://g:5/mcp"}, "tokens": []})
    ident = workstation_identity(ws)
    assert ident["gate_url"] == "http://g:5"
    assert ident["harness"] == {"kind": "claude-code", "dir": str(harness_dir)}
    assert is_enrolled_workstation(ws) and discover_workstations(tmp_path) == [ws.resolve()]
    got = workstation_harness(ws)
    assert got["kind"] == "claude-code" and got["dir"] == harness_dir.resolve()
    bad = _workstation(tmp_path / "w2", "{not json")
    with pytest.raises(WorkstationIdentityError):
        workstation_harness(bad)  # 坏 role 文件 = fail-closed(原自读同样拒, 现经唯一实现)


def test_item4_driver_identity_read_via_connection_resolver(tmp_path, monkeypatch):
    """next_resolver 驱动方身份: 治理根 .lybra/role 经 ConnectionResolver 读; env 不冒充工位声明。"""
    from tools.aipos_cli import next_resolver

    gov = _workstation(tmp_path / "gov", {"role": "hbj-advisor", "instance": "adv.f106probe.h"})
    monkeypatch.setenv("LYBRA_ACTOR", "env-actor")
    monkeypatch.setenv("LYBRA_ROLE", "env-role")
    monkeypatch.setattr(next_resolver, "_scoped_driver", lambda: {})
    assert next_resolver._driver_actor(gov) == "adv.f106probe.h"
    assert next_resolver._driver_role_name(gov) == "hbj-advisor"
    empty = tmp_path / "empty"
    empty.mkdir()
    assert next_resolver._driver_actor(empty, connection_json=None) == ""
    assert next_resolver._driver_role_name(empty) == ""
