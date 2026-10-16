"""AIPOS-F169: pi 模型声明与切换。

件① 声明与缺省: project.json execution.pi_models(config.schema 声明, 缺 = 行为不变)→ loop 唯一拉起口 plan_launch 按
     enums.schema harness pi launch.model.argv 追加 --model <provider>/<id>; by_role 覆盖优先于 default。
件② 切换命令: `lybra project set-model [--role] --model --owner-ref`(set-execution 族同一两阶段包装 + F159 写后落账); 预演核
     `pi --list-models`(harness_launch.harness_model_catalog 唯一实现)、列影响工位与生效时机; 不在清单 = 拒并列相近可用模型。
件③ 可见: check-workstation 显示将用模型与来源; 运行记录 launches[].model; loop status 拉起行显示模型。

靶场: F90/F95 拉起靶场(临时治理根 + 假门 + 临时工位); 假 pi = tests/fake_pi.py(PATH 里名为 pi 的薄壳, 只按夹具清单打印、拉起即
exec F95 假 harness), 不调真实 pi、不发网络请求。拉起用产品真实 enums.schema pi 模板(不替换模板), 命令行原文由假 pi 落盘比对。
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import test_aipos_f90_loop_one_stage as f90  # noqa: E402  — loop 靶场唯一来源
import test_aipos_f95_harness_launch as f95  # noqa: E402  — 拉起靶场唯一来源
from test_aipos_f73d_loop_driver import init_governance_repo  # noqa: E402
from test_aipos_f78_engine_agnostic import AUDITOR, DRIVER, EXEC  # noqa: E402
from test_aipos_f90_loop_one_stage import rig  # noqa: E402,F401  — pytest fixture
from test_aipos_f95_harness_launch import lrig  # noqa: E402,F401  — pytest fixture(拉起靶场 + 收尾无残留断言)
from test_aipos_f131_loop_run_status import _cli  # noqa: E402
from tools.aipos_cli import next_resolver as nr  # noqa: E402
from tools.aipos_cli.autonomy_policy import launchable_harnesses as REAL_LAUNCHABLE  # noqa: E402  — 夹具替换前的产品读取口

FAKE_PI = REPO_ROOT / "tests" / "fake_pi.py"
CATALOG = ("kiwiai/qwen3.7-plus", "kiwiai/qwen3.6-plus", "kiwiai/claude-sonnet-5", "kiwiai/glm-5.2", "kiwiai/deepseek-v4-pro")
SUBAGENT = "exec.sub.lybra.test"


def _show(text: str) -> None:
    print(text, flush=True)


def install_fake_pi(bin_dir: Path, mp: pytest.MonkeyPatch, models: tuple[str, ...] = CATALOG) -> Path:
    """PATH 前置一个名为 pi 的薄壳 → tests/fake_pi.py(清单 = models)。"""
    bin_dir.mkdir(parents=True, exist_ok=True)
    shim = bin_dir / "pi"
    shim.write_text(f'#!/bin/sh\nexec "{sys.executable}" "{FAKE_PI}" "$@"\n', encoding="utf-8")
    shim.chmod(0o755)
    mp.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ.get('PATH', '')}")
    mp.setenv("FAKE_PI_MODELS", ",".join(models))
    mp.delenv("FAKE_PI_LIST_EXIT", raising=False)
    mp.delenv("FAKE_PI_LIST_NOHEADER", raising=False)
    return shim


def _project(gov: Path) -> dict:
    return json.loads((gov / "project.json").read_text(encoding="utf-8"))


def _write_project(gov: Path, data: dict) -> None:
    (gov / "project.json").write_text(json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _declare_execution(gov: Path, pi_models: dict | None = None) -> None:
    """靶场执行方式声明(pi 执行工位 = EXEC, 审计 = AUDITOR); pi_models 给 = 直接写入声明(模拟已切换), None = 未声明。"""
    data = _project(gov)
    data["execution"] = {"default_mode": "subagent", "subagent_executor": SUBAGENT, "pi_executor": EXEC,
                         "pi_allowed_task_modes": ["code"], "auditor": AUDITOR}
    if pi_models is not None:
        data["execution"]["pi_models"] = pi_models
    _write_project(gov, data)


def _md5(path: Path) -> str:
    return hashlib.md5(path.read_bytes()).hexdigest()


def _real_pi_template(mp: pytest.MonkeyPatch) -> None:
    """撤回 F95 靶场替换的拉起模板: 用产品真实 enums.schema pi 模板(argv[0] = pi → PATH 里的假 pi)。"""
    from tools.aipos_cli import autonomy_policy

    mp.setattr(autonomy_policy, "launchable_harnesses", REAL_LAUNCHABLE)


def _run(lrig, card: str):
    import io

    from tools.aipos_cli.loop_driver import run_loop

    out = io.StringIO()
    res = run_loop(card, lrig.gov, actor=DRIVER, policy_id=f95.POLICY_LAUNCH, out=out, interval=0.05, max_wait=60, max_steps=30)
    return res, out.getvalue()


def _argv(lrig, card: str) -> list[str]:
    return json.loads((lrig.log / f"{card}.argv.json").read_text(encoding="utf-8"))


def _run_record_launches(gov: Path, card: str) -> list[dict]:
    out: list[dict] = []
    for path in sorted((gov / "5_tasks" / "records" / "loop_runs" / card).glob("looprun_*.md")):
        out += list(nr._read_frontmatter(path).get("launches") or [])
    return out


# ===========================================================================
# 件① + 件③: 未声明 / 项目缺省 / 角色覆盖 三态的拉起命令行原文; 运行记录 launches[].model; loop status 拉起行
# ===========================================================================

def test_item1_launch_argv_undeclared_default_role_override_and_item3_record_status(lrig, monkeypatch):
    _real_pi_template(monkeypatch)
    install_fake_pi(lrig.home / "fakebin", monkeypatch)
    f95._land(lrig.gov, EXEC, "executor", lrig.ws_exec)
    f95._land(lrig.gov, AUDITOR, "auditor", lrig.ws_audit)
    init_governance_repo(lrig.gov)
    cases = (
        ("PROBE-F169-NONE", None, (None, None)),
        ("PROBE-F169-DEFAULT", {"default": "kiwiai/qwen3.7-plus", "owner_ref": "Owner 10-16: 缺省 qwen3.7-plus"},
         ("kiwiai/qwen3.7-plus", "kiwiai/qwen3.7-plus")),
        ("PROBE-F169-ROLE", {"default": "kiwiai/qwen3.7-plus", "by_role": {"auditor": "kiwiai/claude-sonnet-5"},
                             "owner_ref": "Owner 10-16: 审计换 sonnet-5"},
         ("kiwiai/qwen3.7-plus", "kiwiai/claude-sonnet-5")),
    )
    for card, pi_models, (want_exec, want_audit) in cases:
        if pi_models is None:
            data = _project(lrig.gov)
            data.pop("execution", None)
            _write_project(lrig.gov, data)
        else:
            _declare_execution(lrig.gov, pi_models)
        f90._card(lrig.gov, card, "pending", harness="pi")
        res, text = _run(lrig, card)
        _show(f"---- 件① {card} lybra loop 原文(节选拉起行) ----\n"
              + "\n".join(ln for ln in text.splitlines() if "launch:" in ln or "exit" in ln.split("—")[0][-12:]))
        assert res.exit_code == 0 and res.outcome == "completed", text
        for target, want, role in ((card, want_exec, "executor"), (f"{card}R", want_audit, "auditor")):
            argv = _argv(lrig, target)
            _show(f"[件①] {target}({role}) 拉起命令行原文: {argv}")
            base = ["pi", "-p", "<kickoff>", "--mode", "json"]
            assert argv == (base if want is None else [*base, "--model", want]), argv
        # 件③ 运行记录 launches[].model(实际传入值 + 来源 + 角色)
        launches = _run_record_launches(lrig.gov, card)
        _show(f"[件③] {card} 运行记录 launches[].model: {[l.get('model') for l in launches]}")
        assert [l["model"]["model"] for l in launches] == [want_exec, want_audit]
        assert [l["model"]["role"] for l in launches] == ["executor", "auditor"]
        if pi_models is None:
            assert all(l["model"]["source"].startswith("pi 缺省") for l in launches)
            assert "模型 pi 缺省(未声明 execution.pi_models, 不传 --model; 由 pi 自身配置定)" in text
        elif card.endswith("ROLE"):
            assert [l["model"]["source"] for l in launches] == ["execution.pi_models.default", "execution.pi_models.by_role.auditor"]
            assert "模型 kiwiai/claude-sonnet-5(execution.pi_models.by_role.auditor)" in text
        events = [e for c in (card, f"{card}R") for e in f95._events(lrig.gov, c) if "harness_launch" in e]
        _show(f"[件③] {card} session Events(拉起): " + " | ".join(e.split("privilege_guard=")[-1] for e in events))
        assert len(events) == 2 and all("; model=" in e for e in events)
        # 件③ loop status 拉起行显示模型(最后一次拉起 = 审计卡)
        rc, out, err = _cli(["loop", "status", "--task-id", card, "--workspace-root", str(lrig.gov)])
        line = next(ln for ln in out.splitlines() if ln.strip().startswith("拉起 "))
        _show(f"[件③] loop status --task-id {card} 拉起行原文: {line.strip()}")
        assert rc == 0 and f"拉起 pi {card}R 模型 " in line
        assert (want_audit or "pi 缺省") in line


def test_item1_invalid_declaration_refuses_launch_fail_closed(lrig, monkeypatch):
    from tools.aipos_cli import loop_driver
    from tools.aipos_cli.autonomy_policy import load_policy

    _real_pi_template(monkeypatch)
    install_fake_pi(lrig.home / "fakebin", monkeypatch)
    f95._land(lrig.gov, EXEC, "executor", lrig.ws_exec)
    _declare_execution(lrig.gov, {"default": "qwen3.7-plus", "owner_ref": "x"})  # 缺 provider 段 = 形不合
    card = "PROBE-F169-BAD"
    f90._card(lrig.gov, card, "pending", harness="pi")
    plan = loop_driver.plan_launch(lrig.gov, card, policy=load_policy(lrig.gov, f95.POLICY_LAUNCH), no_launch=False,
                                   decl=loop_driver.launch_declaration(loop_driver.load_loop_contract()), already_launched=set())
    _show(f"[件① 声明不合] refusal={plan.refusal}")
    assert plan.refusal.startswith("pi 模型声明不可用(fail-closed, 不拉起): EXECUTION_DECLARATION_INVALID: ") and not plan.argv
    # 模板缺 model 声明而本项目声明了模型 = 不拉起(不静默丢掉 Owner 选的模型)
    from tools.aipos_cli import autonomy_policy

    _declare_execution(lrig.gov, {"default": "kiwiai/qwen3.7-plus", "owner_ref": "x"})
    monkeypatch.setattr(autonomy_policy, "launchable_harnesses", lambda: {"pi": {"argv": ["pi", "-p", "{kickoff}"], "events": "pi-json"}})
    plan = loop_driver.plan_launch(lrig.gov, card, policy=load_policy(lrig.gov, f95.POLICY_LAUNCH), no_launch=False,
                                   decl=loop_driver.launch_declaration(loop_driver.load_loop_contract()), already_launched=set())
    _show(f"[件① 模板缺 model 声明] refusal={plan.refusal}")
    assert "launch.model 未声明齐" in plan.refusal and not plan.argv


# ===========================================================================
# 件②: set-model 预演 / 拒(不在清单列近似、清单取不到、未声明 execution、缺 owner-ref)/ 确认写入 + 落账 / set-execution 保留
# ===========================================================================

def test_item2_set_model_preview_refusals_confirm_and_landing(lrig, monkeypatch):
    gov = lrig.gov
    install_fake_pi(lrig.home / "fakebin", monkeypatch)
    _real_pi_template(monkeypatch)
    f95._land(gov, EXEC, "executor", lrig.ws_exec)
    f95._land(gov, AUDITOR, "auditor", lrig.ws_audit)
    init_governance_repo(gov)
    base = ["project", "set-model", gov.name, "--home-root", str(gov.parent)]
    pj = gov / "project.json"
    # 未声明 execution 段 = 拒(模型声明只在 execution 段一处)
    before = _md5(pj)
    rc, out, err = _cli([*base, "--model", "kiwiai/qwen3.7-plus", "--owner-ref", "Owner 10-16 缺省"])
    _show(f"---- 件② 未声明 execution rc={rc} ----\n{out}{err}")
    assert rc == 1 and "Error: PI_MODEL_EXECUTION_UNDECLARED(project.json 未改动)" in err and _md5(pj) == before
    _declare_execution(gov)
    subprocess.run(["git", "-C", str(gov), "add", "project.json"], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(gov), "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "-m", "fixture: execution"],
                   check=True, capture_output=True)
    before = _md5(pj)
    # 预演(缺省): 核清单、列影响工位与生效时机, 零写入
    rc, out, err = _cli([*base, "--model", "kiwiai/claude-sonnet-5", "--owner-ref", "Owner 10-16: 后面 pi 执行体换 sonnet-5"])
    _show(f"---- 件② set-model 预演 rc={rc} ----\n{out}{err}")
    assert rc == 0 and _md5(pj) == before
    assert f"project set-model {gov.name}: 预览(未写; 加 --confirm 写入)" in out
    assert "模型可用: kiwiai/claude-sonnet-5 在本机 pi 已配置模型中(`pi --list-models` 列出 5 个已配置模型)" in out
    assert "execution.pi_models.default = kiwiai/claude-sonnet-5  owner_ref = Owner 10-16: 后面 pi 执行体换 sonnet-5" in out
    assert re.search(rf"影响工位 \[executor\] {re.escape(EXEC)} @ .*:{re.escape(str(lrig.ws_exec))} transport=local: 下次拉起 "
                     r"pi 缺省\(未声明 execution.pi_models, 不传 --model; 由 pi 自身配置定\) → kiwiai/claude-sonnet-5\(execution.pi_models.default\)", out), out
    assert f"影响工位 [auditor] {AUDITOR} @ " in out
    assert "生效: 下次 loop 拉起该角色 pi 时生效; 在途已拉起的进程不受影响" in out
    assert '+    "pi_models": {' in out
    # 不在清单 = 拒并列相近可用模型
    rc, out, err = _cli([*base, "--model", "kiwiai/qwen3.7-pro", "--owner-ref", "Owner 10-16"])
    _show(f"---- 件② 不存在的模型 rc={rc} ----\n{out}{err}")
    assert rc == 1 and "Error: PI_MODEL_NOT_CONFIGURED(project.json 未改动)" in err
    assert "kiwiai/qwen3.7-pro 不在本机 pi 已配置模型中" in err and "相近可用模型: kiwiai/qwen3.7-plus" in err and _md5(pj) == before
    # 清单取不到(pi 退出非 0 / 输出无表头 / 不在 PATH)= 核不了不写
    for env, value in (("FAKE_PI_LIST_EXIT", "3"), ("FAKE_PI_LIST_NOHEADER", "1")):
        monkeypatch.setenv(env, value)
        rc, out, err = _cli([*base, "--model", "kiwiai/qwen3.7-plus", "--owner-ref", "Owner 10-16"])
        _show(f"---- 件② 清单取不到({env}={value}) rc={rc} ----\n{err}")
        assert rc == 1 and "Error: PI_MODEL_CATALOG_UNAVAILABLE(project.json 未改动)" in err and _md5(pj) == before
        monkeypatch.delenv(env)
    # owner-ref 空 / 含凭据字样 = 拒
    rc, out, err = _cli([*base, "--model", "kiwiai/qwen3.7-plus", "--owner-ref", " "])
    assert rc == 1 and "PI_MODEL_OWNER_REF_REQUIRED" in err
    rc, out, err = _cli([*base, "--model", "kiwiai/qwen3.7-plus", "--owner-ref", "token=abc"])
    _show(f"[件②] owner-ref 含凭据字样 rc={rc}: {err.strip()}")
    assert rc == 1 and "EXECUTION_DECLARATION_INVALID" in err and "owner_ref" in err and _md5(pj) == before
    # 确认: 写 project.json 并经 F159 同一提交口落账
    rc, out, err = _cli([*base, "--model", "kiwiai/claude-sonnet-5", "--owner-ref", "Owner 10-16: 后面 pi 执行体换 sonnet-5", "--confirm",
                         "--actor", DRIVER])
    _show(f"---- 件② set-model --confirm rc={rc} ----\n{out}{err}")
    assert rc == 0 and f"project set-model {gov.name}: 已写入" in out and "落账" in out
    assert _project(gov)["execution"]["pi_models"] == {"default": "kiwiai/claude-sonnet-5", "by_role": {},
                                                      "owner_ref": "Owner 10-16: 后面 pi 执行体换 sonnet-5"}
    log = subprocess.run(["git", "-C", str(gov), "log", "-1", "--format=%s%n%b", "--name-only"], capture_output=True, text=True).stdout
    _show(f"[件②] 落账提交原文:\n{log}")
    assert f"chore(governance): 项目配置落账 set-model {gov.name}" in log and "Command: lybra project set-model --confirm" in log
    assert "project.json" in log
    assert subprocess.run(["git", "-C", str(gov), "status", "--porcelain", "project.json"], capture_output=True, text=True).stdout == ""
    # 角色覆盖: 只改 by_role.auditor, default 不动
    rc, out, err = _cli([*base, "--role", "auditor", "--model", "kiwiai/qwen3.7-plus", "--owner-ref", "Owner 10-16: 审计保持 qwen", "--confirm",
                         "--actor", DRIVER])
    _show(f"---- 件② set-model --role auditor --confirm rc={rc} ----\n{out}{err}")
    assert rc == 0 and "影响工位 [auditor]" in out and "影响工位 [executor]" not in out
    assert _project(gov)["execution"]["pi_models"] == {"default": "kiwiai/claude-sonnet-5", "by_role": {"auditor": "kiwiai/qwen3.7-plus"},
                                                      "owner_ref": "Owner 10-16: 审计保持 qwen"}
    # set-execution 整段重声明时 pi_models 原样保留(同族不互相清)
    from tools.aipos_cli import enrollment

    monkeypatch.setattr(enrollment, "execution_instance_refusals", lambda root, view: [])  # 实例接入核验由 F143 夹具覆盖, 此处只验保留
    outcome = enrollment.set_project_execution(gov, subagent_executor=SUBAGENT, auditor=AUDITOR, pi_executor=EXEC,
                                               pi_allowed_task_modes=["code", "config"], dry_run=False)
    _show(f"[件②] set-execution 重声明 diff:\n{outcome['diff']}")
    changed = [ln for ln in outcome["diff"].splitlines() if ln[:1] in "+-" and not ln.startswith(("+++", "---"))]
    assert _project(gov)["execution"]["pi_models"]["by_role"] == {"auditor": "kiwiai/qwen3.7-plus"}
    assert changed and not [ln for ln in changed if "pi_models" in ln or "kiwiai/" in ln], changed


# ===========================================================================
# 件③: check-workstation 显示将用模型与来源; 声明的模型不在本机清单 = ✗
# ===========================================================================

def test_item3_check_workstation_shows_model_and_source(lrig, monkeypatch):
    gov = lrig.gov
    install_fake_pi(lrig.home / "fakebin", monkeypatch)
    _real_pi_template(monkeypatch)
    f95._land(gov, AUDITOR, "auditor", lrig.ws_audit)
    f95._land(gov, EXEC, "executor", lrig.ws_exec)
    argv = lambda inst: ["project", "check-workstation", gov.name, "--home-root", str(gov.parent), "--instance", inst, "--harness", "pi"]  # noqa: E731
    for label, pi_models, inst, want_rc, want in (
        ("未声明", None, AUDITOR, 0, "拉起模型 [auditor]: pi 缺省(未声明 execution.pi_models, 不传 --model; 由 pi 自身配置定)"),
        ("角色覆盖", {"default": "kiwiai/qwen3.7-plus", "by_role": {"auditor": "kiwiai/glm-5.2"}, "owner_ref": "o"}, AUDITOR, 0,
         "拉起模型 [auditor]: kiwiai/glm-5.2(execution.pi_models.by_role.auditor) — 在本机 pi 已配置模型中(`pi --list-models` 列出 5 个已配置模型)"),
        ("项目缺省", {"default": "kiwiai/qwen3.7-plus", "by_role": {"auditor": "kiwiai/glm-5.2"}, "owner_ref": "o"}, EXEC, 0,
         "拉起模型 [executor]: kiwiai/qwen3.7-plus(execution.pi_models.default)"),
        ("不在清单", {"default": "kiwiai/gone-model", "owner_ref": "o"}, EXEC, 1, "✗ pi 模型可用: kiwiai/gone-model 不在本机 pi 已配置模型中"),
    ):
        if pi_models is None:
            data = _project(gov)
            data.pop("execution", None)
            _write_project(gov, data)
        else:
            _declare_execution(gov, pi_models)
        rc, out, err = _cli(argv(inst))
        _show(f"---- 件③ check-workstation({label}, {inst}) rc={rc} ----\n{out}{err}")
        assert rc == want_rc and want in out, out + err
        assert "fixture-ws-not-a-secret" not in out + err


# ===========================================================================
# 单元: 清单解析 / 模型标识形 / 相近模型
# ===========================================================================

def test_units_catalog_model_id_and_near_models(tmp_path, monkeypatch):
    from tools.aipos_cli import autonomy_policy
    from tools.aipos_cli.harness_launch import harness_model_catalog, model_launch_args, near_models
    from tools.aipos_cli.workspace_config import pi_model_id_problem

    monkeypatch.setattr(autonomy_policy, "launchable_harnesses", REAL_LAUNCHABLE)
    install_fake_pi(tmp_path / "bin", monkeypatch)
    cat = harness_model_catalog("pi")
    _show(f"[单元] catalog: {cat}")
    assert cat["ok"] and cat["models"] == list(CATALOG) and cat["command"] == ["pi", "--list-models"]
    monkeypatch.setenv("PATH", str(tmp_path / "empty"))
    miss = harness_model_catalog("pi")
    assert not miss["ok"] and "不在本机 PATH" in miss["detail"]
    template = REAL_LAUNCHABLE()["pi"]
    assert model_launch_args(template, "pi", "kiwiai/qwen3.7-plus") == ["--model", "kiwiai/qwen3.7-plus"]
    for bad in ("qwen3.7-plus", "kiwiai/", "/x", "-kiwiai/x", "kiwiai/a b", "kiwiai/{model}", "", None):
        assert pi_model_id_problem(bad), bad
    assert pi_model_id_problem("openrouter/anthropic/claude") == ""
    assert near_models("kiwiai/qwen3.7-pro", list(CATALOG), 5)[0] == "kiwiai/qwen3.7-plus"
    assert near_models("other/glm-5.2", list(CATALOG), 5) == ["kiwiai/glm-5.2"]


# ===========================================================================
# 防碎片化: 声明一处 / 注入一处 / 清单一个实现 / 新键进既有 schema 带缺省
# ===========================================================================

def test_single_declaration_injection_and_catalog_implementation():
    from tools.schema_loader import load_schema

    def grep(pattern: str) -> list[str]:
        return subprocess.run(["git", "grep", "-n", "-E", pattern, "--", "tools/"], cwd=REPO_ROOT, capture_output=True,
                              text=True).stdout.splitlines()

    readers = {h.split(":", 1)[0] for h in grep(r"""\.get\(["']pi_models["']\)|\[["']pi_models["']\]""")}
    _show(f"[防碎片化] pi_models 读写处: {sorted(readers)}")
    assert readers == {"tools/aipos_cli/workspace_config.py", "tools/aipos_cli/enrollment.py"}, readers
    inject = grep(r"model_launch_args\(")
    _show("[防碎片化] model_launch_args 调用:\n" + "\n".join(inject))
    assert {h.split(":", 1)[0] for h in inject if "def model_launch_args" not in h} == {"tools/aipos_cli/loop_driver.py"}
    catalog = grep(r"harness_model_catalog\(")
    assert {h.split(":", 1)[0] for h in catalog if not h.startswith("tools/aipos_cli/harness_launch.py:")} == {
        "tools/aipos_cli/enrollment.py", "tools/aipos_cli/loop_driver.py"}, catalog
    assert not grep(r"list-models[\"']"), "清单命令参数只在 enums.schema launch.model.list_args 声明"
    ex = load_schema("config")["configuration_sources"]["project_json"]["schema"]["execution"]
    pm = ex["schema"]["pi_models"]
    assert pm["required"] is False and pm["default"] is None and pm["roles"] == ["executor", "auditor"]
    assert {"PI_MODEL_NOT_CONFIGURED", "PI_MODEL_CATALOG_UNAVAILABLE", "PI_MODEL_EXECUTION_UNDECLARED", "PI_MODEL_OWNER_REF_REQUIRED"} <= set(ex["reject_codes"])
    pi = next(v for v in load_schema("enums")["enums"]["harness"]["values"] if v["value"] == "pi")
    assert pi["launch"]["argv"] == ["pi", "-p", "{kickoff}", "--mode", "json"], "拉起模板本体不变(未声明 = 行为不变)"
    assert pi["launch"]["model"]["argv"] == ["--model", "{model}"] and pi["launch"]["model"]["list_args"] == ["--list-models"]
    verbs = load_schema("verbs")
    assert "set-model" in verbs["two_phase_protocol"]["project_json_writers"]["commands"]
    assert "set-model" in verbs["verbs"]["lybra_governance_commit"]["post_write_landing"]["commands"]["project_writers"]["subcommands"]
