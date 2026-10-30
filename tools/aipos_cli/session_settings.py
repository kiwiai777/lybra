"""AIPOS-F255: 顾问会话设置渲染(声明 distribution.schema harness_semantics.session_settings; 本模块 = 唯一实现)。

10-31 chris 实测: 子路径会话(.advisor-sessions/<实例>)同载上级治理根 .claude/rules/lybra-advisor.md(别的实例的章程 = 身份串); Claude Code
自动记忆缺省按 git 仓根定位, 治理根同在一个 git 仓时所有 Claude 顾问读写同一份 MEMORY.md。产品在每个本机 claude-code 会话目录渲染官方设置
文件(声明 relative_path), 写两键: claudeMdExcludes(排除上级目录中的 Lybra 章程落点)与 autoMemoryDirectory(本实例独立记忆目录; 新会话
缺省隔离, 既有会话不静默切换, 显式切换走 switch_memory 两阶段)。会话目录 .claude/ 内另写忽略文件, 只列产品渲染物(用户文件照常可见)。

写口只有两处: apply_session_settings(sync 本机会话分支调用)与 switch_memory(lybra roles memory --confirm)。其余只读。
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import shlex
import subprocess
from pathlib import Path
from typing import Any

_TEXT_KEYS = (
    "rules_ok", "rules_risk", "memory_isolated", "memory_disabled", "memory_shared", "memory_default_own", "memory_how_default",
    "memory_how_explicit", "memory_others", "not_applicable", "settings_user_file", "ignore_user_file", "switch_target", "switch_sharers",
    "switch_old_memory", "switch_suggest_isolate", "switch_suggest_share", "switch_target_state_empty", "switch_target_state_files",
    "switch_write", "switch_invariants",
)
_REJECT_CODES = ("MEMORY_SWITCH_NOT_APPLICABLE", "MEMORY_SWITCH_NOT_SYNCED", "MEMORY_SWITCH_USER_FILE", "MEMORY_SWITCH_NO_CHANGE")
_SOURCE_SCOPES = ("local", "project", "user")


class MemorySwitchError(ValueError):
    """lybra roles memory 拒(code = 声明 reject_codes 的键; 消息带出口)。"""

    def __init__(self, code: str, message: str) -> None:
        exit_text = declaration()["reject_codes"][code]
        super().__init__(f"{code}: {message}。{exit_text}")
        self.code = code


def declaration() -> dict[str, Any]:
    """distribution.schema harness_semantics.session_settings(fail-closed: 键不齐 / 形不合 = SchemaLoadError)。"""
    from tools.schema_loader import SchemaLoadError, load_schema

    where = "distribution.schema.json harness_semantics.session_settings"
    decl = (load_schema("distribution").get("harness_semantics") or {}).get("session_settings")
    if not isinstance(decl, dict):
        raise SchemaLoadError(f"{where} 未声明(AIPOS-F255)")
    rel = str(decl.get("relative_path") or "")
    exclude = decl.get("exclude") if isinstance(decl.get("exclude"), dict) else {}
    memory = decl.get("memory") if isinstance(decl.get("memory"), dict) else {}
    modes = memory.get("modes") if isinstance(memory.get("modes"), dict) else {}
    ignore = decl.get("ignore") if isinstance(decl.get("ignore"), dict) else {}
    diag = decl.get("diagnosis") if isinstance(decl.get("diagnosis"), dict) else {}
    sources = diag.get("settings_sources") if isinstance(diag.get("settings_sources"), list) else []
    texts = decl.get("texts") if isinstance(decl.get("texts"), dict) else {}
    codes = decl.get("reject_codes") if isinstance(decl.get("reject_codes"), dict) else {}
    directory = str(memory.get("directory") or "")
    loc = memory.get("default_location") if isinstance(memory.get("default_location"), dict) else {}
    problems = []
    if not isinstance(decl.get("harnesses"), list) or not decl["harnesses"]:
        problems.append("harnesses")
    if not rel or Path(rel).is_absolute() or ".." in Path(rel).parts:
        problems.append("relative_path(相对会话目录, 无 ..)")
    if not str(exclude.get("key") or "").strip() or not str(memory.get("key") or "").strip() or not str(memory.get("enabled_key") or "").strip():
        problems.append("exclude.key / memory.key / memory.enabled_key")
    if not (directory.startswith("~/") or directory.startswith("/")) or any(p not in directory for p in memory.get("placeholders") or ["?"]):
        problems.append("memory.directory(官方: 绝对路径或 ~/ 开头; 含 placeholders)")
    if set(modes) != {"isolated", "shared"} or memory.get("default_new_session") not in modes or memory.get("existing_session") not in modes:
        problems.append("memory.modes {isolated, shared} / default_new_session / existing_session")
    if not str(loc.get("base") or "").strip() or not str(loc.get("leaf") or "").strip():
        problems.append("memory.default_location {base, leaf}")
    if not str(ignore.get("dir") or "").strip() or "/" in str(ignore.get("file") or "/") or not str(ignore.get("header") or "").startswith("#"):
        problems.append("ignore {dir, file(无 /), header(# 起头)}")
    if not sources or any(not isinstance(s, dict) or s.get("scope") not in _SOURCE_SCOPES or not str(s.get("path") or "").strip() for s in sources):
        problems.append(f"diagnosis.settings_sources(每项 scope ∈ {list(_SOURCE_SCOPES)} + path)")
    missing_texts = [k for k in _TEXT_KEYS if not str(texts.get(k) or "").strip()]
    if missing_texts:
        problems.append(f"texts {missing_texts}")
    if any(not str(codes.get(k) or "").strip() for k in _REJECT_CODES):
        problems.append(f"reject_codes {list(_REJECT_CODES)}")
    if problems:
        raise SchemaLoadError(f"{where}: 未声明齐 / 形不合: {'; '.join(problems)}")
    return decl


def applies(kind: str | None) -> bool:
    """该 harness kind 是否渲染会话设置(声明 harnesses)。"""
    return str(kind or "") in declaration()["harnesses"]


def memory_modes() -> list[str]:
    """记忆模式值域(声明 memory.modes 的键; lybra roles memory --mode 的 choices 由此投影)。"""
    return list(declaration()["memory"]["modes"])


def charter_relative_paths(kind: str) -> list[str]:
    """本 harness 全部章程落点相对路径(distributions[kind=charter 且 target.harness = kind].target.relative_path; 各角色类)。"""
    from tools.schema_loader import load_schema

    return sorted({str((d.get("target") or {}).get("relative_path") or "") for d in load_schema("distribution").get("distributions") or []
                   if d.get("kind") == "charter" and (d.get("target") or {}).get("harness") == kind} - {""})


def ancestor_charter_paths(session_dir: str | Path, kind: str) -> list[str]:
    """会话目录每个上级目录(父目录直至文件系统根)× 章程落点 = 绝对路径(声明 exclude.rule)。本会话目录自己的章程不在列。"""
    here = Path(session_dir).expanduser().resolve()
    rels = charter_relative_paths(kind)
    return [str(anc / rel) for anc in here.parents for rel in rels]


def memory_directory(project: str, instance: str) -> str:
    """本实例独立记忆目录(声明 memory.directory 原值, ~/ 开头不展开 = 设置文件换机器照用)。"""
    raw = str(declaration()["memory"]["directory"])
    return raw.replace("{project}", str(project)).replace("{instance}", str(instance))


def _expand(raw: str) -> Path:
    return Path(os.path.expanduser(str(raw)))


def desired_settings(session_dir: str | Path, *, kind: str, project: str, instance: str, memory_mode: str) -> dict[str, Any]:
    """会话设置两键内容(声明 exclude / memory): 排除上级章程落点; 隔离 = 记忆目录指本实例, 共享 = 不写记忆键(Claude Code 缺省)。"""
    decl = declaration()
    if memory_mode not in decl["memory"]["modes"]:
        raise ValueError(f"记忆模式 {memory_mode!r} 不在声明 {list(decl['memory']['modes'])}")
    out: dict[str, Any] = {decl["exclude"]["key"]: ancestor_charter_paths(session_dir, kind)}
    if memory_mode == "isolated":
        out[decl["memory"]["key"]] = memory_directory(project, instance)
    return out


def _dump(obj: Any) -> str:
    return json.dumps(obj, ensure_ascii=False, indent=2) + "\n"


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def settings_path(session_dir: str | Path) -> Path:
    return Path(session_dir) / declaration()["relative_path"]


def ignore_path(session_dir: str | Path) -> Path:
    ig = declaration()["ignore"]
    return Path(session_dir) / ig["dir"] / ig["file"]


def _ownership(path: Path, wanted: str, recorded_sha: str | None) -> str:
    """落点归属: absent / ours(内容 = 本条渲染记录或 = 本次应渲染)/ user(非 Lybra 渲染物, 不覆盖)。"""
    if not path.exists() and not path.is_symlink():
        return "absent"
    if path.is_symlink() or not path.is_file():
        return "user"
    current = path.read_text(encoding="utf-8", errors="replace")
    return "ours" if current == wanted or (recorded_sha and _sha(current) == recorded_sha) else "user"


def memory_mode_for(previous: dict[str, Any] | None, *, has_manifest: bool) -> tuple[str, str]:
    """本次渲染的记忆模式与来源(声明 memory.existing_rule): 清单有记录 = 沿用; 有本地清单无记录 = 既有会话(不静默切换); 无清单 = 新会话缺省。"""
    mem = declaration()["memory"]
    rec = ((previous or {}).get("memory") or {}) if isinstance(previous, dict) else {}
    if rec.get("mode") in mem["modes"]:
        return str(rec["mode"]), str(rec.get("source") or "recorded")
    if has_manifest:
        return str(mem["existing_session"]), "existing_kept"
    return str(mem["default_new_session"]), "new_default"


def _ignore_entries(session_dir: Path, declared_files: list[str], *, settings_ours: bool) -> list[str]:
    """忽略文件条目(相对 ignore.dir, / 起头锚定): 本次 sync 声明在 ignore.dir 内的渲染物 + 本条设置文件(属 Lybra 时)+ 忽略文件自身。"""
    decl = declaration()
    base = (session_dir / decl["ignore"]["dir"]).resolve()
    entries = {"/" + decl["ignore"]["file"]}
    candidates = [Path(f) for f in declared_files] + ([session_dir / decl["relative_path"]] if settings_ours else [])
    for path in candidates:
        resolved = path.resolve() if path.is_absolute() else (session_dir / path).resolve()
        try:
            rel = resolved.relative_to(base)
        except ValueError:
            continue  # 不在 .claude/ 内的渲染物不归本忽略文件管(如 codex 的 AGENTS.md; 本声明只给 claude-code)
        entries.add("/" + rel.as_posix())
    return sorted(entries)


def _ignore_body(entries: list[str]) -> str:
    return declaration()["ignore"]["header"] + "\n" + "\n".join(entries) + "\n"


def apply_session_settings(session_dir: str | Path, *, kind: str, project: str, instance: str, declared_files: list[str],
                           previous: dict[str, Any] | None, has_manifest: bool, dry_run: bool) -> dict[str, Any]:
    """sync 本机会话分支的会话设置渲染(唯一写口之一)。返回 {settings, memory, ignore, notes, record}; record 写进本地清单 session_settings。
    设置落点 / 忽略文件是用户文件 = 不覆盖(notes 出声 + 出口), 其余照常; dry_run = 零写入(action 为将做的事)。"""
    decl = declaration()
    texts = decl["texts"]
    here = Path(session_dir)
    prev = previous if isinstance(previous, dict) else {}
    mode, source = memory_mode_for(prev, has_manifest=has_manifest)
    want = desired_settings(here, kind=kind, project=project, instance=instance, memory_mode=mode)
    s_path, s_text = settings_path(here), _dump(want)
    s_state = _ownership(s_path, s_text, ((prev.get("settings") or {}).get("rendered_sha256")))
    s_action = "user_file" if s_state == "user" else ("unchanged" if s_state == "ours" and s_path.read_text(encoding="utf-8") == s_text else "write")
    entries = _ignore_entries(here, declared_files, settings_ours=s_state != "user")
    i_path, i_text = ignore_path(here), _ignore_body(entries)
    i_state = _ownership(i_path, i_text, ((prev.get("ignore") or {}).get("rendered_sha256")))
    i_action = "user_file" if i_state == "user" else ("unchanged" if i_state == "ours" and i_path.read_text(encoding="utf-8") == i_text else "write")
    notes: list[str] = []
    if s_action == "user_file":
        notes.append(texts["settings_user_file"].format(path=s_path, keys=json.dumps(want, ensure_ascii=False)))
    if i_action == "user_file":
        notes.append(texts["ignore_user_file"].format(path=i_path, lines=json.dumps(entries, ensure_ascii=False)))
    mem_dir = want.get(decl["memory"]["key"])
    if not dry_run:
        if s_action == "write":
            s_path.parent.mkdir(parents=True, exist_ok=True)
            s_path.write_text(s_text, encoding="utf-8")
        if i_action == "write":
            i_path.parent.mkdir(parents=True, exist_ok=True)
            i_path.write_text(i_text, encoding="utf-8")
        if mem_dir and s_action != "user_file":
            _expand(mem_dir).mkdir(parents=True, exist_ok=True)  # 记忆落点在声明位置(Claude Code 写记忆前目录即在)
    record = {
        "settings": {"path": str(s_path), "rendered_sha256": None if s_action == "user_file" else _sha(s_text)},
        "memory": {"mode": mode, "source": source, "directory": mem_dir},
        "ignore": {"path": str(i_path), "rendered_sha256": None if i_action == "user_file" else _sha(i_text), "entries": entries},
    }
    return {"applies": True, "settings": {"path": str(s_path), "action": s_action, "content": want},
            "memory": {"mode": mode, "source": source, "directory": mem_dir},
            "ignore": {"path": str(i_path), "action": i_action, "entries": entries}, "notes": notes, "record": record}


# ---------------------------------------------------------------------------
# 只读诊断(lybra roles list): 会话实际会读到的设置 → 上级章程是否被排除、记忆落点与共享者
# ---------------------------------------------------------------------------

def git_root(path: str | Path) -> Path | None:
    """会话目录所在 git 仓根(主 worktree; 官方: 同仓各 worktree 与子目录共用自动记忆)。不在 git 仓 = None。"""
    here = Path(path).expanduser().resolve()
    try:
        out = subprocess.run(["git", "-C", str(here), "rev-parse", "--path-format=absolute", "--git-common-dir"], capture_output=True,
                             text=True, timeout=30)
    except FileNotFoundError:
        return None  # 本机无 git = 不在 git 仓(诊断按会话目录本身定位)
    if out.returncode != 0 or not out.stdout.strip():
        return None
    common = Path(out.stdout.strip())
    if common.name == ".git":
        return common.parent.resolve()
    top = subprocess.run(["git", "-C", str(here), "rev-parse", "--show-toplevel"], capture_output=True, text=True, timeout=30)
    return Path(top.stdout.strip()).resolve() if top.returncode == 0 and top.stdout.strip() else None


def _read_settings(path: Path) -> dict[str, Any]:
    """读一个设置文件(不存在 = {}; 坏 JSON = ValueError 上抛, 不吞)。"""
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"设置文件 {path} 不是合法 JSON: {exc}") from exc
    return data if isinstance(data, dict) else {}


def effective_settings(session_dir: str | Path) -> dict[str, Any]:
    """会话实际会读到的两键(声明 diagnosis.settings_sources; 数组键合并, 标量键按声明顺序先到先得)。返回
    {excludes: [(pattern, source)], memory: (raw, source) | None, enabled: (bool, source) | None, git_root}。"""
    decl = declaration()
    here = Path(session_dir).expanduser().resolve()
    root = git_root(here)
    ex_key, mem_key, en_key = decl["exclude"]["key"], decl["memory"]["key"], decl["memory"]["enabled_key"]
    seen: set[Path] = set()
    excludes: list[tuple[str, str]] = []
    memory: tuple[str, str] | None = None
    enabled: tuple[bool, str] | None = None
    for src in decl["diagnosis"]["settings_sources"]:
        raw = str(src["path"])
        if "{git_root}" in raw and root is None:
            continue
        path = _expand(raw.replace("{session_dir}", str(here)).replace("{git_root}", str(root)))
        if path in seen:
            continue
        seen.add(path)
        data = _read_settings(path)
        label = f"{src['scope']}:{path}"
        excludes += [(str(p), label) for p in (data.get(ex_key) or []) if isinstance(p, str)]
        if memory is None and isinstance(data.get(mem_key), str) and data[mem_key].strip():
            memory = (data[mem_key].strip(), label)
        if enabled is None and isinstance(data.get(en_key), bool):
            enabled = (data[en_key], label)
    return {"excludes": excludes, "memory": memory, "enabled": enabled, "git_root": root}


def _glob_regex(pattern: str) -> re.Pattern[str]:
    """claudeMdExcludes glob(按绝对路径匹配): ** 跨目录, * / ? 不跨 /。"""
    out, i = "", 0
    while i < len(pattern):
        if pattern.startswith("**/", i):
            out, i = out + "(?:.*/)?", i + 3
        elif pattern.startswith("**", i):
            out, i = out + ".*", i + 2
        elif pattern[i] == "*":
            out, i = out + "[^/]*", i + 1
        elif pattern[i] == "?":
            out, i = out + "[^/]", i + 1
        else:
            out, i = out + re.escape(pattern[i]), i + 1
    return re.compile(out + r"\Z")


def is_excluded(path: str, patterns: list[str]) -> bool:
    """path(绝对)是否被任一排除项命中(绝对路径相等, 或 glob 匹配)。"""
    for pat in patterns:
        expanded = os.path.expanduser(pat)
        if expanded == path or (any(c in expanded for c in "*?") and _glob_regex(expanded).match(path)):
            return True
    return False


def default_memory_dir(session_dir: str | Path, root: Path | None) -> Path:
    """未设 autoMemoryDirectory 时的缺省记忆目录(声明 memory.default_location; 目录名按观察到的命名, 比对按 git 仓根)。"""
    loc = declaration()["memory"]["default_location"]
    anchor = root if root is not None else Path(session_dir).expanduser().resolve()
    return _expand(loc["base"]) / re.sub(r"[^A-Za-z0-9]", "-", str(anchor)) / loc["leaf"]


def memory_view(session_dir: str | Path, *, effective: dict[str, Any] | None = None) -> dict[str, Any]:
    """本会话实际记忆落点: {mode(isolated|default|disabled), dir, key, source, git_root}。key = 比对键(展开 + resolve 的目录)。"""
    eff = effective if effective is not None else effective_settings(session_dir)
    if eff["enabled"] is not None and eff["enabled"][0] is False:
        return {"mode": "disabled", "dir": None, "key": None, "source": eff["enabled"][1], "git_root": eff["git_root"]}
    if eff["memory"] is not None:
        path = _expand(eff["memory"][0])
        return {"mode": "isolated", "dir": str(path), "key": str(path.resolve()), "source": eff["memory"][1], "git_root": eff["git_root"]}
    path = default_memory_dir(session_dir, eff["git_root"])
    return {"mode": "default", "dir": str(path), "key": str(path.resolve()), "source": None, "git_root": eff["git_root"]}


def registered_local_sessions(governance_root: str | Path) -> list[dict[str, Any]]:
    """本机已登记的 claude-code 会话(本治理根 + home 根下各已建项目; enrollment.advisor_sessions 每项另带 governance_root)。
    别的项目治理根读不出 = 不吞: 记一条 {error} 供诊断出声。"""
    from tools.aipos_cli.enrollment import advisor_sessions
    from tools.aipos_cli.workspace_config import _project_candidates, resolve_home_root

    here = Path(governance_root).resolve()
    roots = [here]
    home = resolve_home_root()
    roots += [r for r in ((home / name).resolve() for name in _project_candidates(home)) if r != here]
    out: list[dict[str, Any]] = []
    for root in roots:
        try:
            sessions = advisor_sessions(root)
        except (OSError, ValueError) as exc:
            out.append({"governance_root": str(root), "error": f"{exc.__class__.__name__}: {exc}"})
            continue
        out += [{**s, "governance_root": str(root)} for s in sessions if s["local"] and applies(s["kind"])]
    return out


def sync_command(governance_root: str | Path) -> str:
    root = str(Path(governance_root).resolve())
    return shlex.join(["lybra", "sync", "--harness-root", root, "--workspace-root", root])


def switch_command(governance_root: str | Path, instance: str, mode: str) -> str:
    return shlex.join(["lybra", "roles", "memory", "--workspace-root", str(Path(governance_root).resolve()), "--instance", instance,
                       "--mode", mode])


def session_view(governance_root: str | Path, session: dict[str, Any], *, peers: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    """一个顾问会话的只读诊断(roles list 文本与 --json 同读): 上级章程串身份风险 + 记忆落点与本机共享者。
    返回 {applies, rules: {risk, paths, command}, memory: {mode, dir, source, git_root, shared_with, risk, command}, lines}。"""
    decl = declaration()
    texts = decl["texts"]
    if not session.get("local") or not applies(session.get("kind")):
        return {"applies": False, "lines": [texts["not_applicable"].format(harnesses=", ".join(decl["harnesses"]))]}
    here = Path(session["dir"]).expanduser().resolve()
    eff = effective_settings(here)
    patterns = [p for p, _src in eff["excludes"]]
    loaded = [p for p in ancestor_charter_paths(here, session["kind"]) if Path(p).is_file() and not is_excluded(p, patterns)]
    rules = {"risk": bool(loaded), "paths": loaded, "command": sync_command(governance_root) if loaded else None}
    mem = memory_view(here, effective=eff)
    everyone = peers if peers is not None else registered_local_sessions(governance_root)
    sharers = []
    for other in everyone:
        if other.get("error") or (other["instance"] == session["instance"] and other["governance_root"] == str(Path(governance_root).resolve())):
            continue
        if mem["key"] is not None and memory_view(other["dir"])["key"] == mem["key"]:
            sharers.append(other["instance"])
    own_repo = mem["mode"] == "default" and (eff["git_root"] is None or eff["git_root"] == here)
    risk = bool(sharers) or (mem["mode"] == "default" and not own_repo)
    command = switch_command(governance_root, session["instance"], "isolated") if risk else None
    lines = [texts["rules_risk"].format(paths=", ".join(loaded), command=rules["command"]) if loaded else texts["rules_ok"]]
    if mem["mode"] == "disabled":
        lines.append(texts["memory_disabled"])
    elif not risk:
        lines.append(texts["memory_isolated"].format(dir=mem["dir"]) if mem["mode"] == "isolated"
                     else texts["memory_default_own"].format(dir=mem["dir"]))
    else:
        how = (texts["memory_how_default"].format(root=eff["git_root"]) if mem["mode"] == "default"
               else texts["memory_how_explicit"].format(source=mem["source"]))
        others = texts["memory_others"].format(instances=", ".join(sharers)) if sharers else ""
        lines.append(texts["memory_shared"].format(dir=mem["dir"], how=how, others=others, command=command))
    lines += [f"⚠ {o['governance_root']}: {o['error']}" for o in everyone if o.get("error")]
    return {"applies": True, "rules": rules,
            "memory": {**{k: (str(v) if isinstance(v, Path) else v) for k, v in mem.items() if k != "key"}, "shared_with": sharers, "risk": risk,
                       "command": command},
            "lines": lines}


# ---------------------------------------------------------------------------
# 显式切换(lybra roles memory; 两阶段: 不带 --confirm = 预演零写入)
# ---------------------------------------------------------------------------

def _session_manifest(governance_root: Path, instance: str, role: str) -> tuple[Path, dict[str, Any]]:
    """该会话的本地清单(治理根 .lybra/.version-<键>; 读键同 sync, distribution_sync._session_manifest_keys)。无 = (写键路径, {})。"""
    from tools.aipos_cli.charter_render import workstation_identity
    from tools.aipos_cli.distribution_sync import _load_local_manifest, _local_manifest_path, _session_manifest_keys

    lybra_dir = governance_root / ".lybra"
    top = str(workstation_identity(governance_root).get("instance") or "")
    read_key, _write_key = _session_manifest_keys(lybra_dir, role, instance, slot_scoped=instance != top)
    return (_local_manifest_path(lybra_dir, read_key, manifest_dir=lybra_dir),
            _load_local_manifest(lybra_dir, read_key, manifest_dir=lybra_dir))


def switch_memory(governance_root: str | Path, *, instance: str, mode: str | None, confirm: bool) -> dict[str, Any]:
    """显式切换本实例会话记忆(两阶段)。mode 缺 = 只出现状与建议(零写入)。预演列出: 现落点与共享者、将切到的目录、旧记忆处理建议、
    将写的清单与设置文件; --confirm = 写本地清单 session_settings.memory(source=explicit)并按同一渲染器重写会话设置文件。
    拒(MemorySwitchError): 非本机 claude-code 会话 / 未 sync(无本地清单)/ 设置落点是用户文件 / 无变化。"""
    from tools.aipos_cli.enrollment import _project_name

    decl = declaration()
    texts = decl["texts"]
    root = Path(governance_root).resolve()
    peers = registered_local_sessions(root)
    session = next((s for s in peers if not s.get("error") and s["instance"] == instance and s["governance_root"] == str(root)), None)
    if session is None:
        raise MemorySwitchError("MEMORY_SWITCH_NOT_APPLICABLE", f"实例 {instance} 不是本项目 {root} 已登记的本机 claude-code 会话")
    manifest_path, manifest = _session_manifest(root, instance, str(session["role"]))
    if not manifest:
        raise MemorySwitchError("MEMORY_SWITCH_NOT_SYNCED", f"实例 {instance} 无本地清单 {manifest_path}")
    here = Path(session["dir"]).expanduser().resolve()
    view = session_view(root, session, peers=peers)
    previous = manifest.get("session_settings") if isinstance(manifest.get("session_settings"), dict) else {}
    cur_mode, _src = memory_mode_for(previous, has_manifest=True)
    project = _project_name(root)
    old_dir = view["memory"]["dir"]
    out: dict[str, Any] = {"ok": True, "operation": "roles_memory", "governance_root": str(root), "instance": instance, "session_dir": str(here),
                           "current": {"mode": cur_mode, "effective": view["memory"]}, "mode": mode, "dry_run": not confirm, "written": [],
                           "lines": []}
    if mode is None:
        out["lines"] = view["lines"]
        return out
    if mode not in decl["memory"]["modes"]:
        raise ValueError(f"--mode {mode!r} 不在声明 {list(decl['memory']['modes'])}")
    want = desired_settings(here, kind=session["kind"], project=project, instance=instance, memory_mode=mode)
    s_path, s_text = settings_path(here), _dump(want)
    if _ownership(s_path, s_text, ((previous.get("settings") or {}).get("rendered_sha256"))) == "user":
        raise MemorySwitchError("MEMORY_SWITCH_USER_FILE", texts["settings_user_file"].format(path=s_path, keys=json.dumps(want, ensure_ascii=False)))
    new_dir = str(_expand(want[decl["memory"]["key"]])) if mode == "isolated" else str(default_memory_dir(here, git_root(here)))
    if mode == cur_mode and s_path.is_file() and s_path.read_text(encoding="utf-8") == s_text:
        raise MemorySwitchError("MEMORY_SWITCH_NO_CHANGE", f"实例 {instance} 记忆已是{decl['memory']['modes'][mode]}({new_dir})")
    target = Path(new_dir)
    count = sum(1 for p in target.rglob("*") if p.is_file()) if target.is_dir() else 0
    state = texts["switch_target_state_files"].format(count=count) if count else texts["switch_target_state_empty"]
    suggestion = (texts["switch_suggest_isolate"].format(state=state, old_dir=old_dir, new_dir=new_dir) if mode == "isolated"
                  else texts["switch_suggest_share"].format(new_dir=new_dir, old_dir=old_dir))
    will = "" if confirm else "将"
    modes = decl["memory"]["modes"]
    out["target"] = {"mode": mode, "dir": new_dir, "files": count}
    out["lines"] = [
        texts["switch_target"].format(instance=instance, old_mode=modes[cur_mode], old_dir=old_dir, new_mode=modes[mode], new_dir=new_dir),
        texts["switch_sharers"].format(instances=", ".join(view["memory"]["shared_with"]) or "-"),
        texts["switch_old_memory"].format(old_dir=old_dir, suggestion=suggestion),
        texts["switch_write"].format(will=will, path=manifest_path, mode=mode, settings=s_path),
        texts["switch_invariants"],
    ]
    if not confirm:
        return out
    record = dict(previous)
    record["memory"] = {"mode": mode, "source": "explicit", "directory": want.get(decl["memory"]["key"])}
    record["settings"] = {"path": str(s_path), "rendered_sha256": _sha(s_text)}
    s_path.parent.mkdir(parents=True, exist_ok=True)
    s_path.write_text(s_text, encoding="utf-8")
    if mode == "isolated":
        target.mkdir(parents=True, exist_ok=True)
    ig = record.get("ignore") if isinstance(record.get("ignore"), dict) else {}
    entries = sorted(set(ig.get("entries") or []) | set(_ignore_entries(here, [], settings_ours=True)))
    i_path, i_text = ignore_path(here), _ignore_body(entries)
    written = [str(manifest_path), str(s_path)]
    if _ownership(i_path, i_text, ig.get("rendered_sha256")) != "user":
        i_path.parent.mkdir(parents=True, exist_ok=True)
        i_path.write_text(i_text, encoding="utf-8")
        record["ignore"] = {"path": str(i_path), "rendered_sha256": _sha(i_text), "entries": entries}
        written.append(str(i_path))
    else:
        out["lines"].append(texts["ignore_user_file"].format(path=i_path, lines=json.dumps(entries, ensure_ascii=False)))
    manifest["session_settings"] = record
    manifest_path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")
    out["written"] = written
    return out
