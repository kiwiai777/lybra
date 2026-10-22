"""AIPOS-F216 — 碎片化整理 G(F208 §6 P3-2~P3-6 / §7 卡组 G / §8 R9): 死代码清扫与基线收缩。

- R9 零引用顶层定义棘轮: 产品代码(git ls-files 的 tools/ 与 web/ 下 .py, 测试目录与 test_* 除外)每个顶层 def / class, 产品代码里零引用
  (AST: Name / Attribute / from-import, 另计 schema 与代码中整值点路径的动态分派指针——与 R8 同一引用计数口
  test_aipos_f214_guard_ratchets.product_references; 模块协议名 __x__ 除外)= 入基线 tests/f216_zero_ref_toplevel_baseline.jsonl
  (一行一条带理由, 只减不增); 附「新增零引用函数即红」反例。
- 件② 无人读声明: verbs lybra_project_validation_root 接上真实读者(CLI 退出码读其 exit_codes); 门从未登记的 4 个 MCP 面
  (lybra_project_new_* / lybra_project_set_repo_*)删声明与门侧实现, CLI 恒走本机唯一实现; enums task_mode.routing_rules 与
  schema_loader.get_task_mode_routing 删除。
- 件④ 审计卡识别卡面层单源 audit_derivation.is_audit_task_mode(声明 enums.schema task_mode.audit_card)。
- 件⑤ web 尾巴: 旧 CJK 守卫节点删除、run-all exclude 行删除。
只读扫描 + 临时目录夹具; 不起门、不读凭据、不碰真实治理根。
"""
from __future__ import annotations

import ast
import json
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from ratchet_baseline import read_entries  # noqa: E402
from test_aipos_f214_guard_ratchets import _is_product, product_references  # noqa: E402  — R8 / R9 同一引用计数口

R9_BASELINE = Path(__file__).resolve().parent / "f216_zero_ref_toplevel_baseline.jsonl"
PRODUCT_PREFIXES = ("tools/", "web/")


def _show(text: str) -> None:
    print(text, flush=True)


# ===========================================================================
# R9 零引用顶层定义
# ===========================================================================

def product_files(root: Path = REPO_ROOT) -> list[str]:
    """git ls-files 的产品 .py(tools/ 与 web/ 下, 测试除外)。git 不可用 = 抛(fail-closed)。"""
    out = subprocess.run(["git", "ls-files", "--", *PRODUCT_PREFIXES], cwd=root, capture_output=True, text=True, check=True)
    return sorted(rel for rel in out.stdout.split() if rel.endswith(".py") and _is_product(Path(rel)) and (root / rel).is_file())


def zero_ref_toplevel(root: Path = REPO_ROOT, files: list[str] | None = None) -> list[dict[str, str]]:
    """[{file, name, kind}]: 顶层 def / class 在产品代码里零引用(模块协议名 __x__ 除外)。"""
    refs = product_references(root)
    hits: list[dict[str, str]] = []
    for rel in files if files is not None else product_files(root):
        for node in ast.parse((root / rel).read_text(encoding="utf-8")).body:
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                continue
            if node.name.startswith("__") and node.name.endswith("__"):
                continue
            if refs[node.name] == 0:
                hits.append({"file": rel, "name": node.name, "kind": "class" if isinstance(node, ast.ClassDef) else "def"})
    return hits


def _r9_key(entry: dict) -> tuple[str, str, str]:
    return (str(entry["file"]), str(entry["name"]), str(entry["kind"]))


def test_r9_baseline_well_formed():
    for entry in read_entries(R9_BASELINE):
        assert set(entry) == {"file", "name", "kind", "reason"} and str(entry["reason"]).strip(), entry
        assert entry["kind"] in ("def", "class"), entry


def test_r9_zero_ref_toplevel_only_shrink():
    hits = zero_ref_toplevel()
    allowed = {_r9_key(e) for e in read_entries(R9_BASELINE)}
    current = {_r9_key(h) for h in hits}
    new, stale = sorted(current - allowed), sorted(allowed - current)
    _show(f"[棘轮 R9] 产品顶层定义零引用 {len(hits)} 个; 基线 {len(allowed)} 条")
    for h in hits:
        _show(f"  零引用 [{h['kind']}] {h['file']}: {h['name']}")
    assert not new, f"新增零引用顶层定义(删掉, 或接上产品调用方; 只剩测试调用 = 连同测试改调现行入口或删除): {new}"
    assert not stale, f"基线条目已不存在(已删 / 已接上调用方)→ 从 {R9_BASELINE.name} 删除这些行(只减不增): {stale}"


def test_r9_red_on_new_zero_ref_function(tmp_path):
    """故意改一侧即红: 临时产品树新增一个无人调用的顶层函数 = 命中; 有调用方 / 被 schema 整值点路径声明为分派目标 = 不命中。"""
    pkg = tmp_path / "tools" / "aipos_cli"
    pkg.mkdir(parents=True)
    (tmp_path / "schema").mkdir()
    (pkg / "probe_a.py").write_text("def used():\n    return 1\n\n\ndef lonely_probe():\n    return 2\n\n\nclass LonelyProbe:\n    pass\n\n\n"
                                    "def dispatched_probe():\n    return 3\n", encoding="utf-8")
    (pkg / "probe_b.py").write_text("from tools.aipos_cli.probe_a import used\n\nVALUE = used()\n", encoding="utf-8")
    (tmp_path / "schema" / "p.schema.json").write_text(json.dumps({"execute": "tools.aipos_cli.probe_a.dispatched_probe"}), encoding="utf-8")
    files = ["tools/aipos_cli/probe_a.py", "tools/aipos_cli/probe_b.py"]
    got = {(h["name"], h["kind"]) for h in zero_ref_toplevel(tmp_path, files)}
    _show(f"[R9 反例] {sorted(got)}")
    assert got == {("lonely_probe", "def"), ("LonelyProbe", "class")}
    allowed = {_r9_key(e) for e in read_entries(R9_BASELINE)}
    assert ("tools/aipos_cli/probe_a.py", "lonely_probe", "def") not in allowed  # 不在基线 = 只减不增断言即红


def test_r9_cleared_items_stay_gone():
    """本卡删除项(F208 P3-2 / P3-3 / P3-5 / P3-6 + 件② 级联)不在产品代码, 也不在 R9 基线。"""
    gone = {
        "tools/aipos_cli/worktree_cleanup.py": "WorktreeCleanupError",
        "tools/aipos_cli/next_resolver.py": "verdict_report_ready",
        "tools/aipos_cli/task_complexity.py": "dependencies_satisfied",
        "tools/aipos_cli/board_adapter.py": "validate_draft",
        "tools/aipos_cli/owner_inbox.py": "find_item",
        "tools/aipos_cli/queue_mutation.py": "_append_unique_list",
        "tools/aipos_cli/autonomy_policy.py": "envelope_trace_output",
        "tools/schema_loader.py": "get_task_mode_routing",
        "tools/aipos_cli/workspace_config.py": "set_project_repo",
    }
    for rel, name in gone.items():
        names = {n.name for n in ast.parse((REPO_ROOT / rel).read_text(encoding="utf-8")).body if hasattr(n, "name")}
        assert name not in names, (rel, name)
    ba = {n.name for n in ast.parse((REPO_ROOT / "tools/aipos_cli/board_adapter.py").read_text(encoding="utf-8")).body if hasattr(n, "name")}
    assert not {"block_task", "complete_task", "reopen_task"} & ba
    nr = {n.name for n in ast.parse((REPO_ROOT / "tools/aipos_cli/next_resolver.py").read_text(encoding="utf-8")).body if hasattr(n, "name")}
    assert "scan_project" not in nr and "scan_project_view" in nr
    for rel in ("tools/aipos_cli/project_map.py", "tools/cleanup_governance_residue.py", "tools/inject_governance_status_headers.py",
                "task_cards/AIPOS-276/test_aipos276.py"):
        assert not (REPO_ROOT / rel).exists(), rel
    baseline_names = {e["name"] for e in read_entries(R9_BASELINE)}
    assert not baseline_names & set(gone.values())


# ===========================================================================
# 件② 无人读声明: 接上真实读者 / 删声明
# ===========================================================================

def _verbs() -> dict:
    return json.loads((REPO_ROOT / "schema" / "verbs.schema.json").read_text(encoding="utf-8"))["verbs"]


def test_item2_validation_root_exit_codes_read_from_contract():
    """lybra_project_validation_root 动词契约的读者 = CLI 薄壳退出码(禁写死数字); landing_failed 与写后落账声明同值。"""
    from tools.aipos_cli.verb_contract import declared_exit_codes

    src = (REPO_ROOT / "tools" / "aipos_cli" / "aipos_cli.py").read_text(encoding="utf-8")
    fn = next(n for n in ast.parse(src).body if isinstance(n, ast.FunctionDef) and n.name == "_validation_root_cli")
    literal_returns = [ast.unparse(r) for r in ast.walk(fn) if isinstance(r, ast.Return) and isinstance(r.value, ast.Constant)]
    codes = declared_exit_codes("lybra_project_validation_root")
    landing = _verbs()["lybra_governance_commit"]["post_write_landing"]["landing_failed_exit_code"]
    _show(f"[件②·验证根退出码] 声明 {codes}; 写后落账 landing_failed_exit_code={landing}; 字面 return {literal_returns}")
    assert "declared_exit_codes('lybra_project_validation_root')" in ast.unparse(fn) and literal_returns == []
    assert set(codes) == {"ok", "refused", "usage", "landing_failed"} and codes["landing_failed"] == landing


def test_item2_validation_root_cli_exit_code_follows_declaration(tmp_path, monkeypatch, capsys):
    """改声明一侧即跟随: 把 usage 出口码改成 9, CLI 参数错(--id 与 --run-id 不同给)即退 9。"""
    from tools.aipos_cli import aipos_cli, verb_contract

    real = verb_contract.declared_exit_codes

    def _patched(verb: str) -> dict[str, int]:
        codes = dict(real(verb))
        if verb == "lybra_project_validation_root":
            codes["usage"] = 9
        return codes

    monkeypatch.setattr(verb_contract, "declared_exit_codes", _patched)
    monkeypatch.setenv("HOME", str(tmp_path))
    rc = aipos_cli.main(["project", "validation-root", "helper", "--id", "vr-x"])
    err = capsys.readouterr().err
    _show(f"[件②·反例] usage 改 9 → rc={rc} {err.strip()}")
    assert rc == 9 and "--id 与 --run-id 须同给" in err


def test_item2_unregistered_mcp_surfaces_removed():
    """门从未登记的 4 个 MCP 面: 声明删除(改为 CLI 本机动词 lybra_project_new / lybra_project_set_repo), 门侧实现删除, CLI 不再调门。"""
    from tools.mcp_server.tools import TOOL_HANDLERS

    verbs = _verbs()
    removed = ("lybra_project_new_dry_run", "lybra_project_new_confirm", "lybra_project_set_repo_dry_run", "lybra_project_set_repo_confirm")
    tools_src = (REPO_ROOT / "tools" / "mcp_server" / "tools.py").read_text(encoding="utf-8")
    cli_src = (REPO_ROOT / "tools" / "aipos_cli" / "aipos_cli.py").read_text(encoding="utf-8")
    for name in removed:
        assert name not in verbs and name not in TOOL_HANDLERS and f"def {name}(" not in tools_src and f'"{name}"' not in cli_src, name
    for name, impl in (("lybra_project_new", "tools/aipos_cli/workspace_config.scaffold_project"),
                       ("lybra_project_set_repo", "tools/aipos_cli/workspace_config.update_project_repo")):
        assert verbs[name]["surface"] == ["cli"] and verbs[name]["single_implementation"] == impl, name
    mcp_declared = sorted(n for n, v in verbs.items() if "mcp" in (v.get("surface") or []))
    _show(f"[件②·MCP 面] 声明 mcp {len(mcp_declared)} 个, 门注册 {len(TOOL_HANDLERS)} 个; 未注册 {sorted(set(mcp_declared) - set(TOOL_HANDLERS))}")
    assert set(mcp_declared) <= set(TOOL_HANDLERS)


def test_item2_project_new_with_home_connection_json_stays_local(tmp_path, monkeypatch, capsys):
    """原 ~/.lybra/connection.json 在场 = 经门动词(恒失败); 现恒为本机脚手架, 不取凭据、不连门。"""
    from tools.aipos_cli import aipos_cli, confirm_client

    userhome = tmp_path / "userhome"
    (userhome / ".lybra").mkdir(parents=True)
    (userhome / ".lybra" / "connection.json").write_text(json.dumps({"config_version": 1, "mcp": {"rpc_url": "http://127.0.0.1:9/mcp"},
                                                                      "tokens": []}), encoding="utf-8")
    monkeypatch.setenv("HOME", str(userhome))
    calls: list[str] = []
    monkeypatch.setattr(confirm_client, "GateClient", lambda *a, **k: calls.append("connect"))
    monkeypatch.setattr(confirm_client, "load_gate_client_token", lambda *a, **k: calls.append("token"))
    home = tmp_path / "home"
    rc = aipos_cli.main(["project", "new", "probe-f216", "--home-root", str(home), "--actor", "owner.test"])
    out = capsys.readouterr().out
    _show(f"[件②·project new(connection.json 在场)] rc={rc} 门调用={calls}\n{out}")
    assert rc == 0 and "(local scaffold)" in out and calls == [] and (home / "probe-f216" / "project.json").is_file()


def test_item2_routing_rules_and_reader_removed():
    enums = json.loads((REPO_ROOT / "schema" / "enums.schema.json").read_text(encoding="utf-8"))["enums"]["task_mode"]
    import tools.schema_loader as sl

    assert "routing_rules" not in enums and not hasattr(sl, "get_task_mode_routing") and "get_task_mode_routing" not in sl.__all__


# ===========================================================================
# 件④ 审计卡识别卡面层单源
# ===========================================================================

def test_item4_is_audit_task_mode_reads_declaration(monkeypatch):
    from tools.aipos_cli import audit_derivation as ad
    from tools.schema_loader import SchemaLoadError

    enums = json.loads((REPO_ROOT / "schema" / "enums.schema.json").read_text(encoding="utf-8"))["enums"]["task_mode"]
    flagged = sorted(v["value"] for v in enums["values"] if v.get("audit_card") is True)
    got = {m: ad.is_audit_task_mode(m) for m in ("audit", " AUDIT ", "code", "validation", "", None, "bogus")}
    _show(f"[件④·声明 audit_card] {flagged} 判定 {got}")
    assert flagged == ["audit"] and enums["audit_card"]["single_implementation"] == "tools/aipos_cli/audit_derivation.is_audit_task_mode"
    assert got == {"audit": True, " AUDIT ": True, "code": False, "validation": False, "": False, None: False, "bogus": False}
    assert ad.is_audit_card("X-9", {"task_mode": "audit"}) and not ad.is_audit_card("X-9", {"task_mode": "code"})
    # 声明缺 = fail-closed
    import tools.schema_loader as sl

    real = sl.load_schema

    def _no_decl(name, *a, **k):
        data = real(name, *a, **k)
        if name == "enums":
            data = json.loads(json.dumps(data))
            data["enums"]["task_mode"].pop("audit_card")
        return data

    monkeypatch.setattr(sl, "load_schema", _no_decl)
    with pytest.raises(SchemaLoadError):
        ad.is_audit_task_mode("audit")


def test_item4_f211_baseline_cleared():
    assert read_entries(Path(__file__).resolve().parent / "f211_task_mode_literal_baseline.jsonl") == []


# ===========================================================================
# 件⑤ web 尾巴
# ===========================================================================

def test_item5_old_cjk_guard_node_and_exclude_gone():
    runall = (REPO_ROOT / "tests" / "run-all.sh").read_text(encoding="utf-8")
    guard = (REPO_ROOT / "web" / "board" / "tests" / "test_aipos288_cjk_source_guard.py").read_text(encoding="utf-8")
    assert "test_aipos288_cjk_source_guard.py::test_no_bare_cjk_in_sources" not in runall
    assert "def test_no_bare_cjk_in_sources(" not in guard and "def scan_file_for_cjk(" in guard
    assert read_entries(Path(__file__).resolve().parent / "f214_cjk_literal_baseline.jsonl") == []
