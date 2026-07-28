"""AIPOS-263 — md 原文侧栏:零依赖安全渲染 + 路径白名单读取。

手写子集渲染器(零第三方依赖)。安全模型:**先 HTML 转义后做 md 变换**
——先对整段文本做全量 HTML 实体转义,再做 markdown 子集 → HTML 变换。
变换产出的标签是输出中唯一的标签来源,因此可安全注入(DOM-only 纪律的
字符串等价:escape-first)。链接 href 额外做 scheme 白名单(javascript: 等被拒)。

路径白名单:只允许工作区内 ``5_tasks/queue/**`` 与 ``5_tasks/records/**`` 下的
真实文件(先 resolve 再校验落在白名单根下,符号链接逃逸即拒)。``..`` / 绝对路径 /
越界路径一律 PATH_UNSAFE。

仅读:不写任何文件。
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from tools.aipos_cli.adapter_response import blocked_response, make_response

OPERATION = "get_markdown_source"

# 只允许这两个根下的文件(queue 卡片 + records 记录)。
ALLOWED_ROOTS: tuple[str, ...] = ("5_tasks/queue", "5_tasks/records")

READ_SAFETY_NOTICE = "Local read-only web UI route. No files are written."

# 用于按 record_id 匹配记录文件时读取的 frontmatter 键别名(不同记录类型用不同键)。
_RECORD_ID_KEYS: tuple[str, ...] = (
    "record_id",
    "return_id",
    "verdict_id",
    "dispatch_id",
    "claim_id",
    "session_id",
    "publish_id",
    "decision_id",
)


def escape_html(text: str) -> str:
    """全量 HTML 实体转义(& 必须最先)。"""
    return (
        text.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&#34;")
        .replace("'", "&#39;")
    )


def split_frontmatter(text: str) -> tuple[str, str]:
    """拆出 YAML frontmatter。返回 (frontmatter 原文, body 原文)。

    frontmatter 以首行 ``---`` 开始、下一个独占一行的 ``---`` 结束。无则返回 ("", text)。
    """
    if not text:
        return "", ""
    lines = text.split("\n")
    if not lines or lines[0].strip() != "---":
        return "", text
    for idx in range(1, len(lines)):
        if lines[idx].strip() == "---":
            fm = "\n".join(lines[1:idx])
            body = "\n".join(lines[idx + 1 :])
            # 去掉 body 开头的一个空行(常规写法),其余原样保留。
            if body.startswith("\n"):
                body = body[1:]
            return fm, body
    # 只有起始 ``---`` 没有结束符 → 不是合法 frontmatter,整体当 body。
    return "", text


def parse_frontmatter_kv(fm: str) -> dict[str, str]:
    """极简 frontmatter 键值解析(只为定位 task_id / record_id 用,不还原结构)。

    只识别 ``key: value`` 单行;列表/多行值取不到(本模块不需要)。
    """
    out: dict[str, str] = {}
    for line in (fm or "").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or stripped.startswith("-"):
            continue
        if ":" not in stripped:
            continue
        key, _sep, value = stripped.partition(":")
        key = key.strip()
        value = value.strip().strip("\"'")
        if key:
            out[key] = value
    return out


def validate_path(repo_root: Path, candidate: str) -> Path:
    """校验 candidate 落在白名单根下且未逃逸 repo_root。

    返回 resolve 后的绝对路径。任何不合法都抛 ValueError(由调用方转 PATH_UNSAFE)。
    """
    candidate = (candidate or "").strip()
    if not candidate:
        raise ValueError("path is required")
    # 拒绝绝对路径与显式盘符(Windows 风格)。
    if candidate.startswith("/") or (len(candidate) >= 2 and candidate[1] == ":"):
        raise ValueError("absolute paths are not allowed")
    root = repo_root.resolve()
    target = (root / candidate).resolve()
    # 必须仍在 repo_root 内(符号链接 / ``..`` 逃逸即拒)。
    try:
        rel = target.relative_to(root)
    except ValueError as exc:
        raise ValueError("path escapes workspace root") from exc
    rel_posix = rel.as_posix()
    if not any(rel_posix == root_name or rel_posix.startswith(root_name + "/") for root_name in ALLOWED_ROOTS):
        raise ValueError("path must be under 5_tasks/queue or 5_tasks/records")
    if not target.is_file():
        raise FileNotFoundError(f"file not found: {rel_posix}")
    return target


import re  # stdlib only — renderer stays dependency-free

_PH = "\x00"  # placeholder sentinel for inline-code protection (NUL never in escaped md)


def _safe_href(url: str) -> str:
    """链接 href scheme 白名单。带 scheme 的只放行 http/https/mailto,其余(含
    javascript:/data:)中和为 #。url 来自已转义文本(引号已是实体),不会突破属性。"""
    url = (url or "").strip()
    if re.match(r"^[a-zA-Z][a-zA-Z0-9+.\-]*:", url):
        scheme = url.split(":", 1)[0].lower()
        if scheme not in ("http", "https", "mailto"):
            return "#"
    return url


def _inline(escaped: str) -> str:
    """对【已转义】的单行/段落做内联变换:code → 链接 → 加粗 → 斜体。

    inline code 先抽占位再变换,确保 code 内的 ``*`` / ``[]`` 不被二次处理。
    """
    stash: list[str] = []

    def _stash(match: re.Match[str]) -> str:
        stash.append(match.group(1))
        return f"{_PH}{len(stash) - 1}{_PH}"

    text = re.sub(r"`([^`]+)`", _stash, escaped)
    text = re.sub(
        r"\[([^\]]+)\]\(([^)]+)\)",
        lambda m: f'<a href="{_safe_href(m.group(2))}" rel="noopener noreferrer">{m.group(1)}</a>',
        text,
    )
    text = re.sub(r"\*\*([^*]+)\*\*", r"<strong>\1</strong>", text)
    text = re.sub(r"\*([^*]+)\*", r"<em>\1</em>", text)

    def _restore(match: re.Match[str]) -> str:
        return f"<code>{stash[int(match.group(1))]}</code>"

    return re.sub(rf"{_PH}(\d+){_PH}", _restore, text)


def _heading_level(line: str) -> int:
    m = re.match(r"^(#{1,6})\s+", line)
    return len(m.group(1)) if m else 0


def _is_table_row(line: str) -> bool:
    return "|" in line and line.strip().startswith("|")


def _is_table_separator(line: str) -> bool:
    s = line.strip()
    return bool(s) and set(s.replace("|", "").replace("-", "").replace(":", "").replace(" ", "")) == set() and "-" in s


def _render_table(rows: list[str]) -> str:
    # rows[0] = header, rows[1] = separator, rest = body
    def cells(row: str) -> list[str]:
        s = row.strip()
        if s.startswith("|"):
            s = s[1:]
        if s.endswith("|"):
            s = s[:-1]
        return [c.strip() for c in s.split("|")]

    if len(rows) < 2:
        return ""
    head = cells(rows[0])
    body = rows[2:] if len(rows) > 2 else []
    parts = ['<table class="md-table"><thead><tr>']
    parts.extend(f"<th>{_inline(h)}</th>" for h in head)
    parts.append("</tr></thead><tbody>")
    for r in body:
        cs = cells(r)
        parts.append("<tr>")
        parts.extend(f"<td>{_inline(c)}</td>" for c in cs)
        parts.append("</tr>")
    parts.append("</tbody></table>")
    return "".join(parts)


def render_markdown(body: str) -> str:
    """对 md 正文做【先转义后变换】的安全子集渲染。

    支持子集:标题 h1-h6 / 加粗 / 斜体 / 行内代码 / 围栏代码块 / 有序·无序列表 /
    表格 / 引用 / 分割线 / 链接(scheme 白名单)。frontmatter 由调用方拆出,不在此处理。
    """
    if not body:
        return ""
    escaped = escape_html(body)
    lines = escaped.split("\n")
    out: list[str] = []
    i = 0
    n = len(lines)

    while i < n:
        line = lines[i]

        # 围栏代码块
        fenced = re.match(r"^```(.*)$", line)
        if fenced:
            lang = fenced.group(1).strip()
            cls = f' class="language-{lang}"' if lang and re.match(r"^[a-zA-Z0-9_+-]+$", lang) else ""
            code_lines: list[str] = []
            i += 1
            while i < n and not lines[i].startswith("```"):
                code_lines.append(lines[i])
                i += 1
            i += 1  # consume closing ```
            out.append(f"<pre><code{cls}>" + "\n".join(code_lines) + "</code></pre>")
            continue

        # 标题
        lvl = _heading_level(line)
        if lvl:
            content = re.sub(r"^#{1,6}\s+", "", line).strip()
            out.append(f"<h{lvl}>{_inline(content)}</h{lvl}>")
            i += 1
            continue

        # 分割线
        if re.match(r"^\s*(-{3,}|\*{3,}|_{3,})\s*$", line):
            out.append("<hr>")
            i += 1
            continue

        # 表格:当前行是表格行 + 下一行是分隔行
        if _is_table_row(line) and i + 1 < n and _is_table_separator(lines[i + 1]):
            block: list[str] = [line, lines[i + 1]]
            j = i + 2
            while j < n and _is_table_row(lines[j]):
                block.append(lines[j])
                j += 1
            out.append(_render_table(block))
            i = j
            continue

        # 引用(连续 > 行合并)。escape-first 后首字符已是 &gt;,故两种前缀都认。
        if line.startswith(">") or line.startswith("&gt;"):
            quote: list[str] = []
            while i < n and (lines[i].startswith(">") or lines[i].startswith("&gt;")):
                quote.append(re.sub(r"^(?:>|&gt;)\s?", "", lines[i]))
                i += 1
            out.append("<blockquote>" + _inline("<br>".join(quote)) + "</blockquote>")
            continue

        # 无序列表
        if re.match(r"^\s*[-*+]\s+", line):
            items: list[str] = []
            while i < n and re.match(r"^\s*[-*+]\s+", lines[i]):
                items.append(re.sub(r"^\s*[-*+]\s+", "", lines[i]).strip())
                i += 1
            out.append("<ul>" + "".join(f"<li>{_inline(it)}</li>" for it in items) + "</ul>")
            continue

        # 有序列表
        if re.match(r"^\s*\d+\.\s+", line):
            ol_items: list[str] = []
            while i < n and re.match(r"^\s*\d+\.\s+", lines[i]):
                ol_items.append(re.sub(r"^\s*\d+\.\s+", "", lines[i]).strip())
                i += 1
            out.append("<ol>" + "".join(f"<li>{_inline(it)}</li>" for it in ol_items) + "</ol>")
            continue

        # 空行
        if line.strip() == "":
            i += 1
            continue

        # 段落(连续非空非块行合并)
        para: list[str] = []
        while i < n and lines[i].strip() != "" and not lines[i].startswith(">") \
                and not lines[i].startswith("```") and not _heading_level(lines[i]) \
                and not re.match(r"^\s*[-*+]\s+", lines[i]) and not re.match(r"^\s*\d+\.\s+", lines[i]) \
                and not (_is_table_row(lines[i]) and i + 1 < n and _is_table_separator(lines[i + 1])):
            para.append(lines[i])
            i += 1
        out.append("<p>" + _inline(" ".join(p.strip() for p in para)) + "</p>")

    return "\n".join(out)


def _read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _iter_md(root: Path) -> list[Path]:
    if not root.is_dir():
        return []
    return sorted(p for p in root.rglob("*.md") if p.is_file())


def _resolve_by_task_id(repo_root: Path, task_id: str) -> str:
    """在 5_tasks/queue/** 下按 frontmatter task_id 定位卡片,返回 repo 相对路径。"""
    root = repo_root / "5_tasks" / "queue"
    matches: list[str] = []
    for p in _iter_md(root):
        try:
            fm, _body = split_frontmatter(_read_text(p))
        except OSError:
            continue
        kv = parse_frontmatter_kv(fm)
        if kv.get("task_id") == task_id:
            matches.append(p.resolve().relative_to(repo_root.resolve()).as_posix())
    if not matches:
        raise FileNotFoundError(f"no task card found for task_id: {task_id}")
    if len(matches) > 1:
        raise ValueError(f"duplicate task_id {task_id} under 5_tasks/queue: {', '.join(sorted(matches))}")
    return matches[0]


def _resolve_by_record_id(repo_root: Path, record_id: str, task_id: str | None) -> str:
    """在 5_tasks/records/** 下按 frontmatter 记录 id 别名定位,返回 repo 相对路径。

    多命中时优先取 frontmatter task_id 与传入 task_id 一致者;仍歧义则报错。
    """
    root = repo_root / "5_tasks" / "records"
    matches: list[tuple[str, str | None]] = []  # (rel_path, fm_task_id)
    for p in _iter_md(root):
        try:
            fm, _body = split_frontmatter(_read_text(p))
        except OSError:
            continue
        kv = parse_frontmatter_kv(fm)
        hit = any(str(kv.get(k)) == record_id for k in _RECORD_ID_KEYS)
        if hit:
            rel = p.resolve().relative_to(repo_root.resolve()).as_posix()
            matches.append((rel, kv.get("task_id") or kv.get("reviewed_task_id")))
    if not matches:
        raise FileNotFoundError(f"no record found for record_id: {record_id}")
    if len(matches) > 1:
        preferred = [m for m in matches if task_id and m[1] == task_id]
        pool = preferred or matches
        if len(pool) > 1:
            raise ValueError(f"ambiguous record_id {record_id}: {', '.join(sorted(m[0] for m in pool))}")
        return pool[0][0]
    return matches[0][0]


def get_markdown_source(
    *,
    path: str | None = None,
    task_id: str | None = None,
    record_id: str | None = None,
    repo_root: str | Path | None = None,
) -> dict[str, Any]:
    """读取并安全渲染一张队列卡 / 记录的 md 原文。只读。

    选择子(三选一):``path`` 显式 repo 相对路径;``task_id`` 解析队列卡;
    ``record_id``(+ 可选 ``task_id`` 消歧)解析记录。所有解析出的路径都过白名单校验。
    """
    from pathlib import Path as _Path

    root = _Path(repo_root).resolve() if repo_root else _Path.cwd()
    try:
        # 选择子模型:path 显式(独占);record_id 解析记录(task_id 可选消歧);
        # task_id 单独解析队列卡。path 与其他选择子互斥。
        if not any((path, task_id, record_id)):
            return _selector_error("At least one of path, task_id, or record_id is required")
        if path is not None:
            if task_id or record_id:
                return _selector_error("path is exclusive; do not combine with task_id or record_id")
            rel = path
        elif record_id is not None:
            rel = _resolve_by_record_id(root, record_id.strip(), (task_id or "").strip() or None)
        else:
            assert task_id is not None
            rel = _resolve_by_task_id(root, task_id.strip())

        absolute = validate_path(root, rel)
        raw = _read_text(absolute)
        fm_text, body = split_frontmatter(raw)
        rendered = render_markdown(body)
        rel_posix = absolute.relative_to(root).as_posix()
        kind = "records" if rel_posix.startswith("5_tasks/records") else "queue"
        data = {
            "path": rel_posix,
            "kind": kind,
            "has_frontmatter": bool(fm_text.strip()),
            "frontmatter": fm_text,
            "body": body,
            "rendered_html": rendered,
            "writes_enabled": False,
        }
        return make_response(
            ok=True,
            verdict="PASS",
            operation=OPERATION,
            dry_run=False,
            data=data,
            summary={"path": rel_posix, "kind": kind, "has_frontmatter": bool(fm_text.strip())},
            safety_notice=READ_SAFETY_NOTICE,
            errors=[],
        )
    except FileNotFoundError as exc:
        return blocked_response(
            operation=OPERATION, dry_run=False, category="NOT_FOUND",
            message=str(exc), safety_notice=READ_SAFETY_NOTICE,
        )
    except ValueError as exc:
        msg = str(exc)
        category = "PATH_UNSAFE" if ("escape" in msg or "absolute" in msg or "must be under" in msg) else "VALIDATION_ERROR"
        if "required" in msg:
            category = "VALIDATION_ERROR"
        return blocked_response(
            operation=OPERATION, dry_run=False, category=category,
            message=msg, field="path", safety_notice=READ_SAFETY_NOTICE,
        )


def _selector_error(message: str) -> dict[str, Any]:
    return blocked_response(
        operation=OPERATION, dry_run=False, category="VALIDATION_ERROR",
        message=message, safety_notice=READ_SAFETY_NOTICE,
    )
