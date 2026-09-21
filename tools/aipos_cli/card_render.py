"""AIPOS-F78 件②: `lybra card render` —— 卡意图面的单一渲染器(同一源, 三种输出)。

一句话: 把卡的意图面(goal / lane / acceptance / prohibitions / milestones + 工作树 / 报告落点 / 卡路径)
按 harness 渲染给任何执行引擎; 执行体只看渲染物, 不看账本面, 不碰门。

- pi:          开工提示三行(工作树 / 报告落点 / 卡路径), /go 复用
- codex:       Prompt.md + Plan.md 写入工作树根(OpenAI 长任务四文件形制中的两件)
- claude-code: 同一内容渲染为 CLAUDE.md 片段

单一实现: intent model 一份(build_intent_model), 三个模板只做排版; 禁三处各写。
渲染物零门动词 / 零 token(grep lybra_ = 0, 由 _redact 保证 + 夹具断言)。
落点全读声明: 工作树(config.schema worktree_root) / Return 落点(project.json paths.return_root +
transitions artifact_ingest.return) / 分支(transitions N5.branch_integration) / 节名别名(card.schema intent_face)。
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from typing import Any

from tools.aipos_cli.next_resolver import (
    REPO_ROOT,
    _find_task_in_queue,
    _read_frontmatter,
    _resolve_code_repo_root,
    _resolve_worktree_root,
    _return_artifact_path,
    required_return_frontmatter,
)

_GATE_VERB_RE = re.compile(r"lybra_\w*")  # 含裸 `lybra_` 前缀(卡面「grep lybra_=0」的验收口径)
_REDACTED = "[门动词已隐去: 执行体零门, 由产品铸记录]"


def _redact(text: str) -> str:
    """渲染物不得含门动词全名: 意图面正文若引用了 lybra_* 名(文档性引用), 隐去而非渲染。"""
    return _GATE_VERB_RE.sub(_REDACTED, text)


def _section_aliases() -> dict[str, list[str]]:
    from tools.aipos_cli.machine_zone import intent_face_declaration

    aliases = intent_face_declaration().get("body_sections_rendered") or {}
    return {k: [str(a) for a in v] for k, v in aliases.items() if isinstance(v, list)}


def _split_sections(body: str) -> list[tuple[str, str]]:
    """把 markdown 正文按 `## ` 标题切成 (heading, text) 列表; 标题前的散文归 ('', text)。"""
    sections: list[tuple[str, str]] = []
    heading, buf = "", []
    for line in body.split("\n"):
        if line.startswith("## "):
            sections.append((heading, "\n".join(buf).strip()))
            heading, buf = line[3:].strip(), []
        else:
            buf.append(line)
    sections.append((heading, "\n".join(buf).strip()))
    return [(h, t) for h, t in sections if h or t]


def _pick_section(sections: list[tuple[str, str]], aliases: list[str]) -> str:
    for heading, text in sections:
        if heading and any(alias.lower() in heading.lower() for alias in aliases):
            return text
    return ""


def build_intent_model(task_id: str, governance_root: Path, *, harness: str | None = None) -> dict[str, Any]:
    """从卡 + 声明构造意图模型(唯一)。卡不存在/声明缺 = ValueError/SchemaLoadError(fail-closed)。"""
    from tools.aipos_cli.machine_zone import derive_intent_declarations, intent_face_declaration
    from tools.schema_loader import get_branch_integration

    governance_root = Path(governance_root)
    task_path, queue_dir = _find_task_in_queue(governance_root, task_id)
    if not task_path:
        raise ValueError(f"queue 目录中找不到任务卡 {task_id}")
    fm = _read_frontmatter(task_path)
    from tools.aipos_cli.frontmatter import parse_markdown_frontmatter

    _meta, body, _warn = parse_markdown_frontmatter(task_path.read_text(encoding="utf-8"))
    intent = derive_intent_declarations(fm, governance_root)
    if intent["blocking_reasons"]:
        raise ValueError("; ".join(intent["blocking_reasons"]))
    chosen = str(harness or intent["harness"]).strip()
    allowed = list(intent_face_declaration().get("harness", {}).get("allowed") or [])
    if allowed and chosen not in allowed:
        raise ValueError(f"harness={chosen!r} 不在 card.schema intent_face.harness.allowed {allowed}")

    code_repo = _resolve_code_repo_root(governance_root) or governance_root
    worktree = _resolve_worktree_root(governance_root, code_repo) / task_id
    branch = str(get_branch_integration().get("branch_pattern") or "card/{task_id}").replace("{task_id}", task_id)
    return_path = _return_artifact_path(governance_root, task_id)

    sections = _split_sections(str(body or ""))
    aliases = _section_aliases()
    goal = _pick_section(sections, aliases.get("goal", ["Goal"])) or (sections[0][1] if sections and not sections[0][0] else "")
    refs = [str(r) for r in (fm.get("governance_refs") or []) if isinstance(r, (str, int, float))]
    return {
        "task_id": task_id,
        "title": str(fm.get("title") or task_id),
        "project": str(fm.get("project") or ""),
        "task_mode": str(fm.get("task_mode") or ""),
        "harness": chosen,
        "card_path": str(task_path),
        "queue_state": queue_dir,
        "goal": goal.strip(),
        "acceptance": _pick_section(sections, aliases.get("acceptance", ["验收"])),
        "prohibitions": _pick_section(sections, aliases.get("prohibitions", ["硬约束"])),
        "milestones": _pick_section(sections, aliases.get("milestones", ["里程碑"])),
        "governance_refs": refs,
        "lane": intent["lane"],
        "code_repo": str(code_repo),
        "worktree": str(worktree),
        "branch": branch,
        "return_path": str(return_path),
        "return_frontmatter": required_return_frontmatter(),
        "rework_rounds": [r for r in (fm.get("rework_rounds") or []) if isinstance(r, dict) and not r.get("cleared_at")],
    }


# ---------------------------------------------------------------------------
# 三个模板(只排版, 不取数)
# ---------------------------------------------------------------------------

def _frontmatter_hint(model: dict[str, Any]) -> str:
    keys = ", ".join(model["return_frontmatter"])
    return f"Return 文件 frontmatter 必填: {keys}(branch={model['branch']}, commit_sha=分支 tip, tree_hash=该 commit 的 tree, model=实际模型自报)"


def _common_sections(model: dict[str, Any]) -> list[str]:
    lane = model["lane"]
    lines = [
        f"## 目标",
        model["goal"] or model["title"],
        "",
        "## 车道(lane)",
        f"- 仓: `{lane.get('repo')}`",
        f"- 允许改动: {', '.join('`' + p + '`' for p in lane.get('paths') or []) or '(未声明)'}",
        f"- 角色: {', '.join(lane.get('roles') or []) or '(未声明)'}",
        f"- 工作树: `{model['worktree']}`(分支 `{model['branch']}`, 从当前 main 拉)",
        "",
        "## 验收",
        model["acceptance"] or "(见依据条目)",
        "",
        "## 禁止域",
        model["prohibitions"] or "- 只改车道内路径; 不 push; 不碰治理仓 git; 不调用任何门动词",
        "",
        "## 交付",
        f"- 代码: 提交到分支 `{model['branch']}`(精确 pathspec add, 不 push)",
        f"- 报告: `{model['return_path']}`(含「## 一句话结论」节)",
        f"- {_frontmatter_hint(model)}",
    ]
    if model["rework_rounds"]:
        lines += ["", "## 返工节(未销账)"]
        for r in model["rework_rounds"]:
            lines.append(f"- 第 {r.get('round')} 轮(依据 {r.get('verdict_ref')}): " + "; ".join(str(x) for x in (r.get("focus_items") or [])))
    if model["governance_refs"]:
        lines += ["", "## 依据(卡面 governance_refs)"]
        lines += [f"- {ref}" for ref in model["governance_refs"]]
    return lines


def render_pi(model: dict[str, Any]) -> dict[str, str]:
    text = "\n".join([
        f"工作树: {model['worktree']} (分支 {model['branch']})",
        f"报告落点: {model['return_path']} ({_frontmatter_hint(model)})",
        f"卡路径: {model['card_path']}",
    ]) + "\n"
    return {"stdout": _redact(text)}


def render_codex(model: dict[str, Any]) -> dict[str, str]:
    prompt = "\n".join([f"# {model['task_id']} — {model['title']}", "", *_common_sections(model), ""])
    plan_lines = [f"# Plan — {model['task_id']}", "", "## 里程碑", model["milestones"] or "1. 读卡与依据\n2. 在工作树按车道实现\n3. 补夹具入常驻\n4. 提交分支并写 Return", "",
                  "## 交付清单", f"- [ ] 分支 `{model['branch']}` 有提交, tip 与 Return frontmatter.commit_sha 一致",
                  f"- [ ] `{model['return_path']}` 落盘, frontmatter 含 {', '.join(model['return_frontmatter'])}",
                  "- [ ] 一句话结论非占位", ""]
    return {"Prompt.md": _redact(prompt), "Plan.md": _redact("\n".join(plan_lines))}


def render_claude_code(model: dict[str, Any]) -> dict[str, str]:
    lines = [f"# {model['task_id']} — {model['title']}", "", f"> 卡: `{model['card_path']}`", "", *_common_sections(model), "",
             "## 里程碑", model["milestones"] or "1. 读卡与依据 2. 在工作树按车道实现 3. 补夹具入常驻 4. 提交分支并写 Return", ""]
    return {"CLAUDE.md": _redact("\n".join(lines))}


_RENDERERS = {"pi": render_pi, "codex": render_codex, "claude-code": render_claude_code}


def render_card(task_id: str, governance_root: Path, *, harness: str | None = None) -> dict[str, Any]:
    """渲染入口(唯一): 返回 {harness, model, files: {name: content}}。files 的 'stdout' 键表示只打印。"""
    model = build_intent_model(task_id, governance_root, harness=harness)
    renderer = _RENDERERS.get(model["harness"])
    if renderer is None:
        raise ValueError(f"harness={model['harness']!r} 无渲染模板(card.schema intent_face.harness.renderers)")
    files = renderer(model)
    for name, content in files.items():
        if _GATE_VERB_RE.search(content):  # 防御: 模板本身不得引入门动词
            raise ValueError(f"渲染物 {name} 含门动词, 拒绝输出")
    return {"harness": model["harness"], "model": model, "files": files}


def run_render_cli(args: Any) -> int:
    from tools.aipos_cli.aipos_cli import _find_repo_root_for_args
    from tools.schema_loader import SchemaLoadError

    try:
        governance_root = Path(getattr(args, "workspace_root", None) or _find_repo_root_for_args(args))
    except FileNotFoundError as exc:
        print(f"lybra card render: cannot resolve governance root: {exc}", file=sys.stderr)
        return 1
    try:
        rendered = render_card(args.task_id, governance_root, harness=getattr(args, "harness", None))
    except (ValueError, SchemaLoadError, OSError) as exc:
        print(f"lybra card render: {exc}", file=sys.stderr)
        return 1
    files = rendered["files"]
    if getattr(args, "json", False):
        print(json.dumps({"harness": rendered["harness"], "files": files, "worktree": rendered["model"]["worktree"]}, indent=2, ensure_ascii=False))
        return 0
    if "stdout" in files or getattr(args, "stdout", False):
        for name, content in files.items():
            if name != "stdout":
                print(f"--- {name} ---")
            print(content, end="" if content.endswith("\n") else "\n")
        return 0
    out_dir = Path(getattr(args, "out_dir", None) or rendered["model"]["worktree"])
    if not out_dir.is_dir():
        print(f"lybra card render: 输出目录不存在 {out_dir}(工作树未建? 用 --out-dir 指定或 --stdout 只打印)", file=sys.stderr)
        return 1
    for name, content in files.items():
        (out_dir / name).write_text(content, encoding="utf-8")
        print(f"written: {out_dir / name}")
    return 0


# AIPOS-316: Guard against direct invocation
from tools.aipos_cli._cli_entry_guard import check_direct_invocation  # noqa: E402
check_direct_invocation(__name__)
