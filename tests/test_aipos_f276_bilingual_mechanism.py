"""AIPOS-F276 — P4 双语机制: 语言选择键 + 双语 texts 标准与唯一读取器 + 开工稿 / 顾问启动稿「用户语言」行 + 看板缺省语言读 home 配置。

靶场(禁真门 / 真治理根 / 真工位 / 真 home 配置): 临时 HOME(monkeypatch)+ 临时 home 根 + 临时治理根 + 临时产品仓;
CLI 一律进程内 main(); 看板为进程内线程服务(F201 board 夹具, 门替身 InProcessGate), 不起子进程。

件① 优先序 --lang > LYBRA_LANG > home 配置 language > locale.default, 四档各一用例 + 每档非法值(拒因带来源与出口, 不回退);
   `lybra config set-language` 写 home 配置(保留其余键 / 值不变不改写 / 非法拒不写); 全局 --lang 只在根解析器注册一处。
件② 迁移过的双语块(workstation_plan.texts / owner_inbox relay.texts / config.schema language.texts)走唯一读取器 i18n.texts;
   全部 schema 里的双语块 zh / en 键集相等(守卫); `--lang en owner inbox` 出英文 relay 话术(与看板英文同段逐字一致), 缺省中文;
   workstation-plan 按同一解析出英文。
件③ lybra kickoff 开工稿头行后「用户语言」行(中 / 英原文); 看板顾问启动稿(/api/generate/advisor-prompt)同一行。
件④ home 配置 language=en 后看板首页 <html lang="en"> 与英文文案; URL 参数(浏览器已选)仍优先; 环境变量覆盖 home 配置;
   i18n.js 驱动的旧页带 data-board-default-lang。
"""
from __future__ import annotations

import copy
import inspect
import json
import re
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from test_aipos_f201_owner_relay import (  # noqa: E402,F401  — F201 靶场(home 根 + 治理根 + 进程内门 + 看板线程)唯一来源
    ADV_LX, LYBRA_ADV, SEP, _board_relays, _item, _request, board, rig)
from test_aipos_f180_grant_request import _cli, _show  # noqa: E402  — 进程内 CLI 单源
from tools.aipos_cli.verb_contract import declared_exit_code  # noqa: E402

VERB = "lybra_config_set_language"


def _home_config(home: Path) -> dict:
    path = home / ".lybra" / "config.json"
    return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}


@pytest.fixture
def clean_lang_env(monkeypatch: pytest.MonkeyPatch):
    from tools.aipos_cli.i18n import LANG_ENV

    monkeypatch.delenv(LANG_ENV, raising=False)
    return monkeypatch


# ===========================================================================
# 件① 语言选择: 四档优先序 + 非法值 + 写口 + 根解析器一处注册
# ===========================================================================

def test_item1_precedence_four_tiers_and_invalid_values(tmp_path, clean_lang_env):
    from tools.aipos_cli import i18n

    home = tmp_path / "home"
    (home / ".lybra").mkdir(parents=True)
    (home / ".lybra" / "config.json").write_text(json.dumps({"config_version": 2, "language": "en"}), encoding="utf-8")
    env = {"HOME": str(home)}
    default = i18n.default_language()
    assert default == i18n.locale_declaration()["default"] and i18n.supported_languages() == list(i18n.locale_declaration()["supported"])
    other = next(c for c in i18n.supported_languages() if c != default)
    cases = [
        ("① 全局 --lang 最高(环境变量与 home 配置都给了别的值)", default, {**env, i18n.LANG_ENV: other}, None, (default, "cli_flag")),
        ("② LYBRA_LANG 次之(home 配置 = en)", None, {**env, i18n.LANG_ENV: default}, None, (default, "env")),
        ("③ home 配置 language", None, env, None, ("en", "home_config")),
        ("④ 都没有 = 看板 locale.default", None, {"HOME": str(tmp_path / "no-config")}, None, (default, "default")),
    ]
    for label, cli, environ, home_cfg, expected in cases:
        got = i18n.resolve_language_with_source(cli, environ, home_cfg)
        _show(f"[件① {label}] cli={cli!r} env={ {k: v for k, v in environ.items() if k != 'HOME'} } → {got}")
        assert got == expected, label
    # argparse Namespace(全局 --lang 的 dest)同样是第一档
    ns = __import__("argparse").Namespace(**{i18n.ARGS_DEST: other})
    assert i18n.resolve_language(ns, {"HOME": str(tmp_path / "no-config")}) == other
    # 非法值: 命中档报错(带来源与出口), 不回退到下一档
    bad = [
        ("--lang fr", "fr", env, None, "cli_flag", "改用 --lang"),
        ("LYBRA_LANG=fr", None, {**env, i18n.LANG_ENV: "fr"}, None, "env", "把 LYBRA_LANG 改为"),
        ("home 配置 language=jp", None, {"HOME": str(home)}, {"language": "jp"}, "home_config", "lybra config set-language"),
    ]
    for label, cli, environ, home_cfg, source, exit_hint in bad:
        with pytest.raises(i18n.LanguageError) as exc:
            i18n.resolve_language(cli, environ, home_cfg)
        _show(f"[件① 非法 {label}] {exc.value}")
        assert exc.value.source == source and exit_hint in str(exc.value) and "出口" in str(exc.value)
    # 看板(无 --lang 档): 非法 home 配置 = 用 locale.default 并在 stderr 点名(不静默)
    assert i18n.board_default_language({"HOME": str(home)}) == "en"


def test_item1_config_set_language_writes_home_config_only(tmp_path, clean_lang_env):
    home = tmp_path / "home"
    (home / ".lybra").mkdir(parents=True)
    cfg = home / ".lybra" / "config.json"
    cfg.write_text(json.dumps({"config_version": 2, "home_root": str(tmp_path / "gh"), "active_project": "keep"}), encoding="utf-8")
    clean_lang_env.setenv("HOME", str(home))
    rc, out = _cli("config", "set-language", "en")
    _show(f"[件① lybra config set-language en] exit {rc}\n{out}")
    assert rc == declared_exit_code(VERB, "ok") and "User language not set -> en" in out
    assert f"User language = en (source: home config {home / '.lybra' / 'config.json'} key language)" in out  # 写入后实际生效与来源
    clean_lang_env.setenv("LYBRA_LANG", "zh")
    assert "用户语言 = zh(来源: 环境变量 LYBRA_LANG)" in _cli("config", "set-language", "en")[1]  # 环境变量仍盖过 home 配置, 回显点明
    clean_lang_env.delenv("LYBRA_LANG")
    assert _home_config(home) == {"config_version": 2, "home_root": str(tmp_path / "gh"), "active_project": "keep", "language": "en"}
    before = cfg.read_bytes()
    rc, out = _cli("config", "set-language", "en")
    _show(f"[件① 再设同值] exit {rc}\n{out}")
    assert rc == 0 and "nothing rewritten" in out and cfg.read_bytes() == before
    rc, out = _cli("config", "set-language", "fr")
    _show(f"[件① 非法值 fr] exit {rc}\n{out}")
    assert rc == declared_exit_code(VERB, "refused") and "'fr'" in out and "出口" in out and cfg.read_bytes() == before
    rc, out = _cli("--lang", "xx", "config", "set-language", "zh")
    _show(f"[件① 全局 --lang 非法] exit {rc}\n{out}")
    assert rc == 2 and "全局 --lang" in out and cfg.read_bytes() == before
    rc, out = _cli("--lang", "zh", "config", "set-language", "zh", "--json")
    data = json.loads(out)
    _show(f"[件① --lang zh 回显 + 写回 zh] exit {rc} {data}")
    assert rc == 0 and data["changed"] and data["before"] == "en" and data["after"] == "zh" and data["message"].startswith("用户语言 en → zh")
    assert _home_config(home)["language"] == "zh" and _home_config(home)["active_project"] == "keep"


def test_item1_global_lang_registered_once_on_root_parser():
    import argparse

    from tools.aipos_cli.aipos_cli import build_parser
    from tools.aipos_cli.i18n import ARGS_DEST

    parser = build_parser()
    found: list[tuple[str, str, object]] = []

    def walk(p: argparse.ArgumentParser, path: str) -> None:
        for action in p._actions:  # noqa: SLF001 - argparse 无公开枚举口
            if "--lang" in action.option_strings:
                found.append((path or "<root>", action.dest, action.default))
            if isinstance(action, argparse._SubParsersAction):  # noqa: SLF001
                for name, sub in action.choices.items():
                    walk(sub, f"{path} {name}".strip())

    walk(parser, "")
    _show(f"[件① --lang 注册处] {found}")
    assert found[0] == ("<root>", ARGS_DEST, None)
    assert all(dest == ARGS_DEST and default is argparse.SUPPRESS for _p, dest, default in found[1:])  # 兼容旗标只写同一 dest
    assert getattr(parser.parse_args(["--lang", "en", "project", "workstation-plan", "p"]), ARGS_DEST) == "en"
    assert getattr(parser.parse_args(["project", "workstation-plan", "p", "--lang", "en"]), ARGS_DEST) == "en"
    assert getattr(parser.parse_args(["--lang", "en", "project", "workstation-plan", "p"]), ARGS_DEST) == "en"
    assert getattr(parser.parse_args(["owner", "inbox"]), ARGS_DEST) is None


# ===========================================================================
# 件② 双语 texts 标准 + 唯一读取器 + 守卫
# ===========================================================================

def _bilingual_blocks(node, path: str, supported: set[str], out: list[tuple[str, dict]]) -> None:
    if isinstance(node, dict):
        if set(node) == supported and all(isinstance(v, dict) for v in node.values()):
            out.append((path, node))
            return
        for key, value in node.items():
            _bilingual_blocks(value, f"{path}.{key}" if path else key, supported, out)
    elif isinstance(node, list):
        for i, value in enumerate(node):
            _bilingual_blocks(value, f"{path}[{i}]", supported, out)


def test_item2_every_bilingual_block_has_equal_key_sets():
    """守卫: schema 里每个 {<语言>: {…}} 双语块(语言集 = locale.supported)各语言键集(含嵌套路径)相等、叶子为串。迁移过的三块必在其中。"""
    from tools.aipos_cli.i18n import block_problems, supported_languages

    supported = set(supported_languages())
    blocks: list[tuple[str, dict]] = []
    for path in sorted((REPO_ROOT / "schema").glob("*.schema.json")):
        _bilingual_blocks(json.loads(path.read_text(encoding="utf-8")), path.name, supported, blocks)
    report = {p: (len(b["zh"]), len(b["en"]), block_problems(b)) for p, b in blocks}
    _show("[件② 双语块键集守卫] " + json.dumps(report, ensure_ascii=False, indent=1))
    assert not {p: r for p, r in report.items() if r[2]}, "双语块 zh / en 键集不等或叶子非串"
    for migrated in ("verbs.schema.json.verbs.lybra_project_workstation_plan.texts", "verbs.schema.json.verbs.lybra_owner_inbox.relay.texts",
                     "config.schema.json.language.texts"):
        assert migrated in report, migrated


def test_item2_single_reader_fails_closed_and_migrated_callers_use_it():
    from tools.aipos_cli import i18n, owner_inbox, workstation_plan
    from tools.schema_loader import SchemaLoadError

    block = workstation_plan.declaration()["texts"]
    assert i18n.texts(block, "en", where="t") is block["en"] and i18n.texts(block, None, where="t") is block[i18n.default_language()]
    assert workstation_plan.texts("en") == block["en"] and owner_inbox.relay_texts("en") == owner_inbox.relay_declaration()["texts"]["en"]
    with pytest.raises(i18n.LanguageError):
        i18n.texts(block, "fr", where="t")
    broken = copy.deepcopy(block)
    broken["en"].pop("title")
    broken["en"]["steps"]["decision"].pop("why")
    with pytest.raises(SchemaLoadError) as exc:
        i18n.texts(broken, "zh", where="probe.texts")
    _show(f"[件② 缺 en 键 = 声明校验失败] {exc.value}")
    assert "en 缺" in str(exc.value) and "title" in str(exc.value) and "steps.decision.why" in str(exc.value)
    extra = {**copy.deepcopy(block), "fr": copy.deepcopy(block["en"])}
    assert any("locale.supported" in p for p in i18n.block_problems(extra))
    # 迁移过的读取口只经唯一读取器(不再自己按语言下标取表)
    for fn in (workstation_plan.texts, owner_inbox.relay_texts):
        src = inspect.getsource(fn)
        assert "bilingual_texts(" in src and "table[" not in src, fn.__name__


def test_item2_owner_inbox_relay_follows_user_language(board, clean_lang_env):
    home = board["home"]
    data = json.loads(_cli("owner", "inbox", "--home-root", str(home), "--json")[1])
    fork = _item(data, "LX-21B", "loop_owner_needed")
    rc, out = _cli("owner", "decide", "--item", fork["item_id"], "--choice", "continue", "--relayed-by", LYBRA_ADV, "--owner-words",
                   "按方案 A 继续", "--home-root", str(home), "--confirm")
    assert rc == 0, out

    def relay(text: str, tell: str) -> str:
        lines = text.splitlines()
        start = next(i for i, ln in enumerate(lines) if ln == f"  {tell} {ADV_LX}:")
        block = []
        for ln in lines[start:]:
            if not ln.startswith("  "):
                break
            block.append(ln[2:])
        return "\n".join(block)

    rc_zh, zh = _cli("owner", "inbox", "--home-root", str(home))
    rc_en, en = _cli("--lang", "en", "owner", "inbox", "--home-root", str(home))
    _show(f"---- 件② 缺省(中文)owner inbox 转达段原文 ----\n{relay(zh, '告诉')}")
    _show(f"---- 件② lybra --lang en owner inbox 转达段原文 ----\n{relay(en, 'Tell')}")
    assert rc_zh == rc_en and SEP in zh and "Decided, to relay" in en and SEP not in en
    assert relay(en, "Tell").splitlines()[1].startswith("The Owner has handled the 1 thing(s) below")
    assert zh.splitlines()[0] == en.splitlines()[0]  # 未迁移段(收件箱头行)不翻译、不受影响
    # 看板英文同段(浏览器选 en)与 CLI --lang en 逐字一致
    status, page = _request(board, "/owner/inbox?lang=en")
    assert status == 200 and _board_relays(page)[ADV_LX] == relay(en, "Tell")
    js = json.loads(_cli("--lang", "en", "owner", "inbox", "--home-root", str(home), "--json")[1])
    assert next(g for g in js["relays"] if g["advisor"] == ADV_LX)["lines"][0] == f"Tell {ADV_LX}:"
    # 同一解析: 环境变量 / home 配置 / --lang 覆盖
    clean_lang_env.setenv("LYBRA_LANG", "en")
    assert "Tell " in _cli("owner", "inbox", "--home-root", str(home))[1]
    clean_lang_env.delenv("LYBRA_LANG")
    assert _cli("config", "set-language", "en")[0] == 0
    assert relay(_cli("owner", "inbox", "--home-root", str(home))[1], "Tell") == relay(en, "Tell")
    assert SEP in _cli("--lang", "zh", "owner", "inbox", "--home-root", str(home))[1]
    clean_lang_env.setenv("LYBRA_LANG", "de")
    rc, err = _cli("owner", "inbox", "--home-root", str(home))
    _show(f"[件② LYBRA_LANG=de owner inbox] exit {rc}: {err.strip()}")
    assert rc == declared_exit_code("lybra_owner_inbox", "usage") and "LYBRA_LANG" in err


def test_item2_workstation_plan_follows_user_language(tmp_path, clean_lang_env):
    from tools.aipos_cli import workstation_plan as wp

    home = tmp_path / "home"
    home.mkdir()
    clean_lang_env.setenv("HOME", str(home))
    hroot = tmp_path / "gh"
    assert _cli("project", "new", "probe", "--home-root", str(hroot), "--actor", "owner.probe")[0] == 0
    argv = ["project", "workstation-plan", "probe", "--home-root", str(hroot)]
    zh_title, en_title = wp.texts("zh")["title_none"], wp.texts("en")["title_none"]
    rc, zh = _cli(*argv)
    rc_en, en = _cli("--lang", "en", *argv)
    _show(f"[件② workstation-plan 缺省] {zh.splitlines()[0]}\n[件② --lang en] {en.splitlines()[0]}")
    assert rc == rc_en == 0 and zh.splitlines()[0].startswith(zh_title) and en.splitlines()[0].startswith(en_title)
    assert _cli(*argv, "--lang", "en")[1] == en  # 子命令兼容旗标 = 同一 dest
    clean_lang_env.setenv("LYBRA_LANG", "en")
    assert _cli(*argv)[1] == en
    clean_lang_env.setenv("LYBRA_LANG", "de")
    rc, err = _cli(*argv)
    assert rc == wp.declaration()["exit_codes"]["usage"]["code"] and "'de'" in err


# ===========================================================================
# 件③ 开工稿 / 顾问启动稿「用户语言」行
# ===========================================================================

def test_item3_kickoff_user_language_line(tmp_path, clean_lang_env):
    from test_aipos_f187_kickoff_worktree_cleanup import CHRIS_CARD, CHRIS_EXEC, _chris_rig
    from tools.aipos_cli.agent_profiles import load_agent_profiles
    from tools.aipos_cli.queue_mutation import mutate_queue_task

    from test_aipos_f78_engine_agnostic import _write

    gov = _chris_rig(tmp_path, clean_lang_env)["gov"]
    entry = tmp_path / "gate-bin" / "lybra"
    _write(entry, f"#!{sys.executable}\n")
    entry.chmod(0o755)
    clean_lang_env.setenv("PATH", f"{entry.parent}:/usr/bin:/bin")
    claimed = mutate_queue_task(gov, "claim", task_id=CHRIS_CARD, actor=CHRIS_EXEC, dry_run=False, profiles=load_agent_profiles(gov),
                                with_records=True)
    assert claimed.get("moved"), claimed
    argv = ["kickoff", "--task-id", CHRIS_CARD, "--workspace-root", str(gov)]
    rc, zh = _cli(*argv)
    assert rc == 0, zh
    assert _cli("config", "set-language", "en")[0] == 0  # 临时 HOME 的 home 配置
    rc_en, en = _cli(*argv)
    rc_flag, flag = _cli("--lang", "zh", *argv)
    zh_lines, en_lines = zh.splitlines(), en.splitlines()
    _show(f"---- 件③ kickoff 头两行(缺省) ----\n{chr(10).join(zh_lines[:2])}\n---- 件③ kickoff 头两行(home 配置 language=en) ----\n"
          f"{chr(10).join(en_lines[:2])}")
    assert zh_lines[1] == "用户语言 = 中文(zh); 对用户(Owner)说话一律用该语言"
    assert en_lines[1] == "User language = English (en); always speak to the user (Owner) in this language"
    assert zh_lines[0].startswith(f"你是 ") and zh_lines[0] == en_lines[0]
    assert zh_lines[2:] == en_lines[2:] and flag == zh and rc_en == rc_flag == 0  # 其余逐字不变; --lang 覆盖 home 配置
    data = json.loads(_cli(*argv, "--json")[1])
    assert data["values"]["user_language_line"] == en_lines[1]


def test_item3_board_advisor_prompt_user_language_line(board, clean_lang_env):
    status, body = _request(board, "/api/generate/advisor-prompt?workspace=0&locale=zh")
    zh = json.loads(body)["data"]["prompt"]
    status_d, body_d = _request(board, "/api/generate/advisor-prompt?workspace=0")
    default = json.loads(body_d)["data"]["prompt"]
    assert _cli("config", "set-language", "en")[0] == 0
    en = json.loads(_request(board, "/api/generate/advisor-prompt?workspace=0")[1])["data"]["prompt"]
    _show(f"---- 件③ 看板顾问启动稿头两行(locale=zh) ----\n{chr(10).join(zh.splitlines()[:2])}\n"
          f"---- 件③ 同上(无 locale, home 配置 language=en) ----\n{chr(10).join(en.splitlines()[:2])}")
    assert status == status_d == 200 and zh.splitlines()[1] == "用户语言 = 中文(zh); 对用户(Owner)说话一律用该语言" and default == zh
    assert en.splitlines()[1] == "User language = English (en); always speak to the user (Owner) in this language"
    assert en.startswith("You are the Advisor")


# ===========================================================================
# 件④ 看板缺省语言读 home 配置(浏览器已选仍优先)
# ===========================================================================

def test_item4_board_default_language_reads_home_config(board, clean_lang_env):
    def head(page: str) -> str:
        return re.search(r"<html[^>]*>.*?<nav>.*?</nav>", page, re.S).group(0)

    status, zh = _request(board, "/owner/home")
    assert status == 200 and '<html lang="zh">' in zh and ",d=\"zh\"" in zh
    rc, out = _cli("config", "set-language", "en")
    assert rc == 0, out
    status, en = _request(board, "/owner/home")
    _show(f"---- 件④ home 配置 language=en 后看板首页(无 lang 参数)头部原文 ----\n{head(en)}")
    assert status == 200 and '<html lang="en">' in en and 'data-nav="home" aria-current="page">Home</a>' in en and ",d=\"en\"" in en
    assert 'data-set-lang="zh" href="?lang=zh">中文</a>' in en  # 切换链接 = 另一种语言
    assert 'href="/owner/project"' in en  # 站内链接不带缺省语言参数
    status, chosen = _request(board, "/owner/home?lang=zh")
    _show(f"---- 件④ 浏览器已选 zh(?lang=zh)仍优先 ----\n{head(chosen)}")
    assert '<html lang="zh">' in chosen and 'href="/owner/project?lang=zh"' in chosen
    clean_lang_env.setenv("LYBRA_LANG", "zh")
    assert '<html lang="zh">' in _request(board, "/owner/home")[1]  # 环境变量覆盖 home 配置
    clean_lang_env.delenv("LYBRA_LANG")
    legacy = _request(board, "/overview.html")[1]
    assert 'data-board-default-lang="en"' in legacy.split("\n", 3)[1]  # i18n.js 驱动的旧页: 浏览器未选时用该缺省
    i18n_js = (REPO_ROOT / "web" / "board" / "static" / "i18n.js").read_text(encoding="utf-8")
    assert "localStorage.getItem('lybra_lang') || document.documentElement.getAttribute('data-board-default-lang') || 'zh'" in i18n_js
