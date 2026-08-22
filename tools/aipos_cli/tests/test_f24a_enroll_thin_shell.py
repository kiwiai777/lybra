"""AIPOS-F24A 专项测试 —— 发码单实现修真。

三大项:
  A(动词参数面): 未知参数一律报错(dry_run/confirm/单相动词, 禁静默吞 —— F24 证据②);
     governance_root 从项目注册表校验(裸项目名/绝对路径均可; 未注册根 fail-closed)。
  B(薄壳化): CLI roles enroll-code 只调门动词(源级 grep: aipos_cli 无本地发码调用);
     连接源/token/治理根推导 + E2E(stub gate): 出码来自门, 本地零发码副作用。
  C(缺省回落): 不传 governance_root 时码内治理根=发码门服务根(F23 行为保持)。

跑法: python3 -m pytest tools/aipos_cli/tests/test_f24a_enroll_thin_shell.py -q
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

REPO_ROOT = Path(__file__).resolve().parents[2].parents[0]


def _payload(result):
    return result.get("structuredContent", result)


def _make_home(tmp: str) -> tuple[Path, Path]:
    """临时 home + 一个已注册项目 proj-a(双标记)+ 一个未注册目录 proj-b(无 project.json)。"""
    home = Path(tmp) / "home"
    proj_a = home / "proj-a"
    (proj_a / "5_tasks" / "queue").mkdir(parents=True, exist_ok=True)
    (proj_a / "project.json").write_text('{"name": "proj-a"}\n', encoding="utf-8")
    proj_b = home / "proj-b"
    (proj_b / "5_tasks" / "queue").mkdir(parents=True, exist_ok=True)  # 缺 project.json
    return home, proj_a


def _make_gate_root(tmp: str) -> Path:
    root = Path(tmp) / "gov_ws"
    (root / ".lybra").mkdir(parents=True, exist_ok=True)
    conn = root / ".lybra" / "connection.json"
    if not conn.exists():
        conn.write_text(json.dumps({
            "config_version": 1,
            "mcp": {"rpc_url": "http://127.0.0.1:7118/mcp"},
            "tokens": [],
        }), encoding="utf-8")
    return root


class TestUnknownParameterRejection(unittest.TestCase):
    """大项B/F24 证据②: 未知参数必报错, 禁静默吞。"""

    def test_dry_run_unknown_param_rejected(self):
        from tools.mcp_server import tools as mcp
        with tempfile.TemporaryDirectory(prefix="f24a_unk_") as tmp:
            root = _make_gate_root(tmp)
            with patch.object(mcp, "_repo_root", return_value=root):
                resp = mcp.lybra_enroll_code_dry_run({
                    "role": "executor", "owner_authorization_ref": "x",
                    "governance_rooot": "proj-a",  # 拼错: 未知参数
                })
                p = _payload(resp)
                self.assertFalse(p.get("ok"))
                self.assertEqual(p.get("error_code"), "UNKNOWN_PARAMETER")
                self.assertIn("governance_rooot", json.dumps(p, ensure_ascii=False))

    def test_dry_run_typo_ttl_variant_rejected(self):
        from tools.mcp_server import tools as mcp
        with tempfile.TemporaryDirectory(prefix="f24a_unk2_") as tmp:
            root = _make_gate_root(tmp)
            with patch.object(mcp, "_repo_root", return_value=root):
                resp = mcp.lybra_enroll_code_dry_run({
                    "role": "executor", "owner_authorization_ref": "x", "TTL": 100,
                })
                p = _payload(resp)
                self.assertFalse(p.get("ok"))
                self.assertEqual(p.get("error_code"), "UNKNOWN_PARAMETER")

    def test_confirm_unknown_param_rejected(self):
        from tools.mcp_server import tools as mcp
        resp = mcp.lybra_enroll_code_confirm({
            "dry_run_token": "t", "owner_confirmation_token": "OWNER_CONFIRMED",
            "governance_root": "proj-a",  # confirm 阶段不接受发码参数
        })
        p = _payload(resp)
        self.assertFalse(p.get("ok"))
        self.assertEqual(p.get("error_code"), "UNKNOWN_PARAMETER")

    def test_legacy_single_phase_verb_unknown_param_rejected(self):
        from tools.mcp_server import tools as mcp
        with tempfile.TemporaryDirectory(prefix="f24a_unk3_") as tmp:
            root = _make_gate_root(tmp)
            with patch.object(mcp, "_repo_root", return_value=root):
                resp = mcp.lybra_roles_enroll_code({
                    "role": "executor", "owner_authorization_ref": "x", "project": "proj-a",
                })
                p = _payload(resp)
                self.assertFalse(p.get("ok"))
                self.assertEqual(p.get("error_code"), "UNKNOWN_PARAMETER")


class TestGovernanceRootRegistry(unittest.TestCase):
    """大项B: governance_root 从项目注册表校验; 不存在的根必报错(验收②)。"""

    def test_registered_name_and_path_accepted(self):
        from tools.mcp_server import tools as mcp
        with tempfile.TemporaryDirectory(prefix="f24a_reg_") as tmp:
            home, proj_a = _make_home(tmp)
            root = _make_gate_root(tmp)
            env = {"LYBRA_HOME_ROOT": str(home)}
            with patch.object(mcp, "_repo_root", return_value=root), \
                 patch.dict(os.environ, env):
                # 裸项目名
                v, err = mcp._resolve_governance_root_arg("proj-a")
                self.assertIsNone(err)
                self.assertEqual(v, str(proj_a.resolve()))
                # 绝对路径
                v2, err2 = mcp._resolve_governance_root_arg(str(proj_a))
                self.assertIsNone(err2)
                self.assertEqual(v2, str(proj_a.resolve()))

    def test_unregistered_root_rejected_with_registry_listing(self):
        from tools.mcp_server import tools as mcp
        with tempfile.TemporaryDirectory(prefix="f24a_ureg_") as tmp:
            home, _proj_a = _make_home(tmp)
            with patch.dict(os.environ, {"LYBRA_HOME_ROOT": str(home)}):
                for bad in ("proj-b", "proj-c", str(home / "proj-b"), "/home/kiwi/projects/lybra"):
                    v, err = mcp._resolve_governance_root_arg(bad)
                    self.assertIsNone(v, bad)
                    p = _payload(err)
                    self.assertEqual(p.get("error_code"), "UNKNOWN_GOVERNANCE_ROOT")
                    self.assertIn("proj-a", json.dumps(p, ensure_ascii=False), "报错须列出注册表内的项目")

    def test_dry_run_confirm_embeds_specified_governance_root(self):
        """验收②: 动词直发带 governance_root → 码内治理根=指定值。"""
        from tools.aipos_cli.enrollment import decode_self_contained_code
        from tools.mcp_server import tools as mcp
        with tempfile.TemporaryDirectory(prefix="f24a_embed_") as tmp:
            home, proj_a = _make_home(tmp)
            root = _make_gate_root(tmp)
            with patch.object(mcp, "_repo_root", return_value=root), \
                 patch.dict(os.environ, {"LYBRA_HOME_ROOT": str(home)}), \
                 patch.object(mcp, "_reload_token_registry", lambda: None):
                dry = _payload(mcp.lybra_enroll_code_dry_run({
                    "role": "executor", "owner_authorization_ref": "x",
                    "governance_root": "proj-a",
                }))
                self.assertTrue(dry.get("ok"))
                self.assertEqual(dry["preview"]["governance_root"], str(proj_a.resolve()))
                conf = _payload(mcp.lybra_enroll_code_confirm({
                    "dry_run_token": dry["dry_run_token"],
                    "owner_confirmation_token": "OWNER_CONFIRMED",
                }))
                self.assertTrue(conf.get("ok"))
                self.assertEqual(conf["governance_root"], str(proj_a.resolve()))
                decoded = decode_self_contained_code(conf["self_contained_code"])
                self.assertEqual(decoded["governance_root"], str(proj_a.resolve()))
                # 记录仍落在门服务根(与交换/land 同根), 不因 governance_root 改道
                self.assertTrue((root / ".lybra" / "enrollments.json").exists())
                self.assertFalse((proj_a / ".lybra" / "enrollments.json").exists())

    def test_default_falls_back_to_gate_root(self):
        """缺省回落: 不传 governance_root → 码内治理根=发码门服务根(F23 行为保持)。"""
        from tools.aipos_cli.enrollment import decode_self_contained_code
        from tools.mcp_server import tools as mcp
        with tempfile.TemporaryDirectory(prefix="f24a_dflt_") as tmp:
            root = _make_gate_root(tmp)
            with patch.object(mcp, "_repo_root", return_value=root), \
                 patch.object(mcp, "_reload_token_registry", lambda: None):
                dry = _payload(mcp.lybra_enroll_code_dry_run({
                    "role": "executor", "owner_authorization_ref": "x",
                }))
                conf = _payload(mcp.lybra_enroll_code_confirm({
                    "dry_run_token": dry["dry_run_token"],
                    "owner_confirmation_token": "OWNER_CONFIRMED",
                }))
                decoded = decode_self_contained_code(conf["self_contained_code"])
                self.assertEqual(decoded["governance_root"], str(root))


class _StubGateHandler(BaseHTTPRequestHandler):
    """最小 JSON-RPC 门桩: 记录调用与 bearer, dry_run/confirm 按脚本应答。"""

    seen: dict = {"calls": [], "bearers": [], "dry_run_args": {}}
    script: dict = {"dry_run_ok": True, "confirm_ok": True}

    def log_message(self, *a):  # 静音
        pass

    def do_POST(self):
        length = int(self.headers.get("Content-Length") or 0)
        body = json.loads(self.rfile.read(length).decode("utf-8"))
        _StubGateHandler.seen["bearers"].append(str(self.headers.get("Authorization") or ""))
        name = (body.get("params") or {}).get("name") or ""
        _StubGateHandler.seen["calls"].append(name or body.get("method"))
        if body.get("method") == "initialize":
            payload = {"protocolVersion": "2025-03-26", "capabilities": {}, "serverInfo": {"name": "stub"}}
        elif name == "lybra_enroll_code_dry_run":
            _StubGateHandler.seen["dry_run_args"] = (body.get("params") or {}).get("arguments") or {}
            payload = ({"ok": True, "dry_run_token": "stub-dry-token", "preview": {}} if _StubGateHandler.script["dry_run_ok"]
                       else {"ok": False, "error_code": "UNKNOWN_GOVERNANCE_ROOT", "message": "stub reject"})
        elif name == "lybra_enroll_code_confirm":
            payload = ({"ok": True, "code_id": "stub-code-1", "self_contained_code": "LYBRAENROLL1.stub",
                        "paste_text": "/lybra enroll LYBRAENROLL1.stub", "fingerprint": "sha256:stub",
                        "role": "executor", "instance": "exec.stub", "expires_at": "2999-01-01T00:00:00Z",
                        "gate_url": "http://stub:7118", "governance_root": _StubGateHandler.seen["dry_run_args"].get("governance_root"),
                        "transport_token_fingerprint": "sha256:tt"} if _StubGateHandler.script["confirm_ok"]
                       else {"ok": False, "error_code": "STALE_DRY_RUN", "message": "stub stale"})
        else:
            payload = {"ok": False, "message": f"unknown tool {name}"}
        out = json.dumps({"jsonrpc": "2.0", "id": body.get("id"), "result": {"structuredContent": payload}}).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(out)))
        self.end_headers()
        self.wfile.write(out)


class TestCliThinShell(unittest.TestCase):
    """大项A: CLI enroll-code 薄壳 —— 只调门动词, 本地零发码副作用。"""

    def _run_cli(self, *extra):
        return subprocess.run(
            [sys.executable, "-m", "tools.aipos_cli.aipos_cli", "roles", *extra],
            cwd=str(REPO_ROOT), capture_output=True, text=True, timeout=60,
            env={**os.environ, "PYTHONPATH": str(REPO_ROOT), "LYBRA_MCP_TOKEN": ""},
        )

    def test_thin_shell_issues_via_gate_verb(self):
        with tempfile.TemporaryDirectory(prefix="f24a_cli_") as tmp:
            home, proj_a = _make_home(tmp)
            lybra_dir = proj_a / ".lybra"
            lybra_dir.mkdir(parents=True, exist_ok=True)
            server = ThreadingHTTPServer(("127.0.0.1", 0), _StubGateHandler)
            port = server.server_address[1]
            threading.Thread(target=server.serve_forever, daemon=True).start()
            try:
                (lybra_dir / "connection.json").write_text(json.dumps({
                    "config_version": 1,
                    "workspace_root": str(proj_a),
                    "mcp": {"rpc_url": f"http://127.0.0.1:{port}/mcp"},
                    "tokens": [{"role": "advisor", "token": "STUB-ADVISOR-TOKEN", "token_ref": "svc-advisor", "scopes": []}],
                }), encoding="utf-8")
                _StubGateHandler.seen = {"calls": [], "bearers": [], "dry_run_args": {}}
                _StubGateHandler.script = {"dry_run_ok": True, "confirm_ok": True}
                proc = self._run_cli(
                    "--workspace-root", str(proj_a),
                    "enroll-code", "--role", "executor", "--instance", "exec.stub",
                    "--owner-authorization-ref", "test-ref", "--json",
                )
                self.assertEqual(proc.returncode, 0, proc.stderr)
                out = json.loads(proc.stdout)
                self.assertEqual(out["issued_via"], "gate_verb_thin_shell(F24A)")
                self.assertEqual(out["self_contained_code"], "LYBRAENROLL1.stub")
                self.assertEqual(out["governance_root"], str(proj_a))
                # 薄壳证据: 两阶段动词都被调, bearer=顾问 token, dry_run 带 governance_root
                calls = _StubGateHandler.seen["calls"]
                self.assertIn("lybra_enroll_code_dry_run", calls)
                self.assertIn("lybra_enroll_code_confirm", calls)
                self.assertIn("Bearer STUB-ADVISOR-TOKEN", _StubGateHandler.seen["bearers"])
                self.assertEqual(_StubGateHandler.seen["dry_run_args"].get("governance_root"), str(proj_a))
                # 本地零发码副作用: CLI 不写 enrollments(码由门进程产出)
                self.assertFalse((lybra_dir / "enrollments.json").exists())
            finally:
                server.shutdown()

    def test_gate_down_reports_no_bare_work_and_no_fallback(self):
        with tempfile.TemporaryDirectory(prefix="f24a_down_") as tmp:
            home, proj_a = _make_home(tmp)
            lybra_dir = proj_a / ".lybra"
            lybra_dir.mkdir(parents=True, exist_ok=True)
            (lybra_dir / "connection.json").write_text(json.dumps({
                "config_version": 1, "workspace_root": str(proj_a),
                "mcp": {"rpc_url": "http://127.0.0.1:1/mcp"},
                "tokens": [{"role": "advisor", "token": "T", "token_ref": "svc-advisor", "scopes": []}],
            }), encoding="utf-8")
            proc = self._run_cli(
                "--workspace-root", str(proj_a),
                "enroll-code", "--role", "executor", "--owner-authorization-ref", "test-ref",
            )
            self.assertEqual(proc.returncode, 1)
            self.assertIn("禁自行诊断修复门/服务/部署", proc.stderr)
            self.assertIn("报告顾问", proc.stderr)
            # 绝不回退本地发码
            self.assertFalse((lybra_dir / "enrollments.json").exists())

    def test_gate_rejection_surfaced_verbatim(self):
        with tempfile.TemporaryDirectory(prefix="f24a_rej_") as tmp:
            home, proj_a = _make_home(tmp)
            lybra_dir = proj_a / ".lybra"
            lybra_dir.mkdir(parents=True, exist_ok=True)
            server = ThreadingHTTPServer(("127.0.0.1", 0), _StubGateHandler)
            port = server.server_address[1]
            threading.Thread(target=server.serve_forever, daemon=True).start()
            try:
                (lybra_dir / "connection.json").write_text(json.dumps({
                    "config_version": 1, "workspace_root": str(proj_a),
                    "mcp": {"rpc_url": f"http://127.0.0.1:{port}/mcp"},
                    "tokens": [{"role": "advisor", "token": "T", "token_ref": "svc-advisor", "scopes": []}],
                }), encoding="utf-8")
                _StubGateHandler.seen = {"calls": [], "bearers": [], "dry_run_args": {}}
                _StubGateHandler.script = {"dry_run_ok": False, "confirm_ok": True}
                proc = self._run_cli(
                    "--workspace-root", str(proj_a),
                    "enroll-code", "--role", "executor", "--owner-authorization-ref", "test-ref",
                )
                self.assertEqual(proc.returncode, 1)
                self.assertIn("UNKNOWN_GOVERNANCE_ROOT", proc.stderr)  # 拒因原文透传
            finally:
                server.shutdown()


if __name__ == "__main__":
    unittest.main()
