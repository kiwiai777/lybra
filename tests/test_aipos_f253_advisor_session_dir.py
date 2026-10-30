"""AIPOS-F253 — 同机多个顾问会话目录相撞拦截 + 顾问会话目录缺省 = 治理根(同机第二个顾问 = 治理根内声明子路径, 用户指定优先)。

件① 冲突拦截(判定唯一 enrollment.session_dir_conflicts / 拒因唯一 session_dir_refusal): 同一主机两个已接入顾问实例登记同一会话目录且有实际碰撞
    (章程落点相同且都在该目录落章程 / 都按会话目录分自动记忆)= 拒, 点名占用者与原因, 出口给换目录的命令原文。入口: roles redeclare、
    enroll 与 enroll-deliver(enroll_client.enroll_preflight 同一预检; 旧码换码后复核)、onboarding guide、sync 渲染前兜底; 只读诊断 roles list。
    他机会话(主机不同)不相撞; claude-code 与 codex 同目录(落点不同、无共享记忆)不相撞。
件② 缺省目录(唯一 enrollment.default_session_dir; 声明 distribution.schema harness_semantics.session_dir): 同机尚无顾问登记治理根 = 治理根;
    已有 = 治理根内声明子路径 .advisor-sessions/<实例>(产品建目录 + 忽略文件, 渲染物不进治理落账); 显式 --dir / --advisor-dir 优先(仍受件①拦截)。
    向导缺省改为同一规则(原 ~/<项目>), 治理根经 home 根解析梯(--home-root > LYBRA_HOME_ROOT > ~/.lybra/config.json home_root)定位, 不写死路径。

靶场: AIPOS-F235 chris 形临时治理根(自造人肉期样本: 两顾问实例槽 codex@他机)+ 临时 HOME / 临时 home 根; 不连门、不读真实凭据、
不写真实治理根(禁写 chris-huibojin)。
"""
from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "tests"))

from test_aipos_f187_kickoff_worktree_cleanup import CHRIS, _cli  # noqa: E402
from test_aipos_f235_instance_redeclare import ADV_LANTU, ADV_OTA, MAC, _log_lines, _rig, _role_doc  # noqa: E402  — chris 形靶场唯一来源

LOCAL = socket.gethostname()
SUB = ".advisor-sessions"


def _show(text: str) -> None:
    print(text, flush=True)


def _redeclare(gov: Path, instance: str, *extra: str, confirm: bool = False) -> tuple[int, str, str]:
    argv = ["roles", "redeclare", "--workspace-root", str(gov), "--instance", instance, "--actor", instance, *extra]
    return _cli(*argv, *(["--confirm"] if confirm else []))


def _lantu_at_root(gov: Path) -> None:
    """首个顾问(岚图)改登记到本机 Claude Code、不给 --dir = 缺省治理根(件②首例)。"""
    rc, out, err = _redeclare(gov, ADV_LANTU, "--harness", "claude-code", confirm=True)
    _show(f"---- 首个顾问 claude-code 不给 --dir(--confirm)rc={rc} ----\n{out}{err}")
    assert rc == 0 and f"会话目录: {gov}(缺省 = 治理根)" in out and "已写入" in out
    assert _role_doc(gov)["instances"][ADV_LANTU]["harness"] == {"kind": "claude-code", "dir": str(gov)}


def _legacy_collision(gov: Path) -> None:
    """存量相撞样本(本卡前登记入口不拦): OTA 实例槽直写 claude-code@本机:治理根(land 无 harness=, 登记读实例槽)。"""
    from tools.aipos_cli.enroll_client import write_role_file

    write_role_file(gov / ".lybra", "advisor", ADV_OTA, None, harness={"kind": "claude-code", "dir": str(gov)}, slot_by_instance=True,
                    keep_top=True)


# ===========================================================================
# 件②③: 首个顾问 = 治理根; 第二个顾问 = 治理根内声明子路径(产品建 + 忽略文件, 不进治理落账); 从子目录跑 lybra 解析到治理根
# ===========================================================================

def test_item2_redeclare_default_root_then_secondary_subpath(tmp_path, monkeypatch):
    from tools.aipos_cli.enrollment import registered_harness, session_dir_declaration

    gov = _rig(tmp_path, monkeypatch, launch=["pi", "claude-code", "codex"])
    decl = session_dir_declaration()
    _show(f"[声明] secondary_subpath={decl['secondary_subpath']} ignore_file={decl['ignore_file']}")
    _lantu_at_root(gov)

    sub = gov / SUB / ADV_OTA
    before = _log_lines(gov)
    rc, out, err = _redeclare(gov, ADV_OTA, "--harness", "claude-code")
    _show(f"---- 第二个顾问 claude-code 不给 --dir(预演)rc={rc} ----\n{out}{err}")
    assert rc == 0 and "预览" in out and f"dir: {MAC_DIR_OF(gov)} → {sub}" in out
    assert f"会话目录: {sub}(缺省 = 治理根内声明子路径(同机治理根已被 {ADV_LANTU} 登记)); 将新建 {sub}, {gov / SUB / '.gitignore'}" in out
    assert not (gov / SUB).exists() and _log_lines(gov) == before, "预演零写入(子路径不建)"

    rc, out, err = _redeclare(gov, ADV_OTA, "--harness", "claude-code", confirm=True)
    _show(f"---- 第二个顾问 --confirm rc={rc} ----\n{out}{err}")
    assert rc == 0 and "已写入" in out and "落账: verdict=PASS" in out
    reg = registered_harness(gov, ADV_OTA)
    _show(f"[第二个顾问登记] {reg['kind']}@{reg['host']}:{reg['dir']}; 忽略文件 = {(gov / SUB / '.gitignore').read_text(encoding='utf-8')!r}")
    assert (reg["kind"], reg["host"], reg["dir"]) == ("claude-code", LOCAL, str(sub)) and sub.is_dir()

    # 子路径内渲染物不进治理落账: git 看不见(忽略文件随目录走)
    (sub / ".claude" / "rules").mkdir(parents=True)
    (sub / ".claude" / "rules" / "lybra-advisor.md").write_text("rendered\n", encoding="utf-8")
    porcelain = subprocess.run(["git", "status", "--porcelain", "--untracked-files=all"], cwd=gov, capture_output=True, text=True,
                               check=True).stdout
    _show(f"[治理仓 git status]\n{porcelain or '(干净)'}")
    assert SUB not in porcelain

    # 从子目录跑 lybra(不给 --workspace-root)= 当前目录向上解析到治理根
    monkeypatch.chdir(sub)
    monkeypatch.delenv("LYBRA_HOME_ROOT", raising=False)
    rc, out, err = _cli("roles", "list")
    _show(f"---- 在 {sub} 跑 lybra roles list rc={rc} ----\n{out}{err}")
    assert f"{ADV_OTA:<44} claude-code@{LOCAL}:{sub}" in out and f"claude-code@{LOCAL}:{gov}" in out
    rc, out, err = _cli("charter", "--role", "advisor", "--instance", ADV_OTA, "--body-only")
    _show(f"---- 在子目录跑 lybra charter(章程摘头)rc={rc} ----\n{out[:400]}{err}")
    assert rc == 0 and f"治理根(只读真相): `{gov}`" in out and f"实例 `{ADV_OTA}`" in out


def MAC_DIR_OF(gov: Path) -> str:  # noqa: N802 — 人肉期样本的他机目录(F235 靶场常量)
    from test_aipos_f235_instance_redeclare import MAC_DIR

    return MAC_DIR


def test_item2_explicit_dir_wins(tmp_path, monkeypatch):
    gov = _rig(tmp_path, monkeypatch, launch=["pi", "claude-code"])
    _lantu_at_root(gov)
    mine = tmp_path / "ota-session"
    mine.mkdir()
    rc, out, err = _redeclare(gov, ADV_OTA, "--harness", "claude-code", "--dir", str(mine), confirm=True)
    _show(f"---- 用户指定 --dir 优先 rc={rc} ----\n{out}{err}")
    assert rc == 0 and f"会话目录: {mine}(用户指定)" in out and not (gov / SUB).exists()


# ===========================================================================
# 件①: 各登记入口拒同机同目录(出口给命令原文); 他机同路径不拒; codex 本机会话同理; claude-code 与 codex 同目录不撞
# ===========================================================================

def test_item1_redeclare_refuses_same_host_same_dir(tmp_path, monkeypatch):
    gov = _rig(tmp_path, monkeypatch, launch=["pi", "claude-code", "codex"])
    _lantu_at_root(gov)
    before, role_before = _log_lines(gov), _role_doc(gov)
    rc, out, err = _redeclare(gov, ADV_OTA, "--harness", "claude-code", "--dir", str(gov), confirm=True)
    _show(f"---- redeclare 同机同目录(--confirm)rc={rc} ----\n{out}{err}")
    assert rc == 1 and "REDECLARE_SESSION_DIR_TAKEN: SESSION_DIR_TAKEN" in err and f"已被同机顾问实例 {ADV_LANTU} 登记" in err
    assert "章程落点相撞 .claude/rules/lybra-advisor.md" in err and "会话记忆相撞(claude-code" in err
    sub = gov / SUB / ADV_OTA
    assert (f"lybra roles redeclare --workspace-root {gov} --instance {ADV_OTA} --actor {ADV_OTA} --harness claude-code "
            f"--host {LOCAL} --dir {sub}") in err, "出口 = 换目录的命令原文(按缺省规则给声明子路径)"
    assert _log_lines(gov) == before and _role_doc(gov) == role_before and not (gov / SUB).exists(), "拒 = 零写入"

    # 他机同路径不拒(会话在 Mac, 路径恰与治理根同字面)
    rc, out, err = _redeclare(gov, ADV_OTA, "--harness", "claude-code", "--host", MAC, "--dir", str(gov))
    _show(f"---- 他机同路径(预演)rc={rc} ----\n{out}{err}")
    assert rc == 0 and "SESSION_DIR_TAKEN" not in err

    # claude-code 与 codex 同目录(章程落点不同、无共享记忆)不撞
    rc, out, err = _redeclare(gov, ADV_OTA, "--harness", "codex", "--host", LOCAL, "--dir", str(gov))
    _show(f"---- codex 本机会话与 claude-code 同目录(预演)rc={rc} ----\n{out}{err}")
    assert rc == 0 and "SESSION_DIR_TAKEN" not in err


def test_item1_codex_local_sessions_same_dir_refused(tmp_path, monkeypatch):
    gov = _rig(tmp_path, monkeypatch, launch=["pi", "claude-code", "codex"])
    shared = tmp_path / "codex-session"
    shared.mkdir()
    rc, out, err = _redeclare(gov, ADV_LANTU, "--harness", "codex", "--host", LOCAL, "--dir", str(shared), confirm=True)
    assert rc == 0, out + err
    rc, out, err = _redeclare(gov, ADV_OTA, "--harness", "codex", "--host", LOCAL, "--dir", str(shared))
    _show(f"---- codex 本机会话同机同目录(预演)rc={rc} ----\n{out}{err}")
    assert rc == 1 and "SESSION_DIR_TAKEN" in err and "章程落点相撞 AGENTS.md" in err and "会话记忆相撞" not in err
    assert f"--harness codex --host {LOCAL} --dir {gov}" in err, "出口: 治理根未被占用 = 缺省规则给治理根"


def test_item1_enroll_and_enroll_deliver_refuse(tmp_path, monkeypatch):
    from tools.aipos_cli.enroll_client import enroll_preflight
    from tools.aipos_cli.enroll_deliver import enroll_deliver
    from tools.aipos_cli.enrollment import encode_self_contained_code

    gov = _rig(tmp_path, monkeypatch, launch=["pi", "claude-code"])
    _lantu_at_root(gov)
    sub = gov / SUB / ADV_OTA
    conn_before = (gov / ".lybra" / "connection.json").read_bytes() if (gov / ".lybra" / "connection.json").is_file() else None

    # 预检唯一实现(enroll 换码前 / enroll-deliver 签码前同用)
    with pytest.raises(RuntimeError) as exc:
        enroll_preflight(workspace_root=gov, governance_root=str(gov), declared={"role": "advisor", "instance": ADV_OTA, "role_class": "advisor"},
                         harness_kind="claude-code", harness_dir=str(gov))
    _show(f"[enroll_preflight 拒] {exc.value}")
    assert "SESSION_DIR_TAKEN" in str(exc.value) and f"(--harness-dir)换成 {sub} 后重跑" in str(exc.value)

    # enroll-deliver: 签码前即拒(门客户端不被调用), 出口 = 同一条命令换 --harness-dir 先预演
    class NoGate:
        def call_tool(self, *a, **k):  # pragma: no cover - 被调用即失败
            raise AssertionError("签码前应已拒, 不得连门")

    with pytest.raises(RuntimeError) as exc:
        enroll_deliver(client=NoGate(), role="advisor", governance_root=str(gov), workspace=str(gov), owner_authorization_ref="fx-owner",
                       instance=ADV_OTA, harness_kind="claude-code", harness_dir=str(gov))
    _show(f"[enroll-deliver 拒] {exc.value}")
    assert "SESSION_DIR_TAKEN" in str(exc.value)
    assert (f"lybra roles enroll-deliver --role advisor --instance {ADV_OTA} --governance-root {gov} --workspace {gov} "
            f"--harness claude-code --harness-dir {sub} --owner-authorization-ref fx-owner --dry-run") in str(exc.value)

    # enroll(CLI): 码内带签发声明 = 换码前拒(不连门、不改凭据)
    code = encode_self_contained_code(gate_url="http://127.0.0.1:9", governance_root=str(gov), transport_token="fx", code="fx",
                                      role="advisor", instance=ADV_OTA, role_class="advisor")
    rc, out, err = _cli("roles", "enroll", "--code", code, "--workspace", str(gov), "--harness", "claude-code", "--harness-dir", str(gov))
    _show(f"---- roles enroll 同机同目录 rc={rc} ----\n{out}{err}")
    assert rc != 0 and "SESSION_DIR_TAKEN" in (out + err)
    after = (gov / ".lybra" / "connection.json").read_bytes() if (gov / ".lybra" / "connection.json").is_file() else None
    assert after == conn_before and not (gov / SUB).exists(), "拒 = 凭据未改、子路径未建"


def test_item1_enroll_old_code_rechecked_after_exchange(tmp_path, monkeypatch):
    """旧码(无签发声明)换码前不知实例 = 换码后按门下发实例复核, 先于写 connection.json。"""
    import tools.aipos_cli.enroll_client as ec

    gov = _rig(tmp_path, monkeypatch, launch=["pi", "claude-code"])
    _lantu_at_root(gov)
    from tools.aipos_cli.enrollment import encode_self_contained_code

    code = encode_self_contained_code(gate_url="http://127.0.0.1:9", governance_root=str(gov), transport_token="fx", code="fx")
    monkeypatch.setattr(ec, "exchange_enrollment_code", lambda *a, **k: {
        "ok": True, "token_entry": {"role": "advisor", "agent_instance": ADV_OTA, "fingerprint": "fx", "scopes": [], "token": "fx-token"}})
    conn = gov / ".lybra" / "connection.json"
    conn_before = conn.read_bytes() if conn.is_file() else None
    with pytest.raises(RuntimeError) as exc:
        ec.enroll(code=code, gate_url="http://127.0.0.1:9", workspace_root=gov, harness_kind="claude-code", harness_dir=gov)
    _show(f"[enroll 旧码换码后复核 拒] {exc.value}")
    assert "SESSION_DIR_TAKEN" in str(exc.value) and f"已被同机顾问实例 {ADV_LANTU} 登记" in str(exc.value)
    assert (conn.read_bytes() if conn.is_file() else None) == conn_before


# ===========================================================================
# 件①: sync 兜底(落点章程属另一实例 = 拒不覆盖)+ roles list 只读诊断标出现存相撞
# ===========================================================================

def test_item1_sync_guard_and_roles_list_diagnosis(tmp_path, monkeypatch):
    from tools.aipos_cli.charter_render import workstation_identity
    from tools.aipos_cli.distribution_sync import _sync_harness_dir, harness_distributions, workstation_harness
    from tools.aipos_cli.workstation_wiring import declared_role_distributions

    gov = _rig(tmp_path, monkeypatch, launch=["pi", "claude-code"])
    _lantu_at_root(gov)
    _legacy_collision(gov)
    landing = gov / ".claude" / "rules" / "lybra-advisor.md"
    landing.parent.mkdir(parents=True)
    landing.write_text("岚图顾问的章程渲染物\n", encoding="utf-8")

    rc, out, err = _cli("roles", "--workspace-root", str(gov), "list")
    _show(f"---- roles list(现存相撞诊断)rc={rc} ----\n{out}{err}")
    assert f"{ADV_OTA:<44} claude-code@{LOCAL}:{gov}  ✗ 相撞: 与 {ADV_LANTU}" in out
    assert f"{ADV_LANTU:<44} claude-code@{LOCAL}:{gov}  ✗ 相撞: 与 {ADV_OTA}" in out
    rc, out, err = _cli("roles", "--workspace-root", str(gov), "list", "--json")
    sessions = {s["instance"]: s for s in json.loads(out)["advisor_sessions"]}
    assert sessions[ADV_OTA]["conflicts"] == [ADV_LANTU] and sessions[ADV_LANTU]["conflicts"] == [ADV_OTA]

    identity = workstation_identity(gov, instance=ADV_OTA)
    harness = workstation_harness(gov, instance=ADV_OTA)
    dists = [{**d, "target_harness": "claude-code"} for d in harness_distributions(declared_role_distributions("advisor", "advisor"), "claude-code")]
    ctx = {"role": "advisor", "gate_url": "http://127.0.0.1:9", "harness_root": gov, "lybra_dir": gov / ".lybra"}

    class NoGate:
        def call_tool(self, *a, **k):  # pragma: no cover - 被调用即失败
            raise AssertionError("兜底先于取件, 不得连门")

    result = _sync_harness_dir(NoGate(), ctx, {"distributions": dists, "product_commit": "fx"}, identity=identity, harness=harness,
                               scope=None, dry_run=False, governance_root=gov)
    _show(f"[sync 兜底] {json.dumps(result, ensure_ascii=False)}")
    assert result["ok"] is False and result["status"] == "refused" and "落点章程属另一实例" in result["error"]
    assert f"已被同机顾问实例 {ADV_LANTU} 登记" in result["error"] and f"--dir {gov / SUB / ADV_OTA}" in result["error"]
    assert landing.read_text(encoding="utf-8") == "岚图顾问的章程渲染物\n", "不覆盖他实例章程"


# ===========================================================================
# 件②③: 向导缺省 = 治理根(三种 home 根定位各一例)、已占用 = 声明子路径、显式相撞拒; help 与缺省一致
# ===========================================================================

def _guide_kwargs(tmp_path: Path) -> dict:
    return dict(code_repo=str(tmp_path / "code"), owner_connection_json=str(tmp_path / "owner" / "connection.json"),
                gate_url="http://127.0.0.1:9/mcp")


@pytest.mark.parametrize("how", ["--home-root", "LYBRA_HOME_ROOT", "config home_root"])
def test_item2_guide_default_is_governance_root(tmp_path, monkeypatch, how):
    from tools.aipos_cli.onboarding import generate_onboarding_guide

    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.delenv("LYBRA_HOME_ROOT", raising=False)
    root = tmp_path / f"truth-{how.replace(' ', '-').strip('-').lower()}"
    kw = _guide_kwargs(tmp_path)
    if how == "--home-root":
        guide = generate_onboarding_guide("f253-probe", home_root=str(root), **kw)
    elif how == "LYBRA_HOME_ROOT":
        monkeypatch.setenv("LYBRA_HOME_ROOT", str(root))
        guide = generate_onboarding_guide("f253-probe", **kw)
    else:
        (home / ".lybra").mkdir()
        (home / ".lybra" / "config.json").write_text(json.dumps({"home_root": str(root)}), encoding="utf-8")
        guide = generate_onboarding_guide("f253-probe", **kw)
    gov = root.resolve() / "f253-probe"
    step4 = next(s for s in guide["steps"] if s["step_number"] == 4)
    _show(f"[向导缺省·{how}] home_root={guide['home_root']}({guide['home_root_source']}) advisor_dir={guide['advisor_dir']}"
          f"({guide['advisor_dir_source']})")
    assert guide["governance_root"] == str(gov) and guide["advisor_dir"] == str(gov) and guide["advisor_dir_source"] == "缺省 = 治理根"
    assert f"--harness-dir {gov}" in step4["command"] and "~/f253-probe" not in step4["command"]


def test_item2_guide_occupied_root_and_explicit_collision(tmp_path, monkeypatch):
    from tools.aipos_cli.onboarding import generate_onboarding_guide

    gov = _rig(tmp_path, monkeypatch, launch=["pi", "claude-code"])
    _lantu_at_root(gov)
    home = tmp_path / "home-root"
    home.mkdir()
    (home / CHRIS).symlink_to(gov, target_is_directory=True)  # home 根/<项目> = 本靶场治理根
    kw = _guide_kwargs(tmp_path)
    guide = generate_onboarding_guide(CHRIS, home_root=str(home), host_segment="guidehost", **kw)
    inst = guide["instances"]["advisor"]
    sub = gov / SUB / inst
    _show(f"[向导·治理根已被同机顾问登记] advisor={inst} advisor_dir={guide['advisor_dir']}({guide['advisor_dir_source']})")
    assert guide["advisor_dir"] == str(sub) and ADV_LANTU in guide["advisor_dir_source"]

    rc, out, err = _cli("onboarding", "guide", CHRIS, "--home-root", str(home), "--host-segment", "guidehost", "--advisor-dir", str(gov),
                        "--code-repo", kw["code_repo"], "--owner-connection-json", kw["owner_connection_json"], "--gate-url", kw["gate_url"])
    _show(f"---- 向导显式 --advisor-dir 相撞 rc={rc} ----\n{out}{err}")
    assert rc == 2 and "SESSION_DIR_TAKEN" in err and f"已被同机顾问实例 {ADV_LANTU} 登记" in err
    assert f"--advisor-harness claude-code --advisor-dir {sub}" in err


def test_item3_guide_help_matches_default():
    rc, out, err = _cli("onboarding", "guide", "--help")
    text = " ".join(out.split())
    _show(f"[guide --advisor-dir help] {text[text.find('--advisor-dir ADVISOR_DIR'):][:700]}")
    assert "the project governance root" in text and "~/<project> for a session" not in text
    assert "--home-root > LYBRA_HOME_ROOT > ~/.lybra/config.json home_root" in text


def test_declaration_single_source_and_no_hardcoded_root():
    """子路径名只在 schema 声明一次; 产品代码不写死治理根路径(缺省来自调用方给的治理根)。"""
    from tools.aipos_cli.enrollment import session_dir_declaration

    decl = session_dir_declaration()
    hits = subprocess.run(["git", "grep", "-n", "-F", decl["secondary_subpath"].split("/")[0], "--", "tools/", "schema/"], cwd=REPO_ROOT,
                          capture_output=True, text=True).stdout.splitlines()
    _show(f"[子路径名出现处] {hits}")
    assert len(hits) == 1 and hits[0].startswith("schema/distribution.schema.json:")
    src = (REPO_ROOT / "tools" / "aipos_cli" / "onboarding.py").read_text(encoding="utf-8")
    assert 'f"~/{project_name}" if' not in src and "2_projects" not in src
