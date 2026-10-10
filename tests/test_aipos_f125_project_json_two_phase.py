"""AIPOS-F125 — 写 project.json 的 CLI 命令统一两阶段: set-repo / set-repos / set-workstation 与 set-paths 同一语义。

依据: chris 总顾问按接入提示词「每步先 --dry-run 给 Owner 看」核验时指出 set-repos 等三命令无预演, 一跑就写。
件① 缺省 = 预演(打印将写的 project.json diff 与校验结果, 零写入), --confirm 才写; 两阶段语义声明在
     verbs.schema two_phase_protocol.project_json_writers 一处; 预演/diff 本体 = workspace_config.update_project_json(dry_run,
     写前以读取口预检将写入的内容), CLI 旗标注册与输出渲染 = aipos_cli._project_json_two_phase_* 唯一包装。
件② 调用方(向导 Step 2、既有夹具)补 --confirm; set-repo 改经唯一写路径(原整文件重写会丢 repos / paths / workstations 段)。
件③ 预演输出只涉 project.json, 不印凭据 / connection.json 内容; set-repo 门路径的预演不取凭据、不调门动词。
"""
from __future__ import annotations

import ast
import contextlib
import hashlib
import io
import json
import os
import shlex
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

COMMANDS = ("set-paths", "set-repo", "set-repos", "set-workstation")
# AIPOS-F127 件③: set-meta 登记于同一声明(两阶段旗标 / 输出包装同一份); 其写入正反例见 test_aipos_f127_project_write_target.py
# (键须 config.schema project_json 已声明, 本文件的逐命令写入参数化只覆盖上面四个)
DECLARED_COMMANDS = COMMANDS + ("set-meta", "set-execution")  # AIPOS-F143 件②: set-execution 登记于同一声明(正反例见 test_aipos_f143_execution_mode.py)
SECRET = "lybra_tok_F125SECRETabcdefghijklmnopqrstuvwxyz0123456789"
PREVIEW = "预览(未写; 加 --confirm 写入)"


def _show(line: str) -> None:
    print(line, flush=True)


def _cli(*argv: str) -> tuple[int, str, str]:
    from tools.aipos_cli.aipos_cli import main

    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        try:
            rc = main(list(argv))
        except SystemExit as exc:
            rc = exc.code if isinstance(exc.code, int) else 1
    return int(rc or 0), out.getvalue(), err.getvalue()


def _md5(path: Path) -> str:
    return hashlib.md5(path.read_bytes()).hexdigest()


@pytest.fixture()
def proj(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """临时 home 根下新建项目 probe(本地脚手架; HOME 指临时目录, 无 connection.json → set-repo 走本地路径)。"""
    from tools.aipos_cli.workspace_config import scaffold_project

    userhome = tmp_path / "userhome"
    userhome.mkdir()
    monkeypatch.setenv("HOME", str(userhome))
    home = tmp_path / "home"
    root = scaffold_project(home, "probe", registered_by="owner.probe")
    return {"home": home, "root": root, "pj": root / "project.json", "tmp": tmp_path, "userhome": userhome}


def _argv(command: str, p: dict) -> list[str]:
    base = ["project", command, "probe", "--home-root", str(p["home"])]
    tmp = p["tmp"]
    return base + {
        "set-paths": ["--key", "finalize_mode", "--value", "external"],
        "set-repo": ["--code-repo", str(tmp / "repo-main")],
        "set-repos": ["--repo", f"app={tmp}/app", "--repo", f"lib={tmp}/lib", "--default", "app"],
        "set-workstation": ["--instance", "exec.probe.mac", "--gate-ssh-alias", "gate-dev", "--material-access", "经 ssh gate-dev 读写"],
    }[command]


def _read_back(command: str, p: dict) -> object:
    from tools.aipos_cli.workspace_config import project_paths, project_repos, project_workstation

    if command == "set-paths":
        return project_paths(p["root"])["finalize_mode"]
    if command == "set-repo":
        return str(project_repos(p["root"])["code_repo"])
    if command == "set-repos":
        return {k: str(v) for k, v in project_repos(p["root"])["items"].items()}
    return project_workstation(p["root"], "exec.probe.mac")


# ===========================================================================
# 件① 四个命令: 缺省 / --dry-run 预演零写入(md5 前后一致), --confirm 才写, 读取口见值
# ===========================================================================

@pytest.mark.parametrize("command", COMMANDS)
def test_item1_each_command_previews_by_default_and_writes_only_on_confirm(proj, command):
    pj = proj["pj"]
    argv = _argv(command, proj)
    before = _md5(pj)
    for flags in ([], ["--dry-run"]):
        rc, out, err = _cli(*argv, *flags)
        after = _md5(pj)
        _show(f"[件①·{command} {' '.join(flags) or '(缺省)'}] rc={rc} md5 前={before} 后={after}\n{out}{err}")
        assert rc == 0 and f"project {command} probe: {PREVIEW}" in out, out + err
        assert "(当前)" in out and "(写入后)" in out and "\n+" in out, "预演打印 project.json unified diff"
        assert after == before, "预演零写入"
    rc, out, err = _cli(*argv, "--confirm")
    _show(f"[件①·{command} --confirm] rc={rc} md5 前={before} 后={_md5(pj)}\n{out}{err}")
    assert rc == 0 and f"project {command} probe: 已写入" in out, out + err
    assert _md5(pj) != before
    value = _read_back(command, proj)
    _show(f"[件①·{command} 读取口回读] {value}")
    expected = {
        "set-paths": "external",
        "set-repo": str(proj["tmp"] / "repo-main"),
        "set-repos": {"app": f"{proj['tmp']}/app", "lib": f"{proj['tmp']}/lib"},
        "set-workstation": {"gate_ssh_alias": "gate-dev", "material_access": "经 ssh gate-dev 读写"},
    }[command]
    assert value == expected
    data = json.loads(pj.read_text(encoding="utf-8"))
    assert data["project"] == "probe" and data["registered_by"] == ("owner.probe" if command != "set-repo" else data["registered_by"])
    written = _md5(pj)
    rc, out, _err = _cli(*argv, "--confirm")
    assert rc == 0 and f"project {command} probe: 无改动" in out and _md5(pj) == written, "同值重写 = 无改动"


def test_item1_dry_run_and_confirm_are_mutually_exclusive_and_json_preview(proj):
    pj = proj["pj"]
    before = _md5(pj)
    for command in COMMANDS:
        rc, _out, err = _cli(*_argv(command, proj), "--dry-run", "--confirm")
        assert rc == 2 and "not allowed with argument" in err, (command, err)
    rc, out, err = _cli(*_argv("set-repos", proj), "--json")
    payload = json.loads(out)
    _show(f"[件①·set-repos --json 预演] {json.dumps({k: payload[k] for k in ('command', 'dry_run', 'changed', 'written')}, ensure_ascii=False)}")
    assert rc == 0 and payload["dry_run"] is True and payload["written"] is False and payload["changed"] is True
    assert payload["default"] == "app" and "+  \"repos\": {" in payload["diff"]
    assert _md5(pj) == before


def test_item1_single_declaration_and_shared_implementation():
    from tools.aipos_cli import aipos_cli as cli
    from tools.aipos_cli.aipos_cli import build_parser
    from tools.schema_loader import SchemaLoadError, load_schema

    decl = load_schema("verbs")["two_phase_protocol"]["project_json_writers"]
    _show(f"[件①·声明] verbs.schema two_phase_protocol.project_json_writers.commands={decl['commands']} "
          f"default_phase={decl['default_phase']} flags={decl['flags']}")
    assert sorted(decl["commands"]) == sorted(DECLARED_COMMANDS) and decl["default_phase"] == "dry_run"
    parser = build_parser()
    for command in COMMANDS:
        args = parser.parse_args(["project", command, "probe"] + {
            "set-paths": ["--key", "k", "--value", "v"], "set-repo": ["--code-repo", "/x"], "set-repos": ["--repo", "a=/x"],
            "set-workstation": ["--instance", "i", "--gate-ssh-alias", "g", "--material-access", "m"]}[command])
        assert args.confirm is False and args.dry_run is False, command  # 缺省 = 预演
    with pytest.raises(SchemaLoadError, match="未登记"):
        import argparse

        cli._project_json_two_phase_flags(argparse.ArgumentParser(), "set-unregistered")

    # CLI: 两阶段旗标只在唯一包装里注册; 四个处理段都走同一输出包装; 状态文案只出自声明(代码无散落字面量)
    src = (REPO_ROOT / "tools/aipos_cli/aipos_cli.py").read_text(encoding="utf-8")
    assert src.count('"--confirm", action="store_true", help="Write project.json"') == 0, "set-paths 原本地旗标注册已收归"
    for command in DECLARED_COMMANDS:
        assert f'_project_json_two_phase_flags(project_set' in src and f'"{command}"' in src
    tree = ast.parse(src)
    emit_calls = [n for n in ast.walk(tree) if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
                  and n.func.id == "_project_json_two_phase_emit"]
    emitted = {n.args[0].value for n in emit_calls if n.args and isinstance(n.args[0], ast.Constant)}
    assert emitted == set(DECLARED_COMMANDS), emitted
    flag_calls = {n.args[1].value for n in ast.walk(tree) if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
                  and n.func.id == "_project_json_two_phase_flags"}
    assert flag_calls == set(DECLARED_COMMANDS), flag_calls
    assert "预览(未写" not in src and "已写入\"" not in src, "状态文案只出自 verbs.schema outcome_labels"

    # 写实现: 四个命令的库函数都经 update_project_json(唯一写路径); 无第二处 write_text
    wc_src = (REPO_ROOT / "tools/aipos_cli/workspace_config.py").read_text(encoding="utf-8")
    wc_tree = ast.parse(wc_src)
    for name in ("update_project_repo", "set_project_repos", "set_project_workstation", "set_project_paths", "set_project_meta"):
        fn = next(n for n in wc_tree.body if isinstance(n, ast.FunctionDef) and n.name == name)
        calls = {c.func.id for c in ast.walk(fn) if isinstance(c, ast.Call) and isinstance(c.func, ast.Name)}
        assert "update_project_json" in calls and "write_text" not in ast.unparse(fn), name
        assert "dry_run" in [a.arg for a in fn.args.kwonlyargs], name
    legacy = next(n for n in wc_tree.body if isinstance(n, ast.FunctionDef) and n.name == "set_project_repo")
    assert {c.func.id for c in ast.walk(legacy) if isinstance(c, ast.Call) and isinstance(c.func, ast.Name)} == {"update_project_repo"}, \
        "set_project_repo(门动词调用口)只是唯一实现的薄包装"


# ===========================================================================
# 件② 非法值: 预演即报错(零写入, md5 不变) + 调用方(向导 Step 2)补 --confirm
# ===========================================================================

def test_item2_invalid_values_rejected_at_preview_with_zero_write(proj):
    from tools.aipos_cli.workspace_config import set_project_repos

    pj = proj["pj"]
    cases = [
        (("project", "set-repos", "probe", "--home-root", str(proj["home"]), "--repo", "app=relative/path"), "REPOS_CONFLICT"),
        (("project", "set-paths", "probe", "--home-root", str(proj["home"]), "--key", "verdict_dir", "--value", "x"), "PATHS_KEY_UNKNOWN"),
        (("project", "set-workstation", "probe", "--home-root", str(proj["home"]), "--instance", "exec.probe.mac",
          "--gate-ssh-alias", "gate-dev", "--material-access", "Bearer xyz"), "WORKSTATION_MATERIAL_INVALID"),
        (("project", "set-repo", "ghost", "--home-root", str(proj["home"]), "--code-repo", "/abs/x"), "PROJECT_NOT_ESTABLISHED"),
    ]
    for argv, code in cases:
        before = _md5(pj)
        rc, out, err = _cli(*argv)
        _show(f"[件②·预演即报错 {code}] rc={rc} md5 前={before} 后={_md5(pj)}\n{out}{err}")
        assert rc == 1 and code in err and "project.json 未改动" in err and _md5(pj) == before, (code, out, err)
    # set-repo 与已声明 repos 冲突(code_repo ≠ items[default])= REPOS_CONFLICT, 预演与 --confirm 均零写入(多仓改用 set-repos)
    set_project_repos(proj["home"], "probe", {"app": str(proj["tmp"] / "app")}, default="app")
    before = _md5(pj)
    for flags in ([], ["--confirm"]):
        rc, out, err = _cli("project", "set-repo", "probe", "--home-root", str(proj["home"]), "--code-repo", str(proj["tmp"] / "other"), *flags)
        _show(f"[件②·set-repo 与 repos 冲突 {' '.join(flags) or '(缺省)'}] rc={rc}\n{out}{err}")
        assert rc == 1 and "REPOS_CONFLICT" in err and _md5(pj) == before


def test_item2_set_repo_keeps_other_project_json_keys(proj):
    """原 set_project_repo 经 write_project_json 整文件重写, 会丢 paths / workstations 等段; 现经唯一写路径只改 code_repo 系键。"""
    from tools.aipos_cli.workspace_config import set_project_paths, set_project_repo, set_project_workstation

    set_project_paths(proj["root"], [("finalize_mode", "external")], dry_run=False)
    set_project_workstation(proj["root"], "exec.probe.mac", gate_ssh_alias="gate-dev", material_access="经 ssh 读写", dry_run=False)
    original_registered_at = json.loads(proj["pj"].read_text(encoding="utf-8"))["registered_at"]
    root = set_project_repo(proj["home"], "probe", str(proj["tmp"] / "repo-x"), registered_by="gate.actor")  # 门动词调用口(写)
    data = json.loads(proj["pj"].read_text(encoding="utf-8"))
    _show(f"[件②·set-repo 写后 project.json] {json.dumps(data, ensure_ascii=False, sort_keys=True)}")
    assert root == proj["root"] and data["code_repo"] == str(proj["tmp"] / "repo-x") and data["registered_by"] == "gate.actor"
    assert data["registered_at"] == original_registered_at and data["paths"] == {"finalize_mode": "external"}
    assert data["workstations"]["exec.probe.mac"]["gate_ssh_alias"] == "gate-dev"


def test_item2_onboarding_step2_previews_then_confirms(tmp_path, monkeypatch):
    from tools.aipos_cli.aipos_cli import build_parser
    from tools.aipos_cli.onboarding import generate_onboarding_guide
    from tools.aipos_cli.workspace_config import scaffold_project

    monkeypatch.setenv("HOME", str(tmp_path / "userhome"))
    (tmp_path / "userhome").mkdir()
    home = tmp_path / "home"
    guide = generate_onboarding_guide("probe-g", home_root=str(home), actor="advisor.probe-g.h", repos=[f"app={tmp_path}/app"])
    step2 = next(s for s in guide["steps"] if s["step_number"] == 2)
    lines = [l for l in step2["command"].splitlines() if l.startswith("lybra project set-repos")]
    _show("[件②·向导 Step 2 原文]\n" + step2["command"])
    assert len(lines) == 2 and lines[0].endswith(" --dry-run") and lines[1].endswith(" --confirm")
    parsed = [build_parser().parse_args(shlex.split(l)[1:]) for l in lines]
    assert (parsed[0].dry_run, parsed[0].confirm, parsed[1].dry_run, parsed[1].confirm) == (True, False, False, True)
    root = scaffold_project(home, "probe-g")
    before = _md5(root / "project.json")
    rc, out, err = _cli(*shlex.split(lines[0])[1:])
    assert rc == 0 and PREVIEW in out and _md5(root / "project.json") == before
    rc, out, err = _cli(*shlex.split(lines[1])[1:])
    assert rc == 0 and "已写入" in out and json.loads((root / "project.json").read_text(encoding="utf-8"))["repos"]["default"] == "app"


# ===========================================================================
# 件③ 预演不印凭据 / connection.json; set-repo 门路径预演不取凭据、不调门动词; --confirm 才经门动词
# ===========================================================================

def _connection_json(userhome: Path) -> Path:
    conn = userhome / ".lybra" / "connection.json"
    conn.parent.mkdir(parents=True, exist_ok=True)
    conn.write_text(json.dumps({"config_version": 1, "mode": "service_v0", "mcp": {"rpc_url": "http://127.0.0.1:9/mcp"},
                                "tokens": [{"role": "owner", "token": SECRET, "instance": "owner.probe"}]}), encoding="utf-8")
    return conn


def test_item3_preview_never_prints_credentials_and_gate_path_preview_calls_no_gate(proj, monkeypatch):
    from tools.aipos_cli import confirm_client
    from tools.aipos_cli.workspace_config import set_project_repo

    _connection_json(proj["userhome"])
    gate_calls: list[str] = []
    token_reads: list[str] = []

    class _FakeGate:
        """门进程替身: dry_run 只回 token, confirm 经与门同一的 set_project_repo 写入。"""

        def __init__(self, base_url, token, timeout=None):
            self.token = token

        def call_tool(self, name, arguments, *, timeout=None):
            gate_calls.append(name)
            if name.endswith("_dry_run"):
                self.pending = arguments
                return {"ok": True, "dry_run_token": "setrepodr_fake"}
            a = self.pending
            root = set_project_repo(a["home_root"], a["name"], a["code_repo"], registered_by=a["actor"])
            return {"ok": True, "project_root": str(root)}

    def _load_token(conn):
        token_reads.append(str(conn))
        return "owner", SECRET

    monkeypatch.setattr(confirm_client, "GateClient", _FakeGate)
    monkeypatch.setattr(confirm_client, "load_gate_client_token", _load_token)
    pj = proj["pj"]
    base = ("project", "set-repo", "probe", "--home-root", str(proj["home"]), "--code-repo", str(proj["tmp"] / "repo-gate"),
            "--owner-authorization-ref", "owner-ok-1")
    before = _md5(pj)
    rc, out, err = _cli(*base)
    _show(f"[件③·set-repo 门路径预演] rc={rc} md5 前={before} 后={_md5(pj)} 门调用={gate_calls} 取凭据={len(token_reads)}\n{out}{err}")
    assert rc == 0 and PREVIEW in out and _md5(pj) == before and gate_calls == [] and token_reads == []
    assert SECRET not in out + err and "connection.json" not in out + err
    rc, out, err = _cli(*base, "--confirm")
    _show(f"[件③·set-repo 门路径 --confirm] rc={rc} 门调用={gate_calls}\n{out}{err}")
    assert rc == 0 and "已写入 (via gate verb)" in out and gate_calls == ["lybra_project_set_repo_dry_run", "lybra_project_set_repo_confirm"]
    assert SECRET not in out + err and json.loads(pj.read_text(encoding="utf-8"))["code_repo"] == str(proj["tmp"] / "repo-gate")
    # 其余三命令(connection.json 在场)预演: 输出只涉 project.json
    for command in ("set-paths", "set-repos", "set-workstation"):
        before = _md5(pj)
        rc, out, err = _cli(*_argv(command, proj))
        assert rc == 0 and PREVIEW in out and _md5(pj) == before, command
        assert SECRET not in out + err and "connection.json" not in out + err and '"tokens"' not in out, command


def test_item3_preview_via_shell_subprocess_is_zero_write(proj):
    """换机器: 经 ssh 远程调用 = 远端 shell 起同一 CLI 进程; 以子进程(独立 HOME)同形调用, 预演同样零写入。"""
    pj = proj["pj"]
    before = _md5(pj)
    env = {**os.environ, "HOME": str(proj["userhome"]), "PYTHONPATH": str(REPO_ROOT)}
    cmd = [sys.executable, "-m", "tools.aipos_cli.aipos_cli", *_argv("set-workstation", proj)]
    done = subprocess.run(cmd, cwd=str(REPO_ROOT), env=env, capture_output=True, text=True, timeout=120)
    _show(f"[件③·子进程预演] rc={done.returncode} md5 前={before} 后={_md5(pj)}\n{done.stdout}{done.stderr}")
    assert done.returncode == 0 and PREVIEW in done.stdout and _md5(pj) == before
