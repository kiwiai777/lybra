"""AIPOS-F82: 工位接入收口三件(2026-09-24 重 enroll 与正式 sync 实撞)

件① 共享分发目录 prune 按全角色声明并集(根治执行/审计互删乒乓)+ finalize-slice 退出执行体分发(零门):
    靶场 = 临时工位父根下 executor + auditor 两工位共享 _distributed/, 假门按真 distribution.schema 构建清单
    (tools.distribution_manifest.build_role_manifest, 与真门同一构建器)、按产品树真文件给内容;
    依次 sync A→B→A→B 后两工位 dry-run 的 plan 与 would_prune 皆空(稳态), 双方声明件都在, 退役件被回收。
件② enroll 写工位走声明: 扩展挂载只写目标存在者(lybra-loop 旧门循环扩展已退役 → 不接), 章程种子 = charter_render
    渲染物(与 sync 同一渲染器、同一上下文), 已存在不覆盖。
件③ 车道外 token 取值归单源: confined_worker / lybra-deploy(bash 经 python3 -m tools.aipos_cli.token_resolver)/
    看板登录, [retired, new] 下均取 new 或拒 retired; 全 retired fail-closed 带重签出口。

靶场全部临时目录; 禁碰真实工位 /home/kiwi/projects/kiwiai-pi/*。token 永不上屏(只打印/断言指纹)。
"""
from __future__ import annotations

import base64
import json
import re
import subprocess
import sys
from pathlib import Path
from unittest import mock

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from tools.aipos_cli.token_resolver import TOKEN_REENROLL_EXIT, token_fingerprint as fp  # noqa: E402

EXEC = "exec.lybra.test"
AUDITOR = "audit.lybra.test"
CHRIS_EXEC = "hbj-coder.chris-huibojin.test"


# ---------------------------------------------------------------------------
# 靶场
# ---------------------------------------------------------------------------

def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _secret(role: str, tag: str = "") -> str:
    return f"fixture-not-a-secret-{role}{tag}"


def _make_gov(root: Path, project: str) -> Path:
    for sub in ("pending", "claimed", "completed", "blocked", "withdrawn"):
        (root / "5_tasks" / "queue" / sub).mkdir(parents=True, exist_ok=True)
    (root / "5_tasks" / "policies").mkdir(parents=True, exist_ok=True)
    (root / "task_cards").mkdir(parents=True, exist_ok=True)
    _write(root / "project.json", json.dumps({"project": project, "config_version": 1}))
    return root


def _station(parent: Path, name: str, *, role: str, instance: str, gov: Path, project: str, role_class: str | None = None) -> Path:
    ws = parent / name
    _write(ws / ".lybra" / "role", json.dumps({"role": role, "instance": instance, "owner_policy_ref": "pol_x"}))
    entry = {"role": role, "agent_instance": instance, "token": _secret(role), "projects": [project]}
    if role_class:
        entry["role_class"] = role_class
    _write(ws / ".lybra" / "connection.json", json.dumps({
        "config_version": 1, "mcp": {"rpc_url": "http://127.0.0.1:7999/mcp"}, "governance_root": str(gov),
        "workspace_root": str(gov), "tokens": [entry],
    }))
    return ws


class RepoGate:
    """假门: 清单 = 真 build_role_manifest(本产品树, 调用者角色)(与真门 lybra_distribution_manifest 同一构建器); 内容 = 产品树真文件。"""

    roles: dict[str, tuple[str, str | None]] = {}  # token → (role, role_class)

    def __init__(self, base_url: str, token: str, **_: object) -> None:
        self.role, self.role_class = RepoGate.roles[token]

    def initialize(self) -> None:
        return None

    def _manifest(self) -> dict:
        from tools.distribution_manifest import build_role_manifest

        return build_role_manifest(REPO_ROOT, self.role, role_class=self.role_class)

    def call_tool(self, name: str, args: dict) -> dict:
        from tools.distribution_manifest import get_product_commit

        manifest = self._manifest()
        if name == "lybra_distribution_manifest":
            return {"ok": True, "role": self.role, "harness": "pi", "product_commit": get_product_commit(REPO_ROOT),
                    "distributions": manifest["distributions"]}
        if name == "lybra_distribution_fetch":
            dist = next(d for d in manifest["distributions"] if d["distribution_id"] == args["distribution_id"])
            src = REPO_ROOT / dist["source_path"]
            base = src.parent if dist["source_is_file"] else src
            return {"ok": True, "files": [
                {"path": rel, "content_b64": base64.b64encode((base / rel).read_bytes()).decode()} for rel in args["paths"]
            ]}
        raise AssertionError(f"unexpected verb {name}")


@pytest.fixture
def rig(tmp_path, monkeypatch):
    """工位父根 kiwiai-pi 形: lybra-executor + lybra-auditor 共享 _distributed/; 另有 chris 治理根 + 门注册表(自定义角色类)。"""
    from tools.aipos_cli import confirm_client

    for var in ("LYBRA_TOKEN", "LYBRA_HARNESS_ROOT", "LYBRA_CONNECTION_JSON", "AIPOS_WORKSPACE_ROOT", "LYBRA_GATE_URL"):
        monkeypatch.delenv(var, raising=False)
    gov = _make_gov(tmp_path / "gov-lybra", "lybra")
    gov_c = _make_gov(tmp_path / "gov-chris", "chris-huibojin")
    # 门注册表(home 下 lybra 工作区 connection.json): 自定义角色 hbj-coder → 内建类 executor
    _write(gov / ".lybra" / "connection.json", json.dumps({"config_version": 1, "tokens": [
        {"role": "hbj-coder", "role_class": "executor", "agent_instance": CHRIS_EXEC, "projects": ["chris-huibojin"],
         "token": _secret("hbj-coder"), "fingerprint": fp(_secret("hbj-coder")), "token_ref": "svc-hbj-coder", "scopes": []},
    ]}))
    parent = tmp_path / "kiwiai-pi"
    ws_e = _station(parent, "lybra-executor", role="executor", instance=EXEC, gov=gov, project="lybra")
    ws_a = _station(parent, "lybra-auditor", role="auditor", instance=AUDITOR, gov=gov, project="lybra")
    RepoGate.roles = {_secret("executor"): ("executor", None), _secret("auditor"): ("auditor", None),
                      _secret("hbj-coder"): ("hbj-coder", "executor")}
    monkeypatch.setattr(confirm_client, "GateClient", RepoGate)
    return {"gov": gov, "gov_c": gov_c, "parent": parent, "ws_e": ws_e, "ws_a": ws_a, "tmp": tmp_path}


def _landing_files(parent: Path) -> list[str]:
    d = parent / "_distributed"
    return sorted(p.relative_to(d).as_posix() for p in d.rglob("*") if p.is_file() and not p.name.startswith(".version-")) if d.is_dir() else []


def _summ(res: dict) -> str:
    return json.dumps({
        "plan": [(p["distribution_id"], p["paths"]) for p in res.get("plan") or []],
        "would_prune": [Path(x).relative_to(Path(res["harness_root"]).parent).as_posix() for x in res.get("would_prune") or []],
        "pruned": [Path(x).relative_to(Path(res["harness_root"]).parent).as_posix() for x in res.get("pruned_files") or []],
        "protected": len((res.get("shared_prune_guard") or {}).get("protected") or []),
    }, ensure_ascii=False)


# ===========================================================================
# 件① 共享落点稳态 + finalize-slice 退出执行体分发
# ===========================================================================

def test_item1_shared_landing_steady_state_A_B_A_B(rig):
    """A→B→A→B 正式 sync 后, 两工位 dry-run 的 plan 与 would_prune 皆空(稳态); 双方声明件都在; 旧 finalize-slice 被回收。"""
    from tools.aipos_cli import distribution_sync as ds

    parent, gov = rig["parent"], rig["gov"]
    stale = parent / "_distributed" / "skills" / "finalize-slice" / "SKILL.md"
    _write(stale, "# finalize-slice(09-24 前执行体分发的旧件)\n")
    seq = [("A", rig["ws_e"]), ("B", rig["ws_a"]), ("A", rig["ws_e"]), ("B", rig["ws_a"])]
    for i, (tag, ws) in enumerate(seq, 1):
        res = ds.sync(harness_root=ws, governance_root=gov)
        assert res["status"] == "synced", res
        print(f"sync#{i} {tag}={ws.name}: {_summ(res)}")
        # 任何一轮都不删对方声明件
        assert not any("audit-independent-evidence" in p for p in res["pruned_files"]), res["pruned_files"]
        assert not any("chunked-io" in p or "write-return" in p for p in res["pruned_files"]), res["pruned_files"]
    for ws in (rig["ws_e"], rig["ws_a"]):
        dry = ds.sync(harness_root=ws, governance_root=gov, dry_run=True)
        print(f"steady dry-run {ws.name}: {_summ(dry)}")
        assert dry["plan"] == [] and dry["would_prune"] == [], _summ(dry)
    files = _landing_files(parent)
    print("landing:", files)
    assert "skills/audit-independent-evidence/SKILL.md" in files  # 审计声明件在
    assert "skills/chunked-io/SKILL.md" in files and "skills/write-return/SKILL.md" in files  # 执行声明件在
    assert not stale.exists() and not any(f.startswith("skills/finalize-slice/") for f in files)  # 退役件回收


def test_item1_red_old_prune_semantics_pingpong_green_with_guard(rig):
    """红: 旧 prune(只看本角色声明)下执行工位 sync 会删审计声明件; 绿: 扣全角色声明并集后保留(记 protected)。"""
    from tools.aipos_cli import distribution_sync as ds

    ds.sync(harness_root=rig["ws_a"], governance_root=rig["gov"])  # 审计工位先落齐
    audit_skill = rig["parent"] / "_distributed" / "skills" / "audit-independent-evidence" / "SKILL.md"
    assert audit_skill.is_file()
    remote = RepoGate("", _secret("executor")).call_tool("lybra_distribution_manifest", {})
    _, declared, _ = ds.compute_diffs(rig["ws_e"], remote)
    red = ds._find_files_to_prune(rig["ws_e"], set(declared))  # 旧语义: 无 shared_guard
    guard = ds.shared_landing_declaration(rig["ws_e"])
    green = ds._find_files_to_prune(rig["ws_e"], set(declared), shared_guard=guard)
    print("red(old) would_prune:", [Path(p).name + "@" + Path(p).parent.name for p in red])
    print("green guard:", json.dumps(ds.public_shared_guard(guard), ensure_ascii=False)[:400])
    assert str(audit_skill) in red
    assert str(audit_skill) not in green and str(audit_skill) in guard["protected"]
    assert guard["complete"] and {r["role"] for r in guard["roles"]} == {"executor", "auditor"}


def test_item1_finalize_slice_out_of_executor_distribution():
    """零门: finalize-slice 不再分发给执行体(内建 + 自定义 executor 类); 审计体集合不变。"""
    from tools.distribution_manifest import build_role_manifest

    def skills(role: str, cls: str | None = None) -> set[str]:
        m = build_role_manifest(REPO_ROOT, role, role_class=cls)
        return {f["path"].split("/", 1)[0] for d in m["distributions"] if d["kind"] == "skills" for f in d["files"]}

    ex, custom, au = skills("executor"), skills("probe-coder", "executor"), skills("auditor")
    print("executor skills:", sorted(ex), "| auditor skills:", sorted(au))
    assert "finalize-slice" not in ex and "finalize-slice" not in custom and ex == custom
    assert "audit-independent-evidence" in au and "finalize-slice" not in au
    decl = json.loads((REPO_ROOT / "schema" / "distribution.schema.json").read_text(encoding="utf-8"))
    assert "shared_landing_prune_semantics" in decl


def test_item1_custom_role_sibling_class_via_registry(rig):
    """chris 形工位(自定义角色 hbj-coder)同父根: 类经门注册表解析 = executor, 其声明入并集; lybra 范围 sync 仍跳过它。"""
    from tools.aipos_cli import distribution_sync as ds

    _station(rig["parent"], "hbj-coder", role="hbj-coder", instance=CHRIS_EXEC, gov=rig["gov_c"], project="chris-huibojin", role_class="executor")
    guard = ds.shared_landing_declaration(rig["ws_e"])
    print("roles:", guard["roles"])
    assert guard["complete"], guard["problems"]
    assert {(r["role"], r["role_class"]) for r in guard["roles"]} == {("executor", "executor"), ("auditor", "auditor"), ("hbj-coder", "executor")}
    run = ds.sync_many(rig["parent"], governance_root=rig["gov"], dry_run=True)
    by = {Path(e["harness_root"]).name: e for e in run["workstations"]}
    assert by["hbj-coder"]["status"] == "skipped"


def test_item1_unresolvable_sibling_suspends_shared_prune_fail_closed(rig, capsys):
    """并集不完整(同父根工位的自定义角色类不可解析)= 共享落点 prune 暂停, 点名问题工位; 私有落点照常。"""
    from tools.aipos_cli import distribution_sync as ds

    _station(rig["parent"], "zz-ghost", role="ghost-coder", instance="ghost-coder.nowhere.test", gov=rig["tmp"] / "no-gov", project="nowhere")
    orphan = rig["parent"] / "_distributed" / "skills" / "orphan-skill" / "SKILL.md"
    _write(orphan, "# orphan\n")
    res = ds.sync(harness_root=rig["ws_e"], governance_root=rig["gov"])
    guard = res["shared_prune_guard"]
    print("guard:", json.dumps(guard, ensure_ascii=False)[:500])
    assert guard["complete"] is False and any("zz-ghost" in p and "ghost-coder" in p for p in guard["problems"])
    assert orphan.is_file() and str(orphan) in guard["suspended"]  # 不删
    text = ds.render_sync_text({"dry_run": True, "mode": "single", "workstations": [
        {"harness_root": res["harness_root"], "status": "dry-run", "result": {**res, "plan": []}}]})
    assert "共享落点 prune 暂停" in text and "zz-ghost" in text


# ===========================================================================
# 件② enroll 写工位走声明
# ===========================================================================

def _gov_with_policy(gov: Path, covers: str) -> Path:
    _write(gov / "5_tasks" / "policies" / f"pol_{covers}_1.md", (
        "---\nrecord_type: owner_autonomy_policy\n"
        f"policy_id: pol_{covers}_1\nmode: PreAuthorized\nstatus: active\napproved_by_owner: true\n"
        "owner_approval_ref: dec_probe\nactive_from: '2020-01-01T00:00:00Z'\nexpires_at: '2099-01-01T00:00:00Z'\n"
        f"agent_or_role: {covers}\ntask_selector_task_mode: ''\ntask_selector_project: ''\ntask_selector_task_ids: []\n"
        "max_tasks: 50\n---\n"))
    return gov


def _enroll(ws: Path, gov: Path, role: str, instance: str) -> dict:
    from tools.aipos_cli import enroll_client
    from tools.aipos_cli.enrollment import encode_self_contained_code

    code = encode_self_contained_code(gate_url="http://127.0.0.1:7999", governance_root=str(gov),
                                      transport_token="stub-transport", code="STUBINNER")
    entry = {"role": role, "agent_instance": instance, "token": _secret(role, "-enroll"),
             "fingerprint": fp(_secret(role, "-enroll")), "scopes": [], "projects": ["lybra"]}
    stub = REPO_ROOT / ".deploy" / "current" / "bin" / "lybra"
    made = not stub.exists()
    if made:
        stub.parent.mkdir(parents=True, exist_ok=True)
        stub.write_text("#!/bin/sh\n", encoding="utf-8")
    try:
        with mock.patch.object(enroll_client, "exchange_enrollment_code", return_value={"ok": True, "token_entry": entry}), \
                mock.patch.object(enroll_client, "land_enrollment_code", return_value=True):
            return enroll_client.enroll(code=code, gate_url="", workspace_root=ws)
    finally:
        if made:
            stub.unlink(missing_ok=True)


def _shared(parent: Path, *names: str) -> None:
    for n in names:
        _write(parent / "_shared" / "extensions" / n, "export default function () {}\n")


def test_item2_fresh_enroll_rendered_charter_and_no_dangling_extension(tmp_path, monkeypatch):
    """空工位 enroll: AGENTS.md = charter_render 渲染物(无 {{占位}}, 与 sync 同渲染器同上下文逐字相等);
    .pi/extensions 逐个 resolve 目标存在; lybra-loop 退役不接、finalize-slice 不接, warnings 点名。"""
    from tools.aipos_cli.charter_render import charter_render_context, render_charter, workstation_identity
    from tools.distribution_manifest import get_product_commit

    for var in ("LYBRA_TOKEN", "LYBRA_CONNECTION_JSON", "AIPOS_WORKSPACE_ROOT"):
        monkeypatch.delenv(var, raising=False)
    gov = _gov_with_policy(_make_gov(tmp_path / "gov", "lybra"), "executor")
    parent = tmp_path / "kiwiai-pi"
    _shared(parent, "claim.ts", "go.ts")
    ws = parent / "lybra-executor"
    r = _enroll(ws, gov, "executor", EXEC)
    text = (ws / "AGENTS.md").read_text(encoding="utf-8")
    print("AGENTS.md status:", r["wiring"]["items"]["AGENTS.md"])
    print("placeholders left:", re.findall(r"\{\{\s*\w+\s*\}\}", text))
    assert "{{" not in text and "<!-- lybra:charter-render" in text
    expected = render_charter((REPO_ROOT / "agents/roles/executor/AGENTS.md").read_text(encoding="utf-8"),
                              charter_render_context(gov, identity=workstation_identity(ws), product_commit=get_product_commit(REPO_ROOT)))
    assert text == expected  # 同一渲染器、同一上下文
    ext = sorted((ws / ".pi" / "extensions").iterdir())
    print("extensions:", [(p.name, p.is_symlink(), p.exists(), p.resolve().relative_to(parent).as_posix()) for p in ext])
    assert [p.name for p in ext] == ["claim.ts", "go.ts"] and all(p.exists() for p in ext)
    assert not (ws / ".pi" / "extensions" / "lybra-loop.ts").exists()
    skills = sorted(p.name for p in (ws / ".pi" / "skills").iterdir())
    assert "finalize-slice" not in skills and skills
    print("warnings:", r["warnings"])
    assert any("extension:lybra-loop" in w and "skill:finalize-slice" in w for w in r["warnings"])
    assert r["minimum_bootable_set"]["ok"], r["minimum_bootable_set"]["missing"]


def test_item2_missing_extension_target_not_written(tmp_path, monkeypatch):
    """扩展目标不存在(go.ts 未落地)= 不写 + warnings 点名, 禁写悬空。"""
    for var in ("LYBRA_TOKEN", "LYBRA_CONNECTION_JSON", "AIPOS_WORKSPACE_ROOT"):
        monkeypatch.delenv(var, raising=False)
    gov = _gov_with_policy(_make_gov(tmp_path / "gov", "lybra"), "auditor")
    parent = tmp_path / "kiwiai-pi"
    _shared(parent, "claim.ts")
    ws = parent / "lybra-auditor"
    r = _enroll(ws, gov, "auditor", AUDITOR)
    ext = sorted((ws / ".pi" / "extensions").iterdir())
    print("extensions:", [p.name for p in ext], "| go.ts item:", r["wiring"]["items"]["extensions/go.ts"]["status"])
    assert [p.name for p in ext] == ["claim.ts"] and all(p.exists() for p in ext)
    assert r["wiring"]["items"]["extensions/go.ts"]["status"] == "not_written(target-missing)"
    assert any(".pi/extensions/go.ts 未接" in w for w in r["warnings"])


def test_item2_existing_agents_md_not_overwritten_and_render_failure_not_written(tmp_path, monkeypatch):
    """既有工位 enroll 不覆盖 AGENTS.md(seed 语义保留); 渲染不成立(治理根无 project.json)= 不写 + warnings, 禁落母本裸拷贝。"""
    for var in ("LYBRA_TOKEN", "LYBRA_CONNECTION_JSON", "AIPOS_WORKSPACE_ROOT"):
        monkeypatch.delenv(var, raising=False)
    gov = _gov_with_policy(_make_gov(tmp_path / "gov", "lybra"), "executor")
    parent = tmp_path / "kiwiai-pi"
    _shared(parent, "claim.ts")
    ws = parent / "lybra-executor"
    _write(ws / "AGENTS.md", "# 我的既有章程\n")
    r = _enroll(ws, gov, "executor", EXEC)
    assert (ws / "AGENTS.md").read_text() == "# 我的既有章程\n" and r["wiring"]["items"]["AGENTS.md"]["status"] == "skipped(existing)"
    gov2 = _gov_with_policy(tmp_path / "gov-noproj", "executor")  # 无 project.json
    ws2 = parent / "lybra-executor-2"
    r2 = _enroll(ws2, gov2, "executor", EXEC)
    print("render-failure item:", r2["wiring"]["items"]["AGENTS.md"])
    assert not (ws2 / "AGENTS.md").exists()
    assert r2["wiring"]["items"]["AGENTS.md"]["status"] == "not_written(render-failed)"
    assert any("AGENTS.md 未写" in w for w in r2["warnings"]) and "AGENTS.md" in r2["minimum_bootable_set"]["missing"]


def test_item2_enroll_then_sync_no_declaration_gap(rig, monkeypatch):
    """enroll 渲染的章程与 sync 渲染物同源: 首次 sync 不报声明缺口(无 diff), 之后稳态。"""
    from tools.aipos_cli import distribution_sync as ds

    gov = _gov_with_policy(rig["gov"], "executor")
    parent = rig["parent"]
    _shared(parent, "claim.ts")
    ws = parent / "lybra-executor-new"
    _enroll(ws, gov, "executor", "exec.lybra.newbox")
    RepoGate.roles[_secret("executor", "-enroll")] = ("executor", None)
    res = ds.sync(harness_root=ws, governance_root=gov)
    print("first sync gaps:", res["declaration_gaps"], "| changes:", [(c["distribution_id"], c["reasons"]) for c in res["changes"]])
    assert res["declaration_gaps"] == []


def test_item2_multi_file_extension_mount_is_wrapper_with_distributed_marker(tmp_path):
    """多文件扩展声明 → 转发包装(带分发标记, sync prune 可识别); 单文件 → 相对软链; 目标不存在不写。"""
    from tools.aipos_cli import workstation_wiring as ww
    from tools.aipos_cli.distribution_sync import _is_distributed_file

    dists = [
        {"distribution_id": "multi", "kind": "extension", "target_base": "harness_parent", "source_is_file": False,
         "target_path": "_distributed/extensions/probe-loop", "files": [{"path": "probe-loop.ts"}, {"path": "sib.ts"}]},
        {"distribution_id": "single", "kind": "extension", "target_base": "harness_parent", "source_is_file": True,
         "target_path": "_shared/extensions/one.ts", "files": [{"path": "one.ts"}]},
    ]
    specs = ww.declared_role_extensions(dists)
    assert specs["probe-loop.ts"]["kind"] == "wrapper" and specs["one.ts"]["kind"] == "symlink"
    parent = tmp_path / "root"
    ws = parent / "ws"
    _write(parent / "_distributed" / "extensions" / "probe-loop" / "probe-loop.ts", "export default 1\n")
    warnings: list[str] = []
    a = ww._seed_extension(ws / ".pi", "probe-loop.ts", specs["probe-loop.ts"], warnings, origin="t")
    b = ww._seed_extension(ws / ".pi", "one.ts", specs["one.ts"], warnings, origin="t")
    wrapper = ws / ".pi" / "extensions" / "probe-loop.ts"
    assert a["status"] == "created" and wrapper.is_file() and not wrapper.is_symlink() and _is_distributed_file(wrapper)
    assert '"../../../_distributed/extensions/probe-loop/probe-loop.ts"' in wrapper.read_text()
    assert b["status"] == "not_written(target-missing)" and not (ws / ".pi" / "extensions" / "one.ts").is_symlink()


# ===========================================================================
# 件③ 车道外 token 取值归单源
# ===========================================================================

def _conn(path: Path, tokens: list[dict]) -> Path:
    for t in tokens:
        t.setdefault("fingerprint", fp(t["token"]))
    _write(path, json.dumps({"config_version": 1, "tokens": tokens}))
    return path


def _deploy_resolver(conn: Path) -> subprocess.CompletedProcess:
    """抽出 tools/lybra-deploy 里的 resolve_executor_token 函数原文, 以 PRODUCT_REPO=本产品树执行(不跑部署本体)。"""
    src = (REPO_ROOT / "tools" / "lybra-deploy").read_text(encoding="utf-8")
    m = re.search(r"^resolve_executor_token\(\) \{\n.*?^\}\n", src, re.S | re.M)
    assert m, "lybra-deploy 缺 resolve_executor_token"
    script = f'set -euo pipefail\nPRODUCT_REPO="{REPO_ROOT}"\n{m.group(0)}\nresolve_executor_token "$1"\n'
    return subprocess.run(["bash", "-c", script, "f82", str(conn)], capture_output=True, text=True, timeout=60)


def test_item3_retired_then_new_all_three_callers(tmp_path):
    """[retired, new]: confined_worker 取 new; lybra-deploy(bash→token_resolver CLI)取 new; 看板登录拒 retired、收 new。"""
    from tools.sandbox_runtime import confined_worker as cw
    from web.board.app import verify_login_token

    old, new = _secret("executor", "-old"), _secret("executor", "-new")
    conn = _conn(tmp_path / "connection.json", [
        {"role": "executor", "agent_instance": EXEC, "token": old, "retired": True,
         "retired_at": "2026-09-24T02:41:41Z", "retired_reason": "superseded by re-enrollment"},
        {"role": "executor", "agent_instance": EXEC, "token": new},
    ])
    got_cw = cw.executor_token(conn)
    dep = _deploy_resolver(conn)
    board_old, board_new = verify_login_token(old, [conn]), verify_login_token(new, [conn])
    print(f"old={fp(old)} new={fp(new)}")
    print(f"confined_worker.executor_token → {fp(got_cw)}")
    print(f"lybra-deploy resolve_executor_token → exit={dep.returncode} {fp(dep.stdout.strip())} stderr={dep.stderr.strip()!r}")
    print(f"board verify_login_token(old) → {board_old}; (new) → role={(board_new or {}).get('role')}")
    assert got_cw == new
    assert dep.returncode == 0 and dep.stdout.strip() == new and dep.stderr == ""
    assert board_old is None and board_new and board_new["role"] == "executor"


def test_item3_all_retired_fail_closed_with_reenroll_exit(tmp_path):
    """全 retired: 三处均 fail-closed(不取旧 token), 拒因带重签出口且不含 token 值。"""
    from tools.sandbox_runtime import confined_worker as cw
    from web.board.app import verify_login_token

    old = _secret("executor", "-old")
    conn = _conn(tmp_path / "connection.json", [{"role": "executor", "agent_instance": EXEC, "token": old, "retired": True}])
    with pytest.raises(cw.ConfinedWorkerError) as ei:
        cw.executor_token(conn)
    dep = _deploy_resolver(conn)
    print("confined_worker 拒因:", ei.value)
    print(f"lybra-deploy exit={dep.returncode} stdout={dep.stdout!r} stderr={dep.stderr.strip()}")
    assert TOKEN_REENROLL_EXIT in str(ei.value) and old not in str(ei.value)
    assert dep.returncode == 3 and dep.stdout == "" and TOKEN_REENROLL_EXIT in dep.stderr and old not in dep.stderr
    assert verify_login_token(old, [conn]) is None


def test_item3_token_resolver_cli_contract(tmp_path):
    """CLI 契约: 成功只出 token(或 --fingerprint 只出指纹); 无命中 exit=1; 不回显 token 到 stderr。"""
    new = _secret("executor", "-new")
    conn = _conn(tmp_path / "c.json", [{"role": "executor", "agent_instance": EXEC, "token": new}])
    base = [sys.executable, "-m", "tools.aipos_cli.token_resolver", "--connection-json", str(conn)]
    r_fp = subprocess.run(base + ["--role", "executor", "--fingerprint"], capture_output=True, text=True, cwd=REPO_ROOT)
    r_inst = subprocess.run(base + ["--agent-instance", EXEC], capture_output=True, text=True, cwd=REPO_ROOT)
    r_miss = subprocess.run(base + ["--role", "auditor"], capture_output=True, text=True, cwd=REPO_ROOT)
    print("fingerprint:", r_fp.stdout.strip(), "| miss:", r_miss.returncode, r_miss.stderr.strip())
    assert r_fp.returncode == 0 and r_fp.stdout.strip() == fp(new)
    assert r_inst.returncode == 0 and r_inst.stdout == new + "\n"
    assert r_miss.returncode == 1 and r_miss.stdout == "" and "TokenNotFoundError" in r_miss.stderr and new not in r_miss.stderr


def test_item3_no_inline_selection_left_in_off_lane_callers():
    """静态: lybra-deploy 无内嵌挑选(tokens[0]); confined_worker 不再自遍历挑 executor; 看板登录经退役判据单源。"""
    deploy = (REPO_ROOT / "tools" / "lybra-deploy").read_text(encoding="utf-8")
    assert "tokens[0]" not in deploy and "t.get('role')=='executor'" not in deploy
    assert deploy.count("resolve_executor_token \"$conn_json\"") == 2
    cw_src = (REPO_ROOT / "tools" / "sandbox_runtime" / "confined_worker.py").read_text(encoding="utf-8")
    body = cw_src[cw_src.index("def executor_token"):cw_src.index("def all_raw_secrets")]
    assert "get_token_for_role_and_project" in body and "for item in" not in body
    board = (REPO_ROOT / "web" / "board" / "app.py").read_text(encoding="utf-8")
    assert "is_token_entry_retired(item)" in board
