"""AIPOS-F144 — 读类命令治理根解析与写类同一规则: 显式 --workspace-root(全局与子命令均可)> 当前所在治理根 > 拒, 禁回落 home 活动项目。

依据(10-10 chris 岚图分顾问实撞): 在 chris 治理根下 `lybra needs-owner --lane huibojin-web6` 报「--lane 不是本项目声明的 lane;
可选: ['/home/kiwi/projects/lybra']」—— 读类命令走 AIPOS-226 优先级梯, 自 cwd 向上先撞到 ~/.lybra/config.json(全局配置, 带 home_root)
即进 home 模型 = 活动项目 lybra, 先于「所在治理根」; needs-owner 子命令也不认 --workspace-root。

件① 唯一实现 workspace_config.resolve_governance_root(读写共用; F127 resolve_project_write_target 与 F88 governance_workspace_root 均委托):
     显式 --workspace-root > 环境变量 LYBRA_WORKSPACE_ROOT > 当前目录所在已建治理根 > 工位 connection.json 声明 > (仅全局视图命令)活动项目 > 拒。
件② 子命令级 --workspace-root 唯一注册口 workspace_config.attach_workspace_root_flags(声明 verbs.schema governance_root_resolution.commands),
     与全局同义(两处指向不同 = GOVERNANCE_ROOT_CONFLICT); 查看命令首行标注解析到的项目与来源(--json 时走 stderr, stdout 仍为纯 JSON)。

靶场: 临时 HOME 下 ~/.lybra/config.json(home_root + active_project=alpha), home 根 = HOME/ai-project-os/2_projects(全局配置是治理根的
祖先目录配置 —— 与真实机同形, 复现「向上搜配置先撞全局配置」); 两项目 alpha(活动项目)与 beta(声明仓 beta-web / beta-api)。不触真实治理根。
"""
from __future__ import annotations

import ast
import contextlib
import io
import json
import os
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

VIEW_COMMANDS = (
    ("needs-owner",),
    ("next",),
    ("brief",),
    ("loop", "status"),
)


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


def _first_line(text: str) -> str:
    return text.splitlines()[0] if text else ""


def _expected_line(project: str, source_key: str, root: Path) -> str:
    from tools.aipos_cli.workspace_config import governance_root_declaration

    decl = governance_root_declaration()
    return decl["resolved_line"].format(project=project, source=f"{decl['sources'][source_key]} {root.resolve()}")


@pytest.fixture()
def rig(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    from tools.aipos_cli.workspace_config import scaffold_project, set_project_repos

    userhome = tmp_path / "userhome"
    (userhome / ".lybra").mkdir(parents=True)
    monkeypatch.setenv("HOME", str(userhome))
    for var in ("LYBRA_HOME_ROOT", "LYBRA_WORKSPACE_ROOT", "AIPOS_WORKSPACE_ROOT", "LYBRA_CONNECTION_JSON", "LYBRA_ACTIVE_PROJECT"):
        monkeypatch.delenv(var, raising=False)
    home = userhome / "ai-project-os" / "2_projects"
    repos = {name: tmp_path / "repos" / name for name in ("alpha-core", "beta-web", "beta-api")}
    for path in repos.values():
        path.mkdir(parents=True)
    alpha = scaffold_project(home, "alpha", registered_by="owner.alpha")
    beta = scaffold_project(home, "beta", registered_by="owner.beta")
    set_project_repos(home, "alpha", {"alpha-core": repos["alpha-core"]}, default="alpha-core")
    set_project_repos(home, "beta", {"beta-web": repos["beta-web"], "beta-api": repos["beta-api"]}, default="beta-web")
    for root in (alpha, beta):
        (root / "governance" / "decision_log").mkdir(parents=True, exist_ok=True)  # brief 读决策目录(夹具最小骨架)
    (userhome / ".lybra" / "config.json").write_text(
        json.dumps({"config_version": 2, "home_root": str(home), "active_project": "alpha"}), encoding="utf-8")
    outside = userhome / "elsewhere"  # 在全局配置之下、不在任何治理根内(真实机上 ~ 下随便一个目录)
    outside.mkdir()
    return {"home": home, "alpha": alpha, "beta": beta, "outside": outside, "userhome": userhome}


# ===========================================================================
# 件① 解析: B 根下 → B; A 根下不回落; 不在任何根内无参 = 拒
# ===========================================================================

def test_item1_old_ladder_reproduces_bug_new_resolver_reads_beta(rig, monkeypatch):
    """对照: 原读类缺省(AIPOS-226 梯, task_loader.find_repo_root)在 beta 根下解析成活动项目 alpha; 新唯一实现 = beta(所在治理根)。"""
    from tools.aipos_cli.task_loader import find_repo_root
    from tools.aipos_cli.workspace_config import resolve_governance_root

    monkeypatch.chdir(rig["beta"] / "governance")
    old = find_repo_root()
    hit = resolve_governance_root()
    _show(f"[件①·对照] cwd=beta/governance 原梯 find_repo_root() = {old}; 新 resolve_governance_root() = "
          f"{hit['project']} {hit['kind']} {hit['project_root']}")
    assert old == rig["alpha"].resolve()  # 病理复现(服务进程仍用该梯, 本卡不动, 见读点清单)
    assert hit["project"] == "beta" and hit["kind"] == "cwd_root" and hit["project_root"] == rig["beta"].resolve()


@pytest.mark.parametrize("command", VIEW_COMMANDS, ids=lambda c: " ".join(c))
def test_item1_view_commands_in_beta_root_resolve_beta(rig, monkeypatch, command):
    monkeypatch.chdir(rig["beta"] / "5_tasks")
    rc, out, err = _cli(*command, "--lane", "beta-web")
    _show(f"[件①·beta 根下] lybra {' '.join(command)} --lane beta-web rc={rc}\n{out}{err}")
    assert rc == 0, out + err
    assert _first_line(out) == _expected_line("beta", "cwd_root", rig["beta"])
    assert "不是本项目声明的 lane" not in err and "alpha" not in out


@pytest.mark.parametrize("command", VIEW_COMMANDS, ids=lambda c: " ".join(c))
def test_item1_in_alpha_root_never_falls_back(rig, monkeypatch, command):
    """A 根下(活动项目改钉 beta): 解析 = A(来源 = 所在治理根), --lane <B 仓> 按 A 的声明拒, 绝不回落活动项目 beta。"""
    monkeypatch.setenv("LYBRA_ACTIVE_PROJECT", "beta")
    monkeypatch.chdir(rig["alpha"])
    rc, out, err = _cli(*command, "--lane", "alpha-core")
    _show(f"[件①·alpha 根下 活动项目=beta] lybra {' '.join(command)} --lane alpha-core rc={rc}\n{_first_line(out)}")
    assert rc == 0 and _first_line(out) == _expected_line("alpha", "cwd_root", rig["alpha"]), out + err
    rc, out, err = _cli(*command, "--lane", "beta-web")
    _show(f"[件①·alpha 根下] lybra {' '.join(command)} --lane beta-web rc={rc}\n{out}{err}")
    assert rc == 2 and "beta-web" in err and "alpha-core" in err, out + err


@pytest.mark.parametrize("command", VIEW_COMMANDS, ids=lambda c: " ".join(c))
def test_item1_outside_any_root_without_flag_rejects(rig, monkeypatch, command):
    monkeypatch.chdir(rig["outside"])
    rc, out, err = _cli(*command, "--lane", "beta-web")
    _show(f"[件①·不在任何根内无参] lybra {' '.join(command)} rc={rc}\nstdout={out!r}\nstderr={err.strip()}")
    assert rc != 0 and out == ""
    assert "GOVERNANCE_ROOT_UNRESOLVED" in err and "--workspace-root" in err and "alpha" not in err


# ===========================================================================
# 件② 子命令级与全局 --workspace-root 同义; 首行标注; 冲突拒; 单处注册
# ===========================================================================

@pytest.mark.parametrize("command", VIEW_COMMANDS + (("state", "lint"), ("queue",)), ids=lambda c: " ".join(c))
def test_item2_subcommand_and_global_flag_equivalent(rig, monkeypatch, command):
    monkeypatch.chdir(rig["outside"])
    beta = str(rig["beta"])
    sub = _cli(*command, "--workspace-root", beta)
    glob = _cli("--workspace-root", beta, *command)
    _show(f"[件②·同义] lybra {' '.join(command)} --workspace-root <beta> rc={sub[0]} | lybra --workspace-root <beta> "
          f"{' '.join(command)} rc={glob[0]}\n{_first_line(sub[1])}")
    assert sub == glob
    assert _first_line(sub[1]) == _expected_line("beta", "workspace_root", rig["beta"])


def test_item2_needs_owner_subcommand_flag_and_conflict(rig, monkeypatch):
    monkeypatch.chdir(rig["alpha"])  # 显式 > 所在治理根
    rc, out, err = _cli("needs-owner", "--workspace-root", str(rig["beta"]), "--lane", "beta-api")
    _show(f"[件②·needs-owner 子命令级] cwd=alpha rc={rc}\n{out}{err}")
    assert rc == 0 and _first_line(out) == _expected_line("beta", "workspace_root", rig["beta"])
    rc, out, err = _cli("--workspace-root", str(rig["alpha"]), "needs-owner", "--workspace-root", str(rig["beta"]))
    _show(f"[件②·两处冲突] rc={rc} {err.strip()}")
    assert rc != 0 and "GOVERNANCE_ROOT_CONFLICT" in err and str(rig["alpha"]) in err and str(rig["beta"]) in err


def test_item2_json_mode_keeps_stdout_pure(rig, monkeypatch):
    monkeypatch.chdir(rig["beta"])
    rc, out, err = _cli("needs-owner", "--json")
    assert rc == 0, err
    json.loads(out)
    assert _first_line(err) == _expected_line("beta", "cwd_root", rig["beta"])
    rc, out, err = _cli("next", "--json")
    assert rc == 0, err
    assert isinstance(json.loads(out), list) and _expected_line("beta", "cwd_root", rig["beta"]) in err


def test_item2_env_and_workstation_tiers(rig, monkeypatch, tmp_path):
    """环境变量(显式等级)与工位声明(connection.json#governance_root)都不是活动项目; 来源如实标注。"""
    from tools.aipos_cli.workspace_config import resolve_governance_root

    monkeypatch.chdir(rig["outside"])
    monkeypatch.setenv("LYBRA_WORKSPACE_ROOT", str(rig["beta"]))
    rc, out, _err = _cli("needs-owner")
    assert rc == 0 and _first_line(out) == _expected_line("beta", "env", rig["beta"]).replace("{env}", "LYBRA_WORKSPACE_ROOT")
    monkeypatch.delenv("LYBRA_WORKSPACE_ROOT")
    station = tmp_path / "station"
    (station / ".lybra").mkdir(parents=True)
    (station / ".lybra" / "connection.json").write_text(json.dumps({"governance_root": str(rig["beta"])}), encoding="utf-8")
    hit = resolve_governance_root(start=station)
    assert hit["kind"] == "workstation" and hit["project"] == "beta"


def test_item2_registration_single_point(rig):
    """声明所列命令的 --workspace-root 只由 attach_workspace_root_flags 注册(帮助文案 = 声明); 命令树内其余同名参数不在声明清单。"""
    import argparse

    from tools.aipos_cli.aipos_cli import build_parser
    from tools.aipos_cli.cli_self_describe import subcommand_parser
    from tools.aipos_cli.workspace_config import governance_root_declaration

    decl = governance_root_declaration()
    parser = build_parser()
    for path in decl["commands"]:
        sub = subcommand_parser(parser, path, label="test")
        actions = [a for a in sub._actions if "--workspace-root" in a.option_strings]
        assert len(actions) == 1 and actions[0].help == decl["help"] and actions[0].default is argparse.SUPPRESS, path
    for path in ("needs-owner", "next", "brief", "loop status", "state lint", "queue", "draft list"):
        assert path in decl["commands"]
    assert set(decl["announce_commands"]) <= set(decl["commands"])


# ===========================================================================
# 防碎片化: 读写共用一个实现; 读点棘轮
# ===========================================================================

def _calls_in(func: ast.AST) -> set[str]:
    return {n.func.id if isinstance(n.func, ast.Name) else getattr(n.func, "attr", "")
            for n in ast.walk(func) if isinstance(n, ast.Call)}


def test_single_implementation_shared_by_read_and_write():
    src = (REPO_ROOT / "tools" / "aipos_cli" / "workspace_config.py").read_text(encoding="utf-8")
    funcs = {n.name: n for n in ast.walk(ast.parse(src)) if isinstance(n, ast.FunctionDef)}
    assert "resolve_governance_root" in _calls_in(funcs["resolve_project_write_target"])
    assert "resolve_governance_root" in _calls_in(funcs["governance_workspace_root"])
    callers = [name for name, fn in funcs.items() if "enclosing_governance_root" in _calls_in(fn)]
    assert callers == ["resolve_governance_root"], callers
    cli = (REPO_ROOT / "tools" / "aipos_cli" / "aipos_cli.py").read_text(encoding="utf-8")
    cli_funcs = {n.name: n for n in ast.walk(ast.parse(cli)) if isinstance(n, ast.FunctionDef)}
    assert "resolve_governance_root" in _calls_in(cli_funcs["_find_repo_root_for_args"])


# 读点棘轮(只减不增): aipos_cli 包内(测试除外)直接调用 AIPOS-226 优先级梯 / 活动项目解析的点, 逐个已在 RETURN 读点清单说明「不改」理由。
LADDER_FUNCS = ("resolve_workspace_root", "resolve_workspace_context", "find_repo_root", "find_repo_context", "resolve_active_project")
LADDER_CALLS_ALLOWED = {
    ("aipos_cli.py", "resolve_workspace_root"): 2,      # _resolve_workspace_for_command(board/serve/mcp/roles 服务进程) + finalize 产品仓根
    ("audit_helpers.py", "resolve_workspace_root"): 1,  # 门地址解析的工作区(审计派发), 门侧
    ("board_adapter.py", "find_repo_root"): 1,          # 看板服务
    ("board_adapter.py", "find_repo_context"): 1,       # 看板服务
    ("deploy_gate.py", "resolve_workspace_root"): 1,    # 部署门 verdict 校验
    ("finalize.py", "resolve_workspace_root"): 1,       # finalize 治理根缺省(产品仓 cwd 运行)
    ("onboarding.py", "resolve_active_project"): 1,     # Owner 中央凭据库所在门工作区(home 级)
    ("task_loader.py", "find_repo_root"): 3,            # load_* 以已解析根为 start(直用)
    ("task_loader.py", "find_repo_context"): 1,
    ("task_loader.py", "resolve_workspace_context"): 2,
    ("workspace_config.py", "resolve_workspace_context"): 2,   # resolve_workspace_root 薄壳 + resolve_governance_root 全局视图级
    ("workspace_config.py", "resolve_active_project"): 3,      # AIPOS-226 梯内部(home 模型)
}


def test_read_point_ratchet():
    counts: dict[tuple[str, str], int] = {}
    for path in sorted((REPO_ROOT / "tools" / "aipos_cli").glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                name = node.func.id if isinstance(node.func, ast.Name) else getattr(node.func, "attr", "")
                if name in LADDER_FUNCS:
                    counts[(path.name, name)] = counts.get((path.name, name), 0) + 1
    _show("[棘轮] 直接调用 AIPOS-226 梯/活动项目解析的读点: " + json.dumps({f"{k[0]}:{k[1]}": v for k, v in sorted(counts.items())},
                                                               ensure_ascii=False))
    over = {k: v for k, v in counts.items() if v > LADDER_CALLS_ALLOWED.get(k, 0)}
    assert not over, f"新增直接读点(须经 resolve_governance_root): {over}"


def test_write_target_still_refuses_outside_root(rig, monkeypatch):
    """F127 写命令经同一实现后行为不变: 不在任何根内且不写项目名 = PROJECT_TARGET_UNRESOLVED, 不回落活动项目。"""
    monkeypatch.chdir(rig["outside"])
    rc, out, err = _cli("project", "set-meta", "--phase", "x")
    _show(f"[件①·写] 不在任何根内 set-meta rc={rc} {err.strip()}")
    assert rc == 1 and "PROJECT_TARGET_UNRESOLVED" in err
    monkeypatch.chdir(rig["beta"] / "governance")
    rc, out, err = _cli("project", "set-meta", "--phase", "x")
    assert rc == 0 and "目标项目 beta" in _first_line(out), out + err


def test_workspace_roots_in_beta_root_reports_beta(rig, monkeypatch):
    """F88 命名入口 governance_workspace_root 委托唯一实现: beta 根下 `workspace roots` = beta(原梯 = 活动项目 alpha)。"""
    monkeypatch.chdir(rig["beta"] / "governance")
    rc, out, err = _cli("workspace", "roots", "--field", "governance_root")
    _show(f"[件①·workspace roots] cwd=beta rc={rc} {out.strip()}")
    assert rc == 0 and out.strip() == str(rig["beta"].resolve()), err


def test_loop_derived_commands_carry_governance_root(rig, monkeypatch):
    """loop / next --run 执行派生命令(子进程, 命令文本不带根)时经环境变量 LYBRA_WORKSPACE_ROOT 下传推导所在治理根:
    loop 的 cwd 不在治理根内也解析到该根, 不回落活动项目(本卡前派生的 queue claim 等靠 home 模型兜底)。"""
    from tools.aipos_cli import next_resolver as nr

    monkeypatch.chdir(rig["outside"])
    res = nr._run_product_command("lybra workspace roots --field governance_root", "close", governance_root=rig["beta"])
    _show(f"[件①·loop 派生命令] cwd=outside governance_root=beta → rc={res['exit_code']} {res['output'].strip()}")
    assert res["ok"] and res["output"].strip() == str(rig["beta"].resolve())


def test_read_commands_accept_legacy_queue_root_in_cwd(tmp_path, monkeypatch):
    """读命令所在目录一级兼容无 project.json 的存量队列根(同原梯「向上队列结构」); 写命令仍只认已建治理根。"""
    from tools.aipos_cli.workspace_config import ProjectTargetError, resolve_governance_root

    for var in ("LYBRA_HOME_ROOT", "LYBRA_WORKSPACE_ROOT", "AIPOS_WORKSPACE_ROOT"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("HOME", str(tmp_path / "userhome"))
    legacy = tmp_path / "legacy"
    (legacy / "5_tasks" / "queue" / "pending").mkdir(parents=True)
    hit = resolve_governance_root(start=legacy / "5_tasks" / "queue", established=False)
    assert hit["project_root"] == legacy.resolve() and hit["kind"] == "cwd_root" and hit["project"] is None
    with pytest.raises(ProjectTargetError, match="GOVERNANCE_ROOT_UNRESOLVED"):
        resolve_governance_root(start=legacy, established=True, declared_sources=False)
