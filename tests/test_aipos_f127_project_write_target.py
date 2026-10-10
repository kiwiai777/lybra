"""AIPOS-F127 — project 写命令目标项目解析 fail-closed + 输出首行标明目标 + set-meta(phase/note)写入口。

依据: chris 总顾问在 chris 治理根下不写项目名预演 `lybra project set-paths`, 目标被解析成 home 级活动项目 lybra
(缺省走 resolve_active_project; 带 --confirm 即会改写 lybra 的 project.json)。

件① project 族写命令(set-paths / set-meta / set-repo / set-repos / set-workstation / freeze-legacy / dispatch-mode set)目标解析唯一实现
     workspace_config.resolve_project_write_target: 显式项目名 > --workspace-root / 当前目录所在治理根声明的 project.json#project > 拒;
     禁回落 home 级活动项目; 显式项目名与所在治理根声明不一致 = 拒并列出两者。
件② 预演/写入输出首行 = 目标项目名 + 来源 + project.json 绝对路径(verbs.schema project_json_writers.target_line; 同一包装)。
件③ `lybra project set-meta [项目] [--phase] [--note] [--dry-run|--confirm]`: update_project_json 唯一写路径; 键须在
     config.schema project_json.schema 已声明(未声明 = META_KEY_UNDECLARED 零写入)。

靶场: 临时 home 根下两个项目 alpha(home 级活动项目: 全局配置 active_project + LYBRA_ACTIVE_PROJECT 双钉)与 beta;
HOME 指临时目录, 不触真实治理根。
"""
from __future__ import annotations

import ast
import contextlib
import copy
import hashlib
import io
import json
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

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
def rig(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """临时 home 根 + 两项目; alpha = home 级活动项目(全局配置 active_project 与 LYBRA_ACTIVE_PROJECT 都指 alpha)。"""
    from tools.aipos_cli.workspace_config import scaffold_project

    userhome = tmp_path / "userhome"
    (userhome / ".lybra").mkdir(parents=True)
    monkeypatch.setenv("HOME", str(userhome))
    for var in ("LYBRA_HOME_ROOT", "LYBRA_WORKSPACE_ROOT", "AIPOS_WORKSPACE_ROOT", "LYBRA_CONNECTION_JSON"):
        monkeypatch.delenv(var, raising=False)
    home = tmp_path / "home"
    alpha = scaffold_project(home, "alpha", registered_by="owner.alpha")
    beta = scaffold_project(home, "beta", registered_by="owner.beta")
    (userhome / ".lybra" / "config.json").write_text(
        json.dumps({"home_root": str(home), "active_project": "alpha"}), encoding="utf-8")
    monkeypatch.setenv("LYBRA_ACTIVE_PROJECT", "alpha")
    outside = tmp_path / "elsewhere"
    outside.mkdir()
    return {"home": home, "alpha": alpha, "beta": beta, "outside": outside, "tmp": tmp_path}


def _declare_meta_keys(monkeypatch: pytest.MonkeyPatch, keys: tuple[str, ...] = ("phase", "note")) -> None:
    """把 config.schema configuration_sources.project_json.schema 里 set-meta 登记键的声明状态钉为「恰好声明 keys」
    (其余登记键移除), 用于未声明分支的反例; 真实声明下的正例见 test_item3_set_meta_real_declaration_*。"""
    from tools.aipos_cli.workspace_config import project_meta_declaration

    registered = tuple(project_meta_declaration()["keys"])
    import tools.schema_loader as sl

    original = sl.load_schema

    def patched(schema_type, repo_root=None):
        data = original(schema_type, repo_root)
        if schema_type != "config":
            return data
        data = copy.deepcopy(data)
        decl = data["configuration_sources"]["project_json"]["schema"]
        for key in registered:
            if key not in keys:
                decl.pop(key, None)
        for key in keys:
            decl.setdefault(key, {"type": "string", "required": False, "description": f"(夹具模拟声明) {key}"})
        return data

    monkeypatch.setattr(sl, "load_schema", patched)


def _first_line(out: str) -> str:
    return out.splitlines()[0] if out else ""


# ===========================================================================
# 件① 目标解析: 在 B 治理根下不写项目名 → B(非活动项目 A); 解析不出 = 拒; 显式名与所在根冲突 = 拒
# ===========================================================================

def test_item1_omitted_name_in_beta_root_targets_beta_not_active_alpha(rig, monkeypatch):
    from tools.aipos_cli.workspace_config import resolve_active_project

    sub = rig["beta"] / "governance"
    monkeypatch.chdir(sub)  # 治理根内任一子目录(向上首个已建治理根 = beta)
    old_way = resolve_active_project(rig["home"])
    _show(f"[件①·对照] 原缺省 resolve_active_project(home) = {old_way!r}(home 级活动项目; 本卡前 set-paths 即落此项目)")
    assert old_way == "alpha"
    a_before, b_before = _md5(rig["alpha"] / "project.json"), _md5(rig["beta"] / "project.json")
    argv = ("project", "set-paths", "--home-root", str(rig["home"]), "--key", "finalize_mode", "--value", "external")
    rc, out, err = _cli(*argv)
    _show(f"[件①·beta 根下不写项目名 预演] cwd={sub} rc={rc}\n{out}{err}")
    assert rc == 0, err
    assert _first_line(out) == (f"目标项目 beta(来源: 当前目录所在治理根 {rig['beta'].resolve()}) · project.json "
                                f"{(rig['beta'] / 'project.json').resolve()}")
    assert f"project set-paths beta: {PREVIEW}" in out
    assert _md5(rig["alpha"] / "project.json") == a_before and _md5(rig["beta"] / "project.json") == b_before
    rc, out, err = _cli(*argv, "--confirm")
    _show(f"[件①·beta 根下不写项目名 --confirm] rc={rc}\n{out}{err}")
    assert rc == 0 and "project set-paths beta: 已写入" in out
    assert json.loads((rig["beta"] / "project.json").read_text(encoding="utf-8"))["paths"]["finalize_mode"] == "external"
    assert _md5(rig["alpha"] / "project.json") == a_before, "活动项目 alpha 的 project.json 一字未动"


def test_item1_unresolvable_target_is_refused_never_falls_back_to_active_project(rig, monkeypatch):
    monkeypatch.chdir(rig["outside"])
    pjs = [rig["alpha"] / "project.json", rig["beta"] / "project.json"]
    before = [_md5(p) for p in pjs]
    cases = [
        ("project", "set-paths", "--home-root", str(rig["home"]), "--key", "finalize_mode", "--value", "external", "--confirm"),
        ("project", "set-meta", "--home-root", str(rig["home"]), "--phase", "试运行", "--confirm"),
        ("project", "freeze-legacy", "--queue", "claimed", "--reason", "r", "--actor", "a.b.c", "--confirm"),
        ("project", "dispatch-mode", "set", "--mode", "manual"),
        ("project", "export"),
        ("project", "set-paths", "--workspace-root", str(rig["outside"]), "--key", "finalize_mode", "--value", "external"),
    ]
    for argv in cases:
        rc, out, err = _cli(*argv)
        _show(f"[件①·解析不出 = 拒] cwd={rig['outside']} argv={' '.join(argv[1:3])}… rc={rc}\n{out}{err}")
        assert rc == 1 and "PROJECT_TARGET_UNRESOLVED" in err and "不回落 home 级活动项目" in err, (argv, out, err)
        assert "alpha" not in out + err, "拒因不得带出活动项目"
        assert [_md5(p) for p in pjs] == before, "零写入"


def test_item1_explicit_name_conflicting_with_enclosing_root_is_refused_listing_both(rig, monkeypatch):
    monkeypatch.chdir(rig["beta"])
    pjs = [rig["alpha"] / "project.json", rig["beta"] / "project.json"]
    before = [_md5(p) for p in pjs]
    cases = [
        ("project", "set-paths", "alpha", "--home-root", str(rig["home"]), "--key", "finalize_mode", "--value", "external"),
        ("project", "set-meta", "alpha", "--home-root", str(rig["home"]), "--note", "x"),
        ("project", "set-repos", "alpha", "--home-root", str(rig["home"]), "--repo", f"app={rig['tmp']}/app"),
        ("project", "set-repo", "alpha", "--home-root", str(rig["home"]), "--code-repo", str(rig["tmp"] / "app")),
        ("project", "set-workstation", "alpha", "--home-root", str(rig["home"]), "--instance", "exec.alpha.mac",
         "--gate-ssh-alias", "gate-dev", "--material-access", "经 ssh 读写"),
        # --workspace-root 与显式名冲突同理
        ("project", "set-paths", "beta", "--home-root", str(rig["home"]), "--workspace-root", str(rig["alpha"]),
         "--key", "finalize_mode", "--value", "external"),
    ]
    for argv in cases:
        rc, out, err = _cli(*argv)
        _show(f"[件①·显式名与所在根冲突 = 拒] argv={' '.join(argv[1:3])} rc={rc}\n{out}{err}")
        assert rc == 1 and "PROJECT_TARGET_CONFLICT" in err, (argv, out, err)
        named, other = (argv[2], "beta" if argv[2] == "alpha" else "alpha")
        assert f"显式项目名 {named!r}" in err and f"project.json#project={other!r}" in err, "两者都列出"
        assert str((rig["home"] / named).resolve()) in err and str((rig["home"] / other).resolve()) in err
        assert [_md5(p) for p in pjs] == before, "零写入"


def test_item1_explicit_name_and_workspace_root_paths(rig, monkeypatch):
    # 不在任何治理根: 显式项目名照旧生效(来源 = 显式项目名)
    monkeypatch.chdir(rig["outside"])
    rc, out, err = _cli("project", "set-paths", "beta", "--home-root", str(rig["home"]), "--key", "finalize_mode", "--value", "external")
    _show(f"[件①·根外显式名] rc={rc}\n{out}{err}")
    assert rc == 0 and _first_line(out).startswith("目标项目 beta(来源: 显式项目名) · project.json ")
    # 显式名与所在根一致: 照常(来源注明一致)
    monkeypatch.chdir(rig["beta"])
    rc, out, err = _cli("project", "set-paths", "beta", "--home-root", str(rig["home"]), "--key", "finalize_mode", "--value", "external")
    assert rc == 0 and _first_line(out).startswith("目标项目 beta(来源: 显式项目名(与所在治理根声明一致)) · project.json "), out + err
    # --workspace-root 指定治理根(不写项目名): 目标 = 该根声明
    monkeypatch.chdir(rig["outside"])
    rc, out, err = _cli("project", "set-paths", "--workspace-root", str(rig["alpha"]), "--key", "finalize_mode", "--value", "external")
    _show(f"[件①·--workspace-root alpha] rc={rc}\n{out}{err}")
    assert rc == 0 and _first_line(out).startswith(f"目标项目 alpha(来源: --workspace-root 指定治理根 {rig['alpha'].resolve()})")
    # 全局 --workspace-root 同为显式治理根: 与显式项目名冲突 = 拒
    before = _md5(rig["beta"] / "project.json")
    rc, out, err = _cli("--workspace-root", str(rig["alpha"]), "project", "set-repos", "beta", "--home-root", str(rig["home"]),
                        "--repo", f"app={rig['tmp']}/app", "--confirm")
    _show(f"[件①·全局 --workspace-root alpha + 显式 beta] rc={rc}\n{out}{err}")
    assert rc == 1 and "PROJECT_TARGET_CONFLICT" in err and _md5(rig["beta"] / "project.json") == before


def test_item1_freeze_legacy_and_dispatch_mode_set_use_enclosing_root(rig, monkeypatch):
    monkeypatch.chdir(rig["beta"])
    a_before = _md5(rig["alpha"] / "project.json")
    rc, out, err = _cli("project", "dispatch-mode", "set", "--mode", "manual")
    _show(f"[件①·dispatch-mode set 在 beta 根] rc={rc}\n{out}{err}")
    assert rc == 0 and json.loads((rig["beta"] / "project.json").read_text(encoding="utf-8"))["dispatch_mode"] == "manual"
    rc, out, err = _cli("project", "freeze-legacy", "--queue", "claimed", "--reason", "r", "--actor", "a.b.c")
    _show(f"[件①·freeze-legacy 干跑在 beta 根] rc={rc}\n{out}{err}")
    assert rc == 0 and f"治理根 {rig['beta'].resolve()}" in out
    assert _md5(rig["alpha"] / "project.json") == a_before


def test_item1_resolver_is_single_implementation_and_cli_has_no_active_project_write_path():
    src = (REPO_ROOT / "tools/aipos_cli/aipos_cli.py").read_text(encoding="utf-8")
    assert "resolve_active_project" not in src, "CLI 写路径不再引用 home 级活动项目解析"
    tree = ast.parse(src)
    helper = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "_project_write_target")
    assert "resolve_project_write_target" in ast.unparse(helper)
    calls = [n for n in ast.walk(tree) if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
             and n.func.id == "_project_write_target"]
    _show(f"[件①·静态] _project_write_target 调用点 {len(calls)} 处(set-paths/set-meta/set-repos/set-repo/set-workstation/"
          "set-execution/freeze-legacy/dispatch-mode set/export 隐式目标)")
    assert len(calls) == 9  # AIPOS-F143 件②: + set-execution(同一目标解析)
    wc_src = (REPO_ROOT / "tools/aipos_cli/workspace_config.py").read_text(encoding="utf-8")
    fn = next(n for n in ast.parse(wc_src).body if isinstance(n, ast.FunctionDef) and n.name == "resolve_project_write_target")
    names = {n.id for n in ast.walk(fn) if isinstance(n, ast.Name)} | {n.attr for n in ast.walk(fn) if isinstance(n, ast.Attribute)}
    assert not names & {"resolve_active_project", "global_config_active_project", "active_project_from_config",
                        "load_global_config", "environ", "resolve_workspace_root"}, names


# ===========================================================================
# 件② 输出首行: 六个两阶段写命令同一包装(AIPOS-F143 + set-execution), 首行 = 目标项目 + 来源 + project.json 绝对路径
# ===========================================================================

def test_item2_first_line_names_target_and_absolute_project_json_for_every_writer(rig, monkeypatch):
    _declare_meta_keys(monkeypatch)
    monkeypatch.chdir(rig["outside"])
    tmp = rig["tmp"]
    pj = (rig["beta"] / "project.json").resolve()
    argvs = {
        "set-paths": ["--key", "finalize_mode", "--value", "external"],
        "set-repo": ["--code-repo", str(tmp / "repo-main")],
        "set-repos": ["--repo", f"app={tmp}/app"],
        "set-workstation": ["--instance", "exec.beta.mac", "--gate-ssh-alias", "gate-dev", "--material-access", "经 ssh gate-dev 读写"],
        "set-meta": ["--phase", "试运行"],
        # AIPOS-F143 件②: set-execution 写前核实例已接入——先在 beta 接入登记两条 land 事件(子 agent 执行者 + pi 审计工位)
        "set-execution": ["--subagent-executor", "exec.beta.adv", "--auditor", "audit.beta.ws"],
    }
    from tools.aipos_cli.enrollment import _trail_line, _write_trail_line, enrollment_trail_path, subagent_land_detail

    for inst, role, reason in (("exec.beta.adv", "executor", subagent_land_detail(host="adv", harness="codex", mode="subagent", files=[])),
                               ("audit.beta.ws", "auditor", f"host=fixture workstation={tmp / 'ws-audit'} files=[]")):
        _write_trail_line(enrollment_trail_path(rig["beta"]), _trail_line(action="land", code_id="enroll_fixture", role=role,
                                                                          instance=inst, project="beta", by="t", reason=reason))
    from tools.schema_loader import load_schema

    assert sorted(load_schema("verbs")["two_phase_protocol"]["project_json_writers"]["commands"]) == sorted(argvs)
    for command, extra in argvs.items():
        for flags in ([], ["--confirm"]):
            rc, out, err = _cli("project", command, "beta", "--home-root", str(rig["home"]), *extra, *flags)
            _show(f"[件②·{command} {' '.join(flags) or '(预演)'}] 首行: {_first_line(out)}")
            assert rc == 0, out + err
            assert _first_line(out) == f"目标项目 beta(来源: 显式项目名) · project.json {pj}", out
            assert out.splitlines()[1].startswith(f"project {command} beta: ")


# ===========================================================================
# 件③ set-meta: 两阶段, update_project_json 唯一写路径, 键须 config.schema project_json 已声明
# ===========================================================================

def test_item3_set_meta_preview_confirm_unchanged(rig, monkeypatch):
    _declare_meta_keys(monkeypatch)
    monkeypatch.chdir(rig["beta"])
    pj = rig["beta"] / "project.json"
    before = _md5(pj)
    argv = ("project", "set-meta", "--home-root", str(rig["home"]), "--phase", "接入试运行", "--note", "已接 Pi 执行体")
    rc, out, err = _cli(*argv)
    _show(f"[件③·set-meta 预演] rc={rc} md5 前={before} 后={_md5(pj)}\n{out}{err}")
    assert rc == 0 and f"project set-meta beta: {PREVIEW}" in out and _md5(pj) == before
    assert "phase: None → '接入试运行'" in out and "(当前)" in out and "(写入后)" in out
    rc, out, err = _cli(*argv, "--confirm")
    _show(f"[件③·set-meta --confirm] rc={rc}\n{out}{err}")
    assert rc == 0 and "project set-meta beta: 已写入" in out
    data = json.loads(pj.read_text(encoding="utf-8"))
    assert data["phase"] == "接入试运行" and data["note"] == "已接 Pi 执行体" and data["project"] == "beta"
    assert data["registered_by"] == "owner.beta", "其余键原样保留"
    written = _md5(pj)
    rc, out, _err = _cli(*argv, "--confirm")
    assert rc == 0 and "project set-meta beta: 无改动" in out and _md5(pj) == written
    # 只改一个键: 另一个原样
    rc, out, _err = _cli("project", "set-meta", "--home-root", str(rig["home"]), "--note", "暂停", "--confirm")
    data = json.loads(pj.read_text(encoding="utf-8"))
    assert rc == 0 and data["phase"] == "接入试运行" and data["note"] == "暂停"


def test_item3_set_meta_refusals_zero_write(rig, monkeypatch):
    monkeypatch.chdir(rig["beta"])
    pj = rig["beta"] / "project.json"
    _declare_meta_keys(monkeypatch, keys=("phase",))  # note 未声明
    cases = [
        (("--note", "x"), "META_KEY_UNDECLARED"),
        (("--phase", "x", "--note", "y"), "META_KEY_UNDECLARED"),
        (("--phase", "两行\n文本"), "META_VALUE_INVALID"),
        (("--phase", "   "), "META_VALUE_INVALID"),
        ((), "META_NOTHING_TO_SET"),
    ]
    for extra, code in cases:
        for flags in ([], ["--confirm"]):
            before = _md5(pj)
            rc, out, err = _cli("project", "set-meta", "--home-root", str(rig["home"]), *extra, *flags)
            _show(f"[件③·set-meta 拒 {code} {' '.join(flags) or '(预演)'}] rc={rc}\n{out}{err}")
            assert rc == 1 and code in err and "project.json 未改动" in err and _md5(pj) == before, (extra, out, err)


def test_item3_set_meta_writes_via_single_write_path_and_declaration():
    from tools.aipos_cli.workspace_config import project_meta_declaration

    decl = project_meta_declaration()
    assert decl["keys"] == {"phase": "--phase", "note": "--note"}
    wc_src = (REPO_ROOT / "tools/aipos_cli/workspace_config.py").read_text(encoding="utf-8")
    fn = next(n for n in ast.parse(wc_src).body if isinstance(n, ast.FunctionDef) and n.name == "set_project_meta")
    calls = {c.func.id for c in ast.walk(fn) if isinstance(c, ast.Call) and isinstance(c.func, ast.Name)}
    assert {"update_project_json", "_coerce_paths_value"} <= calls and "write_text" not in ast.unparse(fn)


# ===========================================================================
# 件④ 接入向导: 第 1 步给出 set-meta(可写键 = 唯一判定 declared_meta_keys); 未声明 = 只给说明行, 不给会被拒的命令
# ===========================================================================

def test_item4_onboarding_guide_set_meta_lines_follow_declaration(tmp_path, monkeypatch):
    import shlex

    from tools.aipos_cli.aipos_cli import build_parser
    from tools.aipos_cli.onboarding import generate_onboarding_guide

    def step1() -> str:
        guide = generate_onboarding_guide("probe_proj", home_root=str(tmp_path), code_repo=str(tmp_path / "repo"), host_segment="h")
        return guide["legacy_onboarding"][0]["command"]

    _declare_meta_keys(monkeypatch, keys=())
    text = step1()
    _show(f"[件④·向导第 1 步(键未声明)]\n{text}")
    assert "lybra project set-meta " not in text and "META_KEY_UNDECLARED" in text
    _declare_meta_keys(monkeypatch)
    text = step1()
    _show(f"[件④·向导第 1 步(键已声明)]\n{text}")
    lines = [line for line in text.split("\n") if line.startswith("lybra project set-meta ")]
    assert len(lines) == 2
    parsed = [build_parser().parse_args(shlex.split(line.replace("<META_TEXT>", "x"))[1:]) for line in lines]
    assert [a.project_command for a in parsed] == ["set-meta", "set-meta"] and not parsed[0].confirm and parsed[1].confirm
    assert parsed[1].meta_phase == "x" and parsed[1].meta_note == "x" and parsed[1].name == "probe_proj"


# ===========================================================================
# 续做(lane 补入 schema/config.schema.json): 真实声明下 set-meta 对 phase/note 预演 / --confirm 可用(不 monkeypatch 声明)
# ===========================================================================

def test_item3_set_meta_real_declaration_phase_note_preview_and_confirm(rig, monkeypatch):
    from tools.aipos_cli.workspace_config import declared_meta_keys
    from tools.schema_loader import load_schema

    declared = load_schema("config")["configuration_sources"]["project_json"]["schema"]
    _show("[续做·真实声明] config.schema project_json.schema phase=" + json.dumps(declared["phase"], ensure_ascii=False)
          + " note=" + json.dumps(declared["note"], ensure_ascii=False))
    assert declared["phase"]["type"] == "string" and declared["note"]["type"] == "string"
    assert declared["phase"]["required"] is False and declared["note"]["required"] is False
    assert sorted(declared_meta_keys()) == ["note", "phase"]
    monkeypatch.chdir(rig["beta"])
    pj = rig["beta"] / "project.json"
    a_before, before = _md5(rig["alpha"] / "project.json"), _md5(pj)
    argv = ("project", "set-meta", "--home-root", str(rig["home"]), "--phase", "接入试运行", "--note", "已接执行体")
    rc, out, err = _cli(*argv)
    _show(f"[续做·set-meta 预演(真实声明)] rc={rc} md5 前={before} 后={_md5(pj)}\n{out}{err}")
    assert rc == 0 and f"project set-meta beta: {PREVIEW}" in out and _md5(pj) == before
    rc, out, err = _cli(*argv, "--confirm")
    _show(f"[续做·set-meta --confirm(真实声明)] rc={rc}\n{out}{err}")
    assert rc == 0 and "project set-meta beta: 已写入" in out
    data = json.loads(pj.read_text(encoding="utf-8"))
    assert data["phase"] == "接入试运行" and data["note"] == "已接执行体" and data["registered_by"] == "owner.beta"
    assert _md5(rig["alpha"] / "project.json") == a_before
    rc, out, _err = _cli(*argv, "--confirm")
    assert rc == 0 and "project set-meta beta: 无改动" in out


def test_item4_onboarding_guide_real_declaration_gives_set_meta(tmp_path):
    from tools.aipos_cli.onboarding import generate_onboarding_guide

    guide = generate_onboarding_guide("probe_proj", home_root=str(tmp_path), code_repo=str(tmp_path / "repo"), host_segment="h")
    lines = [line for line in guide["legacy_onboarding"][0]["command"].split("\n") if line.startswith("lybra project set-meta ")]
    _show("[续做·向导第 1 步 set-meta 行(真实声明)]\n" + "\n".join(lines))
    assert len(lines) == 2 and lines[0].endswith("--dry-run") and lines[1].endswith("--confirm")
    assert "--phase <META_TEXT>" in lines[0] and "--note <META_TEXT>" in lines[0]
