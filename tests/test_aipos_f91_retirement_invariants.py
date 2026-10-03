"""AIPOS-F91: 退役老子系统后的不变量夹具(入 run-all)。

① templates/ 下(`lybra init` 随新项目交付的全部文件)不出现已退役子命令:
   pump / auditor 守护(auditor loop|launch) / agent supervise / launch-check / turn-advancer / lybra on(含 /lybra 斜杠);
   模板项目无关: 不出现本项目实例名 / 机器名 / 本机路径 / 本项目信封字面。
② CLI 解析器不再接受已退役子命令(产品代码层守门, 与 ① 文档层互补)。
③ G3 sync 的 .pi 挂载回收三态(与 F83 pi_mount_scan 同一函数):
   声明内保留 / 不指向产品分发区的挂载不碰 / 指向分发区且不在声明内的挂载回收(目标在也回收, 如退役的 claim.ts)。
④ run-all 位置: 门侧读项目声明 test_contract.runall_path(AIPOS-F93 件③), 产品代码零写死; 本夹具已登记。

纯读产品树 + 临时目录, 不连门、不写真实工位/治理根; token 不涉及。
"""
from __future__ import annotations

import argparse
import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
TEMPLATES = REPO_ROOT / "templates"

#: 已退役子命令(AIPOS-F91 件①, 及 F83 退役的 /lybra 斜杠命令族)
RETIRED_PATTERNS = (
    r"\blybra\s+pump\b",
    r"\bpump\s+run\b",
    r"\blybra\s+auditor\b",
    r"\bauditor\s+(loop|launch)\b",
    r"\bagent\s+supervise\b",
    r"\blaunch-check\b",
    r"\bturn-advancer\b",
    r"\blybra\s+on\b",
    r"(?<![\w.])/lybra\b",
)
#: 模板须项目无关(复查报告 H3/H5 式样 + 本项目实例/信封字面)
PROJECT_LITERAL_RE = re.compile(
    r"kiwiai-dev|/home/kiwi|~/projects/lybra|2_projects/lybra|pol_lybra_|\bexec\.lybra\b|\baudit\.lybra\b|~/lybra\b"
)


def _template_files() -> list[Path]:
    files = sorted(p for p in TEMPLATES.rglob("*") if p.is_file())
    assert files, "templates/ 为空(模板树缺失)"
    return files


def test_templates_teach_no_retired_subcommands():
    hits: list[str] = []
    for path in _template_files():
        text = path.read_text(encoding="utf-8", errors="replace")
        for lineno, line in enumerate(text.splitlines(), 1):
            for pat in RETIRED_PATTERNS:
                if re.search(pat, line):
                    hits.append(f"{path.relative_to(REPO_ROOT)}:{lineno}: {pat} :: {line.strip()[:120]}")
    print("templates 退役命令命中:", hits)
    assert hits == []


def test_templates_project_agnostic_no_project_literals():
    hits = []
    for path in _template_files():
        for lineno, line in enumerate(path.read_text(encoding="utf-8", errors="replace").splitlines(), 1):
            if PROJECT_LITERAL_RE.search(line):
                hits.append(f"{path.relative_to(REPO_ROOT)}:{lineno}: {line.strip()[:120]}")
    print("templates 项目字面命中:", hits)
    assert hits == []


def test_templates_teach_current_driving_path():
    """现行推进路径: 驱动方 `lybra loop`, 工位 `/go`(两份运维手册三套模板逐份)。"""
    for guide in ("advisor-operations-guide.md", "owner-manual-mode-runbook.md"):
        copies = sorted(TEMPLATES.glob(f"*/tree/governance/{guide}"))
        assert len(copies) == 3, copies
        for path in copies:
            text = path.read_text(encoding="utf-8")
            assert "lybra loop --task-id" in text and "/go" in text, path


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
    assert "tests/test_aipos_f91_retirement_invariants.py" in runall
    assert not (REPO_ROOT / "agents" / "harness" / "pi" / "lybra-loop").exists()
    assert not (REPO_ROOT / "tools" / "connector").exists() and not (REPO_ROOT / "tools" / "turn_advancer").exists()


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))
