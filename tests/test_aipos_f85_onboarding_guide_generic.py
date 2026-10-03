"""AIPOS-F85: 接入向导与顾问技能通用化三件(F84 缺口 G1–G3, Owner 2026-10-02 裁定)

件① onboarding guide 零门化 + 可跑(不变量夹具):
    guide 产出的每一条 shell 命令里以 `lybra ` 开头的段, 占位换合法值后必须被 aipos_cli.build_parser() 解析;
    非 lybra 段只许登记过的 shell 动词(cd / pi), 未登记 = 红(逼同步);
    pi 内斜杠命令只许 distribution 声明中 kind=extension 条目源文件所 registerCommand 的命令(由声明推导, 禁硬编码白名单);
    check / on_fail 等说明文字里提到的 `lybra <子命令>` 也须是解析器里真实存在的子命令路径(`lybra on` 之类即红)。
    lybra 形 / chris 形 / 缺省三种参数各生成一份同跑。
件② truth-navigator 通用化: grep `FOUNDATION-BACKLOG|LOOP-REDESIGN|DISCIPLINE\\.md|LEDGER\\.md|CONVERGENCE|lybra` 零命中
    (白名单逐条标注理由); 正文引用的 governance_structure.paths 键须在 config.schema 里真实存在。
件③ advisor-commands「过渡期」段去重: 只剩一段且为当前事实(不再声称 ADVISOR-COMMANDS.md 暂保留)。

纯读产品树 + 生成器 + 解析器, 不连门、不写任何工位 / 治理根; token 不涉及。
"""
from __future__ import annotations

import argparse
import contextlib
import io
import json
import re
import shlex
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from tools.aipos_cli.aipos_cli import build_parser  # noqa: E402
from tools.aipos_cli.onboarding import format_guide_text, generate_onboarding_guide  # noqa: E402
from tools.schema_loader import load_schema  # noqa: E402

#: guide 里出现的占位 → 合法值(仅供解析; 新占位须在此登记, 否则红)
#: AIPOS-F92: 九步 guide 的占位 = 上一步输出(三个注册码)/ 顾问自填(仓、卡稿、草稿路径、卡号)/ Owner 工作区解析不到时的占位
PLACEHOLDER_VALUES = {
    "<ADVISOR_CODE>": "LYBRAENROLL1.fixture-not-a-code",
    "<EXECUTOR_CODE>": "LYBRAENROLL1.fixture-not-a-code",
    "<AUDITOR_CODE>": "LYBRAENROLL1.fixture-not-a-code",
    "<OWNER_WORKSPACE>": "/tmp/f85-owner",
    "<REPO_NAME>": "app",
    "<REPO_ABS_PATH>": "/tmp/f85-code/app",
    "<CARD_DRAFT_JSON>": "/tmp/f85-card.json",
    "<DRAFT_PATH>": "5_tasks/drafts/f85-1.md",
    "<TASK_ID>": "F85-1",
}
PLACEHOLDER_RE = re.compile(r"<[A-Za-z_一-鿿][^<>\s]*>")
#: 非 lybra 的 shell 动词登记(guide 新增别的 shell 动词须在此登记并说明, 否则红)
SHELL_VERBS = {
    "cd": "进工位目录",
    "pi": "起 Pi 编码代理(工位 harness)",
}
#: 三种参数形: 缺省(全走推导) / lybra 形(治理根 + 代码仓 + 工位) / chris 形(带连字符项目名 + 空格路径)
GUIDE_SHAPES = {
    "default": dict(project_name="probe-xyz"),
    "lybra-shape": dict(
        project_name="lybra",
        home_root="/tmp/f85-home",
        gate_url="http://127.0.0.1:7118",
        code_repo="/tmp/f85-code/lybra",
        actor="advisor.proj.host",
        workspace_dir="/tmp/f85-ws/lybra-executor",
        auditor_dir="/tmp/f85-ws/lybra-auditor",
        advisor_dir="/tmp/f85-session/lybra",
        owner_workspace="/tmp/f85-home/ops",
    ),
    "chris-shape": dict(
        project_name="chris-huibojin",
        home_root="/tmp/f85 home",
        gate_url="http://10.0.0.2:7118",
        actor="advisor.proj.host",
        workspace_dir="~/f85-ws/hbj coder",
        auditor_dir="~/f85-ws/hbj auditor",
        advisor_dir="~/chris session",
        repos=["app=/tmp/f85 code/app", "lib=/tmp/f85 code/lib"],
        default_repo="app",
        owner_workspace="/tmp/f85 home/ops",
    ),
}
#: 已退役 / 不存在的命令形(词边界匹配; `lybra onboarding` 不算 `lybra on`)
RETIRED_PATTERNS = (r"\blybra on\b", r"(?<![\w.])/lybra\b", r"\blaunch-check\b")


# ---------------------------------------------------------------------------
# 声明推导: pi 内斜杠命令集
# ---------------------------------------------------------------------------

def declared_slash_commands() -> dict[str, str]:
    """distribution 声明中 kind=extension 条目的源文件所注册的斜杠命令 → 来源 distribution_id。"""
    out: dict[str, str] = {}
    for d in load_schema("distribution")["distributions"]:
        if d.get("kind") != "extension":
            continue
        src = REPO_ROOT / d["source"]["path"]
        files = sorted(src.rglob("*.ts")) if src.is_dir() else [src]
        assert files and all(f.is_file() for f in files), f"{d['distribution_id']} 源缺失: {src}"
        for f in files:
            for name in re.findall(r"registerCommand\(\s*[\"']([\w-]+)[\"']", f.read_text(encoding="utf-8")):
                out[name] = d["distribution_id"]
    return out


# ---------------------------------------------------------------------------
# guide 命令抽取
# ---------------------------------------------------------------------------

def _step_command_texts(step: dict) -> list[str]:
    return list(step["commands"]) if step.get("commands") else [step["command"]]


def _iter_command_lines(guide: dict):
    """(step_number, 原始行) —— 去空行与 # 注释行。"""
    for step in guide["steps"]:
        for text in _step_command_texts(step):
            for line in text.splitlines():
                line = line.strip()
                if line and not line.startswith("#"):
                    yield step["step_number"], line


def _substitute(line: str) -> str:
    unknown = sorted({m for m in PLACEHOLDER_RE.findall(line) if m not in PLACEHOLDER_VALUES})
    assert not unknown, f"未登记占位 {unknown}: {line}"
    for k, v in PLACEHOLDER_VALUES.items():
        line = line.replace(k, v)
    return line


def _segments(line: str) -> list[list[str]]:
    tokens = shlex.split(_substitute(line))
    segs: list[list[str]] = [[]]
    for t in tokens:
        if t in ("&&", ";", "||"):
            segs.append([])
        else:
            segs[-1].append(t)
    assert all(segs), f"空命令段: {line}"
    return segs


def _parse_lybra(argv: list[str], raw: str) -> argparse.Namespace:
    err = io.StringIO()
    try:
        with contextlib.redirect_stderr(err):
            args = build_parser().parse_args(argv)
    except SystemExit as exc:
        raise AssertionError(f"argparse 拒: {raw}\n  {err.getvalue().strip()}") from exc
    # AIPOS-F92: 允许顶层全局选项 `--workspace-root <治理根>` 在子命令前(guide 第 9 步 draft create/publish 显式带治理根)
    lead = argv[2:] if argv[:1] == ["--workspace-root"] else argv
    assert args.command == lead[0], raw
    return args


def _subcommand_path_exists(words: list[str]) -> bool:
    parser = build_parser()
    for w in words:
        subs = [a for a in parser._actions if isinstance(a, argparse._SubParsersAction)]
        if not subs:
            return True  # 叶子命令: 余下是参数/说明文字
        if w not in subs[0].choices:
            return False
        parser = subs[0].choices[w]
    return True


def _guide(shape: str) -> dict:
    return generate_onboarding_guide(**GUIDE_SHAPES[shape])


# ===========================================================================
# 件① 不变量夹具
# ===========================================================================

def test_item1_declared_slash_commands_derived():
    cmds = declared_slash_commands()
    print("分发声明推导的 pi 斜杠命令:", cmds)
    assert "go" in cmds  # guide 第 6 步开工所用; 声明改名 / 退役时本夹具先红
    assert "lybra" not in cmds  # lybra-loop 扩展已退役(F83 件②), /lybra 不在声明内


def test_item1_every_guide_command_parses_and_slash_in_declaration():
    slash = declared_slash_commands()
    total_lybra = 0
    for shape in GUIDE_SHAPES:
        guide = _guide(shape)
        assert guide["total_steps"] == 9 and guide["owner_actions"] == [3, 4]  # AIPOS-F92: Owner 一次性动作 ≤2
        seen_slash: list[str] = []
        for step_no, line in _iter_command_lines(guide):
            if line.startswith("/"):
                name = line[1:].split()[0]
                assert name in slash, f"[{shape}] Step {step_no} 斜杠命令 /{name} 不在分发声明内(声明集 {sorted(slash)})"
                seen_slash.append(name)
                print(f"SLASH OK [{shape}] Step {step_no}: {line}  ← {slash[name]}")
                continue
            for seg in _segments(line):
                if seg[0] == "lybra":
                    _parse_lybra(seg[1:], line)
                    total_lybra += 1
                    print(f"PARSE OK [{shape}] Step {step_no}: {shlex.join(seg)}")
                else:
                    assert seg[0] in SHELL_VERBS, f"[{shape}] Step {step_no} 未登记 shell 动词 {seg[0]!r}: {line}"
        assert seen_slash == ["go"], (shape, seen_slash)
    print("lybra 命令解析合计:", total_lybra)
    assert total_lybra >= 3 * 10


def test_item1_steps5_6_are_zero_gate_real_path():
    """AIPOS-F92 九步(原 F85 第 4–6 步的零门不变量平移到第 5/7/8 步):
    第 5 步 = 顾问 enroll 到治理根(--harness claude-code --harness-dir 会话目录)+ sync --dry-run 稳态;
    第 7 步 = 每个工位 enroll(--workspace 工位)→ sync → sync --dry-run, 参数指向工位 / 治理根;
    第 8 步 = onboarding check 自检 → cd 工位 → pi → /go; 第 3/4 步 = Owner 动作(enroll-code --token-role owner / envelope mint --confirm)。"""
    for shape, kw in GUIDE_SHAPES.items():
        guide = _guide(shape)
        home = kw.get("home_root") or guide["home_root"]
        gov = f"{home}/{kw['project_name']}"
        ws = kw.get("workspace_dir") or f"~/{kw['project_name']}-executor"
        aws = kw.get("auditor_dir") or f"~/{kw['project_name']}-auditor"
        adv = kw.get("advisor_dir") or f"~/{kw['project_name']}"
        steps = {st["step_number"]: st for st in guide["steps"]}

        def lybra_segs(step):
            return [seg for _, l in _iter_command_lines({"steps": [step]}) for seg in _segments(l) if seg[0] == "lybra"]

        owner3 = [_parse_lybra(seg[1:], "step3") for seg in lybra_segs(steps[3])]
        assert len(owner3) == 1 and owner3[0].roles_command == "enroll-code" and owner3[0].token_role == "owner"
        assert owner3[0].role == "advisor" and owner3[0].governance_root == kw["project_name"]
        owner4 = [_parse_lybra(seg[1:], "step4") for seg in lybra_segs(steps[4])]
        assert len(owner4) == 1 and owner4[0].envelope_command == "mint" and owner4[0].confirm is True
        assert len(owner4[0].policy_id) == 3 == len(owner4[0].agent_or_role) and owner4[0].workspace_root == gov
        assert steps[3]["owner_action"] == 1 and steps[4]["owner_action"] == 2
        assert all(st["owner_action"] is None for n, st in steps.items() if n not in (3, 4))

        adv_enroll, adv_sync = (_parse_lybra(seg[1:], "step5") for seg in lybra_segs(steps[5]))
        assert (adv_enroll.roles_command, adv_enroll.workspace, adv_enroll.harness) == ("enroll", gov, "claude-code")
        assert adv_enroll.harness_dir == adv and adv_sync.dry_run is True and adv_sync.harness_root == gov

        segs7 = lybra_segs(steps[7])
        enrolls = [_parse_lybra(seg[1:], "step7") for seg in segs7 if seg[1:3] == ["roles", "enroll"]]
        assert [e.workspace for e in enrolls] == [ws, aws]
        syncs = [_parse_lybra(seg[1:], "step7") for seg in segs7 if seg[1] == "sync"]
        assert [(a.harness_root, a.dry_run) for a in syncs] == [(ws, False), (ws, True), (aws, False), (aws, True)]
        for a in syncs:
            assert a.workspace_root == gov, (shape, a)
            assert a.token is None  # token 永不上屏: guide 不带 --token, 由工位 connection.json 取

        lines8 = [l for _, l in _iter_command_lines({"steps": [steps[8]]})]
        assert lines8[-1] == "/go" and lines8[-2] == "pi"
        assert lines8[-3] == f"cd {ws}" or shlex.split(lines8[-3]) == ["cd", ws]
        check = _parse_lybra(shlex.split(lines8[0])[1:], "step8")
        assert (check.command, check.onboarding_command, check.step) == ("onboarding", "check", 8)
        assert check.project_name == kw["project_name"] and check.home_root == home and check.workspace_dir == ws

        loop = [_parse_lybra(seg[1:], "step9") for seg in lybra_segs(steps[9]) if seg[1] == "loop"]
        assert len(loop) == 1 and str(loop[0].workspace_root) == gov and loop[0].envelope == guide["policies"]["driver"]


def test_item1_no_retired_commands_and_prose_subcommands_exist():
    """说明文字(目的/验证/失败出口/注/产物)里点名的 `lybra <子命令>` 也须真实存在; 退役命令零出现。"""
    for shape in GUIDE_SHAPES:
        guide = _guide(shape)
        text = format_guide_text(guide) + "\n" + json.dumps(guide, ensure_ascii=False)
        for pat in RETIRED_PATTERNS:
            assert not re.search(pat, text), f"[{shape}] 退役命令残留: {pat}"
        # 命令行已在上一夹具逐条完整解析; 此处只扫说明字段(项目名本身可能就是 lybra, 命令里的 --reason 会误配)
        prose = "\n".join(
            str(v)
            for st in guide["steps"]
            for k, v in st.items()
            if k in ("title", "purpose", "check", "on_fail", "note", "creates")
        )
        refs = sorted(set(re.findall(r"(?<![\w./-])lybra ([a-z][a-z-]*(?: [a-z][a-z-]*)?)", prose)))
        print(f"[{shape}] 说明文字点名的子命令:", refs)
        bad = [r for r in refs if not _subcommand_path_exists(r.split())]
        assert bad == [], f"[{shape}] 不存在的子命令: {bad}"
        # 斜杠命令在说明文字里也只许声明内的
        named = set(re.findall(r"(?<![\w./~:-])/([a-z][a-z0-9-]*)(?![\w/.-])", format_guide_text(guide)))
        assert named <= set(declared_slash_commands()), (shape, named)


def test_item1_tilde_workspace_expands():
    """缺省工位 ~/<项目>-workstation: 引号不得包住 ~(否则 shell 不展开)。"""
    text = format_guide_text(_guide("default")) + format_guide_text(_guide("chris-shape"))
    assert "'~" not in text and '"~' not in text
    assert "cd ~/probe-xyz-executor" in text
    assert "~/'f85-ws/hbj coder'" in text  # 空格路径: ~ 留在引号外, 其余整体引用


# ===========================================================================
# 件② truth-navigator 通用化
# ===========================================================================

TN = REPO_ROOT / "agents/skills/truth-navigator/SKILL.md"
TN_PATTERN = re.compile(r"FOUNDATION-BACKLOG|LOOP-REDESIGN|DISCIPLINE\.md|LEDGER\.md|CONVERGENCE|lybra")
#: 白名单: (行内片段, 理由) —— 命中行须含片段, 且片段之外不再命中
TN_WHITELIST = [
    (".lybra/role", "AIPOS-F84 占位说明原文(工位身份文件 .lybra/role, 产品通用目录名, 非项目名; F84 夹具要求原文)"),
]


def test_item2_truth_navigator_grep_zero_hits():
    hits: list[str] = []
    for no, line in enumerate(TN.read_text(encoding="utf-8").splitlines(), 1):
        if not TN_PATTERN.search(line):
            continue
        stripped = line
        for frag, _why in TN_WHITELIST:
            stripped = stripped.replace(frag, "")
        if TN_PATTERN.search(stripped):
            hits.append(f"{no}: {line.strip()}")
        else:
            print(f"白名单 {no}: {[w for f, w in TN_WHITELIST if f in line]}")
    print("grep 命中(白名单外):", hits or "0")
    assert hits == []


def test_item2_truth_navigator_structure_keys_declared():
    gs = load_schema("config")["governance_structure"]["paths"]
    text = TN.read_text(encoding="utf-8")
    keys = set(re.findall(r"governance_structure\.paths\.(\w+)", text))
    keys |= set(re.findall(r"`<(\w+)>", text)) & {"stage_archive", "decision_log_dir", "governance_docs"}
    print("引用的 governance_structure.paths 键:", sorted(keys))
    assert {"stage_archive", "decision_log_dir", "governance_docs"} <= keys
    assert keys <= set(gs), sorted(keys - set(gs))
    # AIPOS-F89 件② M17: 卡编年史改为项目可选声明(project.json paths.foundation_backlog), 治理文档名不再是产品契约(无 files 表)
    assert "project.json `paths.foundation_backlog`" in text
    pj_paths = load_schema("config")["configuration_sources"]["project_json"]["schema"]["paths"]["schema"]
    assert pj_paths["foundation_backlog"]["optional"] is True and "files" not in gs["governance_docs"]
    # 方法论保留: 四步导航 + 两目录分工判据
    for anchor in ("① 读 stage_archives 最新一篇", "② 读该篇之后的 decision_log", "③ 文档状态头裁", "④ 仍冲突以时间线后者为准", "两目录分工判据"):
        assert anchor in text, anchor


# ===========================================================================
# 件③ advisor-commands 过渡期段去重
# ===========================================================================

def test_item3_advisor_commands_transition_paragraph_single_and_current():
    text = (REPO_ROOT / "agents/skills/advisor-commands/SKILL.md").read_text(encoding="utf-8")
    paras = [l for l in text.splitlines() if l.startswith("**过渡期")]
    print("过渡期段:", paras)
    assert len(paras) == 1
    assert "过渡期豁免" not in text and "暂保留" not in text and "过渡期保留" not in text
