"""AIPOS-F190: 跨机临时验证根(按 项目 / 主机 / 执行或审计实例 / 卡 / run 声明有限可写根)。

一句话: 需要在其他主机(如 Mac)做按卡隔离验证的卡, 由顾问以 `lybra project validation-root declare` 两阶段声明
「主机 X 上 <base>/<卡ID>/<run-id>/ 归实例 I 写」, 每次运行 run-start 铸 run-id; write-boundary 新面 validation_root 只放行
本实例本卡本活动 run 目录, 跨卡 / 跨 run / 跨角色 / 越界 / .. 穿越 / 符号链接逃逸 / 撤销或到期后一律拒。

防碎片化(全部读既有单源, 本模块不另立第二份):
  - 声明存储 = project.json validation_roots(声明 config.schema configuration_sources.project_json.schema.validation_roots);
    唯一读取口 project_validation_roots; 唯一写入口 apply_validation_root_change → workspace_config.update_project_json
    (project.json 唯一写路径: 锁 + 写前预检 + 写后复核); CLI 两阶段与落账 = aipos_cli._project_json_two_phase_* 同一包装。
  - 判定细则 / base 禁区 / 工具重定向 / harness 沙箱渲染 = roles.schema write_boundary.validation_root(唯一声明);
    判定入口 = write_boundary.check_access(经本模块 judge_access, 面 validation_root)。
  - 主机身份(指纹)与 realpath 判定 = validation_root_helper(纯标准库单文件, 目标主机同一份); 本模块只 import, 不另写。
  - 开工稿 / 章程同源: render_section_lines(措辞读 verbs.schema lybra_kickoff.validation_root_texts)。
  - AIPOS-F194 iOS 本 run 专用模拟器(Owner 10-20 定, 取代已撤下的 simctl --set 独立设备集): 声明 roles.schema
    write_boundary.validation_root.ios_simulator; UDID 绑定在 project.json 本 run 记录(runs[].simulator, 与 run-id 同一处, 不另立登记文件;
    写入口 apply_validation_root_change sim-bind / run-finish --sim-evidence); 主机侧建 / 核 / 删与证据核对 = helper sim-* 与
    helper.verify_sim_evidence(治理根侧 run-finish 同一函数); 设备目录写判定 = judge_access(同面 validation_root, scope ios_simulator)。
  - AIPOS-F191 目录链初始化: 目标主机上的空目录链(<base>/<卡ID>/<run-id>)只经 helper init-run 建; 命令唯一铸造口 init_run_command
    (封印 = helper.init_seal 同一实现), 出口三处同源: run-start --confirm 输出 / 开工稿与章程验证根节 / `validation-root init-command`。
fail-closed: 声明缺 = SchemaLoadError; 段形不合 = ValidationRootError(VR_DECLARATION_INVALID); 违规 = 拒, 不吞。
"""
from __future__ import annotations

import hashlib
import posixpath
import re
import shlex
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from tools.aipos_cli import validation_root_helper as helper

SURFACE = "validation_root"
CARD_KINDS = ("execution_card", "audit_card")
_HOST_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,62}$")
_SHA_RE = re.compile(r"^[0-9a-f]{40}$")
_ISO_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")
ENTRY_KEYS = ("id", "host", "host_fingerprint", "base", "task_id", "instance", "role_class", "owner_ref", "declared_by", "declared_at",
              "expires_at", "revoked_at", "revoked_by", "revoke_reason", "runs")
RUN_KEYS = ("run_id", "candidate", "started_at", "started_by", "finished_at", "finished_by")
RUN_OPTIONAL_KEYS = ("simulator",)  # AIPOS-F194: 缺键 = 本 run 未绑定专用模拟器(行为不变)
SIM_KEYS = ("udid", "name", "bound_at", "bound_by", "verified_at", "verified_by", "evidence_sha256")
TEXT_KEYS = ("title", "relation", "declaration", "directory", "run_active", "run_none", "run_finish", "check_dev", "helper",
             "sandbox", "redirect_title", "redirect", "side_effect", "block", "init_run", "sim_title", "sim_step")
ACTIONS = ("declare", "revoke", "run-start", "run-finish", "sim-bind")


class ValidationRootError(ValueError):
    """声明 / 写命令不合(fail-closed, project.json 不动)。code ∈ config.schema project_json.validation_roots.reject_codes。"""

    def __init__(self, code: str, problems: list[str]):
        super().__init__(f"{code}: " + "; ".join(problems))
        self.code = code
        self.problems = list(problems)


# ---------------------------------------------------------------------------
# 声明读取(单源)
# ---------------------------------------------------------------------------

def declaration() -> dict[str, Any]:
    """roles.schema write_boundary.validation_root(判定细则 / 禁区 / 重定向 / 沙箱)。缺键 = SchemaLoadError。"""
    from tools.aipos_cli.write_boundary import load_write_boundary_declaration
    from tools.schema_loader import SchemaLoadError

    decl = load_write_boundary_declaration().get(SURFACE)
    needed = ("path_template", "run_id_template", "default_ttl_hours", "max_ttl_hours", "forbidden_bases", "check_codes",
              "tool_redirects", "system_side_effects", "block_conditions", "harness_sandbox", "relation_to_hard_rules")
    if not isinstance(decl, dict) or any(k not in decl for k in needed):
        raise SchemaLoadError(f"roles.schema.json write_boundary.validation_root({' / '.join(needed)})未声明齐")
    fb = decl["forbidden_bases"]
    if not isinstance(fb, dict) or any(not isinstance(fb.get(k), list) for k in ("exact", "system_prefixes", "home_roots", "home_relative_global")):
        raise SchemaLoadError("roles.schema.json write_boundary.validation_root.forbidden_bases(exact / system_prefixes / home_roots / "
                              "home_relative_global)未声明")
    kinds = {k: v for k, v in (decl.get("card_kind_by_class") or {}).items() if k != "description"}
    writers = sorted(cls for cls, row in load_write_boundary_declaration()["matrix"].items() if (row or {}).get(SURFACE) == "mutate")
    if sorted(kinds) != writers or any(v not in CARD_KINDS for v in kinds.values()):
        raise SchemaLoadError(f"roles.schema.json write_boundary.validation_root.card_kind_by_class 须恰覆盖 matrix 中 {SURFACE}=mutate 的类 "
                              f"{writers}, 值 ∈ {list(CARD_KINDS)}")
    for key in ("default_ttl_hours", "max_ttl_hours"):
        if isinstance(decl[key], bool) or not isinstance(decl[key], int) or decl[key] <= 0:
            raise SchemaLoadError(f"roles.schema.json write_boundary.validation_root.{key} 须为正整数(小时)")
    return decl


def ios_simulator_declaration() -> dict[str, Any]:
    """AIPOS-F194: roles.schema write_boundary.validation_root.ios_simulator(本 run 专用模拟器: 命名 / 可写面 / 证据 / 动作模板 / 步骤)。
    缺键 = SchemaLoadError。"""
    from tools.schema_loader import SchemaLoadError

    sim = declaration().get("ios_simulator")
    needed = ("name_template", "devices_rel", "writable", "evidence_file", "side_evidence_file", "xcodebuild_actions", "xcodebuild_template",
              "xcode_temp_side_effects", "steps", "not_allowed", "retracted")
    if not isinstance(sim, dict) or any(k not in sim for k in needed):
        raise SchemaLoadError(f"roles.schema.json write_boundary.validation_root.ios_simulator({' / '.join(needed)})未声明齐")
    patterns = (sim["xcode_temp_side_effects"] or {}).get("patterns")
    if not isinstance(patterns, list) or not patterns or not all(isinstance(p, str) and p and "/" not in p for p in patterns):
        raise SchemaLoadError("roles.schema.json write_boundary.validation_root.ios_simulator.xcode_temp_side_effects.patterns 须为非空顶层名 glob 列表")
    if not isinstance(sim["steps"], list) or not all(isinstance(t, str) and t for t in sim["steps"]):
        raise SchemaLoadError("roles.schema.json write_boundary.validation_root.ios_simulator.steps 须为非空串列表")
    return sim


def card_kinds() -> dict[str, str]:
    """可声明验证根的角色类 → 卡种(roles.schema write_boundary.validation_root.card_kind_by_class; 唯一声明)。"""
    return {k: str(v) for k, v in declaration()["card_kind_by_class"].items() if k != "description"}


def reject_codes() -> dict[str, str]:
    """config.schema project_json.schema.validation_roots.reject_codes。缺 = SchemaLoadError。"""
    from tools.schema_loader import SchemaLoadError, load_schema

    decl = (((load_schema("config").get("configuration_sources") or {}).get("project_json") or {}).get("schema") or {}).get("validation_roots")
    codes = decl.get("reject_codes") if isinstance(decl, dict) else None
    needed = ("VR_DECLARATION_INVALID", "VR_BASE_FORBIDDEN", "VR_INSTANCE_NOT_ENROLLED", "VR_ROLE_NOT_ALLOWED", "VR_TASK_NOT_OWNED",
              "VR_HOST_FINGERPRINT_REQUIRED", "VR_HOST_FINGERPRINT_MISMATCH", "VR_NOT_FOUND", "VR_STATE_CONFLICT", "VR_CANDIDATE_INVALID",
              "VR_SIM_INVALID", "VR_SIM_EVIDENCE_REJECTED")
    if not isinstance(codes, dict) or any(not codes.get(k) for k in needed):
        raise SchemaLoadError("config.schema.json configuration_sources.project_json.schema.validation_roots.reject_codes 未声明齐")
    return codes


def section_texts() -> dict[str, str]:
    """开工稿 / 章程「跨机临时验证根」节措辞 = verbs.schema lybra_kickoff.validation_root_texts(同源)。缺 = SchemaLoadError。"""
    from tools.aipos_cli.subagent_kickoff import kickoff_contract
    from tools.schema_loader import SchemaLoadError

    texts = kickoff_contract().get("validation_root_texts")
    if not isinstance(texts, dict) or any(not isinstance(texts.get(k), str) or not texts[k] for k in TEXT_KEYS):
        raise SchemaLoadError(f"verbs.schema.json verbs.lybra_kickoff.validation_root_texts({' / '.join(TEXT_KEYS)})未声明齐")
    return texts


def _err(code: str, *problems: str) -> ValidationRootError:
    reject_codes()  # 拒因码须已声明(fail-closed)
    return ValidationRootError(code, list(problems))


# ---------------------------------------------------------------------------
# 时刻 / 主机
# ---------------------------------------------------------------------------

def _now() -> datetime:
    from tools.aipos_cli.clock import utc_now

    return utc_now()


def _iso(moment: datetime) -> str:
    from tools.aipos_cli.clock import iso_z

    return iso_z(moment)


def _parse_iso(value: Any, where: str) -> datetime:
    if not isinstance(value, str) or not _ISO_RE.match(value):
        raise _err("VR_DECLARATION_INVALID", f"{where}={value!r} 须为 ISO 时刻 YYYY-MM-DDTHH:MM:SSZ")
    return datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)


def local_host_label() -> str:
    """本机主机标签(唯一实现 = validation_root_helper.host_label)。"""
    return helper.host_label()


def is_local_host(host: str | None) -> bool:
    return not str(host or "").strip() or str(host).strip().lower() == local_host_label()


# ---------------------------------------------------------------------------
# 读取口
# ---------------------------------------------------------------------------

def project_validation_roots(governance_root: str | Path, *, project: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    """project.json validation_roots.declarations → 条目列表(形校验后原样; 缺段 = [])。形不合 = ValidationRootError(VR_DECLARATION_INVALID)。
    project = 将写入的内容(update_project_json 写前预检); 缺省读盘(读不出 = 原异常上抛, 不吞成无声明)。只读。"""
    from tools.aipos_cli.workspace_config import read_project_json

    data = project if project is not None else read_project_json(governance_root)
    raw = data.get("validation_roots")
    if raw is None:
        return []
    where = "project.json validation_roots"
    if not isinstance(raw, dict) or set(raw) != {"declarations"} or not isinstance(raw["declarations"], list):
        raise _err("VR_DECLARATION_INVALID", f"{where} 须为 {{declarations: [...]}}")
    seen: set[str] = set()
    out: list[dict[str, Any]] = []
    for index, entry in enumerate(raw["declarations"]):
        at = f"{where}.declarations[{index}]"
        if not isinstance(entry, dict) or set(entry) != set(ENTRY_KEYS):
            keys = sorted(entry) if isinstance(entry, dict) else type(entry).__name__
            raise _err("VR_DECLARATION_INVALID", f"{at} 键须恰为 {list(ENTRY_KEYS)}, 实为 {keys}")
        for key in ("id", "host", "base", "task_id", "instance", "role_class", "owner_ref", "declared_by"):
            if not isinstance(entry[key], str) or not entry[key].strip() or "\n" in entry[key]:
                raise _err("VR_DECLARATION_INVALID", f"{at}.{key} 须为非空单行串")
        if entry["id"] in seen:
            raise _err("VR_DECLARATION_INVALID", f"{at}.id={entry['id']!r} 重复")
        seen.add(entry["id"])
        if entry["role_class"] not in card_kinds():
            raise _err("VR_DECLARATION_INVALID", f"{at}.role_class={entry['role_class']!r} 不在 {sorted(card_kinds())}")
        if entry["host_fingerprint"] is not None and not helper.is_fingerprint(entry["host_fingerprint"]):
            raise _err("VR_DECLARATION_INVALID", f"{at}.host_fingerprint={entry['host_fingerprint']!r} 须为 hfp1 指纹或 null")
        problem = helper.canonical_problem(entry["base"])
        if problem:
            raise _err("VR_DECLARATION_INVALID", f"{at}.base {problem}")
        _parse_iso(entry["declared_at"], f"{at}.declared_at")
        _parse_iso(entry["expires_at"], f"{at}.expires_at")
        if entry["revoked_at"] is not None:
            _parse_iso(entry["revoked_at"], f"{at}.revoked_at")
        if not isinstance(entry["runs"], list):
            raise _err("VR_DECLARATION_INVALID", f"{at}.runs 须为列表")
        for rindex, run in enumerate(entry["runs"]):
            rat = f"{at}.runs[{rindex}]"
            if not isinstance(run, dict) or not set(RUN_KEYS) <= set(run) or set(run) - set(RUN_KEYS) - set(RUN_OPTIONAL_KEYS):
                raise _err("VR_DECLARATION_INVALID", f"{rat} 键须为 {list(RUN_KEYS)}(可选 {list(RUN_OPTIONAL_KEYS)})")
            sim = run.get("simulator")
            if sim is not None:
                if not isinstance(sim, dict) or set(sim) != set(SIM_KEYS) or not helper.is_udid(sim.get("udid")):
                    raise _err("VR_DECLARATION_INVALID", f"{rat}.simulator 键须恰为 {list(SIM_KEYS)} 且 udid 为 UDID")
                _parse_iso(sim["bound_at"], f"{rat}.simulator.bound_at")
                if sim["verified_at"] is not None:
                    _parse_iso(sim["verified_at"], f"{rat}.simulator.verified_at")
            if not isinstance(run["run_id"], str) or "/" in run["run_id"] or helper.canonical_problem("/" + run["run_id"]):
                raise _err("VR_DECLARATION_INVALID", f"{rat}.run_id={run['run_id']!r} 不合法")
            if not isinstance(run["candidate"], str) or not _SHA_RE.match(run["candidate"]):
                raise _err("VR_DECLARATION_INVALID", f"{rat}.candidate 须为 40 位 sha")
            _parse_iso(run["started_at"], f"{rat}.started_at")
            if run["finished_at"] is not None:
                _parse_iso(run["finished_at"], f"{rat}.finished_at")
        out.append(entry)
    return out


def declaration_status(entry: dict[str, Any], now: datetime | None = None) -> str:
    """revoked / expired / unbound(无指纹, 仅预演出现)/ active。"""
    if entry.get("revoked_at"):
        return "revoked"
    if _parse_iso(entry["expires_at"], "expires_at") <= (now or _now()):
        return "expired"
    if entry.get("host_fingerprint") is None:
        return "unbound"
    return "active"


def active_run(entry: dict[str, Any]) -> dict[str, Any] | None:
    return next((r for r in entry.get("runs") or [] if not r.get("finished_at")), None)


def simulator_name(entry: dict[str, Any], run_id: str) -> str:
    """本 run 专用模拟器名(ios_simulator.name_template; 与 helper.sim_name 同值, 测试断言一致)。"""
    template = str(ios_simulator_declaration()["name_template"])
    return template.replace("{task_id}", entry["task_id"]).replace("{run_id}", run_id)


def bound_simulators(entries: list[dict[str, Any]]) -> list[tuple[dict[str, Any], dict[str, Any]]]:
    """全部已绑定专用模拟器的 (声明, run)(UDID 不得跨 run 复用的判据来源)。"""
    return [(e, r) for e in entries for r in e.get("runs") or [] if isinstance(r.get("simulator"), dict)]


def sim_evidence_check(entry: dict[str, Any], run: dict[str, Any], evidence_path: str, *, expect_udid: str | None,
                       require_deleted: bool = True) -> dict[str, Any]:
    """治理根侧读主机侧证据(helper 写的 simulator.json 的治理根所在机副本)并以 helper.verify_sim_evidence 同一函数核对。
    返回 {ok, problems, service, udid, sha256}; 读不出 = ok False。"""
    import json

    path = Path(str(evidence_path or "")).expanduser()
    try:
        raw = path.read_bytes()
        data = json.loads(raw.decode("utf-8"))
    except (OSError, ValueError) as exc:
        return {"ok": False, "problems": [f"--sim-evidence {path} 读不出({exc})"], "service": [], "udid": None, "sha256": None}
    verdict = helper.verify_sim_evidence(data, run_dir=run_directory(entry, run["run_id"]), expect_udid=expect_udid,
                                         require_deleted=require_deleted)
    return {**verdict, "sha256": hashlib.sha256(raw).hexdigest()}


def run_directory(entry: dict[str, Any], run_id: str) -> str:
    template = str(declaration()["path_template"])
    return template.replace("{base}", entry["base"]).replace("{task_id}", entry["task_id"]).replace("{run_id}", run_id)


# ---------------------------------------------------------------------------
# 声明校验: base 禁区 / 实例与卡归属
# ---------------------------------------------------------------------------

def _protected_roots(governance_root: Path) -> list[str]:
    from tools.aipos_cli.workspace_config import project_repos

    repos = project_repos(governance_root)
    roots = [governance_root, *(Path(p) for p in repos["items"].values())]
    if repos.get("code_repo"):
        roots.append(Path(repos["code_repo"]))
    out: list[str] = []
    for root in roots:
        for form in (str(Path(root).expanduser()), str(Path(root).expanduser().resolve())):
            if form not in out:
                out.append(form)
    return out


def base_problems(governance_root: str | Path, base: str, *, local: bool) -> list[str]:
    """base 禁区判定(字面; 本机另按 realpath 复核)。返回问题原文列表(空 = 合法)。"""
    fb = declaration()["forbidden_bases"]
    problem = helper.canonical_problem(base)
    if problem:
        return [f"base {problem}"]
    candidates = [base]
    if local:
        real = helper.real_target(base)
        if real != base:
            candidates.append(real)
    problems: list[str] = []
    homes = [str(h).rstrip("/") for h in fb["home_roots"]]
    local_home = str(Path.home().resolve()) if local else None
    for cand in candidates:
        tag = "" if cand == base else f"(真实路径 {cand})"
        if cand in fb["exact"]:
            problems.append(f"base {base}{tag} 是禁区目录本身(roles.schema validation_root.forbidden_bases.exact)")
        for prefix in fb["system_prefixes"]:
            if helper.is_within(cand, str(prefix)):
                problems.append(f"base {base}{tag} 落在系统路径 {prefix} 下")
        parts = cand.split("/")[1:]
        home = None
        if len(parts) >= 2 and "/" + parts[0] in homes:
            home = "/" + "/".join(parts[:2])
        elif local_home and helper.is_within(cand, local_home):
            home = local_home
        if home is not None:
            if cand == home:
                problems.append(f"base {base}{tag} 是用户主目录本身")
            for rel in fb["home_relative_global"]:
                if helper.is_within(cand, f"{home}/{rel}"):
                    problems.append(f"base {base}{tag} 落在全局缓存 / 证书库 / 配置 {home}/{rel} 下")
        for root in _protected_roots(Path(governance_root)):
            if helper.is_within(cand, root):
                problems.append(f"base {base}{tag} 在治理根 / 产品仓 {root} 内(治理根与主检出 / 卡工作树不是验证根)")
            elif helper.is_within(root, cand):
                problems.append(f"base {base}{tag} 是治理根 / 产品仓 {root} 的祖先")
    return list(dict.fromkeys(problems))


def _card_fm(governance_root: Path, task_id: str) -> dict[str, Any] | None:
    from tools.aipos_cli.next_resolver import _find_task_in_queue, _read_frontmatter

    path, _queue = _find_task_in_queue(governance_root, task_id)
    if not path:
        return None
    return {**_read_frontmatter(path), "task_id": task_id}


def _card_instances(fm: dict[str, Any]) -> set[str]:
    return {str(fm.get(k) or "").strip() for k in ("assigned_to", "claimed_by", "agent_instance")} - {""}


def instance_role_class(governance_root: Path, instance: str) -> str:
    """实例 → 角色类(enrollment.instance_enrollment 唯一判定 + custom_roles 唯一解析); 未接入 / 无行 = ValidationRootError。"""
    from tools.aipos_cli.enrollment import instance_enrollment
    from tools.aipos_cli.write_boundary import WriteBoundaryError, load_write_boundary_declaration, resolve_role_class_for

    view = instance_enrollment(governance_root, instance)
    if not view["enrolled"] or not view.get("role"):
        raise _err("VR_INSTANCE_NOT_ENROLLED", f"实例 {instance} 未接入本项目或无角色记录: {view.get('reason') or '接入日志 land 事件缺 role='}")
    try:
        cls = resolve_role_class_for(governance_root, str(view["role"]))
    except WriteBoundaryError as exc:
        raise _err("VR_INSTANCE_NOT_ENROLLED", f"实例 {instance} 的角色 {view['role']!r} 解析不到角色类: {exc}") from exc
    level = (load_write_boundary_declaration()["matrix"].get(cls) or {}).get(SURFACE)
    if cls not in card_kinds() or level != "mutate":
        raise _err("VR_ROLE_NOT_ALLOWED", f"实例 {instance} 的角色类 {cls} 对 {SURFACE} 声明级别 = {level or '无行'}"
                                          f"(可声明验证根的类: {sorted(card_kinds())})")
    return cls


def task_ownership_problems(governance_root: Path, *, instance: str, role_class: str, task_id: str) -> list[str]:
    """<卡ID> 须是该实例的本卡: 执行实例 = 非审计卡且 assigned_to / claimed_by / agent_instance 为本实例;
    审计实例 = 审计卡(audit_derivation.is_audit_card)且该审计卡归本实例(卡在 = 卡面实例; 未派生 = 被审卡 audit_by / project execution.auditor)。"""
    from tools.aipos_cli.audit_derivation import audit_card_reviewed_id, is_audit_card
    from tools.aipos_cli.workspace_config import project_execution

    fm = _card_fm(governance_root, task_id)
    audit = is_audit_card(task_id, fm or None)
    if card_kinds()[role_class] == "execution_card":
        if audit:
            return [f"卡 {task_id} 是审计卡; 执行实例 {instance} 只得声明执行卡目录(执行与审计隔离)"]
        if fm is None:
            return [f"卡 {task_id} 不在队列(执行实例只得声明已发布的本卡)"]
        if instance not in _card_instances(fm):
            return [f"卡 {task_id} 的 assigned_to / claimed_by = {sorted(_card_instances(fm)) or '(空)'}, 不是执行实例 {instance} 的本卡"]
        return []
    if not audit:
        return [f"卡 {task_id} 不是审计卡; 审计实例 {instance} 只得声明审计卡目录(<被审卡>R 等, 执行与审计隔离)"]
    if fm is not None:
        return [] if instance in _card_instances(fm) else [f"审计卡 {task_id} 的实例 = {sorted(_card_instances(fm)) or '(空)'}, 不是 {instance}"]
    reviewed = audit_card_reviewed_id(task_id, None)
    reviewed_fm = _card_fm(governance_root, reviewed) if reviewed else None
    if reviewed_fm is None:
        return [f"审计卡 {task_id} 未派生且被审卡 {reviewed or '(推不出)'} 不在队列"]
    execution = project_execution(governance_root) or {}
    expected = str(reviewed_fm.get("audit_by") or execution.get("auditor") or "").strip()
    if expected != instance:
        return [f"审计卡 {task_id} 尚未派生, 被审卡 {reviewed} 的审计实例(audit_by / execution.auditor)= {expected or '(未声明)'}, 不是 {instance}"]
    return []


def _single_line(value: Any, name: str) -> str:
    from tools.aipos_cli.harness_launch import redact_progress

    text = str(value or "").strip()
    if not text or "\n" in text or "\r" in text:
        raise _err("VR_DECLARATION_INVALID", f"{name} 须为非空单行串")
    if redact_progress(text) != text:
        raise _err("VR_DECLARATION_INVALID", f"{name} 含凭据字样或长不透明串")
    return text


# ---------------------------------------------------------------------------
# 写入口(两阶段: dry_run = 预演 diff, 零写入)
# ---------------------------------------------------------------------------

def _find(entries: list[dict[str, Any]], decl_id: str) -> dict[str, Any]:
    entry = next((e for e in entries if e["id"] == decl_id), None)
    if entry is None:
        raise _err("VR_NOT_FOUND", f"声明 {decl_id} 不存在(lybra project validation-root list 查看)")
    return entry


def _candidate_problem(governance_root: Path, entry: dict[str, Any], candidate: str) -> str | None:
    import subprocess

    from tools.aipos_cli.audit_derivation import audit_card_reviewed_id
    from tools.aipos_cli.workspace_config import CardRepoUnresolved, resolve_card_repo

    if not _SHA_RE.match(candidate):
        return f"候选 {candidate!r} 须为完整 40 位小写 sha"
    card = entry["task_id"] if card_kinds()[entry["role_class"]] == "execution_card" else audit_card_reviewed_id(entry["task_id"], _card_fm(governance_root, entry["task_id"]))
    fm = _card_fm(governance_root, card) if card else None
    if fm is None:
        return f"卡 {card or entry['task_id']} 不在队列, 无从核候选所在仓"
    try:
        repo = resolve_card_repo(governance_root, fm)
    except CardRepoUnresolved as exc:
        return f"卡 {card} 的产品仓解析不到: {exc}"
    proc = subprocess.run(["git", "-C", str(repo), "cat-file", "-e", f"{candidate}^{{commit}}"], capture_output=True, text=True, timeout=30)
    if proc.returncode != 0:
        return f"候选 {candidate} 不是卡 {card} 产品仓 {repo} 中已有提交"
    return None


def apply_validation_root_change(governance_root: str | Path, action: str, *, dry_run: bool, actor: str, **kw: Any) -> dict[str, Any]:
    """declare / revoke / run-start / run-finish 的唯一写入口(update_project_json: 预演 diff / 写前预检 / 写后复核)。
    返回 update_project_json 结果 + {action, entry(写后条目), run, notes}。不合 = ValidationRootError(零写入)。"""
    from tools.aipos_cli.workspace_config import update_project_json

    gov = Path(governance_root).expanduser().resolve()
    if action not in ACTIONS:
        raise _err("VR_DECLARATION_INVALID", f"未知动作 {action!r}(只认 {list(ACTIONS)})")
    actor = _single_line(actor, "--actor")
    now = _now()
    entries = project_validation_roots(gov)
    notes: list[str] = []
    result: dict[str, Any] = {}

    if action == "declare":
        host = str(kw.get("host") or "").strip()
        if not _HOST_RE.match(host):
            raise _err("VR_DECLARATION_INVALID", f"--host {host!r} 须为主机标签([A-Za-z0-9._-], 不含空白)")
        local = is_local_host(host)
        base = str(kw.get("base") or "")
        problems = base_problems(gov, base, local=local)
        if problems:
            raise _err("VR_BASE_FORBIDDEN", *problems)
        task_id = str(kw.get("task_id") or "").strip()
        instance = str(kw.get("instance") or "").strip()
        if not task_id or "/" in task_id or task_id in (".", ".."):
            raise _err("VR_DECLARATION_INVALID", f"--task-id {task_id!r} 不合法")
        role_class = instance_role_class(gov, instance)
        owned = task_ownership_problems(gov, instance=instance, role_class=role_class, task_id=task_id)
        if owned:
            raise _err("VR_TASK_NOT_OWNED", *owned)
        owner_ref = _single_line(kw.get("owner_ref"), "--owner-ref")
        decl = declaration()
        if kw.get("expires_at"):
            expires = _parse_iso(str(kw["expires_at"]), "--expires-at")
        else:
            expires = now + timedelta(hours=int(decl["default_ttl_hours"]))
            notes.append(f"到期缺省 = 声明时 + {decl['default_ttl_hours']} 小时(roles.schema validation_root.default_ttl_hours)")
        if expires <= now or expires > now + timedelta(hours=int(decl["max_ttl_hours"])):
            raise _err("VR_DECLARATION_INVALID", f"--expires-at {_iso(expires)} 须晚于现在且不超过 {decl['max_ttl_hours']} 小时"
                                                 "(roles.schema validation_root.max_ttl_hours)")
        given = str(kw.get("host_fingerprint") or "").strip() or None
        if given is not None and not helper.is_fingerprint(given):
            raise _err("VR_DECLARATION_INVALID", f"--host-fingerprint {given!r} 不是 hfp1 指纹(在目标主机运行 helper fingerprint 取得)")
        if local:
            mine = helper.host_fingerprint()
            if given is not None and given != mine:
                raise _err("VR_HOST_FINGERPRINT_MISMATCH", f"--host {host} 是本机, 本机指纹 {mine} ≠ 给出的 {given}")
            fingerprint: str | None = mine
            notes.append(f"主机 {host} = 本机, 指纹由本机计算 {mine}")
        elif given is None:
            if not dry_run:
                raise _err("VR_HOST_FINGERPRINT_REQUIRED", f"远端主机 {host} 确认须给 --host-fingerprint(在 {host} 上运行 "
                                                           "`python3 validation_root_helper.py fingerprint` 取得; helper 由 "
                                                           "`lybra project validation-root helper` 打印)")
            fingerprint = None
            notes.append(f"确认前须补 --host-fingerprint: 在目标主机 {host} 运行 helper fingerprint 取得(预演不阻断, --confirm 缺 = "
                         "VR_HOST_FINGERPRINT_REQUIRED)")
        else:
            fingerprint = given
        clash = [e["id"] for e in entries if e["host"] == host and e["task_id"] == task_id and e["instance"] == instance
                 and declaration_status(e, now) in ("active", "unbound")]
        if clash:
            raise _err("VR_STATE_CONFLICT", f"主机 {host} 卡 {task_id} 实例 {instance} 已有有效声明 {clash}; 先 revoke 再声明")
        seq = 1 + sum(1 for e in entries if e["task_id"] == task_id and e["role_class"] == role_class)
        entry = {"id": f"vr-{task_id.lower()}-{role_class}-{seq}", "host": host, "host_fingerprint": fingerprint, "base": base,
                 "task_id": task_id, "instance": instance, "role_class": role_class, "owner_ref": owner_ref, "declared_by": actor,
                 "declared_at": _iso(now), "expires_at": _iso(expires), "revoked_at": None, "revoked_by": None, "revoke_reason": None,
                 "runs": []}
        result = {"entry": entry, "run": None}

        def mutate(data: dict[str, Any]) -> None:
            section = data.get("validation_roots") or {"declarations": []}
            section["declarations"] = list(section.get("declarations") or []) + [entry]
            data["validation_roots"] = section
    else:
        decl_id = str(kw.get("decl_id") or "").strip()
        entry = dict(_find(entries, decl_id))
        status = declaration_status(entry, now)
        if action == "revoke":
            if status == "revoked":
                raise _err("VR_STATE_CONFLICT", f"声明 {decl_id} 已于 {entry['revoked_at']} 撤销")
            entry.update({"revoked_at": _iso(now), "revoked_by": actor, "revoke_reason": _single_line(kw.get("reason"), "--reason")})
            result = {"entry": entry, "run": active_run(entry)}
            if active_run(entry):
                notes.append(f"活动 run {active_run(entry)['run_id']} 随撤销一律拒写(撤销后判 DECLARATION_REVOKED)")
        elif action == "run-start":
            if status != "active":
                raise _err("VR_STATE_CONFLICT", f"声明 {decl_id} 状态 = {status}, 不能起 run(须为 active)")
            given = str(kw.get("host_fingerprint") or "").strip() or None
            if given is not None and given != entry["host_fingerprint"]:
                raise _err("VR_HOST_FINGERPRINT_MISMATCH", f"给出的主机指纹 {given} ≠ 声明 {decl_id} 的 {entry['host_fingerprint']}")
            current = active_run(entry)
            if current:
                raise _err("VR_STATE_CONFLICT", f"声明 {decl_id} 已有活动 run {current['run_id']}; 先 run-finish 再起新 run")
            candidate = str(kw.get("candidate") or "").strip()
            problem = _candidate_problem(gov, entry, candidate)
            if problem:
                raise _err("VR_CANDIDATE_INVALID", problem)
            used = sum(len(e["runs"]) for e in entries if e["host"] == entry["host"] and e["base"] == entry["base"]
                       and e["task_id"] == entry["task_id"])
            run_id = str(declaration()["run_id_template"]).replace("{n}", str(used + 1))
            run = {"run_id": run_id, "candidate": candidate, "started_at": _iso(now), "started_by": actor, "finished_at": None,
                   "finished_by": None}
            entry["runs"] = list(entry["runs"]) + [run]
            result = {"entry": entry, "run": run}
            notes.append(f"本次可写目录: {run_directory(entry, run_id)}(run-id 由产品铸; 只此目录可写)")
            # AIPOS-F191 件②: 下一步 = 在目标主机建目录链(唯一铸造口 init_run_command; 封印不含时刻, 预演所示 = 确认后所铸)
            notes.append(f"{'确认后' if dry_run else ''}下一步(run-start 后第一步): 在目标主机 {entry['host']} 运行: "
                         f"{init_run_command(gov, entry, run)}(只建空目录链 0700, 末行 INIT_OK + 退出码 0 = 成功; 角色不得自行 mkdir base / 卡层)")
        elif action == "sim-bind":
            # AIPOS-F194 件②④: 本 run 专用模拟器 UDID 绑定进本 run 记录(与 run-id 同一处); 审计 run 须新建自己的, 不得复用他 run UDID
            run_id = str(kw.get("run_id") or "").strip()
            run = next((r for r in entry["runs"] if r["run_id"] == run_id), None)
            if run is None:
                raise _err("VR_NOT_FOUND", f"声明 {decl_id} 无 run {run_id!r}")
            if status != "active" or run["finished_at"]:
                raise _err("VR_STATE_CONFLICT", f"声明 {decl_id} 状态 = {status}, run {run_id} {'已收口' if run['finished_at'] else '活动'}; "
                                                "只对 active 声明的活动 run 绑定专用模拟器")
            udid = str(kw.get("udid") or "").strip()
            if not helper.is_udid(udid):
                raise _err("VR_SIM_INVALID", f"--udid {udid!r} 不是 UDID(取目标主机 helper sim-create 末行 SIM_CREATED udid=…)")
            if run.get("simulator"):
                raise _err("VR_SIM_INVALID", f"run {run_id} 已绑定专用模拟器 {run['simulator']['udid']}(一个 run 只一台, 不改绑)")
            reused = [f"{e['id']}/{r['run_id']}(卡 {e['task_id']} 实例 {e['instance']})" for e, r in bound_simulators(entries)
                      if r["simulator"]["udid"].upper() == udid.upper()]
            if reused:
                raise _err("VR_SIM_INVALID", f"UDID {udid} 已绑定于 {reused}; 每个 run(含审计 run)须新建自己的专用模拟器, 不得复用他 run 的 UDID")
            sim = {"udid": udid, "name": simulator_name(entry, run_id), "bound_at": _iso(now), "bound_by": actor, "verified_at": None,
                   "verified_by": None, "evidence_sha256": None}
            if kw.get("sim_evidence"):
                check = sim_evidence_check(entry, run, str(kw["sim_evidence"]), expect_udid=udid, require_deleted=False)
                if not check["ok"]:
                    raise _err("VR_SIM_EVIDENCE_REJECTED", *[f"绑定拒: {p}" for p in check["problems"]])
            run = {**run, "simulator": sim}
            entry["runs"] = [run if r["run_id"] == run_id else r for r in entry["runs"]]
            result = {"entry": entry, "run": run}
            notes.append(f"本 run 专用模拟器 {sim['name']}({udid})只此一台设备目录可写; 结束须在目标主机 sim-delete, 收口 run-finish 带 "
                         "--sim-evidence <simulator.json 在治理根所在机的副本> 核对「只多出并只删除这一台」")
        else:  # run-finish
            run_id = str(kw.get("run_id") or "").strip()
            run = next((r for r in entry["runs"] if r["run_id"] == run_id), None)
            if run is None:
                raise _err("VR_NOT_FOUND", f"声明 {decl_id} 无 run {run_id!r}")
            if run["finished_at"]:
                raise _err("VR_STATE_CONFLICT", f"run {run_id} 已于 {run['finished_at']} 收口")
            sim = run.get("simulator")
            evidence_path = str(kw.get("sim_evidence") or "").strip()
            if sim and not evidence_path:
                raise _err("VR_SIM_EVIDENCE_REJECTED", f"run {run_id} 绑定了专用模拟器 {sim['name']}({sim['udid']}), 收口须带 --sim-evidence "
                                                       f"<目标主机 {run_directory(entry, run_id)}/{ios_simulator_declaration()['evidence_file']} "
                                                       "在治理根所在机的副本>; 未删除 / 无证据不收口")
            if evidence_path:
                check = sim_evidence_check(entry, run, evidence_path, expect_udid=sim["udid"] if sim else None)
                if not check["ok"]:
                    raise _err("VR_SIM_EVIDENCE_REJECTED", *[f"收口拒: {p}" for p in check["problems"]])
                if not sim:
                    reused = [f"{e['id']}/{r['run_id']}" for e, r in bound_simulators(entries)
                              if r["simulator"]["udid"].upper() == str(check["udid"]).upper()]
                    if reused:
                        raise _err("VR_SIM_INVALID", f"证据 UDID {check['udid']} 已绑定于 {reused}(不得复用他 run 的 UDID)")
                    sim = {"udid": check["udid"], "name": simulator_name(entry, run_id), "bound_at": _iso(now), "bound_by": actor}
                    notes.append("本 run 未经 sim-bind: 收口按证据补记专用模拟器(运行中写判定未按 UDID 放行过)")
                sim = {**sim, "verified_at": _iso(now), "verified_by": actor, "evidence_sha256": check["sha256"]}
                notes.append(f"专用模拟器证据核对通过: 只多出并只删除 {sim['udid']}" + (f"; 系统服务副作用 {check['service']}" if check["service"] else ""))
            run = {**run, "finished_at": _iso(now), "finished_by": actor, **({"simulator": sim} if sim else {})}
            entry["runs"] = [run if r["run_id"] == run_id else r for r in entry["runs"]]
            result = {"entry": entry, "run": run}
            notes.append(f"run {run_id} 收口后其目录一律拒写(RUN_NOT_ACTIVE)")

        def mutate(data: dict[str, Any]) -> None:
            section = data.get("validation_roots") or {"declarations": []}
            section["declarations"] = [entry if isinstance(e, dict) and e.get("id") == decl_id else e for e in section["declarations"]]
            data["validation_roots"] = section

    outcome = update_project_json(gov, mutate, verify=project_validation_roots, dry_run=dry_run)
    return {**outcome, "action": action, **result, "notes": notes}


# ---------------------------------------------------------------------------
# 判定(write_boundary.check_access 的 validation_root 面; 主机维度)
# ---------------------------------------------------------------------------

def judge_access(governance_root: str | Path, *, host: str, raw_path: str, want_rank: int, granted: str | None, granted_rank: int | None,
                 instance: str | None, task_id: str | None, run_id: str | None, host_fingerprint: str | None) -> dict[str, Any] | None:
    """一次访问的 validation_root 面判定。None = 路径不在该主机任何声明 base 内(调用方按其他面判)。
    返回 {allowed, code, reason, surface, granted_level, host, run_dir?, host_side_check?}(不含 role_class / hard_rules, 由调用方补)。"""
    gov = Path(governance_root).expanduser().resolve()
    host = str(host or "").strip()
    local = is_local_host(host)
    label = local_host_label() if local else host
    entries = [e for e in project_validation_roots(gov) if (is_local_host(e["host"]) if local else e["host"] == host)]
    if not entries:
        return None
    sim_hit = _sim_target(raw_path, local=local)
    if sim_hit is not None:
        return _judge_sim_access(entries, sim_hit, raw_path=raw_path, label=label, local=local, want_rank=want_rank, granted=granted,
                                 granted_rank=granted_rank, instance=instance, task_id=task_id, run_id=run_id,
                                 host_fingerprint=host_fingerprint)
    normalized = posixpath.normpath(raw_path) if raw_path.startswith("/") else raw_path
    hits = [e for e in entries if helper.is_within(raw_path, e["base"]) or helper.is_within(normalized, e["base"])]
    if not hits:
        return None
    base: dict[str, Any] = {"surface": SURFACE, "granted_level": granted, "host": label}

    def deny(code: str, reason: str, **extra: Any) -> dict[str, Any]:
        return {**base, **extra, "allowed": False, "code": code, "reason": reason}

    problem = helper.canonical_problem(raw_path)
    if problem:
        return deny("PATH_NOT_CANONICAL", f"目标{problem}(验证根内路径须规范, 穿越一律拒)")
    if not granted or granted_rank is None:
        return deny("ROLE_NO_ROW", f"角色类对面 {SURFACE} 无声明行(roles.schema write_boundary.matrix); 出口: 补矩阵声明")
    if want_rank > granted_rank:
        return deny("LEVEL_EXCEEDED", f"面 {SURFACE} 声明级别={granted}, 请求级别超出")
    if want_rank == 0:
        return {**base, "allowed": True, "code": "OK", "reason": f"面 {SURFACE} 只读(主机 {label})"}
    if not instance:
        return deny("INSTANCE_REQUIRED", f"面 {SURFACE} 的写判定须给 --instance(声明按实例归属)")
    base_dir = max((e["base"] for e in hits), key=len)
    rel = raw_path[len(base_dir):].lstrip("/").split("/") if raw_path != base_dir else []
    card_seg = rel[0] if rel and rel[0] else None
    run_seg = rel[1] if len(rel) > 1 else None
    same_base = [e for e in hits if e["base"] == base_dir]
    if card_seg is None or run_seg is None:
        # AIPOS-F191 件③: base / 卡层只经 helper init-run 建空目录链; 他实例声明的卡层 = 执行与审计互不能建对方卡层
        owners = sorted({e["instance"] for e in same_base if e["task_id"] == card_seg}) if card_seg else []
        if owners and instance not in owners:
            return deny("NOT_OWN_DECLARATION", f"{base_dir}/{card_seg}/ 的声明属于 {owners}, 不是实例 {instance}(执行与审计共用 base 时互不能建 / 写对方卡层)")
        return deny("OUTSIDE_TASK_RUN", f"目标 {raw_path} 在 base {base_dir} 或卡目录层, 未进 run 目录(只许 {base_dir}/<本卡>/<活动 run>/ 内); "
                                        "目录链(base / 卡层 / run 目录)只经产品 helper init-run 建空目录(命令见开工稿或 `lybra project "
                                        "validation-root init-command --id <声明>`), 角色不得自行 mkdir, 此层文件 / 改名 / 删除一律拒")
    if not task_id:
        return deny("NOT_OWN_TASK", f"面 {SURFACE} 的写判定须给 --task-id(本卡)")
    if card_seg != task_id:
        return deny("NOT_OWN_TASK", f"目标卡目录 {card_seg} ≠ 本卡 {task_id}(跨卡)")
    mine = [e for e in same_base if e["task_id"] == task_id and e["instance"] == instance]
    if not mine:
        others = sorted({e["instance"] for e in same_base if e["task_id"] == task_id})
        if others:
            return deny("NOT_OWN_DECLARATION", f"{base_dir}/{task_id}/ 的声明属于 {others}, 不是实例 {instance}(跨角色 / 他实例根; 执行与审计隔离)")
        return deny("NOT_OWN_TASK", f"主机 {label} base {base_dir} 下无实例 {instance} 卡 {task_id} 的声明(跨卡)")
    now = _now()
    ordered = sorted(mine, key=lambda e: {"active": 0, "unbound": 1, "expired": 2, "revoked": 3}[declaration_status(e, now)])
    entry = ordered[0]
    status = declaration_status(entry, now)
    if status == "revoked":
        return deny("DECLARATION_REVOKED", f"声明 {entry['id']} 已于 {entry['revoked_at']} 被 {entry['revoked_by']} 撤销({entry['revoke_reason']})")
    if status == "expired":
        return deny("DECLARATION_EXPIRED", f"声明 {entry['id']} 已于 {entry['expires_at']} 到期")
    if status == "unbound":
        return deny("HOST_FINGERPRINT_UNBOUND", f"声明 {entry['id']} 无主机指纹(未确认), 不放行")
    if host_fingerprint and host_fingerprint != entry["host_fingerprint"]:
        return deny("HOST_FINGERPRINT_MISMATCH", f"调用方主机指纹 {host_fingerprint} ≠ 声明 {entry['id']} 的 {entry['host_fingerprint']}(跨机调用)")
    if local:
        try:
            here = helper.host_fingerprint()
        except helper.HostIdentityError as exc:
            return deny("HOST_FINGERPRINT_MISMATCH", f"本机指纹读不出({exc}), fail-closed 拒")
        if here != entry["host_fingerprint"]:
            return deny("HOST_FINGERPRINT_MISMATCH", f"本机指纹 {here} ≠ 声明 {entry['id']} 的 {entry['host_fingerprint']}(本机不是声明主机)")
    if not run_id:
        return deny("RUN_ID_REQUIRED", f"面 {SURFACE} 的写判定须给 --run-id(run-start 铸)")
    if run_seg != run_id:
        return deny("NOT_OWN_RUN", f"目标 run 目录 {run_seg} ≠ --run-id {run_id}(跨 run)")
    run = next((r for r in entry["runs"] if r["run_id"] == run_id), None)
    if run is None:
        return deny("NOT_OWN_RUN", f"run {run_id} 不属于声明 {entry['id']}(他人 / 未铸的 run)")
    if run["finished_at"]:
        return deny("RUN_NOT_ACTIVE", f"run {run_id} 已于 {run['finished_at']} 收口(已结束 run 一律拒)")
    run_dir = run_directory(entry, run_id)
    extra = {"run_dir": run_dir, "declaration_id": entry["id"]}
    if local:
        verdict = helper.judge_realpath(run_dir, raw_path)
        if not verdict["allowed"]:
            return deny(verdict["code"], verdict["reason"], **extra)
        return {**base, **extra, "allowed": True, "code": "OK",
                "reason": f"本实例 {instance} 本卡 {task_id} 活动 run {run_id} 目录内(主机 {label}; {verdict['reason']})"}
    return {**base, **extra, "allowed": True, "code": "OK",
            "host_side_check": f"python3 validation_root_helper.py check --run-dir {shlex.quote(run_dir)} --path {shlex.quote(raw_path)} "
                               f"--expect-fingerprint {entry['host_fingerprint']}",
            "reason": (f"本实例 {instance} 本卡 {task_id} 活动 run {run_id} 目录内(主机 {label}, 字面判定); 远端路径在本机不可 resolve, "
                       "符号链接逃逸须在目标主机以 helper check 复核(只认退出码 0 + ALLOW)")}


def _sim_target(raw_path: str, *, local: bool) -> dict[str, Any] | None:
    """路径是否落在某用户主目录的 Xcode 缺省设备集(<home_roots>/<用户>/<devices_rel>, 本机另认 ~)内: 返回 {devices_dir, udid_seg}; 否则 None。
    字面判定(先 normpath 防 .. 绕开识别; 非规范路径由调用方拒)。"""
    if not raw_path.startswith("/"):
        return None
    rel = str(ios_simulator_declaration()["devices_rel"]).strip("/")
    normalized = posixpath.normpath(raw_path)
    homes = [str(h).rstrip("/") for h in declaration()["forbidden_bases"]["home_roots"]]
    candidates: list[str] = []
    parts = normalized.split("/")[1:]
    if len(parts) >= 2 and "/" + parts[0] in homes:
        candidates.append("/" + "/".join(parts[:2]))
    if local:
        candidates.append(str(Path.home()))
    for home in candidates:
        ddir = f"{home}/{rel}"
        if helper.is_within(normalized, ddir) or helper.is_within(raw_path, ddir):
            rest = normalized[len(ddir):].lstrip("/").split("/") if normalized != ddir else []
            return {"devices_dir": ddir, "udid_seg": rest[0] if rest and rest[0] else None}
    return None


def _judge_sim_access(entries: list[dict[str, Any]], hit: dict[str, Any], *, raw_path: str, label: str, local: bool, want_rank: int,
                      granted: str | None, granted_rank: int | None, instance: str | None, task_id: str | None, run_id: str | None,
                      host_fingerprint: str | None) -> dict[str, Any]:
    """AIPOS-F194 件④: Xcode 缺省设备集内的写判定(同面 validation_root, scope ios_simulator)。只放行本实例本卡本活动 run 绑定的
    那一台 UDID 设备目录; 整个 Devices 目录 / 服务维护项(device_set.plist 等)/ 未绑定 UDID / 他 run·他卡·他角色(含审计复用执行 UDID)一律拒。"""
    base: dict[str, Any] = {"surface": SURFACE, "scope": "ios_simulator", "granted_level": granted, "host": label}

    def deny(code: str, reason: str, **extra: Any) -> dict[str, Any]:
        return {**base, **extra, "allowed": False, "code": code, "reason": reason}

    problem = helper.canonical_problem(raw_path)
    if problem:
        return deny("PATH_NOT_CANONICAL", f"目标{problem}(设备集内路径须规范, 穿越一律拒)")
    if not granted or granted_rank is None:
        return deny("ROLE_NO_ROW", f"角色类对面 {SURFACE} 无声明行(roles.schema write_boundary.matrix); 出口: 补矩阵声明")
    if want_rank > granted_rank:
        return deny("LEVEL_EXCEEDED", f"面 {SURFACE} 声明级别={granted}, 请求级别超出")
    if want_rank == 0:
        return {**base, "allowed": True, "code": "OK", "reason": f"面 {SURFACE}(ios_simulator)只读(主机 {label})"}
    udid = hit["udid_seg"]
    if udid is None or not helper.is_udid(udid):
        return deny("SIM_OUTSIDE_DEVICE", f"目标 {raw_path} 是 Xcode 缺省设备集 {hit['devices_dir']} 本身或其顶层服务维护项(如 device_set.plist)"
                                          "——整个 Devices 目录不放行; 服务维护性写入属 CoreSimulator 系统服务副作用, 不是角色写面")
    if not instance:
        return deny("INSTANCE_REQUIRED", f"面 {SURFACE} 的写判定须给 --instance(声明按实例归属)")
    bound = [(e, r) for e, r in bound_simulators(entries) if r["simulator"]["udid"].upper() == udid.upper()]
    if not bound:
        return deny("SIM_NOT_BOUND", f"设备 {udid} 未绑定于主机 {label} 任何本 run 记录(只放行经 helper sim-create 新建并由顾问 sim-bind "
                                     "绑定的本 run 专用模拟器; 他台设备一律拒)")
    entry, run = bound[0]
    if entry["instance"] != instance or entry["task_id"] != (task_id or "") or run["run_id"] != (run_id or ""):
        return deny("SIM_NOT_OWN_RUN", f"设备 {udid} 绑定于 {entry['id']} {run['run_id']}(卡 {entry['task_id']}, 实例 {entry['instance']}), "
                                       f"不是实例 {instance} 卡 {task_id or '(未给)'} run {run_id or '(未给)'} 的专用模拟器"
                                       "(他 run / 他卡 / 他角色拒; 审计 run 须新建自己的, 不得复用执行 run 的 UDID)")
    now = _now()
    status = declaration_status(entry, now)
    if status == "revoked":
        return deny("DECLARATION_REVOKED", f"声明 {entry['id']} 已于 {entry['revoked_at']} 被 {entry['revoked_by']} 撤销({entry['revoke_reason']})")
    if status == "expired":
        return deny("DECLARATION_EXPIRED", f"声明 {entry['id']} 已于 {entry['expires_at']} 到期")
    if status == "unbound":
        return deny("HOST_FINGERPRINT_UNBOUND", f"声明 {entry['id']} 无主机指纹(未确认), 不放行")
    if host_fingerprint and host_fingerprint != entry["host_fingerprint"]:
        return deny("HOST_FINGERPRINT_MISMATCH", f"调用方主机指纹 {host_fingerprint} ≠ 声明 {entry['id']} 的 {entry['host_fingerprint']}(跨机调用)")
    if run["finished_at"]:
        return deny("RUN_NOT_ACTIVE", f"run {run['run_id']} 已于 {run['finished_at']} 收口(已结束 run 的专用模拟器一律拒)")
    run_dir = run_directory(entry, run["run_id"])
    device_dir = f"{hit['devices_dir']}/{udid}"
    extra = {"run_dir": run_dir, "declaration_id": entry["id"], "simulator_udid": udid, "device_dir": device_dir}
    if local:
        try:
            here = helper.host_fingerprint()
        except helper.HostIdentityError as exc:
            return deny("HOST_FINGERPRINT_MISMATCH", f"本机指纹读不出({exc}), fail-closed 拒", **extra)
        if here != entry["host_fingerprint"]:
            return deny("HOST_FINGERPRINT_MISMATCH", f"本机指纹 {here} ≠ 声明 {entry['id']} 的 {entry['host_fingerprint']}", **extra)
        verdict = helper.judge_realpath(device_dir, raw_path)
        if not verdict["allowed"]:
            return deny(verdict["code"], verdict["reason"], **extra)
    reason = f"本实例 {instance} 本卡 {task_id} 活动 run {run['run_id']} 的专用模拟器 {run['simulator']['name']}({udid})设备目录内(主机 {label})"
    if local:
        return {**base, **extra, "allowed": True, "code": "OK", "reason": reason}
    return {**base, **extra, "allowed": True, "code": "OK",
            "host_side_check": f"python3 validation_root_helper.py check --run-dir {shlex.quote(run_dir)} --sim-udid {udid} "
                               f"--path {shlex.quote(raw_path)} --expect-fingerprint {entry['host_fingerprint']}",
            "reason": reason + "; 字面判定, 符号链接逃逸与 device.plist 名须在目标主机以 helper check --sim-udid 复核(只认退出码 0 + ALLOW)"}


def init_run_command(governance_root: str | Path, entry: dict[str, Any], run: dict[str, Any]) -> str:
    """AIPOS-F191: 目标主机目录链初始化命令(唯一铸造口)。helper 取自治理根所在机(`project validation-root helper`), 经管道交
    `python3 -` 运行(run 目录尚不存在, helper 不落盘); 封印 = helper.init_seal(base, run 目录, 指纹, 到期)。调用方保证声明 active 且 run 活动。"""
    run_dir = run_directory(entry, run["run_id"])
    seal = helper.init_seal(entry["base"], run_dir, entry["host_fingerprint"], entry["expires_at"])
    args = " ".join(shlex.quote(str(a)) for a in ("--base", entry["base"], "--run-dir", run_dir, "--expect-fingerprint",
                                                    entry["host_fingerprint"], "--expires-at", entry["expires_at"], "--seal", seal))
    return f"{_gov_cmd(Path(governance_root).expanduser().resolve())} project validation-root helper | python3 - init-run {args}"


def init_command(governance_root: str | Path, decl_id: str, *, instance: str | None = None) -> dict[str, Any]:
    """`validation-root init-command` 唯一实现(只读): 声明须 active、有活动 run, 给了 --instance 须为声明实例(执行与审计互不能取对方
    卡层的命令)。返回 {entry, run, run_dir, command}; 不合 = ValidationRootError。"""
    gov = Path(governance_root).expanduser().resolve()
    entry = _find(project_validation_roots(gov), str(decl_id or "").strip())
    if instance and instance.strip() != entry["instance"]:
        raise _err("VR_TASK_NOT_OWNED", f"声明 {entry['id']}(卡 {entry['task_id']})属于实例 {entry['instance']}, 不是 {instance.strip()}"
                                        "(执行与审计隔离: 只得初始化本实例本卡的目录链)")
    status = declaration_status(entry)
    if status != "active":
        raise _err("VR_STATE_CONFLICT", f"声明 {entry['id']} 状态 = {status}, 不铸目录链初始化命令(须为 active)")
    run = active_run(entry)
    if run is None:
        raise _err("VR_STATE_CONFLICT", f"声明 {entry['id']} 无活动 run(已结束 run 不再初始化); 先由顾问 / 驱动方 run-start")
    return {"entry": entry, "run": run, "run_dir": run_directory(entry, run["run_id"]), "command": init_run_command(gov, entry, run)}


def executor_run_dir_refs(governance_root: str | Path, reviewed_task_id: str, texts: list[str]) -> list[str]:
    """运行证据校验对接: 运行记录文本中引用了被审执行卡的验证目录(<base>/<被审卡>/)= 返回命中的目录(审计不得以执行 run 作自身证据)。"""
    if not reviewed_task_id:
        return []
    executor_entries = [e for e in project_validation_roots(governance_root)
                        if e["task_id"] == reviewed_task_id and card_kinds().get(e["role_class"]) == "execution_card"]
    prefixes = sorted({f"{e['base']}/{e['task_id']}" for e in executor_entries})
    hits: list[str] = []
    for prefix in prefixes:
        pattern = re.compile(re.escape(prefix) + r"(?=$|/|[\s'\"`])")
        if any(pattern.search(str(text or "")) for text in texts):
            hits.append(prefix + "/")
    # AIPOS-F194 件④: 审计不得引用执行 run 的专用模拟器 UDID(须新建自己的)
    for entry, run in bound_simulators(executor_entries):
        udid = run["simulator"]["udid"]
        pattern = re.compile(r"(?<![0-9A-Fa-f-])" + re.escape(udid) + r"(?![0-9A-Fa-f-])", re.IGNORECASE)
        if any(pattern.search(str(text or "")) for text in texts):
            hits.append(f"专用模拟器 {udid}(执行 {entry['id']} {run['run_id']})")
    return hits


# ---------------------------------------------------------------------------
# 渲染: 开工稿 / 章程同源节、list 视图、helper 下发
# ---------------------------------------------------------------------------

def helper_source() -> str:
    return Path(helper.__file__).read_text(encoding="utf-8")


def helper_sha256() -> str:
    return hashlib.sha256(helper_source().encode("utf-8")).hexdigest()


def _fill(template: str, values: dict[str, Any]) -> str:
    return re.sub(r"\{([a-z0-9_]+)\}", lambda m: str(values.get(m.group(1), m.group(0))), template)


def _gov_cmd(governance_root: Path) -> str:
    return f"lybra --workspace-root {shlex.quote(str(governance_root))}"


def render_section_lines(governance_root: str | Path, *, instance: str, task_id: str | None = None, harness: str | None = None,
                         include_inactive: bool = False) -> list[str]:
    """本实例(可限本卡)的验证根节(开工稿与章程同源; 措辞 verbs.schema lybra_kickoff.validation_root_texts)。无声明 = []。
    include_inactive = 已撤销 / 到期的也列(开工稿: 如实告知一律拒); 章程只列有效声明。"""
    gov = Path(governance_root).expanduser().resolve()
    now = _now()
    entries = [e for e in project_validation_roots(gov) if e["instance"] == instance and (task_id is None or e["task_id"] == task_id)]
    if not include_inactive:
        entries = [e for e in entries if declaration_status(e, now) in ("active", "unbound")]
    if not entries:
        return []
    texts, decl = section_texts(), declaration()
    lines = [texts["title"], _fill(texts["relation"], {"relation": decl["relation_to_hard_rules"]})]
    sandboxes = decl["harness_sandbox"]
    gcmd = _gov_cmd(gov)
    for entry in entries:
        status = declaration_status(entry, now)
        run = active_run(entry)
        run_dir = run_directory(entry, run["run_id"]) if run else run_directory(entry, "<run-id>")
        values = {**entry, "status": status, "fingerprint": entry["host_fingerprint"] or "(未绑定)", "run_dir": run_dir,
                  "run_id": run["run_id"] if run else "<run-id>", "candidate": run["candidate"] if run else "<候选 sha>",
                  "started_at": run["started_at"] if run else "",
                  "run_start": f"{gcmd} project validation-root run-start --id {entry['id']} --candidate <dev 候选 40 位 sha> --confirm --actor <顾问实例>",
                  "run_finish": f"{gcmd} project validation-root run-finish --id {entry['id']} --run-id {run['run_id'] if run else '<run-id>'} --confirm --actor <顾问实例>",
                  "check_command": (f"{gcmd} roles write-boundary --instance {entry['instance']} --task-id {entry['task_id']} --host {entry['host']} "
                                    f"--run-id {run['run_id'] if run else '<run-id>'} --check <目标路径> --level mutate"),
                  "helper_fetch": f"{gcmd} project validation-root helper", "helper_sha256": helper_sha256(),
                  "helper_path": f"{run_dir}/.lybra-vr/validation_root_helper.py",
                  "init_run": init_run_command(gov, entry, run) if run and status == "active" else ""}
        values.update(_sim_values(gov, entry, run, values))
        lines.append(_fill(texts["declaration"], values))
        lines.append(_fill(texts["directory"], values))
        if status not in ("active", "unbound"):
            continue
        lines.append(_fill(texts["run_active"] if run else texts["run_none"], values))
        if values["init_run"]:
            lines.append(_fill(texts["init_run"], values))  # AIPOS-F191 件②: run-start 后第一步 = 目录链初始化
        lines.append(_fill(texts["check_dev"], values))
        if not is_local_host(entry["host"]):
            lines.append(_fill(texts["helper"], values))
        kinds = [harness] if harness and harness in sandboxes else sorted(sandboxes)
        for kind in kinds:
            spec = sandboxes[kind]
            lines.append(_fill(texts["sandbox"], {"harness": kind, "sandbox": _fill(str(spec.get("render") or ""), values),
                                                  "forbid": spec.get("forbid") or "", "source": spec.get("source") or ""}))
        lines.append(texts["redirect_title"])
        for item in decl["tool_redirects"]:
            render = item.get("render") or f"{item.get('name')}={item.get('value')}"
            lines.append(_fill(texts["redirect"], {"toolchain": item.get("toolchain"), "render": _fill(str(render), values),
                                                   "verify": _fill(str(item.get("verify") or ""), values), "source": item.get("source") or ""}))
        for item in decl["system_side_effects"]:
            lines.append(_fill(texts["side_effect"], item))
        lines.append(_fill(texts["sim_title"], values))  # AIPOS-F194: iOS 本 run 专用模拟器步骤(声明 ios_simulator.steps)
        for n, step in enumerate(ios_simulator_declaration()["steps"], 1):
            lines.append(_fill(texts["sim_step"], {"n": n, "text": _fill(step, values)}))
        for text in decl["block_conditions"]:
            lines.append(_fill(texts["block"], {"text": text}))
        lines.append(_fill(texts["run_finish"], values))
    return lines


def _sim_values(governance_root: Path, entry: dict[str, Any], run: dict[str, Any] | None, values: dict[str, Any]) -> dict[str, Any]:
    """AIPOS-F194: 开工稿 / 章程「本 run 专用模拟器」步骤的渲染值(命令全由声明 + 本 run 记录铸; 未绑定 = UDID 占位)。"""
    sim_decl = ios_simulator_declaration()
    run_id = run["run_id"] if run else "<run-id>"
    bound = (run or {}).get("simulator") or {}
    udid = bound.get("udid") or "<本 run UDID(sim-create 末行 SIM_CREATED udid=…)>"
    helper_cmd = f"python3 {values['helper_path']}"
    common = f"--run-dir {values['run_dir']} --expect-fingerprint {values['fingerprint']}"
    patterns = " ".join(f"--pattern {shlex.quote(p)}" for p in sim_decl["xcode_temp_side_effects"]["patterns"])
    gcmd = _gov_cmd(governance_root)
    evidence = f"{values['run_dir']}/{sim_decl['evidence_file']}"
    sim_values = {"sim_name": simulator_name(entry, run_id), "sim_udid": udid, "devices_rel": sim_decl["devices_rel"],
                  "sim_evidence": evidence, "side_evidence": f"{values['run_dir']}/{sim_decl['side_evidence_file']}",
                  "sim_create": f"{helper_cmd} sim-create {common} --device-type <设备型号 id> --runtime <运行时 id>",
                  "sim_verify": f"{helper_cmd} sim-verify {common}", "sim_delete": f"{helper_cmd} sim-delete {common}",
                  "side_before": f"{helper_cmd} sys-snapshot {common} --phase before {patterns}",
                  "side_after": f"{helper_cmd} sys-snapshot {common} --phase after {patterns}",
                  "sim_bind": f"{gcmd} project validation-root sim-bind --id {entry['id']} --run-id {run_id} --udid {bound.get('udid') or '<UDID>'} "
                              "--confirm --actor <顾问实例>",
                  "run_finish_sim": f"{gcmd} project validation-root run-finish --id {entry['id']} --run-id {run_id} --sim-evidence "
                                    "<simulator.json 在治理根所在机的副本> --confirm --actor <顾问实例>"}
    actions = []
    for action in sim_decl["xcodebuild_actions"]:
        actions.append(_fill(str(sim_decl["xcodebuild_template"]), {**values, **sim_values, "action": action}))
    sim_values["xcodebuild_actions"] = "; ".join(f"`{a}`" for a in actions)
    return sim_values


def render_list_lines(entries: list[dict[str, Any]]) -> list[str]:
    now = _now()
    if not entries:
        return ["(无验证根声明; project.json validation_roots 缺段 = 行为不变)"]
    out: list[str] = []
    for e in entries:
        run = active_run(e)
        out.append(f"{e['id']}  [{declaration_status(e, now)}]  host={e['host']}  fp={e['host_fingerprint'] or '(未绑定)'}  "
                   f"task={e['task_id']}  instance={e['instance']}({e['role_class']})  expires={e['expires_at']}")
        out.append(f"    可写 = {run_directory(e, '<run-id>')}  base={e['base']}  owner_ref={e['owner_ref']}  declared_by={e['declared_by']} "
                   f"@ {e['declared_at']}" + (f"  revoked_by={e['revoked_by']} @ {e['revoked_at']}({e['revoke_reason']})" if e["revoked_at"] else ""))
        for r in e["runs"]:
            state = f"finished {r['finished_at']} by {r['finished_by']}" if r["finished_at"] else "ACTIVE"
            out.append(f"    run {r['run_id']}  candidate={r['candidate']}  started {r['started_at']} by {r['started_by']}  {state}"
                       + ("" if r["finished_at"] else f"  → {run_directory(e, r['run_id'])}"))
            sim = r.get("simulator")
            if sim:
                out.append(f"      专用模拟器 {sim['name']} udid={sim['udid']} 绑定 {sim['bound_at']} by {sim['bound_by']}"
                           + (f"  删除核对通过 {sim['verified_at']}(证据 sha256 {sim['evidence_sha256'][:12]}…)" if sim["verified_at"] else "  (未核删除)"))
        if run is None and declaration_status(e, now) == "active":
            out.append("    (无活动 run: 写前须 run-start)")
    return out


# AIPOS-316: Guard against direct invocation
from tools.aipos_cli._cli_entry_guard import check_direct_invocation  # noqa: E402
check_direct_invocation(__name__)
