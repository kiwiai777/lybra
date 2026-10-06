"""AIPOS-F115 失败即拒清扫(gap #63/#67/#58/#61/#64)靶场夹具。

各件用例均「main 红、本分支绿」: 新符号一律在用例内导入/以通用异常类型断言, 使 main 上逐条红而非整文件收集失败。

件① 存量 bug: draft_writer.create_draft 的 SchemaLoadError 分支引用未定义 result(NameError);
      board_adapter._write_fix_closure_derivation_record 引用未定义 closure_id(NameError, 记录类型错写 closure)。
件② loop_context 吞错: connection.json / .lybra/role 坏被吞成「未声明」静默降级; rstrip("/mcp") 按字符集剥。
件③ 产品代码 except-pass / 裸吞: custom_roles 注册表读错误报「不在注册表」、verb_contract、finalize 读卡、gate_drift、
      preview、service_mode 轮换日志、PreAuthorized 读卡、凭据热重载、连接诊断;不变量 f 在 F87 棘轮(本文件只断言车道内清零)。
件④ state_lint 推导状态走 require_frontmatter; PyYAML/stdlib 两路对 NEL/LS/PS 同写(写后回读逐字一致)。

跑法: python3 -m pytest tests/test_aipos_f115_fail_closed.py -q
"""
from __future__ import annotations

import importlib.util
import json
import socket
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
LANE_PREFIXES = ("tools/aipos_cli/", "tools/loop_context.py", "tools/mcp_server/")


# ---------------------------------------------------------------------------
# 件① 存量 NameError
# ---------------------------------------------------------------------------

def _draft_meta(task_id: str) -> dict:
    return {
        "task_id": task_id,
        "title": "F115 draft probe",
        "project": "lybra",
        "assigned_to": "agent-01",
        "agent_instance": "agent-01",
        "context_bundle": "agent-01",
        "task_mode": "code",
        "priority": "medium",
        "status": "pending",
        "created_by": "tester",
        "needs_owner": False,
        "output_target": "tools/aipos_cli/",
        "artifact_policy": "formal_write",
    }


def test_item1_create_draft_schema_load_error_is_warning_not_nameerror(tmp_path, monkeypatch):
    """gap #63: 纪律段派生抛 SchemaLoadError 时, 原分支 result.setdefault(...) 引用未定义 result → NameError 掀翻 create_draft。"""
    from tools.aipos_cli import draft_writer, machine_zone
    from tools.schema_loader import SchemaLoadError

    def _boom(*_a, **_k):
        raise SchemaLoadError("纪律段声明缺失(F115 靶场注入)")

    monkeypatch.setattr(machine_zone, "derive_machine_zone_纪律段", _boom)
    result = draft_writer.create_draft(tmp_path, _draft_meta("AIPOS-F115P1"), "## Goal\n\nprobe\n", dry_run=True)
    assert any("工作纪律节派生失败" in w and "F115 靶场注入" in w for w in result["warnings"]), result["warnings"]


def test_item1_fix_closure_derivation_record_written_at_declared_location(tmp_path):
    """F112 RETURN 缺口: _write_fix_closure_derivation_record 引用未定义 closure_id(NameError), 且类型错写 N6 closure。"""
    from tools.aipos_cli.board_adapter import _write_fix_closure_derivation_record

    node = json.loads((REPO_ROOT / "schema" / "transitions.schema.json").read_text(encoding="utf-8"))["nodes"]["fix_card_closure"]
    rel = _write_fix_closure_derivation_record(
        resolved_root=tmp_path,
        fix_card_closure_node=node,
        fix_task_id="AIPOS-FX9-fix1",
        source_task_id="AIPOS-FX9",
        derived_audit_task_id="AIPOS-FX9R",
        verdict_id="verdict_fx9_fix1_pass",
        derived_at="2026-10-06T06:20:34Z",
    )
    assert rel == "5_tasks/records/fix_closures/AIPOS-FX9-fix1/derivation_AIPOS-FX9-fix1_20261006_062034.md", rel
    text = (tmp_path / rel).read_text(encoding="utf-8")
    for marker in ("record_type: fix_closure_derivation", "fix_task_id: AIPOS-FX9-fix1", "derived_audit_task_id: AIPOS-FX9R",
                   "derived_at:", "verdict_id: verdict_fx9_fix1_pass"):
        assert marker in text, marker
    assert not (tmp_path / "5_tasks" / "records" / "closures").exists(), "不得落到 N6 closure 记录位"


# ---------------------------------------------------------------------------
# 件② loop_context 吞错 → fail-closed 带出口
# ---------------------------------------------------------------------------

def _lybra(tmp_path: Path, files: dict[str, str]) -> Path:
    ws = tmp_path / "ws"
    (ws / ".lybra").mkdir(parents=True)
    for name, text in files.items():
        (ws / ".lybra" / name).write_text(text, encoding="utf-8")
    return ws


def test_item2_corrupt_connection_json_refused_not_degraded_to_env(tmp_path):
    from tools.loop_context import ConnectionResolver

    ws = _lybra(tmp_path, {"connection.json": "{not json"})
    env = {"LYBRA_GATE_URL": "http://env-should-not-win:9/mcp"}
    with pytest.raises(ValueError, match="工位声明文件 .*connection.json.* 不可读/格式坏.*出口"):
        ConnectionResolver.resolve_gate_url(workspace_root=ws, env=env)
    with pytest.raises(ValueError, match="工位声明文件 .*connection.json"):
        ConnectionResolver.resolve_identity(workspace_root=ws, env=env)


def test_item2_corrupt_role_file_refused_not_treated_as_undeclared(tmp_path):
    from tools.loop_context import ConnectionResolver

    ws = _lybra(tmp_path, {"role": '{"role": "executor", '})
    with pytest.raises(ValueError, match="工位声明文件 .*role.* 不可读/格式坏.*lybra enroll"):
        ConnectionResolver.resolve_identity(workspace_root=ws, env={"LYBRA_ROLE": "env-role"})
    with pytest.raises(ValueError, match="工位声明文件 .*role"):
        ConnectionResolver.resolve_role(workspace_root=ws, env={})


def test_item2_undeclared_files_still_fall_through_in_declared_order(tmp_path):
    """回归护栏: 文件不存在 = 未声明, 照声明序落 env(不是坏)。"""
    from tools.loop_context import ConnectionResolver

    ws = _lybra(tmp_path, {})
    assert ConnectionResolver.resolve_gate_url(workspace_root=ws, env={"LYBRA_GATE_URL": "http://env:3/mcp"}) == "http://env:3/mcp"
    ident = ConnectionResolver.resolve_identity(workspace_root=ws, env={"LYBRA_ROLE": "executor"})
    assert ident["role"]["value"] == "executor" and ident["role"]["source"] == "env:LYBRA_ROLE"


def test_item2_schema_gate_url_stripped_by_single_impl_not_charset(tmp_path):
    """原 rstrip("/mcp") 按字符集剥: 以 m/c/p 结尾的主机名被削(http://gate.comp → http://gate.co)。"""
    from tools.loop_context import ConnectionResolver

    empty = tmp_path / "empty"
    empty.mkdir()
    for given, expected in (
        ("http://gate.comp", "http://gate.comp"),
        ("http://gate.example.mcp", "http://gate.example.mcp"),
        ("http://h:7118/mcp", "http://h:7118"),
        ("http://h:7118/", "http://h:7118"),
    ):
        got = ConnectionResolver.resolve_identity(workspace_root=empty, env={}, schema_gate_url=given)["gate_url"]
        assert got["value"] == expected and got["source"] == "schema:urls.gate_local", (given, got)


# ---------------------------------------------------------------------------
# 件③ except-pass / 裸吞 → 精确捕获 + 明确拒因或告警
# ---------------------------------------------------------------------------

def _broken_registry(monkeypatch):
    from tools.aipos_cli import custom_roles

    def _loader(_home):
        raise OSError("permission denied(F115 靶场注入)")

    monkeypatch.setattr(custom_roles, "_gate_registry_loader", lambda: _loader)


def test_item3_custom_roles_registry_read_error_not_reported_as_unknown_role(tmp_path, monkeypatch):
    """gap #58: 注册表读错被 except Exception: return {} 吞 → 拒因误报「不在注册表」。"""
    from tools.aipos_cli.custom_roles import load_custom_roles, resolve_role_to_class

    _broken_registry(monkeypatch)
    proj = tmp_path / "home" / "proj"
    proj.mkdir(parents=True)
    with pytest.raises(RuntimeError, match="门注册表读取失败.*不是「角色不在注册表」.*出口"):
        load_custom_roles(proj)
    with pytest.raises(RuntimeError, match="门注册表读取失败"):
        resolve_role_to_class("hbj-coder", proj, required=True)


def test_item3_scope_role_map_does_not_swallow_registry_error(tmp_path, monkeypatch):
    """gap #61: verb_contract.get_scope_role_map 的 except Exception: pass 把注册表读错吞成「无自定义角色」。"""
    from tools.aipos_cli.verb_contract import get_scope_role_map

    _broken_registry(monkeypatch)
    proj = tmp_path / "home" / "proj"
    proj.mkdir(parents=True)
    assert get_scope_role_map()  # 不给项目根 = 只内建, 照常
    with pytest.raises(RuntimeError, match="门注册表读取失败"):
        get_scope_role_map(str(proj))


def test_item3_finalize_card_unreadable_blocks_instead_of_skipping_branch_gate(tmp_path, monkeypatch):
    """F108R: finalize 读卡 output_target/task_mode 失败原 except Exception: pass 降级跳过「代码任务缺分支硬拒」(fail-open)。"""
    from tools.aipos_cli import finalize as fz

    task = "AIPOS-F115FZ"
    gov = tmp_path / "gov"
    product = tmp_path / "product"
    gov.mkdir()
    product.mkdir()
    integrate_calls: list[dict] = []

    def _raise(*_a, **_k):
        raise ValueError("卡 frontmatter 不可读(F115 靶场注入)")

    monkeypatch.setattr(fz, "_report_frontmatter_verdict_for_display", lambda *a, **k: {"report_path": None, "report_verdict": None})
    monkeypatch.setattr(fz, "_load_branch_integration", lambda: {"branch_pattern": "card/{task_id}", "base_branch": "main"})
    monkeypatch.setattr(fz, "_git_rev_parse_head", lambda root: "c" * 40)
    monkeypatch.setattr(fz, "_git_branch_exists", lambda root, b: False)
    monkeypatch.setattr(fz, "check_task_can_finalize", lambda *a, **k: {"can_finalize": True, "reason": "ok", "verdict_id": "verdict_x", "verdict": "PASS"})
    monkeypatch.setattr(fz, "check_stage_archive_gate", lambda *a, **k: {"passed": True, "message": "ok"})
    monkeypatch.setattr(fz, "_check_deployment_integrity", lambda *a, **k: {"integrity_ok": True, "message": "ok"})
    monkeypatch.setattr(fz, "_ensure_on_main_branch", lambda *a, **k: None)
    monkeypatch.setattr("tools.aipos_cli.deploy_gate.check_deployment_branch", lambda *a, **k: {"on_required_branch": True, "message": "main"})
    monkeypatch.setattr("tools.aipos_cli.task_loader.find_task_by_id", _raise)
    monkeypatch.setattr(fz, "_integrate_card_branch", lambda **k: integrate_calls.append(k) or {"blocked": True, "action": "probe", "message": "probe"})

    result = fz.finalize_task(task, "advisor.lybra.test", product, governance_root=gov, push=False, deploy=False)
    assert integrate_calls == [], "读卡失败后不得继续分支整合(原降级跳过)"
    assert result["verdict"] == "BLOCK" and "读卡失败" in result["message"] and "F115 靶场注入" in result["message"], result


def test_item3_gate_drift_unreadable_version_is_not_no_deployment(tmp_path):
    from tools.aipos_cli.gate_drift import _read_deployed_commit

    (tmp_path / ".deploy" / "current" / "VERSION").mkdir(parents=True)  # 存在但读不了(是目录)
    with pytest.raises(RuntimeError, match="VERSION 存在但不可读.*出口"):
        _read_deployed_commit(tmp_path)
    (tmp_path / ".deploy" / "current" / "VERSION").rmdir()
    assert _read_deployed_commit(tmp_path) is None  # 真无部署 = None(语义不变)


def test_item3_preview_card_unreadable_refused(tmp_path):
    from tools.aipos_cli.preview import build_preview

    (tmp_path / "card.md").mkdir()  # 卡已定位却读不出
    task = {"task_id": "AIPOS-F115PV", "title": "t", "metadata": {}, "repo_root": str(tmp_path), "path": "card.md", "verdict": "PASS"}
    with pytest.raises(ValueError, match="卡文件 .*card.md 不可读"):
        build_preview(task, "agent-01", include_body=True)


def test_item3_rotation_log_failure_returns_warning(tmp_path, monkeypatch):
    from tools.aipos_cli import service_mode

    blocker = tmp_path / "log_is_dir"
    blocker.mkdir()
    monkeypatch.setattr(service_mode, "_rotation_log_path", lambda: blocker)
    got = service_mode._append_rotation_log(actor="a", owner_authorization_ref=None, bindings_before={}, bindings_after={},
                                            binding_changes=[], workspace_root=tmp_path)
    assert isinstance(got, str) and got.startswith("ROTATION_LOG_WRITE_FAILED") and str(blocker) in got, got


def test_item3_preauthorized_autorelease_refuses_when_task_unloadable(tmp_path):
    """PreAuthorized 自动放行读卡失败原 except Exception: pass 继续放行 → needs_owner 闸被静默绕过。"""
    from tools.mcp_server.tools import _preauthorized_claim_autorelease

    out = _preauthorized_claim_autorelease(
        args={}, repo_root=tmp_path, task_id=None, task_path="5_tasks/queue/pending/missing.md",
        canonical_agent_instance="exec.f115.probe", policy_id="pol_probe", resolution_label="probe", reg_available=False,
    )
    payload = out["structuredContent"]
    assert out["isError"] is True and payload["error_code"] == "TASK_LOAD_FAILED", payload


def test_item3_token_registry_reload_failure_is_reported(monkeypatch):
    from tools.mcp_server import http_sse, tools as mcp_tools

    class _Server:
        def reload_token_registry(self):
            raise OSError("registry file vanished(F115 靶场注入)")

    monkeypatch.setattr(http_sse, "_CURRENT_SERVER", _Server())
    got = mcp_tools._reload_token_registry()
    assert isinstance(got, str) and got.startswith("TOKEN_REGISTRY_RELOAD_FAILED") and "F115 靶场注入" in got, got


def test_item3_connection_diagnosis_carries_probe_error():
    from tools.aipos_cli.confirm_client import _diagnose_connection_failure

    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()  # 端口已释放无人听 → 连接拒绝
    text = _diagnose_connection_failure(f"http://127.0.0.1:{port}", OSError("probe"))
    assert "配置URL可达: ✗ (" in text, text


def test_item3_lane_has_zero_broad_except_pass():
    """不变量 f(F87 棘轮)在本卡车道内清零: 车道外残余只在基线清单(lane_blocked)。"""
    spec = importlib.util.spec_from_file_location("f87_ratchet", REPO_ROOT / "tests" / "test_aipos_f87_fragmentation_ratchet.py")
    ratchet = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(ratchet)  # type: ignore[union-attr]
    hits = ratchet.scan_f_broad_except_pass(ratchet.product_files())
    assert [h for h in hits if str(h["file"]).startswith(LANE_PREFIXES)] == [], hits
    baseline_f = ratchet.load_baseline()["invariants"]["f"]["entries"]
    assert baseline_f and all(not str(e["file"]).startswith(LANE_PREFIXES) for e in baseline_f), baseline_f


# ---------------------------------------------------------------------------
# 件④ state_lint require_frontmatter + NEL 两路同判
# ---------------------------------------------------------------------------

def test_item4_derive_state_refuses_unreadable_record(tmp_path):
    """gap #64: _derive_state_from_records 原忽略解析告警 + except Exception: continue, 坏记录被静默跳过、状态推导可能错。"""
    from tools.aipos_cli import state_lint

    task = "AIPOS-F115SL"
    rec = tmp_path / "5_tasks" / "records"
    (rec / "claims" / task).mkdir(parents=True)
    (rec / "claims" / task / "claim_ok.md").write_text("---\nrecord_type: claim\nclaimed_at: '2026-10-06T01:00:00Z'\n---\n", encoding="utf-8")
    (rec / "closures" / task).mkdir(parents=True)
    (rec / "closures" / task / "closure_bad.md").write_text("---\nclosed_at: [unclosed\n  - : :\n---\n", encoding="utf-8")
    with pytest.raises(ValueError, match="closure_bad.md"):
        state_lint._derive_state_from_records(tmp_path, task)
    repaired = state_lint._repair_queue_state(tmp_path, task, dry_run=True)
    assert repaired["repaired"] is False and "记录 frontmatter 不可读, 拒修" in repaired["message"], repaired


def _render_both_ways(value: str) -> dict[str, object]:
    import tools.aipos_cli.frontmatter as fm
    import tools.aipos_cli.record_writer as rw

    out: dict[str, object] = {}
    saved = rw.yaml
    try:
        for mode, mod in (("pyyaml", saved), ("stdlib", None)):
            rw.yaml = mod
            try:
                text = rw.render_markdown({"k": value, "t": "plain"}, "body")
            except ValueError as exc:
                out[mode] = f"REFUSE: {exc}"
                continue
            data, _body, warnings = fm.parse_markdown_frontmatter(text)
            out[mode] = ("WRITE", data.get("k") == value, warnings)
    finally:
        rw.yaml = saved
    return out


@pytest.mark.parametrize("value", ["nel\x85x", "ls x", "ps x", "all\x85  end"])
def test_item4_yaml_line_break_chars_same_verdict_both_paths(value):
    """gap #64: 有 PyYAML 时含 NEL/LS/PS 的值写后回读拒写, stdlib 路径能写(两路不对称)→ 统一为两路同写且逐字回读。"""
    import tools.aipos_cli.record_writer as rw

    if rw.yaml is None:
        pytest.fail("本夹具需要 PyYAML(两路对照); 环境缺 PyYAML 即红, 不 skip")
    got = _render_both_ways(value)
    assert got["pyyaml"] == got["stdlib"] == ("WRITE", True, []), got
