"""AIPOS-F105: 建项目单一入口(碎片化 N-b:N1)不变量夹具(入 run-all)。

依据: governance/research/2026-10-04-fragmentation-recheck.md 条目 N1 —— 建项目曾有两套入口:
旧 `lybra init` / `lybra workspace init`(走 workspace_templates + templates/ 三套旧布局模板, 不产 project.json)
与现行 `lybra onboarding guide` + `lybra project new`。Owner 10-04 裁定只留后者, 旧入口整体删除、不留兼容别名。

① 被删命令在 argparse 层报不存在(`lybra init` / `lybra workspace init` 均 exit 2 + invalid choice), workspace 组只剩只读 roots;
② `lybra project new` 靶场照常(临时 HOME + 临时 home 根, 本地脚手架产 project.json 与队列骨架), 接入向导第 1 步指向它;
③ 看板不再出现 init 入口: POST /api/workspace/init 路由不存在(非 200), 总览页无 init 命令/服务端初始化按钮, 只指路到接入向导;
④ 删除物零引用: templates/、workspace_templates 模块与其符号、看板 init 路由/接口、受控执行 workspace_init 操作在产品树零引用
   (历史任务卡 task_cards/** 与车道外 lane_blocked 文件除外, 见 LANE_BLOCKED);
⑤ npm 包 files 不再含 templates/; README / QUICKSTART 不再教 init, 建项目指向 onboarding guide + project new。

只写 pytest tmp_path(临时 HOME 与临时 home 根), 不连门、不写真实治理根/工位; token 不涉及。
"""
from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
LYBRA_BIN = REPO_ROOT / "bin" / "lybra"

#: 删除物符号(git grep 固定串)。
DELETED_SYMBOLS = (
    "workspace_templates",
    "build_workspace_init_plan",
    "execute_workspace_init",
    "TEMPLATE_OPERATION",
    "_workspace_init_route",
    "/api/workspace/init",
    "serverSideInit",
    "_run_top_level_init",
)
#: 车道外(本卡 lane.paths 不含)且仍含删除物字面的文件: 只许出现在此, 由后续卡清理(见 F105 RETURN 缺口)。
LANE_BLOCKED = {
    "0_control_plane/templates/workspace_template_protocol.md",  # AIPOS-125 模板协议文档(车道外)
}
#: 历史与本夹具自身不计。
EXEMPT_PREFIXES = ("task_cards/",)
SELF = "tests/test_aipos_f105_single_project_entry.py"
#: 否定断言(守「保持退役」)里出现的删除物字面: 文件 → 允许的符号(只许这些, 不整文件豁免)。
NEGATIVE_GUARDS = {
    "web/board/tests/test_aipos288_fix5_label_en.py": {"_workspace_init_route", "serverSideInit"},
}


def _clean_env(home: Path) -> dict[str, str]:
    env = {k: v for k, v in os.environ.items() if not k.startswith("LYBRA_")}
    env["HOME"] = str(home)  # project new 以 ~/.lybra/connection.json 是否存在分路: 临时 HOME 下必走本地脚手架, 不碰生产门
    return env


def _lybra(args: list[str], *, home: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["node", str(LYBRA_BIN), *args],
        cwd=str(home),
        env=_clean_env(home),
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        timeout=120,
    )


# ---------------------------------------------------------------------------
# ① 被删命令 argparse 层报不存在
# ---------------------------------------------------------------------------


def test_lybra_init_rejected_by_argparse(tmp_path):
    proc = _lybra(["init", "--project-id", "demo"], home=tmp_path)
    print("[① lybra init] exit:", proc.returncode, "\n", proc.stderr.strip().splitlines()[-1])
    assert proc.returncode == 2
    assert "invalid choice: 'init'" in proc.stderr
    assert not (tmp_path / ".lybra" / "workspaces").exists()  # 旧缺省落点未被写


def test_lybra_workspace_init_rejected_by_argparse(tmp_path):
    proc = _lybra(["workspace", "init", "--dry-run", "--template", "blank", "--output", str(tmp_path / "ws"), "--actor", "owner"],
                  home=tmp_path)
    print("[① lybra workspace init] exit:", proc.returncode, "\n", proc.stderr.strip().splitlines()[-1])
    assert proc.returncode == 2
    assert "invalid choice: 'init' (choose from roots)" in proc.stderr
    assert not (tmp_path / "ws").exists()


def test_parser_has_no_init_and_workspace_only_roots():
    import argparse

    from tools.aipos_cli.aipos_cli import build_parser

    def subs(parser: argparse.ArgumentParser) -> dict[str, argparse.ArgumentParser]:
        for action in parser._actions:  # noqa: SLF001 - argparse 无公开枚举口
            if isinstance(action, argparse._SubParsersAction):  # noqa: SLF001
                return dict(action.choices)
        return {}

    top = subs(build_parser())
    assert "init" not in top
    assert set(subs(top["workspace"])) == {"roots"}
    assert "new" in subs(top["project"]) and "guide" in subs(top["onboarding"])


# ---------------------------------------------------------------------------
# ② project new 靶场照常 + 接入向导指向它
# ---------------------------------------------------------------------------


def test_project_new_range_local_scaffold(tmp_path):
    home_root = tmp_path / "govhome"
    home_root.mkdir()
    proc = _lybra(["project", "new", "probe", "--home-root", str(home_root), "--actor", "owner.probe"], home=tmp_path)
    print("[② project new] exit:", proc.returncode, "\n", proc.stdout)
    assert proc.returncode == 0, proc.stderr
    assert "(local scaffold)" in proc.stdout
    root = home_root / "probe"
    decl = json.loads((root / "project.json").read_text(encoding="utf-8"))
    assert decl["project"] == "probe" and decl["registered_by"] == "owner.probe"
    for sub in ("5_tasks/queue", "5_tasks/drafts", "5_tasks/records", "governance", "stage_archive"):
        assert (root / sub).is_dir(), sub
    assert not (root / "2_projects").exists()  # 旧模板布局 2_projects/{{project_id}}/ 不再出现


def test_onboarding_guide_step_points_to_project_new(tmp_path):
    home_root = tmp_path / "gh"
    home_root.mkdir()
    proc = _lybra(["onboarding", "guide", "probe2", "--home-root", str(home_root)], home=tmp_path)
    assert proc.returncode == 0, proc.stderr
    assert f"lybra project new probe2 --home-root {home_root}" in proc.stdout
    assert "lybra init" not in proc.stdout and "workspace init" not in proc.stdout
    assert list(home_root.iterdir()) == []  # 向导只读, 不建任何东西


# ---------------------------------------------------------------------------
# ③ 看板不再出现 init 入口
# ---------------------------------------------------------------------------


def test_board_has_no_workspace_init_route():
    from web.board.app import _api_post_routes, _api_routes, dispatch_api_request

    post_routes = _api_post_routes(None)
    assert "/api/workspace/init" not in post_routes
    status, body = dispatch_api_request(method="POST", path="/api/workspace/init", routes=_api_routes(None, None),
                                        post_routes=post_routes, body={"project_id": "x"})
    print("[③ POST /api/workspace/init] status:", status, body)
    assert status != 200 and body.get("ok") is False


def test_overview_page_points_to_guide_not_init():
    html = (REPO_ROOT / "web" / "board" / "static" / "overview.html").read_text(encoding="utf-8")
    i18n = (REPO_ROOT / "web" / "board" / "static" / "i18n.js").read_text(encoding="utf-8")
    for retired in ("lybra init", "/api/workspace/init", "server-init-btn", "serverSideInit", "init-command"):
        assert retired not in html, retired
    assert "lybra onboarding guide" in html and "guide-command" in html
    for key in ("overview.init_now", "overview.initializing", "overview.init_success",
                "overview.new_project_modal.option_b"):
        assert f"'{key}'" not in i18n, key


def test_controlled_execute_no_workspace_init_operation():
    from tools.aipos_cli.controlled_execute import SUPPORTED_OPERATIONS

    assert "workspace_init" not in SUPPORTED_OPERATIONS


# ---------------------------------------------------------------------------
# ④ 删除物零引用
# ---------------------------------------------------------------------------


def test_deleted_artifacts_absent():
    assert not (REPO_ROOT / "templates").exists()
    assert not (REPO_ROOT / "tools" / "aipos_cli" / "workspace_templates.py").exists()
    assert not (REPO_ROOT / "tools" / "aipos_cli" / "tests" / "test_workspace_templates.py").exists()
    assert not (REPO_ROOT / "tools" / "aipos_cli" / "tests" / "test_manifest_contract.py").exists()


def test_deleted_symbols_have_zero_references():
    hits: list[str] = []
    for sym in DELETED_SYMBOLS:
        proc = subprocess.run(["git", "grep", "-n", "-F", sym], cwd=REPO_ROOT, capture_output=True, text=True)
        assert proc.returncode in (0, 1), proc.stderr  # 1 = 无命中; 其他 = git 出错, fail-closed
        for line in proc.stdout.splitlines():
            rel = line.split(":", 1)[0]
            if rel == SELF or rel.startswith(EXEMPT_PREFIXES) or rel in LANE_BLOCKED:
                continue
            if sym in NEGATIVE_GUARDS.get(rel, set()) and ("not in" in line or "retired" in line):
                continue
            hits.append(f"{sym} :: {line[:160]}")
    print("[④ 删除物引用] 命中:", hits)
    assert hits == []


# ---------------------------------------------------------------------------
# ⑤ 包清单与文档指路
# ---------------------------------------------------------------------------


def test_package_files_drop_templates():
    files = json.loads((REPO_ROOT / "package.json").read_text(encoding="utf-8"))["files"]
    assert "templates/" not in files


def test_readme_quickstart_point_to_single_entry():
    for name in ("README.md", "QUICKSTART.md"):
        text = (REPO_ROOT / name).read_text(encoding="utf-8")
        assert "lybra init" not in text and "workspace init" not in text, name
        assert "lybra onboarding guide" in text and "lybra project new" in text, name


def test_fixture_registered_in_runall():
    runall = (Path(__file__).resolve().parent / "run-all.sh").read_text(encoding="utf-8")
    from tools.aipos_cli.workspace_config import runall_unregistered  # AIPOS-F109 件④: 登记判据 = 门同一实现(清单 discover = 命中式样即登记)

    assert runall_unregistered(["tests/test_aipos_f105_single_project_entry.py"], runall)[0] == []


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))
