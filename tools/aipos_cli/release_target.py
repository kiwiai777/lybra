"""AIPOS-F251: 受控生产发布面(发布目标声明 + 只新建写边界 + production 禁区授权 + plan / apply / verify / rollback 受控动词)。

一句话: 项目要把固定已审计提交的静态产物经 SSH 新增式发布到生产主机时, 先由顾问 `lybra project release-target declare` 声明发布目标
(主机 / 源仓 / 白名单 / 发布根 / 入口链接 / 入口顺序; Owner 确认 = 与验证根同一 owner_ref 判定), 执行体只经 `lybra release plan → apply →
verify`(回退 `rollback`)完成发布, 不再需要也不允许裸 ssh 写生产; 审计体用 verify 独立复核, 永不 apply / rollback。

防碎片化(只补既有机制之间缺的那一层, 其余全复用):
  - 声明存储 = project.json release_targets(config.schema project_json.release_targets); 唯一读取口 project_release_targets, 唯一写入口
    apply_release_target_change → workspace_config.update_project_json(锁 + 写前预检 + 写后复核); CLI 两阶段与落账 = aipos_cli._project_json_two_phase_*。
  - 主机别名 = validation_roots.hosts(validation_root.project_validation_hosts, F196); 本机判定 = validation_root.is_local_host / same_host。
  - Owner 确认 = validation_root.authorized_owner_ref(项目授权内自动引用授权修订 / 授权外拒 / 无授权须 --owner-ref), 不新造签发方式。
  - 写边界 = roles.schema write_boundary 面 release_target(本模块 judge_access 由 write_boundary.check_access 调用, 判定与文案独立于 validation_root);
    计划里每个新建路径都经 write_boundary.check_access(角色类 executor, append)判一次, 同一判定。
  - 已审计提交 = deployment_authorization.commit_pass_coverage(门生 PASS 裁决覆盖); 部署记录 = deployment_record.write_deployment_record
    (加 release 字段, 归属 governance_commit.deployment_attribution 按 release_task_id)。
  - 授权 = 本卡认领记录 owner_policy_ref 指向的项目授权(autonomy_policy.load_authorization / envelope_usage / card_zones), 越界出口 =
    autonomy_policy.authorization_refusal_message / render_authorization_command 同一渲染。
  - 主机侧 = release_helper(纯标准库单文件)经 stdin 下发, 引导钉 sha256(validation_root_helper 同一模式: helper 不落目标主机)。
fail-closed: 声明缺 = SchemaLoadError; 不合 = ReleaseTargetError(拒因码); 主机侧失败 = 停, 不继续、不猜。
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import posixpath
import re
import shlex
import subprocess
from datetime import datetime
from pathlib import Path
from typing import Any

from tools.aipos_cli import release_helper as helper
from tools.aipos_cli import validation_root as vr

SURFACE = "release_target"
PLAN_SCHEMA = "lybra-release-plan-v1"
PRODUCTION_ZONE = "production"
WRITE_COMMAND_PREFIX = "release-target "
_ID_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,62}$")
_SHA_RE = re.compile(r"^[0-9a-f]{40}$")
_ISO_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")
_JSON_BLOCK_RE = re.compile(r"```json\n(.*?)\n```", re.S)
_OK_FILE_MODES = ("100644", "100755")


class ReleaseTargetError(ValueError):
    """声明 / 发布动词不合(fail-closed)。code ∈ config.schema project_json.release_targets.reject_codes ∪ verbs.schema lybra_release.reject_codes。"""

    def __init__(self, code: str, problems: list[str], *, command: str | None = None, extra: dict[str, Any] | None = None):
        super().__init__(f"{code}: " + "; ".join(problems))
        self.code = code
        self.problems = list(problems)
        self.command = command
        self.extra = dict(extra or {})


# ---------------------------------------------------------------------------
# 声明读取(单源)
# ---------------------------------------------------------------------------

def declaration() -> dict[str, Any]:
    """roles.schema write_boundary.release_target(缺键 = SchemaLoadError)。"""
    from tools.schema_loader import SchemaLoadError, code_repo_schema_root, load_schema

    decl = (load_schema("roles", code_repo_schema_root()).get("write_boundary") or {}).get(SURFACE)
    keys = ("surface", "releases_dir", "current_link", "index_names", "forbidden_roots", "helper_timeout_seconds", "event_types",
            "check_codes", "kickoff")
    if not isinstance(decl, dict) or any(not decl.get(k) for k in keys) or decl["surface"] != SURFACE:
        raise SchemaLoadError(f"roles.schema.json write_boundary.release_target 未声明齐 {list(keys)}")
    return decl


def _project_json_decl() -> dict[str, Any]:
    from tools.schema_loader import SchemaLoadError, code_repo_schema_root, load_schema

    spec = (((load_schema("config", code_repo_schema_root()).get("configuration_sources") or {}).get("project_json") or {})
            .get("schema") or {}).get("release_targets")
    if (not isinstance(spec, dict) or not isinstance((spec.get("schema") or {}).get("entry_schema"), dict)
            or not isinstance((spec.get("schema") or {}).get("entry_link_schema"), dict) or not spec.get("reject_codes")):
        raise SchemaLoadError("config.schema.json configuration_sources.project_json.schema.release_targets(schema.entry_schema / "
                              "entry_link_schema / reject_codes)未声明齐")
    return spec


def entry_keys() -> tuple[str, ...]:
    return tuple(_project_json_decl()["schema"]["entry_schema"])


def entry_link_keys() -> tuple[str, ...]:
    return tuple(_project_json_decl()["schema"]["entry_link_schema"])


def verb_declaration() -> dict[str, Any]:
    from tools.schema_loader import SchemaLoadError, code_repo_schema_root, load_schema

    decl = (load_schema("verbs", code_repo_schema_root()).get("verbs") or {}).get("lybra_release")
    if not isinstance(decl, dict) or not decl.get("reject_codes") or not decl.get("exit_codes"):
        raise SchemaLoadError("verbs.schema.json verbs.lybra_release(reject_codes / exit_codes)未声明")
    return decl


def write_actions() -> tuple[str, ...]:
    """写动作 = verbs two_phase_protocol.project_json_writers.commands 中 release-target 前缀项(投影, 不另列)。"""
    from tools.schema_loader import code_repo_schema_root, load_schema

    commands = ((load_schema("verbs", code_repo_schema_root()).get("two_phase_protocol") or {}).get("project_json_writers") or {}).get("commands") or []
    return tuple(str(c)[len(WRITE_COMMAND_PREFIX):] for c in commands if str(c).startswith(WRITE_COMMAND_PREFIX))


def _err(code: str, *problems: str, command: str | None = None, **extra: Any) -> ReleaseTargetError:
    known = set(_project_json_decl()["reject_codes"]) | set(verb_declaration()["reject_codes"])
    if code not in known:
        from tools.schema_loader import SchemaLoadError

        raise SchemaLoadError(f"拒因码 {code} 未在 config.schema release_targets.reject_codes / verbs lybra_release.reject_codes 声明")
    return ReleaseTargetError(code, list(problems), command=command, extra=extra)


def _now() -> datetime:
    from tools.aipos_cli.clock import utc_now

    return utc_now().replace(microsecond=0)


def _iso(moment: datetime) -> str:
    from tools.aipos_cli.clock import iso_z

    return iso_z(moment)


def _single_line(value: Any, name: str) -> str:
    text = str(value or "").strip()
    if not text or "\n" in text or "\r" in text:
        raise _err("RT_DECLARATION_INVALID", f"{name} 须为非空单行串")
    return text


def _rel_problem(rel: str) -> str | None:
    """相对路径判据(白名单项 / 入口对象): 非空、不以 / 起首、无 `..` / `.` / 空段、无控制字符。"""
    text = str(rel or "")
    if not text or text.startswith("/"):
        return f"须为相对路径(不得为空或以 / 起首): {text!r}"
    if any(ord(ch) < 32 for ch in text):
        return f"含控制字符: {text!r}"
    parts = text.split("/")
    if any(p in ("..", ".") for p in parts):
        return f"含 `..` 或 `.` 段(路径穿越): {text!r}"
    if any(p == "" for p in parts):
        return f"含空段(重复斜杠): {text!r}"
    return None


def _allow_items(allowlist: list[str]) -> tuple[list[str], list[str]]:
    """白名单 → (文件项, 目录项(去结尾 /))。"""
    files = [a for a in allowlist if not a.endswith("/")]
    dirs = [a[:-1] for a in allowlist if a.endswith("/")]
    return files, dirs


def _covered(rel: str, allowlist: list[str]) -> bool:
    files, dirs = _allow_items(allowlist)
    return rel in files or any(rel == d or rel.startswith(d + "/") for d in dirs)


def _default_order(paths: list[str]) -> list[str]:
    index = [str(n) for n in declaration()["index_names"]]
    return [p for p in paths if posixpath.basename(p) not in index] + [p for p in paths if posixpath.basename(p) in index]


def entry_problems(tid: Any, entry: Any, *, at: str) -> list[str]:
    """一条发布目标声明的形与值校验(读取口与写入口同用)。返回问题原文列表。"""
    keys = entry_keys()
    if not isinstance(tid, str) or not _ID_RE.match(tid):
        return [f"{at}: 发布目标 id 须为 [a-z0-9][a-z0-9._-]*: {tid!r}"]
    if not isinstance(entry, dict) or set(entry) != set(keys):
        got = sorted(entry) if isinstance(entry, dict) else type(entry).__name__
        return [f"{at} 键须恰为 {list(keys)}, 实为 {got}"]
    problems: list[str] = []
    for key in ("host", "repo", "release_root", "owner_ref", "declared_by", "declared_at"):
        if not isinstance(entry[key], str) or not entry[key].strip() or "\n" in entry[key]:
            problems.append(f"{at}.{key} 须为非空单行串")
    if problems:
        return problems
    if not vr.is_host_label(entry["host"]):
        problems.append(f"{at}.host={entry['host']!r} 须为主机标签([A-Za-z0-9._-])")
    for key in ("declared_at", "revoked_at"):
        value = entry[key]
        if (key == "declared_at" or value is not None) and not (isinstance(value, str) and _ISO_RE.match(value)):
            problems.append(f"{at}.{key}={value!r} 须为 ISO 时刻 YYYY-MM-DDTHH:MM:SSZ")
    decl = declaration()
    forbidden = [str(r) for r in decl["forbidden_roots"]]
    root = entry["release_root"]
    problem = vr.helper.canonical_problem(root)
    if problem:
        problems.append(f"{at}.release_root {problem}")
    elif root in forbidden:
        problems.append(f"{at}.release_root={root} 是禁区目录本身(roles.schema write_boundary.release_target.forbidden_roots)")
    allow = entry["allowlist"]
    if not isinstance(allow, list) or not allow or not all(isinstance(a, str) for a in allow):
        problems.append(f"{at}.allowlist 须为非空串列表")
        allow = []
    elif len(set(allow)) != len(allow):
        problems.append(f"{at}.allowlist 有重复项")
    for item in allow:
        bad = _rel_problem(item[:-1] if item.endswith("/") else item)
        if bad:
            problems.append(f"{at}.allowlist 项 {bad}")
    links = entry["entry_links"]
    link_keys = entry_link_keys()
    paths: list[str] = []
    if not isinstance(links, list) or not links:
        problems.append(f"{at}.entry_links 须为非空列表")
        links = []
    current = str(decl["current_link"])
    for index, link in enumerate(links):
        lat = f"{at}.entry_links[{index}]"
        if not isinstance(link, dict) or set(link) != set(link_keys) or not all(isinstance(link[k], str) for k in link_keys):
            problems.append(f"{lat} 键须恰为 {list(link_keys)}(串)")
            continue
        path, target = link["path"], link["target"]
        bad = vr.helper.canonical_problem(path)
        if bad:
            problems.append(f"{lat}.path {bad}")
        elif path in forbidden or posixpath.dirname(path) == "/":
            problems.append(f"{lat}.path={path} 是禁区目录本身或在根目录下")
        elif not problem and vr.helper.is_within(path, root):
            problems.append(f"{lat}.path={path} 在发布根 {root} 内(入口须在发布根外)")
        elif not problem and vr.helper.is_within(root, path):
            problems.append(f"{lat}.path={path} 是发布根 {root} 的祖先")
        if path in paths:
            problems.append(f"{lat}.path={path} 重复")
        paths.append(path)
        if not target.startswith(current + "/"):
            problems.append(f"{lat}.target={target!r} 须为 {current}/<白名单内对象>")
            continue
        obj = target[len(current) + 1:]
        bad = _rel_problem(obj)
        if bad:
            problems.append(f"{lat}.target 对象 {bad}")
        elif allow and not _covered(obj, allow):
            problems.append(f"{lat}.target 对象 {obj} 不在白名单 {allow} 内")
    order = entry["entry_order"]
    if not isinstance(order, list) or sorted(order) != sorted(paths) or len(set(order)) != len(order):
        problems.append(f"{at}.entry_order 须恰为 entry_links 各 path 的一个排列: {order!r}")
    return problems


def project_release_targets(governance_root: str | Path, *, project: dict[str, Any] | None = None) -> dict[str, dict[str, Any]]:
    """project.json release_targets → {id: 条目}(形校验后原样; 缺段 = {})。形不合 = ReleaseTargetError(RT_DECLARATION_INVALID)。
    project = 将写入的内容(update_project_json 写前预检); 缺省读盘。只读。"""
    from tools.aipos_cli.workspace_config import read_project_json

    data = project if project is not None else read_project_json(governance_root)
    raw = data.get("release_targets")
    if raw is None:
        return {}
    if not isinstance(raw, dict):
        raise _err("RT_DECLARATION_INVALID", "project.json release_targets 须为 {<发布目标 id>: {...}}")
    problems: list[str] = []
    for tid, entry in raw.items():
        problems += entry_problems(tid, entry, at=f"project.json release_targets.{tid}")
    if problems:
        raise _err("RT_DECLARATION_INVALID", *problems)
    return {tid: dict(entry) for tid, entry in raw.items()}


def target_status(entry: dict[str, Any]) -> str:
    return "revoked" if entry.get("revoked_at") else "active"


def entry_digest(entry: dict[str, Any]) -> str:
    """声明条目摘要(计划绑定声明; 声明一变计划即失效)。"""
    return hashlib.sha256(helper.canonical_json(entry)).hexdigest()


def active_target(governance_root: Path, target_id: str) -> dict[str, Any]:
    targets = project_release_targets(governance_root)
    entry = targets.get(str(target_id or "").strip())
    if entry is None:
        raise _err("RELEASE_TARGET_INVALID", f"发布目标 {target_id!r} 不存在(已声明: {sorted(targets) or '无'}; 出口: 顾问 "
                                             "`lybra project release-target declare`)")
    if target_status(entry) != "active":
        raise _err("RELEASE_TARGET_INVALID", f"发布目标 {target_id} 已于 {entry['revoked_at']} 撤销({entry.get('revoke_reason')})")
    return entry


# ---------------------------------------------------------------------------
# 声明写入口
# ---------------------------------------------------------------------------

def _protected_problems(governance_root: Path, paths: list[str]) -> list[str]:
    out: list[str] = []
    for path in paths:
        for root in vr._protected_roots(governance_root):
            if vr.helper.is_within(path, root) or vr.helper.is_within(root, path):
                out.append(f"{path} 与治理根 / 产品仓 {root} 重叠(在其内或为其祖先)")
    return out


def _parse_entries(values: list[str] | None) -> list[dict[str, str]]:
    out: list[dict[str, str]] = []
    for raw in values or []:
        if "=" not in str(raw):
            raise _err("RT_DECLARATION_INVALID", f"--entry {raw!r} 须为 <入口绝对路径>=current/<对象>")
        path, target = str(raw).split("=", 1)
        out.append({"path": path.strip(), "target": target.strip()})
    return out


def apply_release_target_change(governance_root: str | Path, action: str, *, dry_run: bool, actor: str, **kw: Any) -> dict[str, Any]:
    """declare / revoke 的唯一写入口(update_project_json: 预演 diff / 写前预检 / 写后复核)。返回 update 结果 + {action, target_id, entry, notes}。"""
    from tools.aipos_cli.workspace_config import project_repos, update_project_json

    gov = Path(governance_root).expanduser().resolve()
    if action not in write_actions():
        raise _err("RT_DECLARATION_INVALID", f"未知动作 {action!r}(只认 {list(write_actions())})")
    actor = _single_line(actor, "--actor")
    now = _now()
    targets = project_release_targets(gov)
    notes: list[str] = []
    tid = str(kw.get("target_id") or "").strip()
    if not _ID_RE.match(tid):
        raise _err("RT_DECLARATION_INVALID", f"--id {tid!r} 须为 [a-z0-9][a-z0-9._-]*")
    if action == "declare":
        host = str(kw.get("host") or "").strip()
        if not vr.is_host_label(host):
            raise _err("RT_DECLARATION_INVALID", f"--host {host!r} 须为主机标签")
        if not vr.is_local_host(host) and host not in vr.project_validation_hosts(gov):
            raise _err("RT_HOST_UNDECLARED", f"主机 {host} 不是本机且未声明 ssh 别名(project.json validation_roots.hosts)",
                       f"出口: 顾问先 `lybra project validation-root set-host --host {host} --ssh-alias <别名>`")
        repo = str(kw.get("repo") or "").strip()
        repos = project_repos(gov)
        if repo not in (repos.get("items") or {}):
            raise _err("RT_DECLARATION_INVALID", f"--repo {repo!r} 不在 project.json repos 清单 {sorted(repos.get('items') or {})} 内"
                                                 "(出口: lybra project set-repos)")
        try:
            owner_ref = vr.authorized_owner_ref(gov, host, kw.get("owner_ref"), notes, now=now)
        except vr.ValidationRootError as exc:
            code = "RT_HOST_NOT_AUTHORIZED" if exc.code == "VR_HOST_NOT_AUTHORIZED" else "RT_DECLARATION_INVALID"
            raise _err(code, *exc.problems) from exc
        links = _parse_entries(kw.get("entries"))
        order = list(kw.get("entry_order") or []) or _default_order([l["path"] for l in links])
        if not kw.get("entry_order"):
            notes.append(f"入口顺序缺省 = 声明顺序且首页最后: {order}")
        entry = {"host": host, "repo": repo, "allowlist": list(kw.get("allowlist") or []), "release_root": str(kw.get("release_root") or ""),
                 "entry_links": links, "entry_order": order, "owner_ref": owner_ref, "declared_by": actor, "declared_at": _iso(now),
                 "revoked_at": None, "revoked_by": None, "revoke_reason": None}
        problems = entry_problems(tid, entry, at=f"发布目标 {tid}")
        if problems:
            raise _err("RT_DECLARATION_INVALID", *problems)
        overlap = _protected_problems(gov, [entry["release_root"], *(l["path"] for l in links)]) if vr.is_local_host(host) else []
        if overlap:
            raise _err("RT_ROOT_FORBIDDEN", *overlap)
        previous = targets.get(tid)
        if previous:
            notes.append(f"发布目标 {tid} 原声明(by {previous['declared_by']} @ {previous['declared_at']}, "
                         f"{'已撤销' if previous.get('revoked_at') else '有效'})本次覆盖(修改须同样的 Owner 确认; 提交史可追溯)")
        notes.append("声明本身不放行任何写入: 写入只经 `lybra release apply`(计划哈希一致 + 本卡项目授权放开 production 且含该主机 + 源提交已审计)")
    else:
        entry = dict(targets.get(tid) or {})
        if not entry:
            raise _err("RT_NOT_FOUND", f"发布目标 {tid} 不存在")
        if entry.get("revoked_at"):
            raise _err("RT_STATE_CONFLICT", f"发布目标 {tid} 已于 {entry['revoked_at']} 撤销")
        entry.update({"revoked_at": _iso(now), "revoked_by": actor, "revoke_reason": _single_line(kw.get("reason"), "--reason")})

    def mutate(data: dict[str, Any]) -> None:
        section = dict(data.get("release_targets") or {})
        section[tid] = entry
        data["release_targets"] = section

    result = update_project_json(gov, mutate, verify=project_release_targets, dry_run=dry_run)
    return {**result, "action": action, "target_id": tid, "entry": entry, "notes": notes}


# ---------------------------------------------------------------------------
# 写边界判定(write_boundary.check_access 调用; 面 release_target)
# ---------------------------------------------------------------------------

def _release_paths(entry: dict[str, Any]) -> dict[str, str]:
    decl = declaration()
    root = entry["release_root"]
    return {"root": root, "releases": f"{root}/{decl['releases_dir']}", "current": f"{root}/{decl['current_link']}"}


def judge_access(governance_root: str | Path, *, host: str | None, raw_path: str, want_rank: int, granted: str | None,
                 granted_rank: int | None) -> dict[str, Any] | None:
    """一次访问的 release_target 面判定。None = 路径与该主机任何发布目标无关(调用方按其他面判)。
    返回 {allowed, code, reason, surface, granted_level, host, target_id?, host_side_check?}(role_class / hard_rules 由调用方补)。"""
    gov = Path(governance_root).expanduser().resolve()
    local = vr.is_local_host(host)
    label = vr.local_host_label() if local else str(host).strip()
    targets = [(tid, e) for tid, e in sorted(project_release_targets(gov).items()) if vr.same_host(e["host"], label)]
    if not targets or not str(raw_path).startswith("/"):
        return None
    path = str(raw_path)
    base: dict[str, Any] = {"surface": SURFACE, "granted_level": granted, "host": label}

    def deny(code: str, reason: str, **extra: Any) -> dict[str, Any]:
        return {**base, **extra, "allowed": False, "code": code, "reason": reason}

    exact: list[tuple[str, dict[str, Any], str]] = []
    context: list[tuple[str, dict[str, Any], str]] = []
    for tid, entry in targets:
        p = _release_paths(entry)
        if path in (p["root"], p["releases"], p["current"]):
            exact.append((tid, entry, {p["root"]: "发布根", p["releases"]: "releases 目录", p["current"]: "current 链接"}[path]))
            continue
        rest = path[len(p["releases"]) + 1:] if path.startswith(p["releases"] + "/") else None
        if rest is not None and _SHA_RE.match(rest.split("/")[0]):
            exact.append((tid, entry, f"release 目录 {rest.split('/')[0]}" + ("" if "/" not in rest else " 内对象")))
            continue
        if any(l["path"] == path for l in entry["entry_links"]):
            exact.append((tid, entry, "入口链接"))
            continue
        parents = {posixpath.dirname(p["root"]), *(posixpath.dirname(l["path"]) for l in entry["entry_links"])}
        if vr.helper.is_within(path, p["root"]) or any(vr.helper.is_within(path, d) for d in parents):
            context.append((tid, entry, ""))
    if not exact and not context:
        return None
    problem = vr.helper.canonical_problem(path)
    if problem:
        return deny("PATH_NOT_CANONICAL", f"目标{problem}(发布面路径须规范, 穿越一律拒)")
    if not exact:
        tid, entry, _ = context[0]
        p = _release_paths(entry)
        return deny("RELEASE_PATH_NOT_DECLARED",
                    f"路径 {path} 不是发布目标 {tid} 声明的精确路径(主机 {label}): 面 {SURFACE} 只覆盖发布根 {p['root']}、"
                    f"{p['releases']}/<完整 commit>/、{p['current']} 与入口 {[l['path'] for l in entry['entry_links']]}; "
                    "父目录其他条目 / 发布根内其他路径一律不在声明内(出口: 改声明, 禁角色自行突破)", target_id=tid)
    tid, entry, what = exact[0]
    if target_status(entry) != "active":
        return deny("RELEASE_TARGET_REVOKED", f"发布目标 {tid} 已于 {entry['revoked_at']} 撤销({entry.get('revoke_reason')})", target_id=tid)
    if not granted or granted_rank is None:
        return deny("ROLE_NO_ROW", f"角色类对面 {SURFACE} 无声明行(roles.schema write_boundary.matrix); 出口: 补矩阵声明", target_id=tid)
    if want_rank > granted_rank:
        return deny("LEVEL_EXCEEDED", f"面 {SURFACE} 声明级别={granted}(只新建), 请求级别超出(发布面不给 mutate: 已有对象不改不删)",
                    target_id=tid)
    if want_rank == 0:
        return {**base, "allowed": True, "code": "OK", "target_id": tid, "reason": f"面 {SURFACE} 只读(发布目标 {tid}, 主机 {label})"}
    if local and os.path.lexists(path):
        return deny("RELEASE_TARGET_EXISTS", f"{what} {path} 已存在(含悬空链接; 面 {SURFACE} 只新建, 不覆盖)", target_id=tid)
    reason = f"发布目标 {tid} 声明的{what}(主机 {label}; 只新建; 实际写入只经 `lybra release apply`, 另须本卡项目授权放开 production 且含该主机)"
    if local:
        return {**base, "allowed": True, "code": "OK", "target_id": tid, "reason": reason + "; 本机 lstat 未存在"}
    return {**base, "allowed": True, "code": "OK", "target_id": tid, "reason": reason + "; 字面判定",
            "host_side_check": f"已存在判定在目标主机: `lybra release plan --task-id <卡> --target {tid} --commit <完整 commit>` 主机侧只读探测"}


# ---------------------------------------------------------------------------
# 主机侧执行(helper 经 stdin 下发, 引导钉 sha256)
# ---------------------------------------------------------------------------

_BOOTSTRAP = ("import sys,hashlib,json\n"
              "b=sys.stdin.buffer\n"
              "n=int(b.readline())\n"
              "s=b.read(n)\n"
              "if hashlib.sha256(s).hexdigest()!='@SHA@':\n"
              " sys.stdout.write(json.dumps({'ok':False,'code':'HELPER_SHA256_MISMATCH','reason':'helper sha256 != pinned @SHA@'})+'\\n')\n"
              " sys.exit(3)\n"
              "g={'__name__':'lybra_release_helper'}\n"
              "exec(compile(s,'lybra_release_helper','exec'),g)\n"
              "sys.exit(g['serve'](b.read()))\n")


def helper_source() -> bytes:
    return Path(helper.__file__).read_bytes()


def helper_sha256() -> str:
    return hashlib.sha256(helper_source()).hexdigest()


def bootstrap_code(sha256: str) -> str:
    return _BOOTSTRAP.replace("@SHA@", sha256)


def transport(governance_root: Path, entry: dict[str, Any]) -> dict[str, Any]:
    """本机主机 = 直接起子进程; 他机 = ssh <validation_roots.hosts 声明的别名>(F196)。未声明别名 = 拒。"""
    if vr.is_local_host(entry["host"]):
        return {"local": True, "alias": None, "label": "本机直接执行"}
    alias = (vr.project_validation_hosts(governance_root).get(entry["host"]) or {}).get("ssh_alias")
    if not alias:
        raise _err("RELEASE_TARGET_INVALID", f"主机 {entry['host']} 未声明 ssh 别名(validation_roots.hosts); 出口: `lybra project validation-root set-host`")
    return {"local": False, "alias": alias, "label": f"ssh {alias}"}


def run_helper(governance_root: Path, entry: dict[str, Any], op: str, request: dict[str, Any]) -> dict[str, Any]:
    """在目标主机执行 helper 一个 op。返回 {rc, result, command(展示, 引导略), helper_sha256}。无 JSON 结果 = result.ok False(HOST_NO_RESULT)。"""
    src = helper_source()
    sha = hashlib.sha256(src).hexdigest()
    way = transport(governance_root, entry)
    argv = ["python3", "-I", "-c", bootstrap_code(sha)]
    shown = f"python3 -I -c <引导: 钉 helper sha256 {sha}>"
    if not way["local"]:
        argv = ["ssh", way["alias"], shlex.join(argv)]
        shown = f"ssh {way['alias']} '{shown}'"
    stdin = f"{len(src)}\n".encode("ascii") + src + json.dumps({"schema": helper.SCHEMA, "op": op, **request}, ensure_ascii=False).encode("utf-8")
    try:
        proc = subprocess.run(argv, input=stdin, capture_output=True, timeout=int(declaration()["helper_timeout_seconds"]))
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {"rc": None, "command": shown, "helper_sha256": sha,
                "result": {"ok": False, "code": "HOST_NO_RESULT", "reason": f"{type(exc).__name__}: {exc}"}}
    lines = [ln for ln in proc.stdout.decode("utf-8", "replace").splitlines() if ln.strip()]
    try:
        result = json.loads(lines[-1]) if lines else None
    except ValueError:
        result = None
    if not isinstance(result, dict):
        tail = proc.stderr.decode("utf-8", "replace").strip()[-600:]
        result = {"ok": False, "code": "HOST_NO_RESULT", "reason": f"主机侧无结果(退出码 {proc.returncode}): {tail}"}
    return {"rc": proc.returncode, "result": result, "command": shown, "helper_sha256": sha}


# ---------------------------------------------------------------------------
# 卡 / 源 / 授权
# ---------------------------------------------------------------------------

def _card(governance_root: Path, task_id: str) -> tuple[dict[str, Any], str]:
    from tools.aipos_cli.next_resolver import _find_task_in_queue, _read_frontmatter

    path, queue = _find_task_in_queue(governance_root, task_id)
    if not path:
        raise _err("RELEASE_CARD_INVALID", f"卡 {task_id} 不在队列中")
    return _read_frontmatter(path), str(queue or "")


def _git(repo: Path, *argv: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", str(repo), *argv], capture_output=True, timeout=120)


def _source_repo(governance_root: Path, entry: dict[str, Any]) -> Path:
    from tools.aipos_cli.workspace_config import project_repos

    repo = (project_repos(governance_root).get("items") or {}).get(entry["repo"])
    if not repo:
        raise _err("RELEASE_TARGET_INVALID", f"源仓 {entry['repo']} 不在 project.json repos 清单内")
    return Path(repo)


def commit_tree(repo: Path, commit: str, tree: str | None) -> str:
    if not _SHA_RE.match(str(commit or "")):
        raise _err("RELEASE_COMMIT_INVALID", f"--commit {commit!r} 须为完整 40 位小写 sha")
    proc = _git(repo, "rev-parse", "--verify", "--quiet", f"{commit}^{{tree}}")
    got = proc.stdout.decode().strip()
    if proc.returncode != 0 or not _SHA_RE.match(got):
        raise _err("RELEASE_COMMIT_INVALID", f"{commit} 不是源仓 {repo} 中已有提交")
    if tree and tree != got:
        raise _err("RELEASE_COMMIT_INVALID", f"提交 {commit} 的 tree = {got} ≠ 预期 {tree}")
    return got


def _ls_tree(repo: Path, commit: str, rel: str, *, recursive: bool) -> list[tuple[str, str, str, str]]:
    argv = ["ls-tree", "-z", "--full-tree"] + (["-r", "-t"] if recursive else []) + [commit, "--", rel]
    proc = _git(repo, *argv)
    if proc.returncode != 0:
        raise _err("RELEASE_SOURCE_REJECTED", f"git ls-tree {rel} 失败: {proc.stderr.decode('utf-8', 'replace').strip()}")
    out = []
    for record in proc.stdout.decode("utf-8", "surrogateescape").split("\0"):
        if not record:
            continue
        meta, path = record.split("\t", 1)
        mode, kind, oid = meta.split(" ")
        out.append((mode, kind, oid, path))
    return out


def _cat_blobs(repo: Path, oids: list[str]) -> dict[str, bytes]:
    """git cat-file --batch 一次取全部 blob(按对象 id)。"""
    if not oids:
        return {}
    proc = subprocess.run(["git", "-C", str(repo), "cat-file", "--batch"], input=("\n".join(oids) + "\n").encode(), capture_output=True,
                          timeout=600)
    if proc.returncode != 0:
        raise _err("RELEASE_SOURCE_REJECTED", f"git cat-file --batch 失败: {proc.stderr.decode('utf-8', 'replace').strip()}")
    data, pos, out = proc.stdout, 0, {}
    for oid in oids:
        nl = data.index(b"\n", pos)
        header = data[pos:nl].decode().split(" ")
        if len(header) != 3 or header[1] != "blob":
            raise _err("RELEASE_SOURCE_REJECTED", f"对象 {oid} 不是 blob: {header}")
        size = int(header[2])
        out[oid] = data[nl + 1: nl + 1 + size]
        pos = nl + 1 + size + 1
    return out


def source_manifest(repo: Path, commit: str, allowlist: list[str], *, only: list[str] | None = None) -> dict[str, Any]:
    """从固定提交(不读工作树)按白名单枚举: {files: [{rel, mode, oid, sha256, size}], dirs: [rel], blobs: {oid: bytes}}。
    白名单外 / 绝对路径 / .. / 源码符号链接 / 子模块 / 项缺失 = RELEASE_SOURCE_REJECTED。"""
    chosen = list(allowlist)
    if only:
        bad = [p for p in only if _rel_problem(p.rstrip("/"))]
        if bad:
            raise _err("RELEASE_SOURCE_REJECTED", *[f"--path {p}: {_rel_problem(p.rstrip('/'))}" for p in bad])
        outside = [p for p in only if p not in allowlist]
        if outside:
            raise _err("RELEASE_SOURCE_REJECTED", f"--path {outside} 不在发布白名单 {allowlist} 内(白名单外项一律拒)")
        chosen = list(only)
    files: list[dict[str, Any]] = []
    dirs: list[str] = []
    problems: list[str] = []
    for item in chosen:
        rel = item.rstrip("/")
        bad = _rel_problem(rel)
        if bad:
            problems.append(f"白名单项 {bad}")
            continue
        top = _ls_tree(repo, commit, rel, recursive=False)
        top = [t for t in top if t[3] == rel]
        if not top:
            problems.append(f"白名单项 {item} 在提交 {commit[:12]} 中不存在")
            continue
        mode, kind, oid, _path = top[0]
        if item.endswith("/"):
            if kind != "tree":
                problems.append(f"白名单目录项 {item} 在提交中不是目录({kind})")
                continue
            dirs.append(rel)
            for m, k, o, p in _ls_tree(repo, commit, rel, recursive=True):
                if p == rel:
                    continue
                if _rel_problem(p):
                    problems.append(f"源路径 {_rel_problem(p)}")
                elif k == "tree":
                    dirs.append(p)
                elif m == "120000":
                    problems.append(f"源路径 {p} 是符号链接(源码符号链接一律拒)")
                elif k != "blob" or m not in _OK_FILE_MODES:
                    problems.append(f"源路径 {p} 不是普通文件(mode {m}, {k}; 子模块等一律拒)")
                else:
                    files.append({"rel": p, "mode": m, "oid": o})
        else:
            if mode == "120000":
                problems.append(f"白名单项 {item} 是符号链接(源码符号链接一律拒)")
            elif kind != "blob" or mode not in _OK_FILE_MODES:
                problems.append(f"白名单文件项 {item} 不是普通文件(mode {mode}, {kind})")
            else:
                files.append({"rel": rel, "mode": mode, "oid": oid})
    if problems:
        raise _err("RELEASE_SOURCE_REJECTED", *problems)
    blobs = _cat_blobs(repo, sorted({f["oid"] for f in files}))
    for f in files:
        raw = blobs[f["oid"]]
        f["sha256"] = hashlib.sha256(raw).hexdigest()
        f["size"] = len(raw)
    files.sort(key=lambda f: f["rel"])
    return {"files": files, "dirs": sorted(set(dirs), key=lambda d: (d.count("/"), d)), "blobs": blobs}


def _latest_claim_ref(governance_root: Path, task_id: str) -> str:
    """本卡认领所用授权引用 = 最新认领记录 owner_policy_ref(门铸; 读不出 = "")。"""
    from tools.aipos_cli.frontmatter import FrontmatterReadError, require_frontmatter
    from tools.aipos_cli.record_writer import record_dir

    directory = record_dir(governance_root, "claims", task_id)
    best: tuple[str, str] = ("", "")
    if directory.is_dir():
        for path in sorted(directory.glob("claim_*.md")):
            try:
                meta, _body = require_frontmatter(path)
            except FrontmatterReadError:
                continue
            stamp = str(meta.get("claimed_at") or "")
            if stamp >= best[0]:
                best = (stamp, str(meta.get("owner_policy_ref") or "").strip())
    return best[1]


def release_authorization(governance_root: Path, task_id: str, fm: dict[str, Any], entry: dict[str, Any], *,
                          target_id: str, now: datetime | None = None) -> dict[str, Any]:
    """件③ 发布写入授权(唯一判定): 卡面 authorization_zones 含 production → 本卡认领所用项目授权 → 最新修订生效 → zones_open 含 production →
    hosts 含目标主机。返回 {ok, code, reason, command, authorization_id, revision}; 越界出口复用 autonomy_policy 同一渲染。"""
    from tools.aipos_cli import autonomy_policy as ap

    host = entry["host"]
    what = f"发布目标 {target_id} 写主机 {host} 被拒"
    card_host = ap.card_host(fm) or "<卡主机>"
    sign = ap.render_authorization_command(governance_root, "sign", [
        "--grant-lane <仓> --task-mode <类型> --driver <驱动方>", f"--host {card_host}", f"--host {host}", f"--open-zone {PRODUCTION_ZONE}",
        "--max-tasks <总卡数> --expires-at <到期>"])
    zones = ap.card_zones(fm)
    if PRODUCTION_ZONE not in zones:
        return {"ok": False, "code": "RELEASE_ZONE_UNDECLARED", "authorization_id": None, "revision": None,
                "reason": f"卡 {task_id} 卡面 authorization_zones={zones} 未声明触及禁区 {PRODUCTION_ZONE}(card.schema authorization_zones)",
                "command": f"顾问改卡: authorization_zones: [{PRODUCTION_ZONE}](卡面须声明触及生产禁区)"}
    ids = ap.authorization_ids(governance_root)
    if not ids:
        return {"ok": False, "code": "RELEASE_NOT_AUTHORIZED", "authorization_id": None, "revision": None,
                "reason": f"项目无常设授权(只用旧信封 / 无授权的项目不能发布生产); {what}", "command": sign}
    ref = _latest_claim_ref(governance_root, task_id)
    if ref not in ids:
        return {"ok": False, "code": "RELEASE_NOT_AUTHORIZED", "authorization_id": None, "revision": None,
                "reason": f"本卡认领所用 {ref or '(无认领记录)'} 不是项目授权 {ids}(旧信封不放行生产发布); {what}", "command": sign}
    auth = ap.load_authorization(governance_root, ref)
    usage = ap.envelope_usage(governance_root, ref, now=now)
    decl = ap.project_authorization_declaration()
    if auth is None or usage.get("state") not in ("active", "exhausted"):
        state = usage.get("state")
        dim = {"inactive": "revoked", "expired": "expiry", "not_yet_active": "not_yet_active"}.get(str(state), "revoked")
        refusal = {"authorization_id": ref, "revision": (auth or {}).get("revision"), "dimension": dim,
                   "label": decl["dimensions"][dim]["label"], "reason": f"授权状态 {state}", "facts": {"task_id": task_id}, "auth": auth,
                   "used": usage.get("used")}
        msg = ap.authorization_refusal_message(governance_root, refusal, what=what, now=now) if auth else {"message": f"授权 {ref} 读不出", "command": sign}
        return {"ok": False, "code": "RELEASE_NOT_AUTHORIZED", "authorization_id": ref, "revision": (auth or {}).get("revision"),
                "reason": msg["message"], "command": msg["command"]}
    for dim, closed, facts in (("zone", PRODUCTION_ZONE not in (auth.get("zones_open") or []), {"zones_closed": [PRODUCTION_ZONE]}),
                               ("host", host not in (auth.get("hosts") or []), {"host": host})):
        if not closed:
            continue
        reason = (f"授权 zones_open={auth.get('zones_open')} 未放开禁区 {PRODUCTION_ZONE}" if dim == "zone"
                  else f"目标主机 {host} 不在授权允许主机 {auth.get('hosts')} 内")
        refusal = {"authorization_id": ref, "revision": auth["revision"], "dimension": dim, "label": decl["dimensions"][dim]["label"],
                   "reason": reason, "facts": {"task_id": task_id, **facts}, "auth": auth, "used": usage.get("used")}
        msg = ap.authorization_refusal_message(governance_root, refusal, what=what, now=now)
        return {"ok": False, "code": "RELEASE_NOT_AUTHORIZED", "authorization_id": ref, "revision": auth["revision"],
                "reason": msg["message"], "command": msg["command"]}
    return {"ok": True, "code": "OK", "authorization_id": ref, "revision": auth["revision"], "command": None,
            "reason": f"本卡认领所用项目授权 {ref} r{auth['revision']} 放开禁区 {PRODUCTION_ZONE} 且允许主机含 {host}"}


def _require_authorized(governance_root: Path, task_id: str, fm: dict[str, Any], entry: dict[str, Any], target_id: str) -> dict[str, Any]:
    auth = release_authorization(governance_root, task_id, fm, entry, target_id=target_id)
    if not auth["ok"]:
        raise _err(auth["code"], auth["reason"], f"出口: {auth['command']}", command=auth["command"])
    return auth


# ---------------------------------------------------------------------------
# plan
# ---------------------------------------------------------------------------

def _events_dir(governance_root: Path, task_id: str) -> Path:
    from tools.aipos_cli.record_writer import record_dir

    return record_dir(governance_root, "events", task_id)


def _write_event(governance_root: Path, task_id: str, event: str, metadata: dict[str, Any], body: str) -> str:
    from tools.aipos_cli.clock import file_slug, iso_z
    from tools.aipos_cli.record_writer import render_markdown, write_records_atomic
    from tools.schema_constants import RecordType

    from datetime import timedelta

    event_type = str(declaration()["event_types"][event])
    moment = datetime.strptime(iso_z(), "%Y-%m-%dT%H:%M:%SZ")
    for _ in range(3600):  # 同卡同类同一秒已有记录 = 顺延到下一个空闲秒(记录只追加, 绝不覆盖前一份)
        timestamp = moment.strftime("%Y-%m-%dT%H:%M:%SZ")
        record_id = f"{event_type}_{task_id}_{file_slug('compact', timestamp)}"
        if not (_events_dir(governance_root, task_id) / f"{record_id}.md").exists():
            break
        moment += timedelta(seconds=1)
    else:
        raise _err("RELEASE_HOST_FAILED", f"卡 {task_id} 的 {event_type} 记录落点连续 3600 秒均已占用(不覆盖既有记录)")
    meta = {"record_type": RecordType.TASK_PROGRESS_EVENT, "event_type": event_type, "task_id": task_id, "timestamp": timestamp, **metadata}
    written = write_records_atomic(governance_root, [("event", record_id, render_markdown(meta, body, list(meta)), task_id)])
    return str(written["paths"][0])


def _creates(entry: dict[str, Any], commit: str, manifest: dict[str, Any]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """计划新建清单(顺序 = 执行顺序)+ 入口明细。入口对象须在本次枚举内(白名单外 = 拒)。"""
    decl = declaration()
    p = _release_paths(entry)
    release_dir = f"{p['releases']}/{commit}"
    creates: list[dict[str, Any]] = [{"kind": "dir", "path": p["root"]}, {"kind": "dir", "path": p["releases"]},
                                     {"kind": "dir", "path": release_dir}]
    creates += [{"kind": "dir", "path": f"{release_dir}/{d}"} for d in manifest["dirs"]]
    creates += [{"kind": "file", "path": f"{release_dir}/{f['rel']}", "src": f["rel"], "sha256": f["sha256"], "size": f["size"]}
                for f in manifest["files"]]
    creates.append({"kind": "link", "path": p["current"], "target": f"{decl['releases_dir']}/{commit}"})
    by_path = {l["path"]: l for l in entry["entry_links"]}
    sha_by_rel = {f["rel"]: f["sha256"] for f in manifest["files"]}
    entries: list[dict[str, Any]] = []
    missing: list[str] = []
    for path in entry["entry_order"]:
        link = by_path[path]
        obj = link["target"][len(str(decl["current_link"])) + 1:]
        if obj not in sha_by_rel and obj not in manifest["dirs"]:
            missing.append(f"入口 {path} 的对象 {obj} 不在本次发布内容内(白名单外或未选)")
            continue
        target = f"{p['root']}/{link['target']}"
        creates.append({"kind": "link", "path": path, "target": target})
        entries.append({"path": path, "target": target, "object": obj, "object_sha256": sha_by_rel.get(obj)})
    if missing:
        raise _err("RELEASE_SOURCE_REJECTED", *missing)
    return creates, entries


def plan_release(governance_root: str | Path, *, task_id: str, target_id: str, commit: str, tree: str | None = None,
                 paths: list[str] | None = None, actor: str) -> dict[str, Any]:
    """件④ plan(目标主机只读): 源提交已审计 → 白名单枚举 + sha256 → 写边界逐路径判 → 主机侧只读探测(已存在含悬空链接 = 拒)→ 计划记录。"""
    from tools.aipos_cli.deployment_authorization import commit_pass_coverage
    from tools.aipos_cli.write_boundary import check_access

    gov = Path(governance_root).expanduser().resolve()
    entry = active_target(gov, target_id)
    fm, queue = _card(gov, task_id)
    repo = _source_repo(gov, entry)
    tree_got = commit_tree(repo, commit, tree)
    from tools.schema_loader import code_repo_schema_root

    coverage = commit_pass_coverage(repo, gov, commit, schema_root=code_repo_schema_root())  # 源仓可为任何项目的仓(无 schema/)
    if not coverage.get("covered"):
        raise _err("RELEASE_COMMIT_NOT_AUDITED", f"源提交 {commit} 未被门生 PASS 裁决覆盖: {coverage.get('reason')}",
                   "出口: 只发布已审计并合入的固定提交(卡面给出完整 commit 与预期 tree)")
    manifest = source_manifest(repo, commit, list(entry["allowlist"]), only=paths)
    creates, entries = _creates(entry, commit, manifest)
    refused: list[str] = []
    for item in creates:
        verdict = check_access(gov, role="executor", path=item["path"], level="append", host=entry["host"])
        if not verdict["allowed"]:
            refused.append(f"{item['path']}: [{verdict['code']}] {verdict['reason']}")
    p = _release_paths(entry)
    parents = sorted({posixpath.dirname(p["root"]), *(posixpath.dirname(e["path"]) for e in entries)})
    probe = run_helper(gov, entry, "probe", {"paths": [c["path"] for c in creates], "parents": parents})
    result = probe["result"]
    if not result.get("ok"):
        raise _err("RELEASE_HOST_FAILED", f"主机侧只读探测失败: [{result.get('code')}] {result.get('reason')}(命令 {probe['command']})")
    for path, info in result["paths"].items():
        if info.get("exists"):
            kind = "悬空链接" if info.get("type") == "link" and info.get("link_target") is not None else info.get("type")
            refused.append(f"{path}: 目的路径已存在({kind}{'→' + str(info.get('link_target')) if info.get('link_target') else ''}; 只新建, 不覆盖)")
    for directory, view in result["parents"].items():
        if (view.get("info") or {}).get("type") != "dir":
            refused.append(f"{directory}: 父目录不是已存在的目录({(view.get('info') or {}).get('type') or '不存在'}; 不越级建父目录)")
    if refused:
        raise _err("RELEASE_PLAN_REFUSED", *refused)
    authorization = release_authorization(gov, task_id, fm, entry, target_id=target_id)
    plan = {
        "schema": PLAN_SCHEMA, "task_id": task_id, "target_id": target_id, "host": entry["host"], "declaration_digest": entry_digest(entry),
        "repo": entry["repo"], "commit": commit, "tree": tree_got, "covering_verdict": coverage.get("verdict_id"),
        "covering_task_id": coverage.get("covering_task_id"), "release_root": p["root"], "creates": creates, "entries": entries,
        "baseline_parents": parents, "baseline": result["parents"], "planned_by": actor, "helper_sha256": probe["helper_sha256"],
    }
    digest = helper.plan_hash(plan)
    counts = {k: sum(1 for c in creates if c["kind"] == k) for k in helper.CREATE_KINDS}
    body = (f"# Release Plan: {task_id} → {target_id}\n\n"
            f"源 {entry['repo']} 提交 {commit}(tree {tree_got}; 门生 PASS 裁决 {coverage.get('verdict_id')} 覆盖)→ 主机 {entry['host']} "
            f"发布根 {p['root']}; 新建 目录 {counts['dir']} / 文件 {counts['file']} / 链接 {counts['link']}; 入口顺序 "
            f"{[e['path'] for e in entries]}。\n\n计划哈希 `{digest}`(apply 只执行与此一致的计划; 本记录的计划原文被改 = 拒)。\n\n"
            f"```json\n{json.dumps(plan, ensure_ascii=False, indent=1, sort_keys=True)}\n```\n")
    record = _write_event(gov, task_id, "plan", {"actor": actor, "target_id": target_id, "host": entry["host"], "commit": commit,
                                                 "tree": tree_got, "plan_hash": digest, "creates": len(creates),
                                                 "covering_verdict": coverage.get("verdict_id"), "production_changed": False}, body)
    return {"plan": plan, "plan_hash": digest, "record": record, "card_queue": queue, "counts": counts, "authorization": authorization,
            "probe_command": probe["command"]}


def find_plan(governance_root: Path, task_id: str, target_id: str, plan_hash: str) -> tuple[dict[str, Any], str]:
    """按计划哈希找计划记录并复核(重算哈希 = 记录 = 给出)。不存在 / 被改 = RELEASE_PLAN_MISMATCH。"""
    from tools.aipos_cli.frontmatter import FrontmatterReadError, require_frontmatter

    event = str(declaration()["event_types"]["plan"])
    directory = _events_dir(governance_root, task_id)
    for path in sorted(directory.glob(f"{event}_*.md")) if directory.is_dir() else []:
        try:
            meta, body = require_frontmatter(path)
        except FrontmatterReadError as exc:
            raise _err("RELEASE_PLAN_MISMATCH", f"计划记录 {path} 读不出: {exc}") from exc
        if meta.get("event_type") != event or meta.get("target_id") != target_id or meta.get("plan_hash") != plan_hash:
            continue
        match = _JSON_BLOCK_RE.search(body or "")
        if not match:
            raise _err("RELEASE_PLAN_MISMATCH", f"计划记录 {path} 无计划原文(被改)")
        try:
            plan = json.loads(match.group(1))
        except ValueError as exc:
            raise _err("RELEASE_PLAN_MISMATCH", f"计划记录 {path} 计划原文不是 JSON(被改): {exc}") from exc
        got = helper.plan_hash(plan)
        if got != plan_hash:
            raise _err("RELEASE_PLAN_MISMATCH", f"计划记录 {path} 重算哈希 {got} ≠ 记录 / 给出的 {plan_hash}(计划被改, 不执行; 出口: 重新 plan)")
        if plan.get("task_id") != task_id or plan.get("target_id") != target_id:
            raise _err("RELEASE_PLAN_MISMATCH", f"计划记录 {path} 属卡 {plan.get('task_id')} 目标 {plan.get('target_id')}")
        return plan, str(path)
    raise _err("RELEASE_PLAN_MISMATCH", f"卡 {task_id} 目标 {target_id} 无计划哈希 {plan_hash} 的计划记录(出口: 先 `lybra release plan`)")


# ---------------------------------------------------------------------------
# apply
# ---------------------------------------------------------------------------

def _baseline_diff(before: dict[str, Any], after: dict[str, Any], created: set[str]) -> dict[str, list[str]]:
    """写前写后父目录基线对照: 新增(本次新建 / 非本次)、删除、变更。"""
    added, unexpected = [], []
    for directory in sorted(set(before) | set(after)):
        b = {e["name"]: e for e in ((before.get(directory) or {}).get("entries") or [])}
        a = {e["name"]: e for e in ((after.get(directory) or {}).get("entries") or [])}
        for name in sorted(set(a) - set(b)):
            path = f"{directory.rstrip('/')}/{name}"
            (added if path in created else unexpected).append(f"新增 {path}" + ("" if path in created else "(非本次新建)"))
        for name in sorted(set(b) - set(a)):
            unexpected.append(f"删除 {directory.rstrip('/')}/{name}")
        for name in sorted(set(a) & set(b)):
            keys = ("type", "mode", "uid", "gid", "link_target", "sha256", "size")
            if any(a[name].get(k) != b[name].get(k) for k in keys):
                unexpected.append(f"变更 {directory.rstrip('/')}/{name}")
    return {"added": added, "unexpected": unexpected}


def _free_deployed_at(governance_root: Path, commit: str, stamp: str) -> str:
    """部署记录落点按 (commit, 秒) 命名; 同一提交同一秒已有记录 = 顺延到下一个空闲秒(记录只追加, 绝不覆盖前一份)。"""
    from datetime import timedelta

    from tools.aipos_cli.deployment_record import record_path

    moment = datetime.strptime(stamp, "%Y-%m-%dT%H:%M:%SZ")
    for _ in range(3600):
        candidate = moment.strftime("%Y-%m-%dT%H:%M:%SZ")
        if not record_path(governance_root, commit, candidate).exists():
            return candidate
        moment += timedelta(seconds=1)
    raise _err("RELEASE_HOST_FAILED", f"提交 {commit} 的部署记录落点连续 3600 秒均已占用(不覆盖既有记录)")


def apply_release(governance_root: str | Path, *, task_id: str, target_id: str, plan_hash: str, actor: str) -> dict[str, Any]:
    """件④ apply: 只执行与计划哈希一致的计划(授权 + 声明未变 + 源内容未变), 主机侧原子不覆盖新建, 落部署记录。"""
    from tools.aipos_cli.clock import iso_z
    from tools.aipos_cli.deployment_record import write_deployment_record

    gov = Path(governance_root).expanduser().resolve()
    entry = active_target(gov, target_id)
    fm, queue = _card(gov, task_id)
    if queue != "claimed":
        raise _err("RELEASE_CARD_INVALID", f"卡 {task_id} 在 {queue or '?'} 队列; 发布只在本卡执行中(claimed)进行")
    auth = _require_authorized(gov, task_id, fm, entry, target_id)
    plan, plan_record = find_plan(gov, task_id, target_id, plan_hash)
    if plan.get("declaration_digest") != entry_digest(entry):
        raise _err("RELEASE_PLAN_MISMATCH", f"发布目标 {target_id} 的声明在计划后已变(计划 {plan.get('declaration_digest')} ≠ 现 {entry_digest(entry)}); 出口: 重新 plan")
    repo = _source_repo(gov, entry)
    commit_tree(repo, plan["commit"], plan["tree"])
    manifest = source_manifest(repo, plan["commit"], list(entry["allowlist"]))
    by_rel = {f["rel"]: f for f in manifest["files"]}
    files: dict[str, str] = {}
    for item in plan["creates"]:
        if item["kind"] != "file":
            continue
        src = by_rel.get(item["src"])
        if src is None or src["sha256"] != item["sha256"]:
            raise _err("RELEASE_PLAN_MISMATCH", f"源文件 {item['src']} 的内容与计划 sha256 {item['sha256']} 不符(提交内容不可能变 = 计划被改)")
        files[item["src"]] = base64.b64encode(manifest["blobs"][src["oid"]]).decode("ascii")
    started = iso_z()
    ran = run_helper(gov, entry, "apply", {"plan": plan, "plan_hash": plan_hash, "files": files})
    result = ran["result"]
    finished = iso_z()
    created = [c["path"] for c in result.get("created") or []]
    diff = _baseline_diff(result.get("baseline_before") or {}, result.get("baseline_after") or {}, set(created))
    release = {
        "operation": "release", "release_task_id": task_id, "release_target": target_id, "host": entry["host"], "repo": entry["repo"],
        "source_tree": plan["tree"], "plan_hash": plan_hash, "plan_record": str(Path(plan_record).relative_to(gov)),
        "production_changed": bool(created), "created": created, "exit_code": ran["rc"], "host_result": result.get("code") or "OK",
        "host_reason": result.get("reason") or "", "failed": result.get("failed"), "started_at": started, "finished_at": finished,
        "authorization": f"{auth['authorization_id']}@r{auth['revision']}", "helper_sha256": ran["helper_sha256"],
        "baseline_added": diff["added"], "baseline_unexpected": diff["unexpected"],
    }
    record = write_deployment_record(governance_root=gov, commit=plan["commit"], actor=actor, authorization_type="verdict_ref",
                                     authorization_ref=str(plan["covering_verdict"]), runtime_directory=f"{plan['release_root']}/"
                                     f"{declaration()['releases_dir']}/{plan['commit']}", release=release,
                                     deployed_at=_free_deployed_at(gov, plan["commit"], finished))
    out = {"ok": bool(result.get("ok")), "release": release, "record": record["path"], "command": ran["command"], "result": result}
    if not result.get("ok"):
        raise _err("RELEASE_HOST_FAILED", f"主机侧 [{result.get('code')}] {result.get('reason')}; 已新建 {len(created)} 项(production_changed="
                                          f"{bool(created)}), 部署记录 {record['path']}", extra=out)
    return out


# ---------------------------------------------------------------------------
# verify / rollback
# ---------------------------------------------------------------------------

def latest_release_record(governance_root: Path, task_id: str, target_id: str) -> tuple[dict[str, Any], Path]:
    """本卡本目标最新一份 production_changed 的发布部署记录。无 = RELEASE_NOT_APPLIED。"""
    from tools.aipos_cli.frontmatter import FrontmatterReadError, require_frontmatter
    from tools.aipos_cli.record_writer import record_dir

    directory = record_dir(governance_root, "deployments")
    best: tuple[str, dict[str, Any] | None, Path | None] = ("", None, None)
    for path in sorted(directory.rglob("deployment_*.md")) if directory.is_dir() else []:
        try:
            meta, _body = require_frontmatter(path)
        except FrontmatterReadError:
            continue
        if (meta.get("operation") == "release" and meta.get("release_task_id") == task_id and meta.get("release_target") == target_id
                and meta.get("production_changed") is True and str(meta.get("deployed_at") or "") >= best[0]):
            best = (str(meta.get("deployed_at") or ""), meta, path)
    if best[1] is None or best[2] is None:
        raise _err("RELEASE_NOT_APPLIED", f"卡 {task_id} 目标 {target_id} 无改动过生产的发布部署记录")
    return best[1], best[2]


def _fetch(spec: str, sha_by_rel: dict[str, str]) -> dict[str, Any]:
    import urllib.request

    if "=" not in spec:
        return {"path": spec, "kind": "public", "ok": False, "problem": "--fetch 须为 <URL>=<release 内相对文件>"}
    url, rel = spec.rsplit("=", 1)
    want = sha_by_rel.get(rel)
    if want is None:
        return {"path": url, "kind": "public", "ok": False, "problem": f"{rel} 不是本次发布的文件"}
    try:
        with urllib.request.urlopen(url, timeout=30) as resp:  # noqa: S310 — 只读 GET, 地址由调用方给出
            status, raw = resp.status, resp.read()
    except OSError as exc:
        return {"path": url, "kind": "public", "ok": False, "problem": f"{type(exc).__name__}: {exc}"}
    got = hashlib.sha256(raw).hexdigest()
    ok = status == 200 and got == want
    return {"path": url, "kind": "public", "ok": ok, "status": status,
            "problem": None if ok else f"HTTP {status}, sha256 {got} ≠ 源清单 {want}({rel})"}


def verify_release(governance_root: str | Path, *, task_id: str, target_id: str, fetch: list[str] | None = None) -> dict[str, Any]:
    """件④ verify(只读; 审计同用): 主机侧核计划内全部新建对象与入口解析对象 sha256; --fetch 公网核摘要。不写任何记录。"""
    gov = Path(governance_root).expanduser().resolve()
    entry = active_target(gov, target_id)
    meta, record = latest_release_record(gov, task_id, target_id)
    plan, _plan_record = find_plan(gov, task_id, target_id, str(meta.get("plan_hash") or ""))
    ran = run_helper(gov, entry, "verify", {"plan": plan})
    items = list(ran["result"].get("items") or [])
    if not items and not ran["result"].get("ok"):
        items = [{"path": "(主机侧)", "kind": "host", "ok": False, "problem": f"[{ran['result'].get('code')}] {ran['result'].get('reason')}"}]
    sha_by_rel = {c["src"]: c["sha256"] for c in plan["creates"] if c["kind"] == "file"}
    items += [_fetch(spec, sha_by_rel) for spec in fetch or []]
    ok = bool(items) and all(i.get("ok") for i in items)
    return {"ok": ok, "items": items, "record": str(record), "command": ran["command"], "commit": plan["commit"],
            "plan_hash": meta.get("plan_hash")}


def rollback_release(governance_root: str | Path, *, task_id: str, target_id: str, actor: str) -> dict[str, Any]:
    """件④ rollback: 只删本次部署记录里本卡新建、当前 lstat 与链接目标仍精确匹配的入口链接(首页优先); 落回退记录。"""
    gov = Path(governance_root).expanduser().resolve()
    entry = active_target(gov, target_id)
    fm, _queue = _card(gov, task_id)
    _require_authorized(gov, task_id, fm, entry, target_id)
    meta, record = latest_release_record(gov, task_id, target_id)
    plan, _plan_record = find_plan(gov, task_id, target_id, str(meta.get("plan_hash") or ""))
    created = set(meta.get("created") or [])
    entry_paths = [e["path"] for e in plan["entries"] if e["path"] in created]
    targets = {e["path"]: e["target"] for e in plan["entries"]}
    index = [str(n) for n in declaration()["index_names"]]
    ordered = list(reversed(entry_paths))
    ordered = [p for p in ordered if posixpath.basename(p) in index] + [p for p in ordered if posixpath.basename(p) not in index]
    if not ordered:
        raise _err("RELEASE_NOT_APPLIED", f"部署记录 {record.name} 无本卡新建的入口链接可回退")
    ran = run_helper(gov, entry, "rollback", {"links": [{"path": p, "target": targets[p]} for p in ordered]})
    result = ran["result"]
    removed = list(result.get("removed") or [])
    body = (f"# Release Rollback: {task_id} → {target_id}\n\n只删本卡新建且仍精确匹配的入口链接(首页优先), 不递归、不删 release 目录。\n\n"
            f"- 计划顺序: {ordered}\n- 已删: {removed}\n- 结果: {result.get('code') or 'OK'} {result.get('reason') or ''}\n")
    event = _write_event(gov, task_id, "rollback", {"actor": actor, "target_id": target_id, "host": entry["host"],
                                                    "deployment_record": record.name, "removed": removed,
                                                    "stopped": (result.get("stopped") or {}).get("path"), "exit_code": ran["rc"],
                                                    "production_changed": bool(removed)}, body)
    out = {"ok": bool(result.get("ok")), "removed": removed, "order": ordered, "record": event, "command": ran["command"], "result": result}
    if not result.get("ok"):
        raise _err("RELEASE_ROLLBACK_STOPPED", f"回退停线: [{result.get('code')}] {result.get('reason')}; 回退记录 {event}", extra=out)
    return out


# ---------------------------------------------------------------------------
# 开工稿发布节(件⑤)
# ---------------------------------------------------------------------------

def render_kickoff_lines(governance_root: str | Path, *, task_id: str, fm: dict[str, Any]) -> list[str]:
    """卡(审计卡 = 被审卡)卡面触及禁区 production 且项目有有效发布目标 = 发布节(plan → apply → verify 命令原文与回退命令); 否则 []。"""
    from tools.aipos_cli import autonomy_policy as ap
    from tools.aipos_cli.audit_derivation import audit_card_reviewed_id, is_audit_card

    gov = Path(governance_root).expanduser().resolve()
    targets = {tid: e for tid, e in project_release_targets(gov).items() if target_status(e) == "active"}
    if not targets:
        return []
    auditing = is_audit_card(task_id, fm)
    subject_id, subject_fm = task_id, fm
    if auditing:
        subject_id = audit_card_reviewed_id(task_id, fm)
        if not subject_id:
            return []
        try:
            subject_fm, _queue = _card(gov, subject_id)
        except ReleaseTargetError:
            return []
    if PRODUCTION_ZONE not in ap.card_zones(subject_fm):
        return []
    texts = declaration()["kickoff"]
    gov_cmd = vr._gov_cmd(gov)
    lines = [str(texts["title"]), str(texts["rule"])]
    for tid, entry in sorted(targets.items()):
        try:
            way = transport(gov, entry)["label"]
        except ReleaseTargetError as exc:
            way = f"不可达: {exc}"
        values = {"target": tid, "host": entry["host"], "transport": way, "repo": entry["repo"], "release_root": entry["release_root"],
                  "allowlist": ", ".join(entry["allowlist"]), "entries": " → ".join(entry["entry_order"]), "gov_cmd": gov_cmd,
                  "task_id": subject_id}
        lines.append(vr._fill(str(texts["target"]), values))
        if auditing:
            lines.append(vr._fill(str(texts["auditor"]), values))
            continue
        lines += [vr._fill(str(texts[k]), values) for k in ("plan", "apply", "verify", "rollback")]
        auth = release_authorization(gov, subject_id, subject_fm, entry, target_id=tid)
        state = auth["reason"] if auth["ok"] else f"未就绪 [{auth['code']}] {auth['reason'].splitlines()[0]}; 出口: {auth['command']}"
        lines.append(vr._fill(str(texts["auth"]), {"auth": state}))
    return lines


def render_list_lines(targets: dict[str, dict[str, Any]], hosts: dict[str, dict[str, Any]]) -> list[str]:
    out: list[str] = []
    for tid, e in sorted(targets.items()):
        state = f"已撤销 {e['revoked_at']}({e.get('revoke_reason')})" if e.get("revoked_at") else "有效"
        alias = "本机" if vr.is_local_host(e["host"]) else f"ssh {(hosts.get(e['host']) or {}).get('ssh_alias') or '(未声明别名)'}"
        out.append(f"{tid}: {state} 主机 {e['host']}({alias}) 源仓 {e['repo']} 发布根 {e['release_root']} owner_ref={e['owner_ref']} "
                   f"by {e['declared_by']} @ {e['declared_at']}")
        out.append(f"  白名单 {e['allowlist']}")
        for path in e["entry_order"]:
            link = next(l for l in e["entry_links"] if l["path"] == path)
            out.append(f"  入口 {path} → {e['release_root']}/{link['target']}")
    return out or ["(无发布目标声明)"]


def release_history(governance_root: str | Path, task_id: str) -> list[dict[str, Any]]:
    """本卡的发布记录一览(看板「历史与审计」/ `lybra history search` 同读, 复用既有记录展示: 验证条目 {command, exit_code}):
    计划(records/events/<卡>/release_plan_*)、执行(部署记录 operation=release, 归属 release_task_id)、回退(release_rollback_*), 按时刻先后。"""
    from tools.aipos_cli.frontmatter import FrontmatterReadError, require_frontmatter
    from tools.aipos_cli.governance_commit import card_deployment_records

    gov = Path(governance_root)
    rows: list[tuple[str, dict[str, Any]]] = []
    types = declaration()["event_types"]
    directory = _events_dir(gov, task_id)
    for path in sorted(directory.glob("*.md")) if directory.is_dir() else []:
        try:
            meta, _body = require_frontmatter(path)
        except FrontmatterReadError:
            continue
        kind = meta.get("event_type")
        if kind == types["plan"]:
            rows.append((str(meta.get("timestamp") or ""), {"command": f"lybra release plan --target {meta.get('target_id')} --commit "
                                                                      f"{meta.get('commit')} → 计划哈希 {meta.get('plan_hash')}",
                                                           "exit_code": 0}))
        elif kind == types["rollback"]:
            rows.append((str(meta.get("timestamp") or ""), {"command": f"lybra release rollback --target {meta.get('target_id')} → 已删入口 "
                                                                      f"{len(meta.get('removed') or [])} 项", "exit_code": meta.get("exit_code")}))
    for path in card_deployment_records(gov, task_id):
        try:
            meta, _body = require_frontmatter(path)
        except FrontmatterReadError:
            continue
        if meta.get("operation") != "release":
            continue
        rows.append((str(meta.get("deployed_at") or ""), {"command": f"lybra release apply --target {meta.get('release_target')} → 主机 "
                                                                    f"{meta.get('host')} 新建 {len(meta.get('created') or [])} 项 "
                                                                    f"production_changed={meta.get('production_changed')}(部署记录 {path.name})",
                                                         "exit_code": meta.get("exit_code")}))
    return [row for _stamp, row in sorted(rows, key=lambda r: r[0])]
