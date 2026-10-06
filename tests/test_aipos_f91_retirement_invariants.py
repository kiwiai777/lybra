"""AIPOS-F91: 退役老子系统后的不变量夹具(入 run-all)。

① (已随 AIPOS-F105 退役) 原 templates/ 模板树不变量: templates/ 与 init / workspace init 一并删除(建项目单入口 =
   lybra onboarding guide + lybra project new), 不再有随新项目交付的模板文件; 删除不变量见 tests/test_aipos_f105_single_project_entry.py。
② CLI 解析器不再接受已退役子命令(产品代码层守门)。
③ G3 sync 的 .pi 挂载回收三态(与 F83 pi_mount_scan 同一函数):
   声明内保留 / 不指向产品分发区的挂载不碰 / 指向分发区且不在声明内的挂载回收(目标在也回收, 如退役的 claim.ts)。
④ run-all 位置: 门侧读项目声明 test_contract.runall_path(AIPOS-F93 件③), 产品代码零写死; 本夹具已登记。

纯读产品树 + 临时目录, 不连门、不写真实工位/治理根; token 不涉及。
"""
from __future__ import annotations

import argparse
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]


def _subcommands(parser: argparse.ArgumentParser) -> dict[str, argparse.ArgumentParser]:
    for action in parser._actions:  # noqa: SLF001 - argparse 无公开枚举口
        if isinstance(action, argparse._SubParsersAction):  # noqa: SLF001
            return dict(action.choices)
    return {}


def test_cli_parser_rejects_retired_subcommands():
    from tools.aipos_cli.aipos_cli import build_parser

    top = _subcommands(build_parser())
    print("顶层子命令:", sorted(top))
    assert not {"pump", "auditor", "turn-advancer"} & set(top)
    agent = _subcommands(top["agent"])
    print("agent 子命令:", sorted(agent))
    assert not {"supervise", "launch-check"} & set(agent)
    assert {"loop", "next", "my-tasks"} <= set(top)


# ---------------------------------------------------------------------------
# ③ G3: .pi 挂载回收三态
# ---------------------------------------------------------------------------

def _link(ws: Path, sub: str, name: str, target: str) -> Path:
    p = ws / ".pi" / sub / name
    p.parent.mkdir(parents=True, exist_ok=True)
    p.symlink_to(target)
    return p


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def test_distribution_area_derived_from_declaration():
    from tools.aipos_cli.distribution_sync import SHARED_LANDING, distribution_area_dirs
    from tools.schema_loader import load_schema

    areas = distribution_area_dirs()
    print("产品分发区:", areas)
    assert SHARED_LANDING in areas
    # AIPOS-F92 件②: 分发区只属 pi harness(distribution.schema harness_semantics; claude-code 件落会话目录, 不在工位父根)
    first_segments = {
        str(d["target"]["relative_path"]).split("/", 1)[0]
        for d in load_schema("distribution")["distributions"]
        if d.get("kind") != "charter" and str(d["target"].get("harness") or "pi") == "pi"
    }
    assert first_segments <= set(areas) and "AGENTS.md" not in areas  # 章程落工位根, 不属父根分发区


def test_pi_mount_scan_three_states(tmp_path):
    """声明内保留 / 分发区外不碰 / 分发区内未声明回收(目标在也回收)。"""
    from tools.aipos_cli import distribution_sync as ds

    parent = tmp_path / "harness-parent"
    ws = parent / "station"
    _write(ws / ".lybra" / "role", '{"role": "executor"}')
    _write(parent / "_shared" / "extensions" / "go.ts", "export default 1\n")
    _write(parent / "_shared" / "extensions" / "claim.ts", "export default 1\n")
    _write(parent / "_distributed" / "skills" / "old-skill" / "SKILL.md", "# old\n")
    _write(parent / "_distributed" / "skills" / "kept-skill" / "SKILL.md", "# kept\n")
    _write(parent / "my-tools" / "own.ts", "export default 1\n")
    _write(tmp_path / "elsewhere" / "abs.ts", "export default 1\n")

    declared_ext = _link(ws, "extensions", "go.ts", "../../../_shared/extensions/go.ts")
    declared_skill = _link(ws, "skills", "kept-skill", "../../../_distributed/skills/kept-skill")
    retired_ext = _link(ws, "extensions", "claim.ts", "../../../_shared/extensions/claim.ts")
    retired_skill = _link(ws, "skills", "old-skill", "../../../_distributed/skills/old-skill")
    user_rel = _link(ws, "extensions", "own.ts", "../../../my-tools/own.ts")
    user_abs = _link(ws, "extensions", "abs.ts", str(tmp_path / "elsewhere" / "abs.ts"))
    user_file = ws / ".pi" / "extensions" / "local.ts"
    _write(user_file, "export default 1\n")  # 工位自有真实文件(非挂载)
    assert all(p.exists() for p in (declared_ext, declared_skill, retired_ext, retired_skill, user_rel, user_abs))

    mounts = {"extensions": {"go.ts"}, "skills": {"kept-skill"}}
    scan = ds.pi_mount_scan(ws, [], [], mounts=mounts)
    print("scan:", scan)
    assert sorted(scan["prune"]) == sorted([str(retired_ext), str(retired_skill)])  # 分发区内未声明: 目标在也回收
    assert scan["declared_missing"] == [] and scan["foreign"] == []

    result = ds._prune_files(list(scan["prune"]))  # 与共享落点 prune 同一删除出口
    assert result["pruned_files"] == scan["prune"] and result["errors"] == [], result
    assert not retired_ext.is_symlink() and not retired_skill.is_symlink()
    assert (parent / "_shared" / "extensions" / "claim.ts").is_file()  # 只删链接本身, 目标不动
    assert declared_ext.is_symlink() and declared_skill.is_symlink()  # 声明内保留
    assert user_rel.is_symlink() and user_abs.is_symlink() and user_file.is_file()  # 分发区外/工位自有不碰

    again = ds.pi_mount_scan(ws, [], [], mounts=mounts)
    assert again == {"prune": [], "declared_missing": [], "foreign": []}  # 稳态


def test_minimum_bootable_set_no_longer_declares_claim_ts():
    from tools.aipos_cli.distribution_sync import declared_pi_mounts
    from tools.aipos_cli.workstation_wiring import declared_role_distributions, minimum_bootable_set_items

    assert not any("claim.ts" in str(i.get("path") or "") for i in minimum_bootable_set_items())
    for role in ("executor", "auditor"):
        assert "claim.ts" not in declared_pi_mounts(declared_role_distributions(role, role))["extensions"]


def test_runall_location_single_declaration_and_registered():
    # AIPOS-F93 件③: 门交回检查的测试清单位置改读项目声明(project.json test_contract.runall_path), 产品代码不再写死 lybra 的 run-all;
    # lybra 产品仓自己的夹具清单由夹具自定位。
    import tools.aipos_cli.board_adapter as adapter

    assert not hasattr(adapter, "RUNALL_RELATIVE_PATH")
    assert "tests/run-all.sh" not in (REPO_ROOT / "tools" / "aipos_cli" / "board_adapter.py").read_text(encoding="utf-8")
    runall = (Path(__file__).resolve().parent / "run-all.sh").read_text(encoding="utf-8")
    from tools.aipos_cli.workspace_config import runall_unregistered  # AIPOS-F109 件④: 登记判据 = 门同一实现(清单 discover = 命中式样即登记)

    assert runall_unregistered(["tests/test_aipos_f91_retirement_invariants.py"], runall)[0] == []
    assert not (REPO_ROOT / "agents" / "harness" / "pi" / "lybra-loop").exists()
    assert not (REPO_ROOT / "tools" / "connector").exists() and not (REPO_ROOT / "tools" / "turn_advancer").exists()


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))
