"""AIPOS-F277 — P4 Owner 面双语: setup check / onboarding guide·check / owner inbox·recommend / project new / serve install·uninstall 的
Owner 可见输出声明化(双语 texts / 译文覆盖层)并接 F276 语言选择; 看板登录页纳入看板 i18n 通道。

靶场(禁真门 / 真治理根 / 真工位 / 真 home 配置): 临时 HOME(monkeypatch)+ 临时 home 根 + 临时治理根; 工具链 = 夹具替身(node / git / pi 固定版本,
python3 = 真解释器的链接, 版本串归一化); 门地址 = 已关闭端口; CLI 一律进程内 main()。收件箱场景复用 F201 靶场(rig)。

件①② 验收: home 配置 language=en 时上列命令输出(含向导第 0 步与十一步)无中日韩字符(收件箱里卡标题 / 运行消息 / Owner 原话等用户数据除外,
   以靶场写入的卡面与运行记录为准); 缺省语言输出与改前逐字一致——比对的是改前(main a1d7faf)同一场景输出归一化后的 sha256(不在测试里复制原文;
   有意改中文措辞的卡须同步更新 ZH_DIGESTS)。
件③ 看板登录页: data-board-default-lang 跟 home 配置; 文案键在 static/i18n.js 中英各一份; 页内无写死的中日韩用户可见文案。
件④ 双语块 / 覆盖层守卫 + 术语表(英文译文不出现术语表 avoid 列的旧译)。
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import socket
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from test_aipos_f180_grant_request import _cli  # noqa: E402  — 进程内 CLI 单源
from test_aipos_f201_owner_relay import LYBRA_ADV, _item, rig  # noqa: E402,F401  — F201 靶场唯一来源

CJK = re.compile(r"[　-〿㐀-䶿一-鿿＀-￯]+")
PROJECT = "demo"
#: 改前(main a1d7fafc4ff4)同一场景缺省语言输出, 归一化后的 sha256(F277_RECORD=1 跑本文件 test_zh_output_unchanged 打印)
# AIPOS-F280 件②: 向导第 3 步 grant sign 加 --project <项目>(门的显式目标项目)与 PROJECT_AMBIGUOUS 出口行, guide / guide_skip / guide_after_new 三份摘要随之更新(其余逐字不变)
ZH_DIGESTS = {
    "setup_check": "074cb9af4537dd71b6a61ec83dddd776dc543f130b56490200d19ebf5a72aff4",
    "guide": "1336e1fb5fc3e92559abe5f6f86f53cc2c30da675f3af4c361b17196e56d96c0",
    "guide_skip": "d6f697a40499252446047060151e152102452ce380550e1b9dd20881987aba8e",
    "project_new": "49def277f1cc2fb99cdaa93bdc4874a8e8a991e603a54ff682a8e4eb07e76d79",
    "guide_after_new": "066611258a5aa87dafb7677b5be75f139a8083a779f012d300ef070b5955dd34",
    "onboarding_check": "8f292a54605a675404291be970436a75ba6bfd07eeaae2199202a7eeff6797a1",
    "serve_install": "ae07452642661b7a10b456b5a4953d4a120c3ccb80e39b05a77842e3e945e899",
    "serve_uninstall": "5bfe047723acb8fb7fe96915db3e37af397a8132c8fbbf4957c705411dc24792",
    # AIPOS-F283 件①: 「卡待 Owner」要定改一句话决定、原文降为「详情」行, 选项行给推荐的选项标【推荐】; 另 _norm 归一裸日期(授权请求缺省到期 =
    # 今天 + N 天, 原摘要次日即不成立); inbox / inbox_decided / inbox_stale 三份摘要随之更新(遗留视图除日期归一外逐字不变)
    "inbox": "d4b9f7c9b0e06b06cb5cfc5848e42a725fdb34793731d23c21a7f92618b3ac1e",
    "inbox_decided": "a95e629bfeb2a11762eda8ed5d96f25b6d884a78f87ff01331955ef4a3e9934b",
    "inbox_stale": "45776ecbc1cf02f989b0636c52578531ea21fea6bb3bf2f6f40fd4fe39ecbcb6",
}


def _show(text: str) -> None:
    print(text, flush=True)


def _closed_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _script(path: Path, body: str) -> None:
    path.write_text("#!/bin/sh\n" + body, encoding="utf-8")
    path.chmod(0o755)


def _toolchain(tmp: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """固定版本的工具链替身(node / git / pi)+ 真 python3 链接; PATH 只含它(结果与本机安装无关)。"""
    bindir = tmp / "f277-bin"
    bindir.mkdir(exist_ok=True)
    py = Path(sys.executable).resolve()
    if not (bindir / "python3").exists():
        os.symlink(py, bindir / "python3")
    _script(bindir / "node", 'echo v20.11.0\n')
    _script(bindir / "git", 'echo "git version 2.43.0"\n')
    _script(bindir / "pi", 'case "$1" in --version) echo 0.85.1;; --list-models) printf "provider  model  context\\nkiwiai  m-one  100K\\n";; esac\nexit 0\n')
    monkeypatch.setenv("PATH", str(bindir))
    return bindir


def _norm(text: str, tmp: Path) -> str:
    """归一化: 临时根 / 代码根 / 解释器 / 主机名 / 时刻 / 事项 ID(仅位置相关, 与语言无关)。"""
    out = text.replace(str(tmp), "<T>").replace(str(REPO_ROOT), "<R>").replace(str(Path(sys.executable).resolve()), "<PY>")
    out = out.replace(sys.executable, "<PY>").replace(".".join(map(str, sys.version_info[:3])), "<PYVER>")
    out = out.replace(socket.gethostname().split(".")[0], "<HOST>")
    out = re.sub(r"\d{4}-\d\d-\d\dT[0-9:.]+(\+00:00|Z)?", "<TS>", out)
    # AIPOS-F283: 裸日期也归一(授权请求缺省到期 = 今天 + N 天, 原摘要只在录制当天成立, 次日 main 同样不过)
    out = re.sub(r"(?<!\d)\d{4}-\d\d-\d\d(?!\d)", "<DAY>", out)
    out = re.sub(r"\d{8}_", "<DATE>_", out)
    out = re.sub(r"OI-[0-9a-f]+", "OI-X", out)
    return re.sub(r"owner-inbox-[0-9a-f]+", "owner-inbox-X", out)


def _sorted_blocks(text: str) -> str:
    """收件箱事项同档位按事项 ID(哈希)排序 = 顺序随运行而变; 按空行分块后排序再比。"""
    return "\n\n".join(sorted(b.strip("\n") for b in text.split("\n\n")))


def _digest(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


@pytest.fixture
def owner_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    home = tmp_path / "owner-home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    for var in ("LYBRA_LANG", "LYBRA_HOME_ROOT", "LYBRA_ACTIVE_PROJECT", "LYBRA_GATE_URL", "LYBRA_CONNECTION_JSON", "AIPOS_WORKSPACE_ROOT",
                "LYBRA_WORKSPACE_ROOT", "LYBRA_DRIVER_INSTANCE", "XDG_CONFIG_HOME"):
        monkeypatch.delenv(var, raising=False)
    hroot = tmp_path / "lhome"
    hroot.mkdir()
    _toolchain(tmp_path, monkeypatch)
    return {"tmp": tmp_path, "home": home, "hroot": hroot, "gate": f"http://127.0.0.1:{_closed_port()}", "monkeypatch": monkeypatch}


def _set_language(home: Path, lang: str | None) -> None:
    path = home / ".lybra" / "config.json"
    if lang is None:
        path.unlink(missing_ok=True)
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"language": lang}), encoding="utf-8")


def _scenario(env: dict, lang: str | None) -> dict[str, str]:
    """一个全新 home 根上的 Owner 面命令序列(与语言无关的同一场景), 返回 {名: 归一化输出}。"""
    env["monkeypatch"].setenv("HOME", str(env["home"]))
    env["monkeypatch"].setenv("LYBRA_HOME_ROOT", str(env["hroot"]))
    _set_language(env["home"], lang)
    tmp, hroot, gate = env["tmp"], env["hroot"], env["gate"]
    out: dict[str, str] = {}

    def run(name: str, *argv: str) -> None:
        rc, text = _cli(*argv)
        out[name] = _norm(text.replace(gate, "<GATE>"), tmp) + f"\n[exit {rc}]"

    run("setup_check", "setup", "check", "--gate-url", gate)
    run("guide", "onboarding", "guide", PROJECT, "--home-root", str(hroot), "--gate-url", gate, "--actor", "tester")
    run("guide_skip", "onboarding", "guide", PROJECT, "--home-root", str(hroot), "--gate-url", gate, "--actor", "tester", "--skip-preflight",
        "--repo", f"{PROJECT}=/srv/{PROJECT}")
    run("project_new", "project", "new", PROJECT, "--home-root", str(hroot), "--actor", "tester")
    run("guide_after_new", "onboarding", "guide", PROJECT, "--home-root", str(hroot), "--gate-url", gate, "--actor", "tester",
        "--skip-preflight")
    run("onboarding_check", "onboarding", "check", PROJECT, "--step", "10", "--home-root", str(hroot))
    run("serve_install", "--workspace-root", str(hroot / PROJECT), "serve", "install", "--dry-run")
    run("serve_uninstall", "--workspace-root", str(hroot / PROJECT), "serve", "uninstall")
    return out


def _inbox_scenario(rig_env: dict, lang: str | None, monkeypatch: pytest.MonkeyPatch) -> dict[str, str]:
    """F201 靶场上的收件箱三视图(待定 / 裁决一项后含转达段 / 遗留视图); 每次用新靶场(裁决会改真相)。"""
    home, tmp = rig_env["home"], rig_env["tmp"]
    monkeypatch.setenv("HOME", str(Path(home).parent))  # 靶场的 HOME(home 根 = <HOME>/lybra-home)
    monkeypatch.delenv("LYBRA_HOME_ROOT", raising=False)
    lang_args = ["--lang", lang] if lang else []
    out: dict[str, str] = {}
    out["inbox"] = _sorted_blocks(_norm(_cli(*lang_args, "owner", "inbox", "--home-root", str(home))[1], tmp))
    data = json.loads(_cli("owner", "inbox", "--home-root", str(home), "--json")[1])
    fork = _item(data, "LX-21B", "loop_owner_needed")
    rc, text = _cli("owner", "decide", "--item", fork["item_id"], "--choice", "continue", "--relayed-by", LYBRA_ADV, "--owner-words",
                    "go with plan A", "--home-root", str(home), "--confirm")
    assert rc == 0, text
    out["inbox_decided"] = _sorted_blocks(_norm(_cli(*lang_args, "owner", "inbox", "--home-root", str(home))[1], tmp))
    out["inbox_stale"] = _sorted_blocks(_norm(_cli(*lang_args, "owner", "inbox", "--home-root", str(home), "--stale-before", "2099-01-01")[1], tmp))
    return out


def _user_data(rig_env: dict) -> str:
    """靶场写入的用户数据(卡面 / 运行记录 / 决策记录原文): 收件箱英文输出里的中日韩字符只许来自这里。"""
    corpus = []
    for path in Path(rig_env["gov"]).rglob("*"):
        if path.is_file() and path.suffix in (".md", ".json", ".yaml", ".yml"):
            corpus.append(path.read_text(encoding="utf-8", errors="replace"))
    return "\n".join(corpus)


# ===========================================================================
# 件② 缺省语言(中文)输出与改前逐字一致
# ===========================================================================

def test_zh_output_unchanged(owner_home, rig, monkeypatch: pytest.MonkeyPatch):
    outs = {**_scenario(owner_home, None), **_inbox_scenario(rig, None, monkeypatch)}
    digests = {name: _digest(text) for name, text in outs.items()}
    if os.environ.get("F277_RECORD"):
        _show("F277_DIGESTS " + json.dumps(digests, indent=1))
        for name, text in outs.items():
            _show(f"---- {name} ----\n{text}")
        return
    for name, text in outs.items():
        _show(f"---- 缺省语言 {name}(归一化)sha256 {digests[name]} ----\n{text}")
    assert digests == ZH_DIGESTS


# ===========================================================================
# 件① language=en: Owner 面命令输出无中日韩字符
# ===========================================================================

def test_english_outputs_have_no_cjk(owner_home):
    outs = _scenario(owner_home, "en")
    for name, text in outs.items():
        _show(f"---- language=en {name} ----\n{text}")
    leaks = {name: CJK.findall(text) for name, text in outs.items() if CJK.search(text)}
    assert not leaks, leaks
    # 向导含第 0 步(前置检查)与十一步; project new 的首份阶段快照名随用户语言
    assert "── Pre-flight check:" in outs["guide"] and all(f"── Step {n}:" in outs["guide"] for n in range(1, 12))
    assert "_project-created.md" in outs["project_new"]
    # 缺省(中文)与英文是同一场景、同样的命令行(命令名 / 参数 / 路径不译)
    zh = _scenario(owner_home, None)
    zh_cmds = [ln.strip() for ln in zh["guide_after_new"].splitlines() if ln.strip().startswith("lybra ")]
    en_cmds = [ln.strip() for ln in outs["guide_after_new"].splitlines() if ln.strip().startswith("lybra ")]
    assert len(zh_cmds) == len(en_cmds) and len(zh_cmds) > 20
    from tools.aipos_cli.i18n import owner_block

    ph = {code: owner_block("onboarding_guide")[code]["card_policy"]["pattern_placeholder"] for code in ("zh", "en")}
    differ = [(a, b) for a, b in zip(zh_cmds, en_cmds) if a != b]
    assert differ and all(a.replace(ph["zh"], ph["en"]) == b for a, b in differ), differ  # 只有卡号正则占位按语言


def test_language_precedence_applies_to_owner_commands(owner_home, monkeypatch: pytest.MonkeyPatch):
    from tools.aipos_cli.i18n import has_cjk

    _set_language(owner_home["home"], "en")
    assert not has_cjk(_cli("setup", "check", "--gate-url", owner_home["gate"])[1])
    assert has_cjk(_cli("--lang", "zh", "setup", "check", "--gate-url", owner_home["gate"])[1])  # --lang 盖过 home 配置
    monkeypatch.setenv("LYBRA_LANG", "zh")
    assert has_cjk(_cli("setup", "check", "--gate-url", owner_home["gate"])[1])  # 环境变量盖过 home 配置
    monkeypatch.setenv("LYBRA_LANG", "de")
    rc, err = _cli("setup", "check", "--gate-url", owner_home["gate"])
    _show(f"[LYBRA_LANG=de setup check] exit {rc}: {err.strip()}")
    assert rc == 2 and "LYBRA_LANG" in err


def test_english_owner_inbox_has_no_product_cjk(rig, monkeypatch: pytest.MonkeyPatch):
    outs = _inbox_scenario(rig, "en", monkeypatch)
    corpus = _user_data(rig)
    for name, text in outs.items():
        _show(f"---- --lang en owner inbox · {name} ----\n{text}")
    leaks = {name: [run for run in CJK.findall(text) if run not in corpus] for name, text in outs.items()}
    assert not any(leaks.values()), leaks
    assert outs["inbox"].count("To decide:") >= 4 and "Tell " in outs["inbox_decided"]


def test_owner_recommend_cli_follows_language(rig):
    from tools.aipos_cli.i18n import has_cjk

    home = rig["home"]
    data = json.loads(_cli("owner", "inbox", "--home-root", str(home), "--json")[1])
    item = _item(data, "LX-21B", "loop_owner_needed")
    argv = ["owner", "recommend", "--item", item["item_id"], "--choice", "continue", "--reason", "plan A is ready",
            "--actor", "owner", "--home-root", str(home), "--connection-json", str(rig["conn"])]
    rc_zh, zh = _cli(*argv)
    rc_en, en = _cli("--lang", "en", *argv)
    _show(f"---- owner recommend 预演 缺省 ----\n{zh}\n---- --lang en ----\n{en}")
    assert rc_zh == rc_en
    assert has_cjk(zh) and not [r for r in CJK.findall(en) if r not in _user_data(rig)]


# ===========================================================================
# 件③ 看板登录页纳入 i18n 通道
# ===========================================================================

def test_login_page_joins_board_i18n_channel(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    from web.board import app as board_app

    page = (REPO_ROOT / "web" / "board" / "static" / "login.html").read_text(encoding="utf-8")
    script = (REPO_ROOT / "web" / "board" / "static" / "i18n.js").read_text(encoding="utf-8")
    keys = sorted(set(re.findall(r'data-i18n(?:-attr)?="(?:placeholder:)?(login\.[a-z0-9_]+)"', page))
                  | set(re.findall(r'T\("(login\.[a-z0-9_]+)"\)', page)))
    _show(f"[件③ 登录页文案键] {keys}")
    assert 'src="/i18n.js"' in page and len(keys) >= 25
    zh_part, en_part = script.split("\n  en: {", 1)
    for key in keys:
        assert f"'{key}':" in zh_part and f"'{key}':" in en_part, key
        en_value = re.search(rf"'{re.escape(key)}': '((?:[^'\\]|\\.)*)'", en_part).group(1)
        assert not CJK.search(en_value), (key, en_value)
    visible = re.sub(r"<!--.*?-->|//[^\n]*|/\*.*?\*/", "", page, flags=re.S)
    assert not CJK.search(visible), CJK.findall(visible)
    # 服务端给登录页注入看板缺省语言(home 配置 language=en)
    home = tmp_path / "home"
    (home / ".lybra").mkdir(parents=True)
    (home / ".lybra" / "config.json").write_text(json.dumps({"language": "en"}), encoding="utf-8")
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.delenv("LYBRA_LANG", raising=False)
    served = board_app._with_board_default_lang(page.encode("utf-8")).decode("utf-8")
    _show("---- 件③ 登录页(home 配置 language=en)首行 ----\n" + served.splitlines()[1])
    assert 'data-board-default-lang="en"' in served


# ===========================================================================
# 件④ 双语块 / 覆盖层守卫 + 术语表
# ===========================================================================

def test_bilingual_blocks_overlays_and_glossary():
    from tools.aipos_cli import i18n
    from tools.aipos_cli.enrollment import session_dir_declaration
    from tools.aipos_cli.gate_resident import gate_resident_declaration
    from tools.aipos_cli.owner_inbox import inbox_declaration, inbox_overlay_problems
    from tools.aipos_cli.owner_recommend import recommend_declaration
    from tools.aipos_cli.setup_check import setup_check_declaration
    from tools.schema_loader import code_repo_schema_root, load_schema

    config = load_schema("config", code_repo_schema_root())
    blocks = {"setup_check.texts": setup_check_declaration()["texts"], "gate_resident.texts": gate_resident_declaration()["texts"]}
    for name in ("home_root_source", "project_new", "onboarding_guide"):
        blocks[f"owner_texts.{name}.texts"] = i18n.owner_block(name)
    report = {}
    for where, block in blocks.items():
        report[where] = (len(i18n._key_paths(block["en"])), i18n.block_problems(block), i18n.placeholder_problems(block))
        assert not report[where][1] and not report[where][2], (where, report[where])
        assert not [p for p in i18n._key_paths(block["en"]) if i18n.has_cjk(i18n._leaf(block["en"], p))], where
    status = (load_schema("verbs", code_repo_schema_root()).get("verbs") or {})["lybra_loop_status"]
    overlays = {"lybra_owner_inbox": inbox_overlay_problems(inbox_declaration()),
                "lybra_owner_recommend": i18n.overlay_problems(recommend_declaration(), recommend_declaration()["translations"]["sections"]),
                "lybra_loop_status": i18n.overlay_problems(status, status["translations"]["sections"]),
                "session_dir": i18n.overlay_problems(session_dir_declaration(), ["source_labels"])}
    _show("[件④ 双语块] " + json.dumps(report, ensure_ascii=False) + "\n[件④ 覆盖层] " + json.dumps(overlays, ensure_ascii=False))
    assert not any(overlays.values()), overlays
    # 术语表: 中英对照非空; 英文译文(双语块 en + 覆盖层 en)不出现 avoid 列的旧译
    terms = config["owner_texts"]["glossary"]["terms"]
    assert len(terms) >= 20 and all(t["zh"] and t["en"] for t in terms)
    english = [i18n._leaf(b["en"], p) for b in blocks.values() for p in i18n._key_paths(b["en"])]
    for decl in (inbox_declaration(), recommend_declaration(), status):
        english += list(i18n._string_leaves(decl["translations"]["en"]).values())
    english += [line for line in (REPO_ROOT / "web" / "board" / "static" / "i18n.js").read_text(encoding="utf-8")
                .split("\n  en: {", 1)[1].splitlines() if "'login." in line]
    hits = [(t["en"], bad, text[:80]) for t in terms for bad in t["avoid"] for text in english if bad.lower() in text.lower()]
    assert not hits, hits
    # 卡号正则占位: 向导缺省语言占位与 card_policy 声明逐字一致(门侧占位拒判同读)
    card_policy = (load_schema("verbs", code_repo_schema_root())["verbs"].get("lybra_project_card_policy") or {})
    placeholder = json.dumps(card_policy, ensure_ascii=False)
    assert i18n.owner_block("onboarding_guide")["zh"]["card_policy"]["pattern_placeholder"] in placeholder


def test_reader_single_source_and_localized_value_semantics():
    """双语渲染值: str 本身 = 缺省语言(事项 ID / 哈希 / JSON / YAML / pickle 不变); 拼接走 i18n.join; 占位缺值 fail-closed。"""
    import pickle

    import yaml

    from tools.aipos_cli import i18n
    from tools.schema_loader import SchemaLoadError

    value = i18n.localize({"zh": "甲 {x}", "en": "A {x}"}, {"x": i18n.localize({"zh": "乙", "en": "B"}, None, where="t")}, where="t")
    assert isinstance(value, i18n.Localized) and i18n.pick(value, "en") == "A B" and i18n.pick(value) == str(value)
    assert i18n.pick(pickle.loads(pickle.dumps(value)), "en") == "A B"
    assert json.loads(json.dumps({"v": value}))["v"] == str(value) and yaml.safe_load(yaml.safe_dump({"v": value}))["v"] == str(value)
    joined = i18n.join(" / ", [value, "k"])
    assert i18n.pick(joined, "en") == "A B / k" and str(joined) == f"{value} / k"
    assert i18n.fill("{a!r} {{b}} {c, d}", {"a": "q"}, where="t") == "'q' {b} {c, d}"
    with pytest.raises(SchemaLoadError):
        i18n.fill("{missing}", {}, where="t")
    assert i18n.materialize({"k": [value]}, "en") == {"k": ["A B"]}
    # 语言取值域只读 verbs.schema board_readonly_pages.locale(与看板 pages_declaration 同值; i18n 不导入 board_pages)
    from tools.aipos_cli.board_pages import pages_declaration

    assert i18n.locale_declaration() == pages_declaration()["locale"]
    source = (REPO_ROOT / "tools" / "aipos_cli" / "i18n.py").read_text(encoding="utf-8")
    assert "from tools.aipos_cli.board_pages import" not in source and "import board_pages" not in source
