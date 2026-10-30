"""AIPOS-F254 — 顾问会话目录缺省按层级: 项目级顾问占治理根, 子项目顾问缺省进治理根内声明子路径(F253 原为先到先得)。

件① 层级判据(唯一 enrollment.session_tier; 声明 distribution.schema harness_semantics.session_dir.tiers): 登记的角色名 = roles.schema 内建角色
    (角色即其类, advisor)= 项目级; 门注册表自定义角色(advisor-lantu / advisor-ota)= 子项目。只看登记角色名, 不按实例名前缀猜。
件② 缺省按层级(唯一 default_session_dir): 子项目顾问 = 声明子路径(治理根空闲也是); 项目级顾问 = 治理根; 同机第二个项目级顾问 = 子路径(F253 不变);
    治理根被子项目顾问占 = 不静默降级, 拒因点名占用者并给换位命令原文(session_dir_refusal; redeclare / guide / enroll / enroll-deliver 同一文本)。
件③ roles list 层级诊断(session_hierarchy): 子项目顾问占治理根 / 项目级顾问被挤出治理根 = 违例 + 换位命令; 其余非缺省目录只标「用户指定」。
件④ sync --harness-root <治理根> 给每个登记在本机的顾问会话(顶层 + 实例槽)按各自会话目录、各自凭据渲染各自章程与技能, 清单按实例分键互不覆盖。

靶场: 临时治理根(chris 形, 自造人肉期样本: 三个顾问 land + 实例槽 codex@他机)+ 临时 HOME; 门 = 进程内假门(按凭据认角色, 件取本产品树);
不连真实门、不读真实凭据、不写真实治理根(禁写 chris-huibojin)。
"""
from __future__ import annotations

import base64
import json
import re
import shlex
import socket
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "tests"))

import test_aipos_f78_engine_agnostic as f78  # noqa: E402  — 靶场骨架唯一来源
from test_aipos_f187_kickoff_worktree_cleanup import CHRIS, _cli, _update_project  # noqa: E402
from test_aipos_f235_instance_redeclare import MAC, MAC_DIR, _authorize, _land, _log_lines, _role_doc  # noqa: E402
from test_aipos_f73d_loop_driver import init_governance_repo  # noqa: E402

LOCAL = socket.gethostname()
SUB = ".advisor-sessions"
ADV_MAIN = f"advisor.{CHRIS}.{MAC}"            # 项目级顾问(总顾问; 内建角色 advisor)
ADV_MAIN2 = f"advisor.{CHRIS}.dev2"            # 同项目第二个项目级顾问(同角色)
ADV_LANTU = f"advisor-lantu.{CHRIS}.{MAC}"     # 子项目顾问(自定义角色 advisor-lantu)
ADV_OTA = f"advisor-ota.{CHRIS}.{MAC}"         # 子项目顾问(自定义角色 advisor-ota; 留在他机 Codex)
ROLES = {ADV_MAIN: "advisor", ADV_MAIN2: "advisor", ADV_LANTU: "advisor-lantu", ADV_OTA: "advisor-ota"}
TOKENS = {f"fx-tok-{i.split('.')[0]}-{i.split('.')[-1]}": i for i in ROLES}  # 假凭据(夹具值, 非真实凭据)


def _show(text: str) -> None:
    print(text, flush=True)


def _rig(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, instances: tuple[str, ...] = (ADV_MAIN, ADV_LANTU, ADV_OTA)) -> Path:
    """chris 形治理根: 各顾问 land(role = 门签码角色)+ 实例槽 codex@他机(人肉期样本), 最后写入的 OTA 为 .lybra/role 顶层(同 chris 现状:
    顶层是他机会话); 治理根连接文件 = 各实例凭据条目(自定义角色带 role_class = 门注册表); 项目常设授权 hosts = 本机 + 他机。"""
    from tools.aipos_cli.enroll_client import write_role_file

    gov = f78._make_gov(tmp_path, monkeypatch, shape="chris", project=CHRIS)
    (gov / ".lybra" / "role").unlink()  # 靶场骨架自带的单实例记录不要(本靶场治理根只有下列顾问)
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    app = f78._init_product_repo(tmp_path / "repos" / "android-app")
    _update_project(gov, code_repo=str(app), repos={"default": "android-app", "items": {"android-app": str(app)}})
    (gov / "5_tasks" / "records" / "sessions").mkdir(parents=True, exist_ok=True)
    for inst in instances:
        _land(gov, inst, ROLES[inst], f"host={LOCAL} workstation={gov} files=['connection.json', 'role']")
        write_role_file(gov / ".lybra", ROLES[inst], inst, None, harness={"kind": "codex", "dir": MAC_DIR, "host": MAC}, slot_by_instance=True)
    tokens = [{"token": tok, "role": ROLES[inst], "agent_instance": inst, "token_ref": f"svc-{inst}", "projects": [CHRIS],
               "scopes": [], "fingerprint": f"fx{n}", **({} if ROLES[inst] == "advisor" else {"role_class": "advisor"})}
              for n, (tok, inst) in enumerate(TOKENS.items()) if inst in instances]
    (gov / ".lybra" / "connection.json").write_text(json.dumps({
        "config_version": 1, "governance_root": str(gov), "workspace_root": str(gov),
        "mcp": {"rpc_url": "http://127.0.0.1:9/mcp"}, "tokens": tokens}, ensure_ascii=False, indent=2), encoding="utf-8")
    _authorize(gov, hosts=[LOCAL, MAC], launch=["pi", "claude-code", "codex"])
    init_governance_repo(gov)
    return gov


def _redeclare(gov: Path, instance: str, *extra: str, confirm: bool = False, actor: str | None = None) -> tuple[int, str, str]:
    argv = ["roles", "redeclare", "--workspace-root", str(gov), "--instance", instance, "--actor", actor or instance, *extra]
    return _cli(*argv, *(["--confirm"] if confirm else []))


def _swap_cmds(text: str) -> list[list[str]]:
    """拒因 / 诊断原文里的换位命令(lybra roles redeclare … --dir <子路径>)→ argv(去掉首个 lybra)。"""
    return [shlex.split(m)[1:] for m in re.findall(r"lybra roles redeclare --workspace-root \S+ --instance \S+ --actor \S+ --dir \S+", text)]


def _sessions(gov: Path) -> dict[str, dict]:
    rc, out, err = _cli("roles", "--workspace-root", str(gov), "list", "--json")
    assert rc == 0, err
    return {s["instance"]: s for s in json.loads(out)["advisor_sessions"]}


# ===========================================================================
# 件①: 层级判据单一声明 + 唯一实现(登记角色名; 不按实例名前缀)
# ===========================================================================

def test_item1_tier_judgment_from_registered_role(tmp_path, monkeypatch):
    from tools.aipos_cli.enrollment import session_dir_declaration, session_tier

    gov = _rig(tmp_path, monkeypatch)
    tiers = session_dir_declaration()["tiers"]
    _show(f"[声明 tiers.rule] {tiers['rule']}")
    got = {i: session_tier(gov, i) for i in (ADV_MAIN, ADV_LANTU, ADV_OTA)}
    _show(f"[层级] {got}")
    assert got == {ADV_MAIN: "project", ADV_LANTU: "subproject", ADV_OTA: "subproject"}
    # 判据只看登记角色名: 实例名首段是 advisor-lantu、但登记角色给 advisor = 项目级(不按实例名前缀猜)
    assert session_tier(gov, ADV_LANTU, role="advisor") == "project"
    with pytest.raises(ValueError, match="角色名判不了"):
        session_tier(gov, f"advisor-x.{CHRIS}.{MAC}")  # 未登记 = fail-closed


# ===========================================================================
# 件②: 缺省按层级(redeclare 不给 --dir)
# ===========================================================================

def test_item2_defaults_by_tier_subproject_subpath_then_project_root(tmp_path, monkeypatch):
    from tools.aipos_cli.enrollment import registered_harness

    gov = _rig(tmp_path, monkeypatch)
    sub_lantu = gov / SUB / ADV_LANTU
    # 子项目顾问先迁本机、治理根空闲 = 仍缺省声明子路径(F253 先到先得时它会占治理根)
    rc, out, err = _redeclare(gov, ADV_LANTU, "--harness", "claude-code", confirm=True)
    _show(f"---- 子项目顾问 claude-code 不给 --dir(治理根空闲, --confirm)rc={rc} ----\n{out}{err}")
    assert rc == 0 and f"会话目录: {sub_lantu}(缺省 = 治理根内声明子路径(子项目顾问))" in out and "已写入" in out
    assert registered_harness(gov, ADV_LANTU)["dir"] == str(sub_lantu) and sub_lantu.is_dir()

    # 项目级顾问后来 = 缺省治理根
    rc, out, err = _redeclare(gov, ADV_MAIN, "--harness", "claude-code", confirm=True)
    _show(f"---- 项目级顾问 claude-code 不给 --dir(--confirm)rc={rc} ----\n{out}{err}")
    assert rc == 0 and f"会话目录: {gov}(缺省 = 治理根)" in out
    assert registered_harness(gov, ADV_MAIN)["dir"] == str(gov)

    # 用户显式 --dir 优先(子项目顾问指到别处 = 用户指定)
    mine = tmp_path / "ota-session"
    mine.mkdir()
    rc, out, err = _redeclare(gov, ADV_OTA, "--harness", "claude-code", "--host", LOCAL, "--dir", str(mine), confirm=True)
    _show(f"---- 子项目顾问显式 --dir rc={rc} ----\n{out}{err}")
    assert rc == 0 and f"会话目录: {mine}(用户指定)" in out

    sessions = _sessions(gov)
    _show("[roles list --json hierarchy] " + json.dumps({k: v["hierarchy"] for k, v in sessions.items()}, ensure_ascii=False))
    assert sessions[ADV_MAIN]["tier"] == "project" and sessions[ADV_MAIN]["hierarchy"]["status"] == "default"
    assert sessions[ADV_LANTU]["tier"] == "subproject" and sessions[ADV_LANTU]["hierarchy"]["status"] == "default"
    assert sessions[ADV_OTA]["hierarchy"] == {**sessions[ADV_OTA]["hierarchy"], "status": "explicit", "violation": False}


def test_item2_project_advisor_swaps_subproject_out_of_root(tmp_path, monkeypatch):
    """子项目顾问占治理根(F253 先到先得的存量形)→ 项目级顾问缺省治理根: 预演即拒、点名占用者、给换位命令; 照命令两步后项目级顾问落治理根。"""
    from tools.aipos_cli.enrollment import registered_harness

    gov = _rig(tmp_path, monkeypatch)
    rc, out, err = _redeclare(gov, ADV_LANTU, "--harness", "claude-code", "--host", LOCAL, "--dir", str(gov), confirm=True)
    assert rc == 0 and f"会话目录: {gov}(用户指定)" in out, out + err

    rc, out, err = _cli("roles", "--workspace-root", str(gov), "list")
    _show(f"---- roles list(子项目顾问占治理根)----\n{out}{err}")
    assert f"{ADV_LANTU:<44} claude-code@{LOCAL}:{gov}  [子项目顾问] ✗ 违层级: 子项目顾问占治理根" in out
    diag_swap = _swap_cmds(out)
    assert diag_swap and diag_swap[0][diag_swap[0].index("--instance") + 1] == ADV_LANTU

    before, role_before = _log_lines(gov), _role_doc(gov)
    rc, out, err = _redeclare(gov, ADV_MAIN, "--harness", "claude-code")
    _show(f"---- 项目级顾问 claude-code 不给 --dir(预演)rc={rc} ----\n{out}{err}")
    assert rc == 1 and "REDECLARE_SESSION_DIR_TAKEN: SESSION_DIR_TAKEN" in err
    assert f"项目级顾问 {ADV_MAIN} 的会话目录 {LOCAL}:{gov} = 治理根, 被同机子项目顾问 {ADV_LANTU} 登记" in err and "不静默降到子路径" in err
    sub_lantu = gov / SUB / ADV_LANTU
    swap = (f"lybra roles redeclare --workspace-root {gov} --instance {ADV_LANTU} --actor {ADV_MAIN} --dir {sub_lantu}")
    rerun = (f"lybra roles redeclare --workspace-root {gov} --instance {ADV_MAIN} --actor {ADV_MAIN} --harness claude-code "
             f"--host {LOCAL} --dir {gov}")
    assert f"① 把占用者改到它的声明子路径(只改目录, 第二档): {swap}(预演, 加 --confirm 写入); ② 再跑本命令: {rerun}" in err
    assert str(gov / SUB / ADV_MAIN) not in err, "不给子路径兜底(不静默降级)"
    assert _log_lines(gov) == before and _role_doc(gov) == role_before, "拒 = 零写入"

    # 照命令两步: ① 占用者换到其声明子路径(只改目录 = 第二档)② 本命令重跑
    rc, out, err = _cli(*shlex.split(swap)[1:], "--confirm")
    _show(f"---- ① 换位命令 --confirm rc={rc} ----\n{out}{err}")
    assert rc == 0 and "第二档 —— 只改工作目录" in out and "已写入" in out
    rc, out, err = _cli(*shlex.split(rerun)[1:], "--confirm")
    _show(f"---- ② 本命令重跑 --confirm rc={rc} ----\n{out}{err}")
    assert rc == 0 and "已写入" in out
    assert registered_harness(gov, ADV_MAIN)["dir"] == str(gov) and registered_harness(gov, ADV_LANTU)["dir"] == str(sub_lantu)
    sessions = _sessions(gov)
    assert not any(s["hierarchy"]["violation"] or s["conflicts"] for s in sessions.values())


def test_item2_two_project_advisors_second_gets_subpath(tmp_path, monkeypatch):
    gov = _rig(tmp_path, monkeypatch, instances=(ADV_MAIN, ADV_MAIN2, ADV_OTA))
    rc, out, err = _redeclare(gov, ADV_MAIN, "--harness", "claude-code", confirm=True)
    assert rc == 0 and f"会话目录: {gov}(缺省 = 治理根)" in out, out + err
    rc, out, err = _redeclare(gov, ADV_MAIN2, "--harness", "claude-code", "--host", LOCAL)
    _show(f"---- 第二个项目级顾问(预演)rc={rc} ----\n{out}{err}")
    assert rc == 0 and f"会话目录: {gov / SUB / ADV_MAIN2}(缺省 = 治理根内声明子路径(同机治理根已被 {ADV_MAIN} 登记))" in out


def test_item3_roles_list_flags_displaced_project_advisor(tmp_path, monkeypatch):
    gov = _rig(tmp_path, monkeypatch)
    elsewhere = tmp_path / "main-session"
    elsewhere.mkdir()
    assert _redeclare(gov, ADV_LANTU, "--harness", "claude-code", "--host", LOCAL, "--dir", str(gov), confirm=True)[0] == 0
    assert _redeclare(gov, ADV_MAIN, "--harness", "claude-code", "--host", LOCAL, "--dir", str(elsewhere), confirm=True)[0] == 0
    rc, out, err = _cli("roles", "--workspace-root", str(gov), "list")
    _show(f"---- roles list(项目级顾问被子项目顾问挤出治理根)----\n{out}{err}")
    assert f"{ADV_MAIN:<44} claude-code@{LOCAL}:{elsewhere}  [项目级顾问] ✗ 违层级: 项目级顾问不在治理根(治理根被子项目顾问 {ADV_LANTU} 占)" in out
    assert (f"再 lybra roles redeclare --workspace-root {gov} --instance {ADV_MAIN} --actor {ADV_MAIN} --dir {gov}") in out
    assert f"{ADV_OTA:<44} codex@{MAC}:{MAC_DIR}  [子项目顾问]" in out, "他机会话不判层级"
    hier = _sessions(gov)[ADV_MAIN]["hierarchy"]
    assert hier["status"] == "project_displaced" and hier["violation"] and len(hier["commands"]) == 2


# ===========================================================================
# 件②④: guide / enroll / enroll-deliver 入口缺省与拒因与 redeclare 一致
# ===========================================================================

def test_item2_guide_and_enroll_entries_match_redeclare(tmp_path, monkeypatch):
    from tools.aipos_cli.enroll_client import enroll_preflight
    from tools.aipos_cli.enroll_deliver import enroll_deliver
    from tools.aipos_cli.onboarding import generate_onboarding_guide

    gov = _rig(tmp_path, monkeypatch)
    assert _redeclare(gov, ADV_LANTU, "--harness", "claude-code", "--host", LOCAL, "--dir", str(gov), confirm=True)[0] == 0
    home = tmp_path / "home-root"
    home.mkdir()
    (home / CHRIS).symlink_to(gov, target_is_directory=True)
    kw = dict(code_repo=str(tmp_path / "code"), owner_connection_json=str(tmp_path / "owner" / "connection.json"),
              gate_url="http://127.0.0.1:9/mcp")
    swap = f"lybra roles redeclare --workspace-root {gov} --instance {ADV_LANTU} --actor {ADV_LANTU} --dir {gov / SUB / ADV_LANTU}"

    # 向导(接入项目级顾问)缺省治理根: 治理根被子项目顾问占 = 拒并给换位命令(不降到子路径)
    with pytest.raises(ValueError) as exc:
        generate_onboarding_guide(CHRIS, home_root=str(home), host_segment="guidehost", **kw)
    _show(f"[向导·治理根被子项目顾问占] {exc.value}")
    assert "SESSION_DIR_TAKEN" in str(exc.value) and f"被同机子项目顾问 {ADV_LANTU} 登记" in str(exc.value) and swap in str(exc.value)
    assert f"② 再跑本命令: lybra onboarding guide {CHRIS} --home-root {home} --advisor-harness claude-code --advisor-dir {gov} " in str(exc.value)

    # enroll 预检(enroll 与 enroll-deliver 同一实现): 项目级顾问要治理根 = 同一换位拒因
    with pytest.raises(RuntimeError) as exc:
        enroll_preflight(workspace_root=gov, governance_root=str(gov), declared={"role": "advisor", "instance": ADV_MAIN, "role_class": "advisor"},
                         harness_kind="claude-code", harness_dir=str(gov))
    _show(f"[enroll_preflight 项目级顾问] {exc.value}")
    assert "不静默降到子路径" in str(exc.value) and swap in str(exc.value)

    class NoGate:
        def call_tool(self, *a, **k):  # pragma: no cover - 被调用即失败
            raise AssertionError("签码前应已拒, 不得连门")

    with pytest.raises(RuntimeError) as exc:
        enroll_deliver(client=NoGate(), role="advisor", governance_root=str(gov), workspace=str(gov), owner_authorization_ref="fx-owner",
                       instance=ADV_MAIN, harness_kind="claude-code", harness_dir=str(gov))
    _show(f"[enroll-deliver 项目级顾问] {exc.value}")
    assert swap in str(exc.value) and f"--harness-dir {gov} --owner-authorization-ref fx-owner --dry-run" in str(exc.value)

    # 换位后: 向导缺省 = 治理根; 子项目顾问的 enroll 预检若要治理根 = 普通相撞拒, 出口给它自己的声明子路径
    assert _cli(*shlex.split(swap)[1:], "--confirm")[0] == 0
    guide = generate_onboarding_guide(CHRIS, home_root=str(home), host_segment="guidehost", **kw)
    _show(f"[向导·换位后] advisor_dir={guide['advisor_dir']}({guide['advisor_dir_source']})")
    assert guide["advisor_dir"] == str((home / CHRIS).resolve()) and guide["advisor_dir_source"] == "缺省 = 治理根"
    assert _redeclare(gov, ADV_MAIN, "--harness", "claude-code", confirm=True)[0] == 0
    with pytest.raises(RuntimeError) as exc:
        enroll_preflight(workspace_root=gov, governance_root=str(gov), declared={"role": "advisor-ota", "instance": ADV_OTA, "role_class": "advisor"},
                         harness_kind="claude-code", harness_dir=str(gov))
    _show(f"[enroll_preflight 子项目顾问要治理根] {exc.value}")
    assert f"已被同机顾问实例 {ADV_MAIN} 登记" in str(exc.value) and f"(--harness-dir)换成 {gov / SUB / ADV_OTA} 后重跑" in str(exc.value)


# ===========================================================================
# 件④: sync 给每个本机顾问会话渲染各自章程与技能(零手工)
# ===========================================================================

class _FakeGate:
    """进程内假门: 按凭据认调用者(角色 / 角色类, 同门 _capability_token 口径), 清单与件取本产品树(门同一构建器 build_role_manifest)。"""

    calls: list[tuple[str, str]] = []

    def __init__(self, gate_url: str, token: str) -> None:
        inst = TOKENS[token]
        self.role, self.role_class = ROLES[inst], "advisor"

    def initialize(self) -> None:
        return None

    def call_tool(self, name: str, args: dict) -> dict:
        from tools.distribution_manifest import build_role_manifest

        _FakeGate.calls.append((name, self.role))
        manifest = build_role_manifest(REPO_ROOT, self.role, role_class=self.role_class)
        if name == "lybra_distribution_manifest":
            return {"ok": True, "manifest_version": 1, "product_commit": "fx-commit", "role": self.role, "harness": manifest["harness"],
                    "distributions": manifest["distributions"]}
        dist = next(d for d in manifest["distributions"] if d["distribution_id"] == args["distribution_id"])
        src = REPO_ROOT / dist["source_path"]
        src = src.parent if dist.get("source_is_file") else src
        return {"ok": True, "distribution_id": dist["distribution_id"],
                "files": [{"path": p, "content_b64": base64.b64encode((src / p).read_bytes()).decode("ascii")} for p in args["paths"]]}


def test_item4_sync_renders_each_local_session(tmp_path, monkeypatch):
    import tools.aipos_cli.confirm_client as cc

    gov = _rig(tmp_path, monkeypatch, instances=(ADV_MAIN, ADV_MAIN2, ADV_LANTU, ADV_OTA))
    assert _redeclare(gov, ADV_MAIN, "--harness", "claude-code", confirm=True)[0] == 0           # 项目级 → 治理根
    assert _redeclare(gov, ADV_MAIN2, "--harness", "claude-code", "--host", LOCAL, confirm=True)[0] == 0  # 第二个项目级 → 子路径
    assert _redeclare(gov, ADV_LANTU, "--harness", "claude-code", confirm=True)[0] == 0          # 子项目 → 子路径
    top_before = {k: v for k, v in _role_doc(gov).items() if k != "instances"}
    assert top_before["instance"] == ADV_OTA, "顶层 = 他机 Codex 会话(chris 同形: sync 原先只渲染顶层)"
    monkeypatch.setattr(cc, "GateClient", _FakeGate)
    _FakeGate.calls = []

    dirs = {ADV_MAIN: gov, ADV_MAIN2: gov / SUB / ADV_MAIN2, ADV_LANTU: gov / SUB / ADV_LANTU}
    rc, out, err = _cli("sync", "--harness-root", str(gov), "--workspace-root", str(gov), "--dry-run")
    _show(f"---- sync --dry-run rc={rc} ----\n{out}{err}")
    assert rc == 0 and all(f"[实例槽 {i}]: dry-run" in out for i in dirs)
    assert not any((d / ".claude").exists() for d in dirs.values()), "预演零写入"

    rc, out, err = _cli("sync", "--harness-root", str(gov), "--workspace-root", str(gov))
    _show(f"---- sync rc={rc} ----\n{out}{err}")
    assert rc == 0
    for inst, d in dirs.items():
        charter = (d / ".claude" / "rules" / "lybra-advisor.md").read_text(encoding="utf-8")
        skills = sorted(p.name for p in (d / ".claude" / "skills").iterdir())
        others = [o for o in dirs if o != inst]
        _show(f"[{inst}] 章程 {d / '.claude/rules/lybra-advisor.md'} 实例行: "
              f"{next(ln for ln in charter.splitlines() if '- 项目:' in ln)}; 技能 {skills}")
        assert f"实例 `{inst}`" in charter and not any(f"实例 `{o}`" in charter for o in others), "章程按实例区分"
        assert skills, "技能件落该会话目录"
    assert {r for _n, r in _FakeGate.calls} == {"advisor", "advisor-lantu", "advisor-ota"}, "各实例以各自凭据(各自角色)取件"
    keys = sorted(p.name for p in (gov / ".lybra").glob(".version-*"))
    _show(f"[本地清单] {keys}")
    assert {f".version-advisor@{ADV_MAIN}", f".version-advisor@{ADV_MAIN2}", f".version-advisor-lantu@{ADV_LANTU}"} <= set(keys)
    doc = _role_doc(gov)
    _show(f"[sync 后 .lybra/role 顶层] {json.dumps({k: v for k, v in doc.items() if k != 'instances'}, ensure_ascii=False)}")
    assert (doc["instance"], doc["role"], doc["harness"]) == (top_before["instance"], top_before["role"], top_before["harness"])
    assert all("owner_policy_ref" not in doc["instances"][i] for i in dirs), "槽 sync 不做信封校正(_correct_owner_policy_ref 只认顶层实例)"

    # 再 sync = 各会话都已最新(清单按实例分键, 同角色两实例互不覆盖渲染记录)
    rc, out, err = _cli("sync", "--harness-root", str(gov), "--workspace-root", str(gov), "--dry-run", "--json")
    run = json.loads(out)
    plans = {e.get("instance"): e["result"]["plan"] for e in run["workstations"] if e.get("instance")}
    _show(f"[再预演 plan] {plans}")
    assert plans == {ADV_MAIN: [], ADV_MAIN2: [], ADV_LANTU: []}


def test_item4_slot_manifest_key_survives_top_slot_swap(tmp_path):
    """实例在顶层与槽之间换位后, 读键沿用其旧清单(只认 workstation.instance = 本实例者); 别的实例的清单不认。"""
    from tools.aipos_cli.distribution_sync import _session_manifest_keys

    md = tmp_path / ".lybra"
    md.mkdir()
    (md / ".version-advisor").write_text(json.dumps({"workstation": {"instance": ADV_MAIN}}), encoding="utf-8")
    assert _session_manifest_keys(md, "advisor", ADV_MAIN, slot_scoped=True) == ("advisor", f"advisor@{ADV_MAIN}")
    assert _session_manifest_keys(md, "advisor", ADV_MAIN2, slot_scoped=True) == (f"advisor@{ADV_MAIN2}",) * 2
    assert _session_manifest_keys(md, "advisor", ADV_MAIN2, slot_scoped=False) == ("advisor", "advisor"), "别的实例的清单不认 = 无记录"
    (md / ".version-advisor").write_text(json.dumps({"distributions": []}), encoding="utf-8")  # 存量清单无 workstation = 顶层认
    assert _session_manifest_keys(md, "advisor", ADV_MAIN, slot_scoped=False) == ("advisor", "advisor")


def test_declaration_single_source():
    """层级判据只声明一处; 判定只在 enrollment.session_tier(代码不写死自定义角色名)。"""
    hits = __import__("subprocess").run(["git", "grep", "-n", "-e", "advisor-lantu", "--", "tools/"], cwd=REPO_ROOT, capture_output=True,
                                        text=True).stdout.splitlines()
    _show(f"[tools/ 内自定义角色名出现处] {hits}")
    assert hits == []
