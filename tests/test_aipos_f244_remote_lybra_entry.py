"""AIPOS-F244 — 他机会话拉取出口照抄即可执行: 经 ssh 的命令里 lybra = 治理根所在机稳定入口的绝对路径; 主机未声明时同一行给声明命令。

实撞(chris 理想 Mac Claude): 出口 `ssh <治理根主机> 'cd <治理根> && lybra charter …'` 在 Mac 照抄 → 治理根所在机的非登录非交互 ssh
不读 ~/.profile, ~/.local/bin 不进 PATH → `lybra: command not found`, 章程与技能都没落盘。

件① 唯一推导 workstation_wiring.stable_lybra_entry(本进程启动入口 / PATH / 入口缺省目录; 不跟随到部署快照 releases/<版本>, 不取 shebang
    依赖 PATH 的入口), 唯一拼装 charter_render.remote_cli; 章程 / 技能拉取出口(sync pull 行、向导第 5 步、codex 出口、子 agent 开工稿)同调。
件③ 主机未声明: 出口行同一行给声明命令原文(候选值 = 治理根所在机主机名); redeclare / enroll(-deliver)登记到他机且未声明 = 提示一次。

靶场: 件①③ 出口 = AIPOS-F92 probe_range(临时 HOME / home 根 / 自起临时门, 向导原样走到第 4 步); 「他机经非登录 ssh」= 假 ssh(靶场 bin 下,
只认声明的门机别名, 以 `env -i HOME=… PATH=/usr/bin:/bin` 执行引号内命令, 同非登录 ssh 不加载 ~/.local/bin); 件① 部署切换 = 临时部署目录
(releases/<A>、releases/<B> 两份代码快照 + current 链接, 入口由 deploy_runtime.install_entry 写入临时 bin)。禁真门 / 真治理根 / 真工位 / 真 pi;
不写 chris-huibojin 治理根(改登记靶场 = AIPOS-F235 chris 形临时治理根)。

验收(卡面 ★验收): ① 最小环境照抄 sync --dry-run 的章程与技能拉取出口均落盘且与本机渲染一致, codex 出口同样可执行(对照: 裸 lybra = 127);
② 稳定路径在一次部署切换(current 指向新版本)后仍有效; ④ 未声明主机时出口行含声明命令原文。
"""
from __future__ import annotations

import io
import json
import os
import re
import shlex
import shutil
import socket
import subprocess
import sys
import tarfile
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "tests"))

from test_aipos_f92_onboarding_walkthrough import Range, _write, probe_range  # noqa: E402,F401  — 靶场构件唯一来源
from test_aipos_f136_advisor_follow_through import _run_guide_to_step5  # noqa: E402  — 向导走法唯一来源
from test_aipos_f235_instance_redeclare import ADV_LANTU, MAC, MAC_DIR, _redeclare, _rig  # noqa: E402  — chris 形改登记靶场
from test_aipos_f243_claude_code_remote_advisor import _declared_skill_members, _tree  # noqa: E402  — 技能件期望唯一来源

LOCAL = socket.gethostname()
PLACEHOLDER = "<治理根主机>"
MINIMAL_PATH = "/usr/bin:/bin"


def _show(text: str) -> None:
    print(text, flush=True)


def _minimal_ssh(r: Range, alias: str) -> None:
    """假 ssh: 只认 alias(其余 = 255 同连不上); 引号内命令以最小环境(env -i, PATH 只含 /usr/bin:/bin)执行 = 非登录非交互 ssh。"""
    _write(r.bin / "ssh", "#!/bin/bash\n"
                          f'[ "$1" = {shlex.quote(alias)} ] || {{ echo "ssh: unknown host $1" >&2; exit 255; }}\nshift\n'
                          f'exec env -i HOME={shlex.quote(str(r.home))} PATH={MINIMAL_PATH} bash -c "$*"\n')
    (r.bin / "ssh").chmod(0o755)


def _entry(r: Range) -> str:
    return f"{r.bin}/lybra"  # 靶场跑向导 / sync 的入口(本进程启动入口 = stable_lybra_entry 候选 ①)


# ===========================================================================
# 件① 唯一推导: 候选序与跳过规则
# ===========================================================================

def _exe(path: Path, text: str) -> Path:
    _write(path, text)
    path.chmod(0o755)
    return path


def test_item1_stable_entry_candidates_and_skips(tmp_path):
    from tools.aipos_cli.workstation_wiring import stable_lybra_entry

    py = f"#!{sys.executable}\n"
    started = _exe(tmp_path / "started" / "lybra", py)
    on_path = _exe(tmp_path / "onpath" / "lybra", py)
    home_bin = _exe(tmp_path / "home" / ".local" / "bin" / "lybra", py)
    node_shim = _exe(tmp_path / "shim" / "lybra", "#!/usr/bin/env node\n")
    # 部署快照内的入口(releases/<版本>/bin/lybra + 同目录 current 链接)= 下次部署即悬空
    deploy = tmp_path / "repo" / ".deploy"
    in_snapshot = _exe(deploy / "releases" / "r1" / "bin" / "lybra", py)
    (deploy / "current").symlink_to(deploy / "releases" / "r1")
    via_link = tmp_path / "linkbin" / "lybra"
    via_link.parent.mkdir()
    via_link.symlink_to(in_snapshot)

    got = stable_lybra_entry(argv0=str(started), path_env=str(on_path.parent), default_bin_dir=str(home_bin.parent))
    _show(f"[①·启动入口优先] {got}")
    assert got == {"path": str(started), "source": "argv0", "skipped": []}
    got = stable_lybra_entry(argv0="/x/pytest", path_env=str(on_path.parent), default_bin_dir=str(home_bin.parent))
    assert (got["path"], got["source"]) == (str(on_path), "path")
    got = stable_lybra_entry(argv0=str(in_snapshot), path_env=f"{node_shim.parent}:{via_link.parent}",
                             default_bin_dir=str(home_bin.parent))
    _show("[①·跳过: 部署快照内 / 符号链接到快照 / shebang 经 env]\n  " + "\n  ".join(got["skipped"]))
    assert (got["path"], got["source"]) == (str(home_bin), "default_bin_dir")
    assert len(got["skipped"]) == 2 and "位于部署快照内" in got["skipped"][0] and "按 PATH 找解释器" in got["skipped"][1]
    got = stable_lybra_entry(argv0=str(in_snapshot), path_env=str(via_link.parent), default_bin_dir=str(tmp_path / "none"))
    assert got["path"] is None and "位于部署快照内" in got["skipped"][0] and "不是可执行文件" in got["skipped"][-1]
    # 相对路径的启动入口 = 绝对化, 不跟随符号链接(入口本身跨部署不变)
    link_to_started = tmp_path / "alias" / "lybra"
    link_to_started.parent.mkdir()
    link_to_started.symlink_to(started)
    got = stable_lybra_entry(argv0=str(link_to_started), path_env="", default_bin_dir=str(tmp_path / "none"))
    assert got["path"] == str(link_to_started)


def test_item1_remote_cli_falls_back_loudly(tmp_path, monkeypatch):
    """入口都不可用 = 保留裸 lybra, hint 点名原因与出口(不猜); 首词不是 lybra = 声明错(fail-closed)。"""
    from tools.aipos_cli import charter_render
    from tools.schema_loader import SchemaLoadError

    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("PATH", str(tmp_path / "empty"))
    monkeypatch.setattr(sys, "argv", ["pytest"])
    got = charter_render.remote_cli("lybra charter")
    _show(f"[①·无入口] {got}")
    assert got["cli"] == "lybra charter" and got["entry"] is None and "install-entry" in got["hint"] and "command not found" in got["hint"]
    entry = _exe(tmp_path / ".local" / "bin" / "lybra", f"#!{sys.executable}\n")
    got = charter_render.remote_cli("lybra skills")
    assert got == {"cli": f"{entry} skills", "entry": str(entry), "hint": ""}
    with pytest.raises(SchemaLoadError):
        charter_render.remote_cli("python3 -m tools.aipos_cli charter")


def test_item1_builders_route_through_remote_cli():
    """拉取出口拼装只经 remote_cli(不自拼 lybra 字面); 本机形(local_command)仍为 cli_command 原文。"""
    import inspect

    from tools.aipos_cli import charter_render

    for fn in (charter_render.charter_pull_command, charter_render.skills_pull_command, charter_render._workstation_declare_command):
        src = inspect.getsource(fn)
        assert "remote_cli(" in src and "&& lybra" not in src, fn.__name__


# ===========================================================================
# 件① 验收②: 稳定路径在一次部署切换(current 指向新版本)后仍有效
# ===========================================================================

def _snapshot(dest: Path, release: str, commit: str) -> None:
    ignore = shutil.ignore_patterns("__pycache__", "*.pyc", "tests")
    for name in ("tools", "schema", "agents", "config"):
        if (REPO_ROOT / name).is_dir():
            shutil.copytree(REPO_ROOT / name, dest / name, ignore=ignore)
    (dest / "VERSION").write_text(f"release: {release}\ngit_commit: {commit}\n", encoding="utf-8")


def test_item1_entry_survives_deploy_switch(tmp_path):
    from tools.aipos_cli.deploy_runtime import install_entry
    from tools.aipos_cli.workstation_wiring import stable_lybra_entry

    deploy = tmp_path / "prod" / ".deploy"
    rel_a, rel_b = deploy / "releases" / "20261028_000001-aaaaaaa", deploy / "releases" / "20261028_000002-bbbbbbb"
    _snapshot(rel_a, rel_a.name, "a" * 40)
    _snapshot(rel_b, rel_b.name, "b" * 40)
    current = deploy / "current"
    current.symlink_to(rel_a)
    bin_dir = tmp_path / "home" / ".local" / "bin"
    installed = install_entry(current, bin_dir, interpreter=sys.executable)
    entry = Path(installed["entry"])
    env = {"HOME": str(tmp_path / "home"), "PATH": MINIMAL_PATH}

    def version() -> tuple[int, str]:
        p = subprocess.run(["env", "-i", *[f"{k}={v}" for k, v in env.items()], "bash", "-c", f"{shlex.quote(str(entry))} --version"],
                           capture_output=True, text=True, timeout=120)
        return p.returncode, (p.stdout + p.stderr).strip()

    got = stable_lybra_entry(argv0=str(entry), path_env=MINIMAL_PATH, default_bin_dir=str(tmp_path / "none"))
    assert got["path"] == str(entry) and got["source"] == "argv0"
    rc, out = version()
    _show(f"[②·切换前] stable_lybra_entry = {got['path']}\n  $ env -i PATH={MINIMAL_PATH} {entry} --version → exit {rc}: {out}")
    assert rc == 0 and rel_a.name in out
    # 模拟部署切换: current 原子改指新版本(同 tools/lybra-deploy: 临时链接 + rename)
    tmp_link = deploy / ".current.tmp"
    tmp_link.symlink_to(rel_b)
    os.replace(tmp_link, current)
    got2 = stable_lybra_entry(argv0=str(entry), path_env=MINIMAL_PATH, default_bin_dir=str(tmp_path / "none"))
    rc, out = version()
    _show(f"[②·切换后(current → {rel_b.name})] stable_lybra_entry = {got2['path']}\n"
          f"  $ env -i PATH={MINIMAL_PATH} {entry} --version → exit {rc}: {out}")
    assert got2["path"] == got["path"] and rc == 0 and rel_b.name in out and rel_a.name not in out
    # 对照: 跟随到快照的路径(旧做法 realpath)会被跳过 —— 它在切换后指向旧版本, 版本被清理即悬空
    pinned = stable_lybra_entry(argv0=str(rel_a / "bin" / "lybra"), path_env="", default_bin_dir=str(tmp_path / "none"))
    assert pinned["path"] is None and "不是可执行文件" in pinned["skipped"][0]
    _exe(rel_a / "bin" / "lybra", f"#!{sys.executable}\n")
    pinned = stable_lybra_entry(argv0=str(rel_a / "bin" / "lybra"), path_env="", default_bin_dir=str(tmp_path / "none"))
    _show(f"[②·对照: releases/<版本> 下的入口被跳过] {pinned['skipped']}")
    assert pinned["path"] is None and "位于部署快照内" in pinned["skipped"][0]


# ===========================================================================
# 验收①④: 他机 claude-code 会话 —— 未声明主机出口行带声明命令; 声明后最小环境照抄落盘, 与本机渲染一致; 裸 lybra 对照 127
# ===========================================================================

def test_item13_remote_claude_code_pull_exits_run_in_minimal_env(probe_range: Range):
    r = probe_range
    gov = r.hroot / "lybra-probe"
    entry = _entry(r)
    guide, enroll_out = _run_guide_to_step5(
        r, f"lybra-probe --repo app={r.session}/app --default-repo app --advisor-harness claude-code --advisor-host mac-probe "
           f"--advisor-dir /Users/probe/lybra-probe --envelope-days 7 --max-tasks 20")
    inst = guide["instances"]["advisor"]
    _show(f"[③·guide 第 4 步 enroll-deliver --confirm 输出(会话登记在他机, 主机未声明 = 提示一次)]\n{enroll_out}")
    assert enroll_out.count("提示: 实例 ") == 1 and f"--gate-ssh-alias {LOCAL}" in enroll_out
    step4_note = guide["steps"][3]["note"]
    assert f"经 ssh 运行时 lybra 写治理根所在机入口绝对路径 {entry}" in step4_note
    step5 = guide["steps"][4]["command"]
    _show("---- ①·guide 第 5 步 command 原文 ----\n" + step5)
    assert f"{entry} charter --role advisor --instance {inst} --body-only" in step5 and f"{entry} skills --role advisor" in step5

    # ④ 未声明主机: 出口行(每一行)= 占位命令 + 同一行声明命令原文(候选值 = 本机主机名)
    out = r.sh(f"lybra sync --harness-root {gov} --workspace-root {gov} --dry-run")
    _show("---- ④·sync --dry-run 原文(主机未声明) ----\n" + out)
    pull_lines = [ln for ln in out.splitlines() if "= pull:" in ln]
    assert len(pull_lines) == 2
    declare = None
    for ln in pull_lines:
        assert f"ssh {PLACEHOLDER} " in ln and f"{entry} project set-workstation lybra-probe --instance {inst} --gate-ssh-alias {LOCAL}" in ln
        declare = ln.split("之后本出口带真实主机: ", 1)[1]
    _show(f"[④·出口行里的声明命令原文] {declare}")

    # 照抄该声明命令(在治理根所在机运行)→ 出口带真实主机
    r.sh(declare)
    res = json.loads(r.sh(f"lybra sync --harness-root {gov} --workspace-root {gov} --dry-run --json"))["workstations"][0]["result"]
    pulls = {p["distribution_id"]: p for p in res["pull"]}
    land, skills = pulls["advisor-charter-claude-code"]["land_command"], pulls["advisor-skills"]["command"]
    _show(f"---- ①·声明后出口原文 ----\n  章程: {land}\n  技能: {skills}")
    assert land.startswith(f"mkdir -p .claude/rules && ssh {LOCAL} ") and f"{entry} charter" in land and f"{entry} skills" in skills
    assert all(p["host_declared"] and p["host_hint"] == "" for p in res["pull"])

    # ① 他机(Mac 会话目录)照抄执行, 远端 = 最小环境(非登录 ssh)
    _minimal_ssh(r, LOCAL)
    mac = r.tmp / "mac-session"
    mac.mkdir()
    r.sh(land, cwd=mac)
    r.sh(skills, cwd=mac)
    pulled = _tree(mac / ".claude")
    local_charter = r.sh(f"lybra charter --role advisor --instance {inst} --workspace-root {gov} --body-only") + "\n"
    _show(f"[①·最小环境照抄落盘] {len(pulled)} 个文件; 章程 {len(pulled['rules/lybra-advisor.md'])} 字节 vs 本机渲染 "
          f"{len(local_charter.encode('utf-8'))} 字节")
    assert pulled["rules/lybra-advisor.md"] == local_charter.encode("utf-8")
    assert {f".claude/{k}": v for k, v in pulled.items() if k.startswith("skills/")} == _declared_skill_members()

    # 对照(修复前形): 同一出口里的稳定入口换回裸 lybra → 非登录 ssh 下 command not found
    bare = r.sh(skills.replace(f"{entry} skills", "lybra skills") + "; echo pipe=${PIPESTATUS[0]}", cwd=mac)
    _show(f"[①·对照: 裸 lybra 出口] {bare}")
    assert "lybra: command not found" in bare and "pipe=127" in bare


# ===========================================================================
# 验收①: codex 出口(只读出, 不落盘)同样可执行
# ===========================================================================

def test_item1_remote_codex_pull_exit_runs_in_minimal_env(probe_range: Range):
    r = probe_range
    gov = r.hroot / "lybra-probe"
    entry = _entry(r)
    guide, _enroll = _run_guide_to_step5(
        r, f"lybra-probe --repo app={r.session}/app --default-repo app --advisor-harness codex --advisor-host mac-probe "
           f"--host-segment devbox --envelope-days 7 --max-tasks 20")
    inst = guide["instances"]["advisor"]
    out = r.sh(f"lybra sync --harness-root {gov} --workspace-root {gov} --dry-run")
    _show("---- ④·codex sync --dry-run 原文(主机未声明) ----\n" + out)
    line = next(ln for ln in out.splitlines() if "= pull:" in ln)
    assert f"`ssh {PLACEHOLDER} '" in line and f"--gate-ssh-alias {LOCAL}" in line
    r.sh(line.split("之后本出口带真实主机: ", 1)[1])
    res = json.loads(r.sh(f"lybra sync --harness-root {gov} --workspace-root {gov} --dry-run --json"))["workstations"][0]["result"]
    cmd = res["pull"][0]["command"]
    assert cmd == f"ssh {LOCAL} " + shlex.quote(f"cd {gov} && {entry} charter --role advisor --instance {inst}")
    _minimal_ssh(r, LOCAL)
    mac = r.tmp / "mac-codex"
    mac.mkdir()
    charter = r.sh(cmd, cwd=mac)
    _show(f"[①·codex 出口最小环境照抄] `{cmd}` → 章程 {len(charter)} 字符, 前 2 行: {charter.splitlines()[:2]}")
    assert f"实例 `{inst}`" in charter and "<!-- lybra:charter-render" in charter


# ===========================================================================
# 件③ redeclare 改登记到他机且未声明 = 提示一次; 已声明 / 本机 = 不提示
# ===========================================================================

def test_item3_redeclare_to_remote_host_hints_declaration_once(tmp_path, monkeypatch):
    from tools.aipos_cli.charter_render import remote_host_declaration_hint
    from tools.aipos_cli.workspace_config import set_project_workstation

    gov = _rig(tmp_path, monkeypatch, launch=["pi", "claude-code", "codex"])
    rc, out, err = _redeclare(gov, "--harness", "claude-code", "--host", MAC, "--dir", MAC_DIR)
    _show(f"---- ③·redeclare 到他机 {MAC}(未声明)预演原文 rc={rc} ----\n{out}{err}")
    assert rc == 0 and out.count("提示: 实例 ") == 1 and f"会话登记在他机 {MAC}" in out
    assert re.search(rf"project set-workstation \S+ --instance {re.escape(ADV_LANTU)} --gate-ssh-alias {re.escape(LOCAL)} ", out)
    rc, out, err = _redeclare(gov, "--harness", "claude-code", "--host", MAC, "--dir", MAC_DIR, "--json")
    assert rc == 0 and "set-workstation" in json.loads(out)["host_declaration_hint"]
    assert remote_host_declaration_hint(gov, ADV_LANTU, LOCAL) is None, "本机 = 不提示"
    set_project_workstation(gov, ADV_LANTU, gate_ssh_alias="gate-dev", material_access="经 ssh gate-dev 读写治理根", dry_run=False)
    rc, out, err = _redeclare(gov, "--harness", "claude-code", "--host", MAC, "--dir", MAC_DIR)
    _show(f"---- ③·已声明后预演原文 rc={rc} ----\n{out}{err}")
    assert rc == 0 and "提示: 实例 " not in out
