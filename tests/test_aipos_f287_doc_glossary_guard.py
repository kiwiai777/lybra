"""AIPOS-F287 — 文案与守卫收口。

件① 仓根文档全文术语守卫: glossary.guarded_docs 声明的文档(en = README.md / QUICKSTART.md 核 avoid; zh = 中文两份核 avoid_zh, 可选)逐行对照
    config.schema owner_texts.glossary 每条旧译, 命中即红并给 文件:行号; 读取与判定唯一实现 tests/glossary_terms.py(F277 英文块守卫、
    F262 第 4 步标题同读), 测试不写死任何术语。avoid_except = avoid 词在该短语里是另一个意思(如 `board open` 的一次性登录票据)。
    现存命中(车道外文档, 由文档卡清)登记 tests/f287_doc_glossary_baseline.jsonl(读写口 tests/ratchet_baseline.py, 一行一条):
    比对键 = (file, avoid) 多重集——不按行指纹, 文档卡改写同一行的其他措辞不致误红; 某文档某旧译次数超过基线 = 新增 → 红。
    基线残留(文档已改掉)只打印不判红: 文档在文档卡车道、tests/ 不在其车道, 判红会让「把旧译改掉」的文档卡反而变红; 残留条目由下一张
    代码卡顺手删(只减不增)。
    F262 过渡容忍(F281: 标题与正文同时用旧译时放过)随本守卫删除, 见 tests/test_aipos_f262_onboarding_current_flow.py。
件② 向导「落账遇暂存」行 = F285 口径: 他项目 / 范围外暂存保留不拒, 仅本次路径内暂存冲突时拒并给出口; 出口行的键是 F285 拒因原文
    (verbs.schema lybra_governance_commit.concurrent_landing.texts.<语言>.overlap)的子串(锁一致, 按用户语言)。
件③ 向导以外的 enroll-deliver 出口命令带 --project <项目>: workstation-plan 接入步、子 agent 重登记出口(subagent_reenroll)、
    token 全 retired 重签出口(reenroll_exit)、未接入工位 sync 拒因; 命令形锁向导同一渲染 onboarding.render_enroll_deliver_command
    (F282 向导 --project 同一字段, 禁第二实现); schema 里声明的每条 enroll-deliver 命令模板都须带 --project。

靶场: 临时目录副本 / 临时 home + 临时治理根(f198 / f78 靶场复用); 不连门、不读凭据、不碰真实治理根与主检出文档。
"""
from __future__ import annotations

import contextlib
import io
import json
import re
import shlex
import shutil
import sys
from collections import Counter
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import glossary_terms as gl  # noqa: E402  — 术语表测试侧唯一读取口与判定
import test_aipos_f78_engine_agnostic as f78  # noqa: E402  — 治理根靶场骨架唯一来源
from ratchet_baseline import read_entries  # noqa: E402
from test_aipos_f198_workstation_plan import P2, TARGET, _coder, _plan_cli, _step_commands, rig  # noqa: E402,F401  — 工位计划靶场复用

BASELINE_PATH = Path(__file__).resolve().parent / "f287_doc_glossary_baseline.jsonl"


def _show(text: str) -> None:
    print(text, flush=True)


# ===========================================================================
# 件① 仓根文档全文术语守卫
# ===========================================================================

def _key(item: dict) -> tuple[str, str]:
    return (str(item["file"]), str(item["avoid"]))


def guard_report(repo_root: Path, term_list: list[dict] | None = None) -> dict[str, list]:
    """守卫核心(真实文档与临时副本同用): 现存命中 vs 基线 → {hits, problems(红: 文件:行号), stale(只打印)}。"""
    hits = gl.doc_hits(repo_root, term_list)
    entries = read_entries(BASELINE_PATH)
    allowed = Counter(_key(e) for e in entries)
    seen: Counter = Counter()
    problems: list[str] = []
    for hit in hits:
        seen[_key(hit)] += 1
        if seen[_key(hit)] > allowed[_key(hit)]:
            problems.append(f"{hit['file']}:{hit['line']}: glossary avoid 词 {hit['avoid']!r}(术语「{hit['term']}」"
                            f"应作 {_en_of(hit['term'], term_list)!r}): {hit['text']}")
    stale = [e for e in entries if Counter(_key(h) for h in hits)[_key(e)] < allowed[_key(e)]]
    return {"hits": hits, "problems": problems, "stale": stale}


def _en_of(zh: str, term_list: list[dict] | None) -> str:
    return next(t["en"] for t in (gl.terms() if term_list is None else term_list) if t["zh"] == zh)


def test_baseline_well_formed_and_only_lane_blocked_known_terms():
    entries = read_entries(BASELINE_PATH)
    assert len(BASELINE_PATH.read_text(encoding="utf-8").splitlines()) == len(entries)
    docs = {lang: gl.guarded_docs(lang) for lang in gl.AVOID_KEY}
    for entry in entries:
        assert entry.get("item") and entry.get("reason") and entry.get("text") and entry.get("lane_blocked") is True, entry
        lang = next((lg for lg, files in docs.items() if entry["file"] in files), None)
        assert lang, f"基线条目文件不在 glossary.guarded_docs: {entry}"
        term = gl.term_by_zh(str(entry["term"]))
        assert entry["avoid"] in gl.avoid_words(term, lang), f"基线条目 avoid 不是该术语声明的旧译: {entry}"


def test_docs_full_text_glossary_guard():
    """现文档: 不出现基线以外的 avoid 命中(命中即红并给 文件:行号)。"""
    report = guard_report(REPO_ROOT)
    decl = gl.glossary()
    words = {lang: sum(len(gl.avoid_words(t, lang)) for t in decl["terms"]) for lang in gl.AVOID_KEY}
    _show(f"[件①·全文守卫] guarded_docs={decl['guarded_docs']} avoid 词数={words} "
          f"avoid_except={[(t['zh'], [e['phrase'] for e in t['avoid_except']]) for t in decl['terms'] if t.get('avoid_except')]}\n"
          f"  现存命中 {len(report['hits'])} 条(基线 {len(read_entries(BASELINE_PATH))} 条): "
          + json.dumps([f"{h['file']}:{h['line']} {h['avoid']}" for h in report["hits"]], ensure_ascii=False))
    for entry in report["stale"]:
        _show(f"  基线残留(文档已改掉, 下一张代码卡顺手从 {BASELINE_PATH.name} 删该条): {json.dumps(entry, ensure_ascii=False)}")
    assert not report["problems"], "\n".join(report["problems"])


def _copy_docs(dst: Path) -> None:
    for lang in gl.AVOID_KEY:
        for rel in gl.guarded_docs(lang):
            (dst / rel).parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(REPO_ROOT / rel, dst / rel)


def test_guard_turns_red_on_planted_temp_copy(tmp_path: Path):
    """临时副本植入 avoid 词即红(给行号); 例外短语不红; 中文 avoid_zh 同理(合成术语: 由 glossary 条目派生, 不写死)。真实文档不动。"""
    _copy_docs(tmp_path)
    clean = guard_report(tmp_path)
    assert not clean["problems"], clean["problems"]
    term_list = gl.terms()
    en_doc = gl.guarded_docs("en")[-1]
    planted = [(t["zh"], bad) for t in term_list for bad in gl.avoid_words(t, "en")]
    assert planted, "glossary 未声明任何 avoid 词"
    base_lines = len((tmp_path / en_doc).read_text(encoding="utf-8").splitlines())
    extra = [f"Planted line {i}: see the {bad} here." for i, (_zh, bad) in enumerate(planted)]
    excepts = [e["phrase"] for t in term_list for e in t.get("avoid_except", [])]
    extra += [f"Exception line: opens with a {phrase}." for phrase in excepts]
    with (tmp_path / en_doc).open("a", encoding="utf-8") as fh:
        fh.write("\n".join(extra) + "\n")
    report = guard_report(tmp_path)
    _show("[件①·临时副本植入 avoid 词] 判红原文:\n" + "\n".join(report["problems"]))
    expected = {(f"{en_doc}:{base_lines + 1 + i}", bad) for i, (_zh, bad) in enumerate(planted)}
    got = {(p.split(": ", 1)[0], next(bad for _z, bad in planted if repr(bad) in p)) for p in report["problems"]}
    assert expected <= got, (expected - got, report["problems"])
    assert not [p for p in report["problems"] if "Exception line" in p], "avoid_except 短语不应判红"
    # 每个植入词都按行号判红(一个 avoid 词是另一个的子串时同一行可多条命中, 不少于植入数)
    assert len([p for p in report["problems"] if p.startswith(f"{en_doc}:")]) >= len(planted)
    # 中文: 合成 avoid_zh(取第一条术语 zh + 后缀), 植入中文文档副本 → 红
    zh_doc = gl.guarded_docs("zh")[0]
    synthetic = [dict(t) for t in term_list]
    synthetic[0] = {**synthetic[0], "avoid_zh": [f"{synthetic[0]['zh']}旧称甲"]}
    zh_lines = len((tmp_path / zh_doc).read_text(encoding="utf-8").splitlines())
    with (tmp_path / zh_doc).open("a", encoding="utf-8") as fh:
        fh.write(f"植入行: 这里写了{synthetic[0]['zh']}旧称甲。\n")
    zh_report = guard_report(tmp_path, synthetic)
    _show("[件①·中文副本植入合成 avoid_zh] 判红原文:\n" + "\n".join(p for p in zh_report["problems"] if p.startswith(zh_doc)))
    assert any(p.startswith(f"{zh_doc}:{zh_lines + 1}:") for p in zh_report["problems"]), zh_report["problems"]
    # 真实文档未动
    assert not guard_report(REPO_ROOT)["problems"]


def test_ratchet_counts_by_file_and_word_not_line_text(tmp_path: Path):
    """基线按 (file, avoid) 计次: 存量行改写措辞仍在 = 不红; 同文档多一处同一旧译 = 红; 存量改掉 = 只打印残留不红。"""
    entries = read_entries(BASELINE_PATH)
    if not entries:
        pytest.fail("基线已清空: 本例用基线条目验证计次口径, 清空后改为用合成基线(届时同卡改本例)")
    entry = entries[0]
    _copy_docs(tmp_path)
    doc = tmp_path / str(entry["file"])
    text = doc.read_text(encoding="utf-8")
    assert entry["avoid"] in text
    doc.write_text(text.replace(str(entry["text"]), f"Reworded stock line keeps the {entry['avoid']} word."), encoding="utf-8")
    assert not guard_report(tmp_path)["problems"]
    with doc.open("a", encoding="utf-8") as fh:
        fh.write(f"One more {entry['avoid']} line.\n")
    assert guard_report(tmp_path)["problems"]
    doc.write_text(text.replace(str(entry["text"]), "Fixed stock line."), encoding="utf-8")
    fixed = guard_report(tmp_path)
    assert not fixed["problems"] and entry in fixed["stale"], fixed


# ===========================================================================
# 件② 向导「落账遇暂存」行 = F285 口径
# ===========================================================================

def test_onboarding_landing_staged_line_follows_f285():
    from tools.aipos_cli import i18n
    from tools.aipos_cli.onboarding import landing_on_fail
    from tools.schema_loader import code_repo_schema_root, load_schema

    concurrent = load_schema("verbs", code_repo_schema_root())["verbs"]["lybra_governance_commit"]["concurrent_landing"]["texts"]
    block = i18n.owner_block("onboarding_guide")
    rendered = landing_on_fail()
    for lang in i18n.supported_languages():
        landing = block[lang]["landing"]
        lines = i18n.materialize(rendered, lang)
        _show(f"[件②·向导落账遇暂存行 {lang}] {landing['staged_key']}: {landing['staged']}")
        assert lines.get(landing["staged_key"]) == landing["staged"], lines
        # 出口行的键 = F285 拒因原文(按同语言)里的一段: 输出里出现即对上本行
        assert landing["staged_key"] in concurrent[lang]["overlap"], (lang, landing["staged_key"])
        assert "--paths" not in landing["staged_key"], "旧口径「--paths 外暂存 = 拒」已退役(AIPOS-F285)"
        assert "AIPOS-F285" in landing["staged"]
        if lang == "en":
            assert not i18n.has_cjk(landing["staged_key"] + landing["staged"])


# ===========================================================================
# 件③ 向导以外的 enroll-deliver 出口带 --project
# ===========================================================================

def _render_like(command: str) -> tuple[str, list[str]]:
    """把一条 enroll-deliver 命令按向导唯一渲染重渲(参数取自命令本身)→ (重渲结果, extra)。形不同于 render 输出 = 不相等。"""
    from tools.aipos_cli.onboarding import render_enroll_deliver_command

    argv = shlex.split(command)
    i = argv.index("enroll-deliver")
    head = argv[i + 1:i + 9]
    assert head[0::2] == ["--role", "--instance", "--governance-root", "--workspace"], argv
    role, instance, gov, ws = head[1::2]
    extra = argv[i + 9:-1]
    again = render_enroll_deliver_command(argv[:i], role=role, instance=instance, governance_root=gov, workspace=ws, extra=extra,
                                          phase=argv[-1])
    return again, extra


def _parses(command: str) -> None:
    from tools.aipos_cli.aipos_cli import build_parser

    argv = shlex.split(re.sub(r"<[^<>]*>", "x", command))  # 占位(可含空格)换成单 token
    with contextlib.redirect_stderr(io.StringIO()):
        args = build_parser().parse_args(argv[1:])
    assert getattr(args, "project", None), f"--project 未被 CLI 解析: {command}"


def test_token_reenroll_exit_carries_project():
    from tools.aipos_cli.onboarding import render_enroll_deliver_command
    from tools.aipos_cli.token_resolver import TOKEN_REENROLL_EXIT

    _show(f"[件③·token 重签出口] {TOKEN_REENROLL_EXIT}")
    expected = render_enroll_deliver_command(["lybra", "roles"], role="<角色>", instance="<实例>", governance_root="<治理根>",
                                             workspace="<工位根>", extra=["--project", "<项目>", "--owner-authorization-ref", "<授权引用>"],
                                             phase="--dry-run")
    assert TOKEN_REENROLL_EXIT == expected
    _parses(TOKEN_REENROLL_EXIT)


def test_subagent_reenroll_exit_carries_declared_project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    from tools.aipos_cli.enrollment import subagent_reenroll_command, workstation_occupancy_declaration

    gov = f78._make_gov(tmp_path, monkeypatch, shape="lybra", project="f287-demo")
    command = subagent_reenroll_command(gov, "executor.f287-demo.host", role="executor", harness="codex")
    _show(f"[件③·子 agent 重登记出口] {command}")
    again, extra = _render_like(command)
    assert command == again and extra[extra.index("--project") + 1] == "f287-demo", (command, again)
    _parses(command)
    # project.json 读不出 = 占位(出口不失声; 门侧 PROJECT_MISMATCH 仍一处判)
    (gov / "project.json").write_text("{}", encoding="utf-8")
    placeholder = workstation_occupancy_declaration()["texts"]["project_placeholder"]
    assert f"--project {placeholder} " in subagent_reenroll_command(gov, "executor.f287-demo.host", role="executor", harness="codex")


def test_workstation_plan_enroll_commands_carry_project(rig):
    rc, out, err = _plan_cli(rig, P2, "--pi-executor", _coder(P2), *TARGET)
    assert rc == 0, err
    cmds = _step_commands(out, 3)
    _show("[件③·workstation-plan 接入步]\n" + "\n".join(f"  {k}: {v}" for k, v in cmds.items()))
    for phase, command in (("预演", cmds["预演"]), ("确认", cmds["确认"])):
        again, extra = _render_like(command)
        assert command == again, (phase, command, again)
        assert extra[extra.index("--project") + 1] == P2, (phase, extra)
        _parses(command)


def test_unenrolled_sync_exit_carries_project(tmp_path: Path):
    from tools.aipos_cli.distribution_sync import _validate_enrolled

    ws = tmp_path / "ws"
    (ws / ".lybra").mkdir(parents=True)
    with pytest.raises(ValueError) as exc:
        _validate_enrolled(ws)
    command = str(exc.value).rsplit("): ", 1)[1].strip()
    _show(f"[件③·未接入工位 sync 拒因出口] {command}")
    again, extra = _render_like(command)
    assert command == again and "--project" in extra


def test_declared_enroll_deliver_templates_all_carry_project():
    """schema 里声明的每条 `roles enroll-deliver --role …` 命令模板都带 --project(新出口不带 = 红)。"""
    from tools.aipos_cli.i18n import _string_leaves
    from tools.schema_loader import code_repo_schema_root, load_schema

    found: dict[str, str] = {}
    for name in ("config", "verbs"):
        for path, text in _string_leaves(load_schema(name, code_repo_schema_root())).items():
            if "roles enroll-deliver --role" in text and "description" not in path.rsplit(".", 1)[-1]:
                found[f"{name}.{path}"] = text
    _show("[件③·schema 声明的 enroll-deliver 命令模板]\n" + "\n".join(f"  {k}: {v}" for k, v in sorted(found.items())))
    assert len(found) >= 4, found  # reenroll_exit / subagent_reenroll / workstation-plan 预演与确认
    missing = {k: v for k, v in found.items() if " --project " not in v}
    assert not missing, missing
