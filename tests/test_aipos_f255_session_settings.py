"""AIPOS-F255 — 子目录顾问会话不再加载上级治理根别的实例的章程 + 顾问自动记忆可按实例隔离 + 治理根 .claude/ 渲染物不进 git 视野。

依据(10-31 chris 换位后实测): 岚图会话目录 = <治理根>/.advisor-sessions/<实例>, /context 同载本目录章程与 <治理根>/.claude/rules/lybra-advisor.md
(总顾问的章程 = 身份串); Claude Code 自动记忆按 git 仓根定位, 治理根同在一个 git 仓 = 所有 Claude 顾问读写同一份 MEMORY.md; 治理根渲染的
.claude/ 在治理仓 git status 显示为未跟踪。

件① 上级章程隔离: sync 在每个本机 claude-code 会话目录渲染官方设置文件(声明 harness_semantics.session_settings.relative_path =
    .claude/settings.local.json), claudeMdExcludes = 每个上级目录 × 章程落点的绝对路径(官方: 绝对路径按绝对文件路径匹配, rules 文件受管)。
件② 记忆隔离: autoMemoryDirectory = 声明目录(按实例); 新会话缺省隔离; 既有会话(已有本地清单)不静默切换, 显式切换 lybra roles memory 两阶段。
件③ 渲染物不进 git 视野: 会话目录 .claude/.gitignore 只列产品渲染物(含自身), 用户文件照常可见; 用户自己的设置文件 / 忽略文件不覆盖。
件④ 只读诊断: lybra roles list(文本与 --json)对每个本机 claude-code 会话列会加载的上级章程(串身份风险)与记忆共享者; 修复后无风险。

靶场: 复用 F254 靶场(临时治理根 chris 形 + 临时 HOME + 进程内假门); 不连真实门、不读真实凭据、不写真实治理根与真实 ~/.claude。
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "tests"))

import test_aipos_f254_advisor_session_tier as f254  # noqa: E402  — 靶场与假门唯一来源(F254)
from test_aipos_f187_kickoff_worktree_cleanup import CHRIS, _cli  # noqa: E402

ADV_MAIN, ADV_MAIN2, ADV_LANTU, ADV_OTA = f254.ADV_MAIN, f254.ADV_MAIN2, f254.ADV_LANTU, f254.ADV_OTA
SUB = f254.SUB
CHARTER = ".claude/rules/lybra-advisor.md"
SETTINGS = ".claude/settings.local.json"


def _show(text: str) -> None:
    print(text, flush=True)


def _decl() -> dict:
    from tools.aipos_cli.session_settings import declaration

    return declaration()


def _settings(d: Path) -> dict:
    return json.loads((d / SETTINGS).read_text(encoding="utf-8"))


def _rig_sessions(tmp_path, monkeypatch, instances=(ADV_MAIN, ADV_LANTU, ADV_OTA)) -> Path:
    """F254 靶场 + 项目级顾问在治理根、子项目顾问在声明子路径(都在本机 claude-code); 假门接上。"""
    import tools.aipos_cli.confirm_client as cc

    gov = f254._rig(tmp_path, monkeypatch, instances=instances)
    assert f254._redeclare(gov, ADV_MAIN, "--harness", "claude-code", confirm=True)[0] == 0
    assert f254._redeclare(gov, ADV_LANTU, "--harness", "claude-code", confirm=True)[0] == 0
    monkeypatch.setattr(cc, "GateClient", f254._FakeGate)
    f254._FakeGate.calls = []
    return gov


def _sync(gov: Path, *extra: str) -> tuple[int, str, str]:
    return _cli("sync", "--harness-root", str(gov), "--workspace-root", str(gov), *extra)


def _roles_list(gov: Path) -> tuple[str, dict]:
    rc, out, err = _cli("roles", "--workspace-root", str(gov), "list")
    assert rc == 0, err
    rc, js, err = _cli("roles", "--workspace-root", str(gov), "list", "--json")
    assert rc == 0, err
    text = out[out.index("顾问会话登记"):]
    return text, {s["instance"]: s["session_settings"] for s in json.loads(js)["advisor_sessions"]}


def _git_untracked(gov: Path) -> list[str]:
    out = subprocess.run(["git", "-c", "core.quotepath=false", "status", "--porcelain", "--untracked-files=all"], cwd=str(gov),
                         capture_output=True, text=True, check=True).stdout
    return [ln[3:] for ln in out.splitlines() if ln.startswith("?? ")]


# ===========================================================================
# 件① + 件② + 件④: 既有会话(F254 已渲染, 无会话设置)→ sync 排除上级章程、记忆不静默切换 → 显式切换两阶段 → 诊断前后对比
# ===========================================================================

def test_existing_sessions_rules_isolated_memory_not_switched_then_explicit_switch(tmp_path, monkeypatch):
    import tools.aipos_cli.session_settings as ss

    gov = _rig_sessions(tmp_path, monkeypatch)
    home = Path(str(tmp_path / "home"))
    lantu = gov / SUB / ADV_LANTU

    # F254 时代的 sync(本卡之前的产品: 不渲染会话设置)= 既有会话
    with monkeypatch.context() as m:
        m.setattr(ss, "applies", lambda kind: False)
        rc, out, err = _sync(gov)
    assert rc == 0, out + err
    assert (gov / CHARTER).is_file() and (lantu / CHARTER).is_file() and not (lantu / SETTINGS).exists()

    before_text, before = _roles_list(gov)
    _show(f"---- 件④ roles list 诊断·修复前(F254 渲染的既有会话)----\n{before_text}")
    assert before[ADV_LANTU]["rules"]["risk"] and before[ADV_LANTU]["rules"]["paths"] == [str(gov / CHARTER)], "子路径会话会加载治理根别的实例的章程"
    assert "✗ 串身份风险: 会加载上级章程" in before_text
    assert before[ADV_LANTU]["memory"]["risk"] and before[ADV_LANTU]["memory"]["shared_with"] == [ADV_MAIN]
    assert before[ADV_MAIN]["memory"]["shared_with"] == [ADV_LANTU] and not before[ADV_MAIN]["rules"]["risk"]

    # F255 sync: 排除上级章程; 既有会话记忆不切换(不写 autoMemoryDirectory)
    rc, out, err = _sync(gov, "--dry-run")
    _show(f"---- sync --dry-run rc={rc} ----\n{out}{err}")
    assert rc == 0 and "会话设置(AIPOS-F255): write" in out and not (lantu / SETTINGS).exists(), "预演零写入"
    rc, out, err = _sync(gov)
    _show(f"---- sync rc={rc} ----\n{out}{err}")
    assert rc == 0
    s_lantu, s_main = _settings(lantu), _settings(gov)
    _show(f"[件① 子路径会话设置 {lantu / SETTINGS}]\n{json.dumps(s_lantu, ensure_ascii=False, indent=2)}")
    decl = _decl()
    ex_key, mem_key = decl["exclude"]["key"], decl["memory"]["key"]
    assert str(gov / CHARTER) in s_lantu[ex_key] and str(lantu / CHARTER) not in s_lantu[ex_key], "排除上级章程, 不排除本会话自己的"
    assert all(Path(p).is_absolute() for p in s_lantu[ex_key]), "官方: 绝对路径按绝对文件路径匹配"
    assert mem_key not in s_lantu and mem_key not in s_main, "既有会话不静默切换记忆目录"
    manifest = json.loads((gov / ".lybra" / f".version-advisor-lantu@{ADV_LANTU}").read_text(encoding="utf-8"))
    _show(f"[本地清单 session_settings] {json.dumps(manifest['session_settings'], ensure_ascii=False)}")
    assert manifest["session_settings"]["memory"] == {"mode": "shared", "source": "existing_kept", "directory": None}

    mid_text, mid = _roles_list(gov)
    _show(f"---- 件④ roles list 诊断·sync 后(章程已隔离, 记忆待显式切换)----\n{mid_text}")
    assert not mid[ADV_LANTU]["rules"]["risk"] and "上级章程: 无串身份风险" in mid_text
    assert mid[ADV_LANTU]["memory"]["risk"] and "lybra roles memory" in mid[ADV_LANTU]["memory"]["command"]

    # 显式切换两阶段: 预演零写入, 列将切到的目录与旧记忆处理建议
    target = home / ".lybra" / "advisor-memory" / CHRIS / ADV_LANTU
    settings_before = (lantu / SETTINGS).read_text(encoding="utf-8")
    rc, out, err = _cli("roles", "memory", "--workspace-root", str(gov), "--instance", ADV_LANTU, "--mode", "isolated")
    _show(f"---- roles memory --mode isolated(预演)rc={rc} ----\n{out}{err}")
    assert rc == 0 and "预览" in out and str(target) in out and "旧记忆处理建议" in out and ADV_MAIN in out
    assert (lantu / SETTINGS).read_text(encoding="utf-8") == settings_before and not target.exists(), "预演零写入"
    rc, out, err = _cli("roles", "memory", "--workspace-root", str(gov), "--instance", ADV_LANTU, "--mode", "isolated", "--confirm")
    _show(f"---- roles memory --mode isolated --confirm rc={rc} ----\n{out}{err}")
    assert rc == 0 and "已写入" in out
    assert _settings(lantu)[mem_key] == f"~/.lybra/advisor-memory/{CHRIS}/{ADV_LANTU}" and target.is_dir()
    assert mem_key not in _settings(gov), "只切本实例"
    rc, out, err = _cli("roles", "memory", "--workspace-root", str(gov), "--instance", ADV_LANTU, "--mode", "isolated", "--confirm")
    assert rc == 1 and "MEMORY_SWITCH_NO_CHANGE" in err

    # 再 sync 沿用显式记录(不回退)
    assert _sync(gov)[0] == 0
    assert _settings(lantu)[mem_key] == f"~/.lybra/advisor-memory/{CHRIS}/{ADV_LANTU}"
    assert _cli("roles", "memory", "--workspace-root", str(gov), "--instance", ADV_MAIN, "--mode", "isolated", "--confirm")[0] == 0

    after_text, after = _roles_list(gov)
    _show(f"---- 件④ roles list 诊断·修复后(两实例均已显式隔离)----\n{after_text}")
    for inst in (ADV_MAIN, ADV_LANTU):
        assert not after[inst]["rules"]["risk"] and not after[inst]["memory"]["risk"] and after[inst]["memory"]["mode"] == "isolated", inst
    assert "✗" not in after_text
    assert after[ADV_OTA]["applies"] is False, "他机会话不适用"


# ===========================================================================
# 件②: 新会话(无本地清单)首次 sync 缺省隔离; 记忆目录按实例、~/ 原值
# ===========================================================================

def test_new_session_isolated_by_default(tmp_path, monkeypatch):
    gov = _rig_sessions(tmp_path, monkeypatch, instances=(ADV_MAIN, ADV_MAIN2, ADV_LANTU, ADV_OTA))
    assert f254._redeclare(gov, ADV_MAIN2, "--harness", "claude-code", "--host", f254.LOCAL, confirm=True)[0] == 0
    rc, out, err = _sync(gov)
    _show(f"---- sync(全新会话)rc={rc} ----\n{out}{err}")
    assert rc == 0
    mem_key = _decl()["memory"]["key"]
    dirs = {ADV_MAIN: gov, ADV_MAIN2: gov / SUB / ADV_MAIN2, ADV_LANTU: gov / SUB / ADV_LANTU}
    seen = set()
    for inst, d in dirs.items():
        value = _settings(d)[mem_key]
        _show(f"[{inst}] {mem_key} = {value}")
        assert value == f"~/.lybra/advisor-memory/{CHRIS}/{inst}" and value not in seen
        seen.add(value)
        assert (Path(str(tmp_path / "home")) / value[2:]).is_dir(), "记忆落点在声明位置"
    _text, view = _roles_list(gov)
    assert all(not view[i]["memory"]["risk"] and not view[i]["rules"]["risk"] for i in dirs), view
    # 再 sync / 再预演 = 无变化(幂等)
    rc, out, err = _sync(gov, "--dry-run", "--json")
    run = json.loads(out)
    actions = {e.get("instance") or e["workstation"]["instance"]: (e["result"].get("session_settings") or {}).get("settings", {}).get("action")
               for e in run["workstations"] if e.get("result")}
    _show(f"[再预演 会话设置动作] {actions}")
    assert {actions[i] for i in dirs} == {"unchanged"}


# ===========================================================================
# 件③: 渲染物不进 git 视野; 用户文件可见; 用户自己的设置文件不覆盖
# ===========================================================================

def test_rendered_claude_dir_invisible_to_git_user_files_visible(tmp_path, monkeypatch):
    gov = _rig_sessions(tmp_path, monkeypatch)
    user_settings = gov / SETTINGS
    user_settings.parent.mkdir(parents=True)
    user_settings.write_text('{"permissions": {"allow": ["Bash(ls:*)"]}}\n', encoding="utf-8")  # 用户自己的会话权限文件
    rc, out, err = _sync(gov)
    _show(f"---- sync rc={rc} ----\n{out}{err}")
    assert rc == 0
    assert user_settings.read_text(encoding="utf-8") == '{"permissions": {"allow": ["Bash(ls:*)"]}}\n', "用户设置文件不覆盖"
    assert "是用户文件(非 Lybra 渲染), 产品不覆盖" in out and _decl()["exclude"]["key"] in out, "出声并给并入键的出口"
    ignore = (gov / ".claude" / ".gitignore").read_text(encoding="utf-8")
    _show(f"[件③ {gov / '.claude/.gitignore'}]\n{ignore}")
    assert "/rules/lybra-advisor.md" in ignore and "/.gitignore" in ignore and "/settings.local.json" not in ignore, "只列产品渲染物"
    (gov / ".claude" / "notes.md").write_text("mine\n", encoding="utf-8")
    (gov / ".claude" / "skills" / "my-own").mkdir(parents=True)
    (gov / ".claude" / "skills" / "my-own" / "SKILL.md").write_text("---\nname: my-own\n---\n", encoding="utf-8")
    untracked = _git_untracked(gov)
    _show(f"[件③ git status --porcelain --untracked-files=all 未跟踪] {untracked}")
    claude = sorted(p for p in untracked if p.startswith(".claude/"))
    assert claude == [".claude/notes.md", ".claude/settings.local.json", ".claude/skills/my-own/SKILL.md"], "渲染物不可见, 用户文件可见"
    assert not any(p.startswith(f"{SUB}/") for p in untracked), "子路径整目录忽略(F253)不变"


def test_user_ignore_file_not_overwritten_and_switch_refusals(tmp_path, monkeypatch):
    from tools.aipos_cli.session_settings import MemorySwitchError, switch_memory

    gov = _rig_sessions(tmp_path, monkeypatch)
    (gov / ".claude").mkdir()
    (gov / ".claude" / ".gitignore").write_text("my-scratch/\n", encoding="utf-8")
    with pytest.raises(MemorySwitchError) as exc:
        switch_memory(gov, instance=ADV_LANTU, mode="isolated", confirm=False)
    _show(f"[未 sync 拒] {exc.value}")
    assert exc.value.code == "MEMORY_SWITCH_NOT_SYNCED"
    with pytest.raises(MemorySwitchError) as exc:
        switch_memory(gov, instance=ADV_OTA, mode="isolated", confirm=False)
    assert exc.value.code == "MEMORY_SWITCH_NOT_APPLICABLE", "他机 codex 会话不适用"
    rc, out, err = _sync(gov)
    assert rc == 0 and "忽略文件" in out and "是用户文件" in out
    assert (gov / ".claude" / ".gitignore").read_text(encoding="utf-8") == "my-scratch/\n"
    # 设置文件被用户改过(非 Lybra 渲染物)= 切换拒, 不覆盖
    lantu = gov / SUB / ADV_LANTU
    (lantu / SETTINGS).write_text('{"model": "x"}\n', encoding="utf-8")
    with pytest.raises(MemorySwitchError) as exc:
        switch_memory(gov, instance=ADV_LANTU, mode="shared", confirm=True)
    _show(f"[用户文件拒] {exc.value}")
    assert exc.value.code == "MEMORY_SWITCH_USER_FILE" and (lantu / SETTINGS).read_text(encoding="utf-8") == '{"model": "x"}\n'


# ===========================================================================
# 声明与判定单元
# ===========================================================================

def test_exclude_list_covers_every_ancestor_and_charter_kind(tmp_path):
    from tools.aipos_cli.session_settings import ancestor_charter_paths, charter_relative_paths, desired_settings

    rels = charter_relative_paths("claude-code")
    _show(f"[claude-code 章程落点] {rels}")
    assert CHARTER in rels and ".claude/rules/lybra-executor.md" in rels
    d = (tmp_path / "g" / SUB / "x").resolve()
    paths = ancestor_charter_paths(d, "claude-code")
    assert len(paths) == len(d.parents) * len(rels) and str(d.parent.parent.parent / CHARTER) in paths and str(d / CHARTER) not in paths
    want = desired_settings(d, kind="claude-code", project="p", instance="i.p.h", memory_mode="shared")
    assert set(want) == {_decl()["exclude"]["key"]}, "共享 = 不写记忆键(Claude Code 缺省)"
    with pytest.raises(ValueError):
        desired_settings(d, kind="claude-code", project="p", instance="i", memory_mode="nope")


def test_glob_and_effective_settings(tmp_path, monkeypatch):
    from tools.aipos_cli.session_settings import effective_settings, is_excluded, memory_view

    assert is_excluded("/a/b/.claude/rules/lybra-advisor.md", ["**/b/.claude/rules/*.md"])
    assert is_excluded("/a/b/.claude/rules/lybra-advisor.md", ["/a/b/.claude/rules/lybra-advisor.md"])
    assert not is_excluded("/a/b/.claude/rules/lybra-advisor.md", ["/a/*/lybra-advisor.md", "/a/b/CLAUDE.md"]), "* 不跨目录"
    home = tmp_path / "home"
    (home / ".claude").mkdir(parents=True)
    monkeypatch.setenv("HOME", str(home))
    sess = tmp_path / "s"
    (sess / ".claude").mkdir(parents=True)
    (home / ".claude" / "settings.json").write_text(json.dumps({"claudeMdExcludes": ["/u"], "autoMemoryDirectory": "~/user-mem"}),
                                                     encoding="utf-8")
    (sess / ".claude" / "settings.local.json").write_text(json.dumps({"claudeMdExcludes": ["/l"], "autoMemoryDirectory": "~/local-mem"}),
                                                          encoding="utf-8")
    eff = effective_settings(sess)
    _show(f"[层级合并] {eff}")
    assert [p for p, _ in eff["excludes"]] == ["/l", "/u"], "数组跨层合并"
    assert eff["memory"][0] == "~/local-mem" and memory_view(sess, effective=eff)["dir"] == str(home / "local-mem"), "local 先于 user"
    (sess / ".claude" / "settings.json").write_text(json.dumps({"autoMemoryEnabled": False}), encoding="utf-8")
    assert memory_view(sess)["mode"] == "disabled"
    (sess / ".claude" / "settings.json").write_text("{bad", encoding="utf-8")
    with pytest.raises(ValueError, match="不是合法 JSON"):
        effective_settings(sess)


def test_declaration_fail_closed_and_single_source(monkeypatch):
    import tools.schema_loader as sl
    from tools.aipos_cli.session_settings import declaration

    real = sl.load_schema

    def broken(name: str):
        data = json.loads(json.dumps(real(name)))
        if name == "distribution":
            data["harness_semantics"]["session_settings"]["memory"]["directory"] = "relative/{project}/{instance}"
        return data

    monkeypatch.setattr(sl, "load_schema", broken)
    with pytest.raises(sl.SchemaLoadError, match="memory.directory"):
        declaration()
    monkeypatch.setattr(sl, "load_schema", real)
    decl = declaration()
    _show(f"[声明 relative_path] {decl['relative_path']}; 记忆目录 {decl['memory']['directory']}; 出处 {len(decl['evidence']['sources'])} 条")
    # 官方键名只在声明里: 产品代码不写死 claudeMdExcludes / autoMemoryDirectory / 记忆目录
    hits = subprocess.run(["git", "grep", "-n", "-e", "claudeMdExcludes", "-e", "autoMemoryDirectory", "-e", "advisor-memory", "--", "tools/"],
                          cwd=REPO_ROOT, capture_output=True, text=True).stdout.splitlines()
    code_hits = [h for h in hits if not h.split(":", 2)[2].lstrip().startswith(("#", '"""')) and "texts" not in h]
    _show(f"[tools/ 内官方键名出现处] {code_hits}")
    assert [h for h in code_hits if '"claudeMdExcludes"' in h or '"autoMemoryDirectory"' in h or "advisor-memory/" in h] == []
    from tools.aipos_cli.aipos_cli import build_parser
    from tools.aipos_cli.session_settings import memory_modes

    parser = build_parser()
    roles = next(a for a in parser._subparsers._group_actions[0].choices["roles"]._actions if hasattr(a, "choices") and a.choices and "memory" in a.choices)
    mode = next(a for a in roles.choices["memory"]._actions if "--mode" in a.option_strings)
    assert list(mode.choices) == memory_modes() == list(decl["memory"]["modes"]), "--mode 值域投影声明"
