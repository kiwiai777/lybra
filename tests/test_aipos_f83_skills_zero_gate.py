"""AIPOS-F83: 技能母本零门三件(F82R 后 G2/G3/G5 实查)

件① 分发给执行体/审计体的技能正文零门 + 去项目字面: 按 distribution 声明(与门同一构建器)取执行体/审计体实际分发的
    技能文件, grep 门动作与项目字面零命中(白名单须带「反例/禁止」标注); finalize-slice 母本退役、audit-card-template 退役。
件② 工具包单一来源: roles.schema roles[].tool_package 退役(只留指向说明), lybra-loop 退役; 全仓 tool_package 命中逐条归类。
件③ sync 回收工位 .pi 悬空挂载: 本项目工位 .pi/skills、.pi/extensions 下目标不存在(或本次 prune 后不存在)且不在声明内的
    产品接线形态挂载回收(manifest 记 pruned); 声明内暂缺不删列 warnings; 非接线形态不碰; 非本项目工位跳过; 再 sync 稳态。

靶场全部临时目录(假门 = F82 RepoGate: 真 build_role_manifest + 产品树真文件); 禁碰真实工位。token 永不上屏。
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "tests"))

from test_aipos_f82_workstation_intake import (  # noqa: E402  (F82 靶场件复用, 禁第二套假门)
    AUDITOR,
    CHRIS_EXEC,
    EXEC,
    RepoGate,
    _make_gov,
    _secret,
    _station,
    _write,
)

#: 件① 靶场 grep(卡面原文)
ZERO_GATE_PATTERN = re.compile(r"/claim|lybra_queue_|lybra_audit_verdict|lybra_task_progress|projects/lybra|kiwiai-dev")
#: 说明性反例白名单: {相对路径: [行内片段, ...]}; 命中行须同时带「反例」或「禁止」标注。当前为空 = 零命中。
ZERO_GATE_WHITELIST: dict[str, list[str]] = {}


def _distributed_skill_files(roles: tuple[tuple[str, str], ...]) -> dict[str, Path]:
    """按 distribution 声明(workstation_wiring.declared_role_distributions → build_role_manifest)取分发给这些角色的技能文件。"""
    from tools.aipos_cli.workstation_wiring import declared_role_distributions

    out: dict[str, Path] = {}
    for role, cls in roles:
        for d in declared_role_distributions(role, cls):
            if d.get("kind") != "skills":
                continue
            for f in d["files"]:
                out[f"agents/skills/{f['path']}"] = REPO_ROOT / d["source_path"] / f["path"]
    return dict(sorted(out.items()))


# ===========================================================================
# 件① 技能正文零门 + 去项目字面
# ===========================================================================

def test_item1_distributed_skills_zero_gate_grep():
    files = _distributed_skill_files((("executor", "executor"), ("auditor", "auditor"), ("probe-coder", "executor")))
    print("分发给执行体/审计体的技能文件:", list(files))
    assert files and all(p.is_file() for p in files.values())
    hits: list[str] = []
    for rel, path in files.items():
        for no, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if not ZERO_GATE_PATTERN.search(line):
                continue
            allowed = any(frag in line for frag in ZERO_GATE_WHITELIST.get(rel, [])) and ("反例" in line or "禁止" in line)
            if not allowed:
                hits.append(f"{rel}:{no}: {line.strip()}")
    print("grep 命中(白名单外):", hits or "0")
    assert hits == []


def test_item1_skills_teach_no_gate_actions_and_point_to_card_landing():
    """正文零门的正向断言: 不教下一棒命令; write-return / task-closure-loop 写明「卡面声明的落点 + 写完即止 + 门动作由驱动方经产品完成」。"""
    files = _distributed_skill_files((("executor", "executor"), ("auditor", "auditor")))
    for rel, path in files.items():
        text = path.read_text(encoding="utf-8")
        assert "下一棒" not in text and "粘贴行" not in text and "自产审计卡" not in text, rel
        assert not re.search(r"~/|/home/", text), rel  # 无机器/产品仓绝对路径
    wr = (REPO_ROOT / "agents/skills/write-return/SKILL.md").read_text(encoding="utf-8")
    tcl = (REPO_ROOT / "agents/skills/task-closure-loop/SKILL.md").read_text(encoding="utf-8")
    for text in (wr, tcl):
        assert "卡面声明的落点" in text and "驱动方经产品完成" in text
    assert "写完即止" in wr


def test_item1_finalize_slice_and_audit_card_template_retired():
    from tools.schema_loader import load_schema

    decl = load_schema("distribution")
    includes = {n for d in decl["distributions"] if d.get("kind") == "skills" for n in (d.get("filter") or {}).get("include", [])}
    missing_masters = sorted(n for n in includes if not (REPO_ROOT / "agents" / "skills" / n / "SKILL.md").is_file())
    print("任一 skills 条目 include:", sorted(includes), "| 声明而母本缺:", missing_masters)
    assert "finalize-slice" not in includes
    assert not (REPO_ROOT / "agents/skills/finalize-slice").exists()  # 与声明一致: 无声明 → 母本删除
    assert not (REPO_ROOT / "agents/skills/task-closure-loop/audit-card-template.md").exists()
    assert missing_masters == []  # 声明的技能母本全在


# ===========================================================================
# 件② 工具包单一来源
# ===========================================================================

#: 全仓 tool_package 命中逐条归类(新增命中 = 须在此归类, 否则红)。reader_code = 读 roles.schema tool_package 的代码。
TOOL_PACKAGE_INVENTORY: dict[str, str] = {
    "schema/roles.schema.json": "退役指向说明(tool_package_retired)",
    "schema/distribution.schema.json": "单源声明(tool_package_single_source / 条目 notes / minimum_bootable_set 描述)",
    "tools/aipos_cli/workstation_wiring.py": "模块文档退役注记(非读取)",
    "tests/test_aipos_f54.py": "夹具: 断言 roles.schema 无 tool_package / warnings 无 tool_package",
    "tests/test_aipos_f82_workstation_intake.py": "夹具: 断言 warnings 无 tool_package",
    "tests/test_aipos_f83_skills_zero_gate.py": "夹具: 本盘点",
    "tools/aipos_cli/tests/test_aipos_f57_onboarding.py": "夹具文档: 改读 distribution 声明",
    "tools/schema_loader.py": "reader_code(车道外死代码: get_role_tool_package / get_roles_with_tool_package 零调用方, 登记缺口)",
    "agents/README.md": "车道外文档(过时: 仍称 tool_package 为装配单源, 登记缺口)",
    "agents/roles/README.md": "车道外文档(过时: 仍称 tool_package 为装配单源, 登记缺口)",
}


def test_item2_roles_schema_tool_package_retired_single_source():
    from tools.schema_loader import get_roles_with_tool_package, load_schema

    roles = load_schema("roles")
    assert not any("tool_package" in r for r in roles["roles"])
    assert "distribution.schema" in roles["tool_package_retired"]
    assert get_roles_with_tool_package() == []  # 车道外存量读取方已读不到任何清单
    decl = load_schema("distribution")
    assert "tool_package_single_source" in decl
    # lybra-loop 旧门循环扩展: 任何声明里都没有
    assert "lybra-loop" not in json.dumps(roles["roles"], ensure_ascii=False)
    for d in decl["distributions"]:
        assert "lybra-loop" not in json.dumps(d, ensure_ascii=False), d["distribution_id"]


def test_item2_tool_package_inventory_classified():
    out = subprocess.run(["git", "grep", "-n", "tool_package", "--", "."], cwd=REPO_ROOT, capture_output=True, text=True, check=False)
    assert out.returncode in (0, 1), out.stderr
    by_file: dict[str, int] = {}
    for line in out.stdout.splitlines():
        by_file[line.split(":", 1)[0]] = by_file.get(line.split(":", 1)[0], 0) + 1
    print("tool_package 盘点:")
    for f, n in sorted(by_file.items()):
        print(f"  {f} ×{n} → {TOOL_PACKAGE_INVENTORY.get(f, '<未归类>')}")
    assert set(by_file) <= set(TOOL_PACKAGE_INVENTORY), sorted(set(by_file) - set(TOOL_PACKAGE_INVENTORY))
    readers = [f for f in by_file if TOOL_PACKAGE_INVENTORY[f].startswith("reader_code")]
    assert readers == ["tools/schema_loader.py"]  # 车道内读取方 = 0
    for f in ("tools/aipos_cli/workstation_wiring.py", "tools/aipos_cli/distribution_sync.py"):
        src = (REPO_ROOT / f).read_text(encoding="utf-8")
        assert 'get("tool_package")' not in src and '["tool_package"]' not in src and "tool_package_for_class" not in src


# ===========================================================================
# 件③ sync 回收工位 .pi 悬空挂载
# ===========================================================================

@pytest.fixture
def rig(tmp_path, monkeypatch):
    from tools.aipos_cli import confirm_client

    for var in ("LYBRA_TOKEN", "LYBRA_HARNESS_ROOT", "LYBRA_CONNECTION_JSON", "AIPOS_WORKSPACE_ROOT", "LYBRA_GATE_URL"):
        monkeypatch.delenv(var, raising=False)
    gov = _make_gov(tmp_path / "gov-lybra", "lybra")
    gov_c = _make_gov(tmp_path / "gov-chris", "chris-huibojin")
    _write(gov / ".lybra" / "connection.json", json.dumps({"config_version": 1, "tokens": [
        {"role": "hbj-coder", "role_class": "executor", "agent_instance": CHRIS_EXEC, "projects": ["chris-huibojin"],
         "token": _secret("hbj-coder"), "token_ref": "svc-hbj-coder", "scopes": []},
    ]}))
    parent = tmp_path / "kiwiai-pi"
    ws_e = _station(parent, "lybra-executor", role="executor", instance=EXEC, gov=gov, project="lybra")
    ws_a = _station(parent, "lybra-auditor", role="auditor", instance=AUDITOR, gov=gov, project="lybra")
    ws_h = _station(parent, "hbj-coder", role="hbj-coder", instance=CHRIS_EXEC, gov=gov, project="chris-huibojin", role_class="executor")
    RepoGate.roles = {_secret("executor"): ("executor", None), _secret("auditor"): ("auditor", None),
                      _secret("hbj-coder"): ("hbj-coder", "executor")}
    monkeypatch.setattr(confirm_client, "GateClient", RepoGate)
    for n in ("claim.ts",):
        _write(parent / "_shared" / "extensions" / n, "export default function () {}\n")
    return {"gov": gov, "parent": parent, "ws_e": ws_e, "ws_a": ws_a, "ws_h": ws_h}


def _link(ws: Path, sub: str, name: str, target: str) -> Path:
    p = ws / ".pi" / sub / name
    p.parent.mkdir(parents=True, exist_ok=True)
    p.symlink_to(target)
    return p


def _rel(res: dict, paths: list[str]) -> list[str]:
    base = Path(res["harness_root"]).parent
    return sorted(Path(x).relative_to(base).as_posix() for x in paths)


def _view(res: dict) -> str:
    return json.dumps({
        "plan": [(p["distribution_id"], p["paths"]) for p in res.get("plan") or []],
        "would_prune": _rel(res, res.get("would_prune") or []),
        "pruned": _rel(res, res.get("pruned_files") or []),
        "pi_mount_warnings": res.get("pi_mount_warnings"),
    }, ensure_ascii=False)


def test_item3_dangling_skill_link_reclaimed_valid_kept_then_steady(rig):
    """卡面靶场: .pi/skills 一条悬空链(finalize-slice)+ 一条有效链(block-and-report): sync 后前者回收后者保留, 再 sync 稳态。"""
    from tools.aipos_cli import distribution_sync as ds

    ws, gov = rig["ws_e"], rig["gov"]
    ds.sync(harness_root=ws, governance_root=gov)  # 先落齐分发物(有效链的目标)
    dangling = _link(ws, "skills", "finalize-slice", "../../../_distributed/skills/finalize-slice")
    valid = _link(ws, "skills", "block-and-report", "../../../_distributed/skills/block-and-report")
    assert valid.exists() and dangling.is_symlink() and not dangling.exists()

    dry = ds.sync(harness_root=ws, governance_root=gov, dry_run=True)
    print("dry-run:", _view(dry))
    assert _rel(dry, dry["would_prune"]) == ["lybra-executor/.pi/skills/finalize-slice"] and dangling.is_symlink()  # dry-run 零写入

    res = ds.sync(harness_root=ws, governance_root=gov)
    print("sync:", _view(res))
    assert _rel(res, res["pruned_files"]) == ["lybra-executor/.pi/skills/finalize-slice"]
    assert not dangling.is_symlink() and valid.is_symlink() and valid.exists()
    assert (ws / ".pi" / "skills").is_dir()  # 只删链接, 不清最小集目录
    manifest = json.loads((rig["parent"] / "_distributed" / ".version-executor").read_text(encoding="utf-8"))
    print("manifest pruned:", manifest.get("pruned"))
    assert manifest["pruned"] == [str(dangling)]

    again = ds.sync(harness_root=ws, governance_root=gov, dry_run=True)
    print("steady dry-run:", _view(again))
    assert again["plan"] == [] and again["would_prune"] == [] and again["pi_mount_warnings"] == []


def test_item3_declared_missing_kept_foreign_untouched_extension_reclaimed(rig):
    """声明内暂缺 = 不删列 warnings(fetch 落地后消失); 非接线形态悬空 = 不碰列 warnings; 未声明悬空扩展挂载 = 回收;
    目标仍在的未声明挂载 = 不在本语义内(不删); 本次 prune 后才悬空的链 = 同轮回收。"""
    from tools.aipos_cli import distribution_sync as ds

    ws, gov, parent = rig["ws_e"], rig["gov"], rig["parent"]
    declared_missing = _link(ws, "skills", "chunked-io", "../../../_distributed/skills/chunked-io")  # 声明内, sync 前目标未落地
    foreign = _link(ws, "skills", "my-own", "/nonexistent-f83/skills/my-own")  # 非产品接线形态
    ext = _link(ws, "extensions", "lybra-loop.ts", "../../../_distributed/extensions/lybra-loop/lybra-loop.ts")  # 退役扩展悬空包装形
    alive_extra = _link(ws, "skills", "extra-claim", "../../../_shared/extensions/claim.ts")  # 未声明但目标在
    stale = parent / "_distributed" / "skills" / "finalize-slice" / "SKILL.md"
    _write(stale, "# 旧件(无任何角色声明)\n")
    after_prune = _link(ws, "skills", "finalize-slice", "../../../_distributed/skills/finalize-slice")  # 目标在, 但本次 prune 后即悬空

    dry = ds.sync(harness_root=ws, governance_root=gov, dry_run=True)
    print("dry-run:", _view(dry))
    wp = _rel(dry, dry["would_prune"])
    assert "lybra-executor/.pi/extensions/lybra-loop.ts" in wp and "lybra-executor/.pi/skills/finalize-slice" in wp
    assert "_distributed/skills/finalize-slice/SKILL.md" in wp
    assert not any(x.endswith(("chunked-io", "my-own", "extra-claim")) for x in wp)
    warns = dry["pi_mount_warnings"]
    assert any(w.startswith(".pi/skills/chunked-io") and "声明内" in w for w in warns)
    assert any(w.startswith(".pi/skills/my-own") and "非产品接线形态" in w for w in warns)

    res = ds.sync(harness_root=ws, governance_root=gov)
    print("sync:", _view(res))
    assert not ext.is_symlink() and not after_prune.is_symlink() and not stale.exists()
    assert declared_missing.exists()  # fetch 落地后恢复
    assert foreign.is_symlink() and alive_extra.is_symlink()  # 不碰
    assert res["pi_mount_warnings"] and all("my-own" in w for w in res["pi_mount_warnings"])  # 声明内暂缺已落地, 只剩非接线形态

    again = ds.sync(harness_root=ws, governance_root=gov, dry_run=True)
    print("steady dry-run:", _view(again))
    assert again["plan"] == [] and again["would_prune"] == []
    assert [w for w in again["pi_mount_warnings"] if "my-own" not in w] == []


def test_item3_non_project_workstation_skipped_untouched(rig):
    """非本项目工位(chris 形 hbj-coder)照 F66B 跳过: 其悬空 finalize-slice 链不碰; 本项目工位同轮回收。"""
    from tools.aipos_cli import distribution_sync as ds

    parent, gov = rig["parent"], rig["gov"]
    h = _link(rig["ws_h"], "skills", "finalize-slice", "../../../_distributed/skills/finalize-slice")
    e = _link(rig["ws_e"], "skills", "finalize-slice", "../../../_distributed/skills/finalize-slice")
    dry = ds.sync_many(parent, governance_root=gov, dry_run=True)
    by = {Path(x["harness_root"]).name: x for x in dry["workstations"]}
    print({k: (v["status"], _rel(v["result"], v["result"].get("would_prune") or []) if v.get("result") and v["status"] != "skipped" else v.get("reason")) for k, v in by.items()})
    assert by["hbj-coder"]["status"] == "skipped"
    assert "lybra-executor/.pi/skills/finalize-slice" in _rel(by["lybra-executor"]["result"], by["lybra-executor"]["result"]["would_prune"])
    run = ds.sync_many(parent, governance_root=gov)
    assert run["ok"], run
    assert h.is_symlink() and not e.is_symlink()


def test_item3_declared_wrapper_not_pruned_undeclared_wrapper_pruned(tmp_path):
    """声明内的转发包装不回收(旧语义会误删声明内包装); 未声明的带标记包装照旧回收。"""
    from tools.aipos_cli import distribution_sync as ds
    from tools.aipos_cli.workstation_wiring import EXTENSION_WRAPPER_TEMPLATE

    ws = tmp_path / "pi" / "lybra-executor"
    _write(ws / ".lybra" / "role", json.dumps({"role": "executor"}))
    target = tmp_path / "pi" / "_distributed" / "extensions" / "multi" / "multi.ts"
    _write(target, "export default 1\n")
    declared = ws / ".pi" / "extensions" / "multi.ts"
    _write(declared, EXTENSION_WRAPPER_TEMPLATE.format(target="../../../_distributed/extensions/multi/multi.ts"))
    retired = ws / ".pi" / "extensions" / "lybra-loop.ts"
    _write(retired, EXTENSION_WRAPPER_TEMPLATE.format(target="../../../_distributed/extensions/lybra-loop/lybra-loop.ts"))
    red = ds._find_files_to_prune(ws, set())
    green = ds._find_files_to_prune(ws, set(), pi_mounts={"skills": set(), "extensions": {"multi.ts", "claim.ts"}})
    print("red(old):", sorted(Path(p).name for p in red), "| green:", sorted(Path(p).name for p in green))
    assert str(declared) in red and str(declared) not in green
    assert str(retired) in red and str(retired) in green
    # 声明内包装目标缺 = 不删, 列 declared_missing
    target.unlink()
    scan = ds.pi_mount_scan(ws, [], [], mounts={"skills": set(), "extensions": {"multi.ts"}})
    print("scan:", scan)
    assert scan["prune"] == [str(retired)]  # 未声明 + 目标缺 = 回收(与旧包装判据一致)
    assert any(w.startswith(".pi/extensions/multi.ts") for w in scan["declared_missing"])  # 声明内 = 不删, 告警


def test_item3_declared_mounts_single_derivation():
    """声明内挂载名与 enroll 接线同一推导(declared_role_skills / declared_role_extensions + minimum_bootable_set)。"""
    from tools.aipos_cli.distribution_sync import declared_pi_mounts
    from tools.aipos_cli.workstation_wiring import declared_role_distributions, declared_role_extensions, declared_role_skills

    dists = declared_role_distributions("executor", "executor")
    m = declared_pi_mounts(dists)
    print("executor 声明挂载:", {k: sorted(v) for k, v in m.items()})
    assert m["skills"] == set(declared_role_skills(dists)) and "finalize-slice" not in m["skills"]
    assert m["extensions"] == set(declared_role_extensions(dists)) | {"claim.ts"}
    assert os.sep not in "".join(m["extensions"])
