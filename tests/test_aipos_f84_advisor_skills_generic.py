"""AIPOS-F84: 技能与装配遗留清理三件(F83 缺口 G1/G2/G3/G5/G7, Owner 2026-09-24 裁定)

件① 顾问侧技能项目无关: 顾问侧技能 = distribution 声明里 advisor 技能集减去 executor/auditor 技能集(与门同一构建器)。
    grep 实例名/项目名/机器名/绝对路径/`/claim` 零命中(白名单须带「反例/禁止」标注, 当前为空); 开头一句占位说明;
    ```bash 块内 `lybra ...` 示例把尖括号占位换成合法值后须被 aipos_cli.build_parser() 解析(未知占位 = 红, 逼映射同步)。
    技能不渲染(copy_tree 原样分发), 不新增渲染机制。
件② card-policy-author 退役: 母本目录删除; 任何分发声明不含; 全仓引用只剩退役注记与夹具。
件③ 死代码与过时文案: schema_loader 两个零调用函数删除(含 __all__); 两份 README 装配清单单源 = distribution.schema;
    turn_advancer/rules.py 审计卡文案不再引用已退役 audit-card-template, 改指产品派生。

纯读产品树 + 解析器, 不连门、不写任何工位/治理根; token 不涉及。
"""
from __future__ import annotations

import contextlib
import io
import re
import shlex
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "tests"))

from test_aipos_f83_skills_zero_gate import _distributed_skill_files  # noqa: E402  (F83 同一声明构建器, 禁第二套判据)

#: 卡面靶场 grep(原文) + 项目字面扩展(他项目名/本项目信封与实例前缀/旧示例卡号)
CARD_PATTERN = re.compile(r"advisor\.lybra\.|kiwiai-dev|/home/kiwi|~/projects/lybra|/claim")
PROJECT_LITERAL_PATTERN = re.compile(
    r"exec\.lybra\.|audit(or)?\.lybra\.|pol_lybra|--project lybra|chris|AIPOS-XXX|lybra 项目|advisor\.<project>"
)
#: 说明性反例白名单: {相对路径: [行内片段, ...]}; 命中行须同时带「反例」或「禁止」标注。当前为空 = 零命中。
WHITELIST: dict[str, list[str]] = {}
#: 技能开头的占位说明(卡面原文)
PLACEHOLDER_NOTE = "示例中的尖括号为占位, 以本工位 .lybra/role 与 project.json 为准"
#: 占位 → 合法值(仅供解析; 新占位须在此登记, 否则红)
PLACEHOLDER_VALUES = {
    "<卡ID>": "PROJ-1",
    "<卡ID小写>": "proj-1",
    "<续卡ID>": "PROJ-1B",
    "<你的顾问实例>": "advisor.proj.host",
    "<执行体实例>": "exec.proj.host",
    "<审计体实例>": "audit.proj.host",
    "<项目名>": "proj",
    "<治理根>": "/tmp/f84-gov",
    "<项目根>": "/tmp/f84-gov/proj",
    "<代码仓>": "/tmp/f84-code",
    "<信封ID>": "pol_proj_1",
    "<裁决记录ID>": "verdict_PROJ-1_x",
    "<工位目录>": "/tmp/f84-ws",
    "<注册码>": "LYBRAENROLL1.fixture-not-a-code",
    "<门地址>": "http://127.0.0.1:7118",
    "<输出目录>": "/tmp/f84-out",
    "<清单文件>": "/tmp/f84-paths.txt",
    "<到期时间>": "2026-11-01T00:00:00Z",
    "<步骤号>": "1",
}
PLACEHOLDER_RE = re.compile(r"<[^<>\s'\"]+>")
#: 已退役的子命令(示例不得再教)
RETIRED_SUBCOMMANDS = {"next-step", "turn-advancer"}


def _advisor_side_skill_files() -> dict[str, Path]:
    advisor = _distributed_skill_files((("advisor", "advisor"),))
    worker = _distributed_skill_files((("executor", "executor"), ("auditor", "auditor")))
    return {rel: p for rel, p in advisor.items() if rel not in worker}


def _lybra_examples(text: str) -> list[str]:
    """```bash 块内的 `lybra ...` 行(续行合并, 去行尾注释)。"""
    out: list[str] = []
    for block in re.findall(r"```bash\n(.*?)```", text, re.S):
        block = re.sub(r"\\\n\s*", " ", block)
        for line in block.splitlines():
            line = line.split(" #", 1)[0].strip()
            if line.startswith("lybra "):
                out.append(line)
    return out


def _substitute(line: str) -> str:
    unknown = sorted({m for m in PLACEHOLDER_RE.findall(line) if m not in PLACEHOLDER_VALUES})
    assert not unknown, f"未登记占位 {unknown}: {line}"
    for k, v in PLACEHOLDER_VALUES.items():
        line = line.replace(k, v)
    assert "<" not in line and "..." not in line.replace('"..."', ""), line
    return line


def _git_grep(term: str) -> dict[str, int]:
    out = subprocess.run(["git", "grep", "-n", "-F", term, "--", "."], cwd=REPO_ROOT, capture_output=True, text=True, check=False)
    assert out.returncode in (0, 1), out.stderr
    by_file: dict[str, int] = {}
    for line in out.stdout.splitlines():
        f = line.split(":", 1)[0]
        by_file[f] = by_file.get(f, 0) + 1
    return by_file


# ===========================================================================
# 件① 顾问侧技能项目无关
# ===========================================================================

def test_item1_advisor_side_skills_inventory():
    files = _advisor_side_skill_files()
    print("顾问侧技能(advisor 集 − executor/auditor 集):", list(files))
    assert set(files) == {
        "agents/skills/advisor-commands/SKILL.md",
        "agents/skills/card-author/SKILL.md",
        "agents/skills/lybra-onboarding/SKILL.md",
        "agents/skills/truth-first-drafting/SKILL.md",
        "agents/skills/truth-navigator/SKILL.md",
    }
    assert all(p.is_file() for p in files.values())


def test_item1_advisor_skills_grep_zero_hits():
    hits: list[str] = []
    for rel, path in _advisor_side_skill_files().items():
        for no, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if not (CARD_PATTERN.search(line) or PROJECT_LITERAL_PATTERN.search(line)):
                continue
            allowed = any(frag in line for frag in WHITELIST.get(rel, [])) and ("反例" in line or "禁止" in line)
            if not allowed:
                hits.append(f"{rel}:{no}: {line.strip()}")
    print("grep 命中(白名单外):", hits or "0")
    assert hits == []


def test_item1_placeholder_note_at_skill_head():
    for rel, path in _advisor_side_skill_files().items():
        head = "\n".join(path.read_text(encoding="utf-8").splitlines()[:16])
        assert PLACEHOLDER_NOTE in head, rel


def test_item1_command_examples_parse():
    from tools.aipos_cli.aipos_cli import build_parser

    total = 0
    per_skill: dict[str, int] = {}
    for rel, path in _advisor_side_skill_files().items():
        examples = _lybra_examples(path.read_text(encoding="utf-8"))
        per_skill[rel] = len(examples)
        for raw in examples:
            line = _substitute(raw)
            argv = shlex.split(line)[1:]
            assert argv[0] not in RETIRED_SUBCOMMANDS, raw
            err = io.StringIO()
            try:
                with contextlib.redirect_stderr(err):
                    args = build_parser().parse_args(argv)
            except SystemExit as exc:
                raise AssertionError(f"argparse 拒: {raw}\n  → {line}\n  {err.getvalue().strip()}") from exc
            assert args.command == argv[0], raw
            total += 1
            print(f"PARSE OK  {rel.split('/')[2]}: {line}")
    print("每技能示例数:", per_skill, "合计:", total)
    assert per_skill["agents/skills/advisor-commands/SKILL.md"] >= 25
    assert per_skill["agents/skills/lybra-onboarding/SKILL.md"] >= 8


def test_item1_no_gate_self_claim_teaching_and_skills_not_rendered():
    """不教执行体/审计体自领; 技能仍 copy_tree 原样分发(无渲染机制)。"""
    from tools.schema_loader import load_schema

    card_author = (REPO_ROOT / "agents/skills/card-author/SKILL.md").read_text(encoding="utf-8")
    assert "/claim" not in card_author and "驱动方经产品完成" in card_author
    assert "自产审计卡" not in card_author
    decl = load_schema("distribution")
    for d in decl["distributions"]:
        if d.get("kind") == "skills":
            assert d["operation"] == "copy_tree", d["distribution_id"]
    for path in _advisor_side_skill_files().values():
        assert "{{" not in path.read_text(encoding="utf-8")  # 无章程式渲染占位


# ===========================================================================
# 件② card-policy-author 退役
# ===========================================================================

def test_item2_card_policy_author_retired_zero_refs():
    from tools.schema_loader import load_schema

    assert not (REPO_ROOT / "agents/skills/card-policy-author").exists()
    decl = load_schema("distribution")
    for d in decl["distributions"]:
        assert "card-policy-author" not in (d.get("filter") or {}).get("include", []), d["distribution_id"]
    refs = _git_grep("card-policy-author")
    print("card-policy-author 引用:", refs)
    assert set(refs) <= {
        "schema/distribution.schema.json",  # advisor-skills notes 退役注记
        "tests/test_aipos_f84_advisor_skills_generic.py",  # 本夹具
        "agents/harness/pi/lybra-loop/tests/run-all.sh",  # run-all F84 块标题文字
    }
    assert "card-policy-author 母本退役" in json_text("schema/distribution.schema.json")


def json_text(rel: str) -> str:
    return (REPO_ROOT / rel).read_text(encoding="utf-8")


# ===========================================================================
# 件③ 死代码与过时文案
# ===========================================================================

def test_item3_schema_loader_dead_functions_removed():
    import tools.schema_loader as schema_loader

    for name in ("get_role_tool_package", "get_roles_with_tool_package"):
        assert not hasattr(schema_loader, name), name
        assert name not in schema_loader.__all__, name
        refs = _git_grep(name)
        print(name, "引用:", refs)
        assert set(refs) <= {"tests/test_aipos_f83_skills_zero_gate.py", "tests/test_aipos_f84_advisor_skills_generic.py"}
    assert all(hasattr(schema_loader, n) for n in schema_loader.__all__)


def test_item3_readmes_single_source_is_distribution_schema():
    for rel in ("agents/README.md", "agents/roles/README.md"):
        text = json_text(rel)
        assert "distribution.schema.json" in text, rel
        assert "roles[].tool_package" not in text and "get_role_tool_package" not in text, rel
        assert "roles.schema.json 单一源" not in text and "roles.schema.json 一条数据" not in text, rel
    print("agents/roles/README.md 标题:", [l for l in json_text("agents/roles/README.md").splitlines() if l.startswith("## 装配清单")])


def test_item3_rules_audit_card_text_points_to_product_derivation():
    from tools.turn_advancer.rules import infer_next_action

    state = {
        "task_id": "PROJ-1",
        "queue_status": "claimed",
        "task_frontmatter": {"task_mode": "code", "audit": "required"},
        "latest_claim": {"canonical_agent_instance": "exec.proj.host", "claim_id": "claim_x"},
        "latest_return": {"executor_status": "completed"},
        "latest_verdict": None,
        "has_return_artifact": True,
        "has_audit_card": False,
        "events": [],
    }
    result = infer_next_action(state)
    print("rule:", result["rule"], "| reason:", result["human_judgment_reason"])
    assert result["action"] == "wait_human"
    text = result["rule"] + result["human_judgment_reason"]
    assert "audit-card-template" not in text and "task-closure-loop" not in text
    assert "产品派生" in text and "audit_derivation" in text and "审计卡" in result["rule"]
    assert "audit-card-template" not in json_text("tools/turn_advancer/rules.py")
