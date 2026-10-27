"""AIPOS-362 — 远端 agent 凭据注册式下发(enrollment).

设计:
  - 注册码(enrollment code)是一次性、可时效、可吊销的设备注册凭证。
  - Owner/授权顾问签发注册码,绑定 role/instance/ttl。
  - 远端 agent 用注册码兑换(exchange)出真实的 capability token。
  - Token 明文只在 gate→agent 的网络响应中传输,不落任何中间态。
  - 注册码 ≠ token,可以明文传递(它本身不是凭据,只是兑换凭据的临时通行证)。

AIPOS-F23 自包含码(单一格式定义处, 红线: 码格式只有一处定义):
  - `LYBRAENROLL1.<base64url(JSON)>` 其中 JSON = {v, gate_url, governance_root,
    transport_token, code}。内嵌 gate 对外可达地址/治理根/运输通行凭证;
    码即认证(单次 + TTL + 可撤销),工位侧不再需要任何 bootstrap token。
  - 运输通行凭证(transport token)是零 scope 的 service token,只够过 HTTP
    transport 层认证调公开动词(enroll_exchange/enroll_land),兑换出的角色
    token 才是真正的凭据。
  - 交换与落盘原子(F23 验收⑦): 首次 exchange 开 grace 窗口(默认 10 分钟),
    窗口内同码免费重试(返回同一 token, 不重铸);工位落盘成功后调
    lybra_roles_enroll_land 标记 landed;landed 后或 grace 过期后码彻底消费。

存储:
  - 注册码数据存于 <workspace>/.lybra/enrollments.json(仅 gate 侧,0600)。
  - 格式: {code_id: {code, role, instance, status, created_at, expires_at, used_at,
      landed_at, grace_until, minted_token_entry, revoked_at, ...}}
  - 状态: pending(未使用) / used(已兑换) / revoked(已吊销) / expired(已过期)

Red lines:
  - Token 明文永不进日志/输出/argv。
  - 注册码签发需 owner 授权(owner-gated)。
  - 兑换是公开端点(不需要已有 token),但需校验 code 有效性。
"""
from __future__ import annotations

import base64
import binascii
import json
import secrets
import re
from datetime import datetime, timedelta
from tools.aipos_cli.clock import iso_z, utc_now
from pathlib import Path
from typing import Any, Literal

EnrollmentStatus = Literal["pending", "used", "revoked", "expired"]

# ---------------------------------------------------------------------------
# AIPOS-F23: 自包含码格式 —— 唯一定义处(编码/解码/前缀/字段表都在本模块)
# ---------------------------------------------------------------------------

SELF_CONTAINED_CODE_PREFIX = "LYBRAENROLL1."
SELF_CONTAINED_CODE_VERSION = 1
#: grace 窗口秒数: exchange 后未落盘, 同码可免费重试的时限(验收⑦ "码不白烧")
ENROLL_LANDING_GRACE_SECONDS = 600
#: 自包含码默认 TTL(未显式给 ttl 时; 同时绑定运输凭证的有效期)
ENROLL_DEFAULT_TTL_SECONDS = 86400
#: 运输凭证的角色名(零 scope, 仅过 transport 层; 兑换出的角色 token 才是真凭据)
TRANSPORT_TOKEN_ROLE = "enroll-transport"


#: AIPOS-F151 件③: 码内非秘密的签发声明字段(可缺; 旧码无此三键)。只供兑换方换码前预检(工位类须有覆盖信封),
#: 不是授权依据——换码后以门下发的 token_entry 为准, 两者不一致即拒(enroll_client.enroll)
SELF_CONTAINED_DECLARED_KEYS = ("role", "instance", "role_class")


def encode_self_contained_code(
    *,
    gate_url: str,
    governance_root: str,
    transport_token: str,
    code: str,
    role: str | None = None,
    instance: str | None = None,
    role_class: str | None = None,
) -> str:
    """F23: 编码自包含码 —— 格式唯一定义处。

    payload = {v:1, gate_url, governance_root, transport_token, code[, role, instance, role_class]}
    输出 = "LYBRAENROLL1." + base64url(JSON, 无 padding)
    AIPOS-F151 件③: role / instance / role_class = 签发时的声明(非秘密, 给了才写; 同版本 v1 —— 旧解码器忽略多余键)。
    """
    payload = {
        "v": SELF_CONTAINED_CODE_VERSION,
        "gate_url": str(gate_url),
        "governance_root": str(governance_root),
        "transport_token": str(transport_token),
        "code": str(code),
    }
    for key, value in zip(SELF_CONTAINED_DECLARED_KEYS, (role, instance, role_class)):
        if str(value or "").strip():
            payload[key] = str(value).strip()
    raw = json.dumps(payload, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
    b64 = base64.urlsafe_b64encode(raw.encode("utf-8")).decode("ascii").rstrip("=")
    return SELF_CONTAINED_CODE_PREFIX + b64


def decode_self_contained_code(text: str) -> dict[str, Any] | None:
    """F23: 解码自包含码。非自包含码(旧裸码)返回 None, 调用方走旧路径。

    Returns:
        {v, gate_url, governance_root, transport_token, code, role, instance, role_class} 或 None
        (AIPOS-F151: role / instance / role_class 旧码无 = 空串)
    """
    s = str(text or "").strip()
    if not s.startswith(SELF_CONTAINED_CODE_PREFIX):
        return None
    b64 = s[len(SELF_CONTAINED_CODE_PREFIX):]
    # 容错: base64url 无 padding → 补齐
    pad = (-len(b64)) % 4
    try:
        raw = base64.urlsafe_b64decode(b64 + "=" * pad).decode("utf-8")
        payload = json.loads(raw)
    except (binascii.Error, UnicodeDecodeError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict):
        return None
    if payload.get("v") != SELF_CONTAINED_CODE_VERSION:
        return None
    for key in ("gate_url", "transport_token", "code"):
        if not str(payload.get(key) or "").strip():
            return None
    return {
        "v": payload["v"],
        "gate_url": str(payload["gate_url"]),
        "governance_root": str(payload.get("governance_root") or ""),
        "transport_token": str(payload["transport_token"]),
        "code": str(payload["code"]),
        **{key: str(payload.get(key) or "").strip() for key in SELF_CONTAINED_DECLARED_KEYS},
    }


def mint_transport_token_entry(
    workspace_root: str | Path,
    *,
    ttl_seconds: int | None = None,
    code_id: str | None = None,
) -> dict[str, Any]:
    """F23: 铸一枚零 scope 运输通行凭证并注册进 gate 侧 connection.json。

    - role=enroll-transport(不在 roles 注册表 → 任何 scope 检查 fail-closed,
      只能过 transport 层调公开动词)
    - scopes=[] 显式声明零权限
    - expires_at 跟随码 TTL(码即认证: 码过期 = 运输凭证同时失效)
    - 注册后由调用方触发 _reload_token_registry() 热生效
    """
    from tools.aipos_cli.service_mode import secret_fingerprint
    from tools.aipos_cli.enroll_client import (
        ensure_lybra_dir,
        load_or_create_connection_json,
        upsert_token_entry,
        write_connection_json,
    )

    root = _workspace_root_path(workspace_root)
    token = secrets.token_urlsafe(32)
    now = utc_now().replace(microsecond=0)
    ttl = int(ttl_seconds) if ttl_seconds and int(ttl_seconds) > 0 else ENROLL_DEFAULT_TTL_SECONDS
    expires_at = (now + timedelta(seconds=ttl)).isoformat().replace("+00:00", "Z")
    entry = {
        "role": TRANSPORT_TOKEN_ROLE,
        "token": token,
        "token_ref": f"svc-{TRANSPORT_TOKEN_ROLE}",
        "scopes": [],
        "fingerprint": secret_fingerprint(token),
        "expires_at": expires_at,
    }
    if code_id:
        entry["agent_instance"] = code_id  # 可追溯: 运输凭证 ←→ 码

    lybra_dir = ensure_lybra_dir(root)
    connection_data = load_or_create_connection_json(lybra_dir, gate_url=None)
    upsert_token_entry(connection_data, entry)
    write_connection_json(lybra_dir, connection_data)
    return entry


def resolve_gate_url_default(
    workspace_root: str | Path,
    *,
    instance: str | None = None,
    governance_root: str | None = None,
) -> dict[str, Any]:
    """F23: 自包含码内嵌 gate_url 的缺省推导(未给显式 gate_url 时; 唯一实现)。

    AIPOS-F106 件①②: 门基址经 confirm_client.resolve_gate_base_url(唯一推导口, 委托 ConnectionResolver.resolve_gate_url;
    env 不参与——码内嵌地址只认工作区声明 connection.json#mcp.rpc_url)。
    AIPOS-F153 件②(gap #93): 按 config.schema lybra_dir_authority.same_host_rule 取址——码在门机兑换(同机)= loopback
    (enroll_client.loopback_gate_url 唯一渲染, 端口取声明地址的端口; 免疫代理劫道); 跨机才用对外地址。跨机判据 = 已有声明:
    实例在所属项目 project.json workstations 下声明(F110 跨机工位开工材料, 唯一读取口 workspace_config.project_workstation;
    所属项目经 enrollment_owner_root 唯一解析)。跨机而声明的门地址只有 loopback = 拒(ValueError: 远端连不上本机回环; 出口 = 显式
    --gate-url 给对外地址)。跨机经 ssh 交付(enroll_deliver --ssh)本就显式传对外地址, 不走本推导。
    返回 {url, cross_host, source}。"""
    from urllib.parse import urlparse

    from tools.aipos_cli.confirm_client import resolve_gate_base_url
    from tools.aipos_cli.enroll_client import loopback_gate_url
    from tools.aipos_cli.workspace_config import WorkstationDeclarationError, project_workstation

    root = _workspace_root_path(workspace_root)
    url = resolve_gate_base_url(workspace_root=root, env={})
    declared_loopback = (urlparse(url).hostname or "") in ("127.0.0.1", "localhost", "::1", "0.0.0.0")
    name = str(instance or "").strip()
    owner = enrollment_owner_root(root, governance_root=governance_root, instance=name) if name else {"root": None}
    cross_declared = None
    if owner["root"] is not None:
        try:
            project_workstation(owner["root"], name)
            cross_declared = f"{owner['root']}/project.json workstations.{name}"
        except WorkstationDeclarationError as exc:
            if exc.code != "WORKSTATION_MATERIAL_UNDECLARED":  # 声明形坏 = 照抛(fail-closed), 不当作同机
                raise
    if cross_declared:
        if declared_loopback:
            raise ValueError(f"实例 {name} 声明为跨机工位({cross_declared}), 但门工作区 connection.json 声明的门地址 {url} 是本机回环, "
                             "远端兑换连不上; 出口: 显式 --gate-url <远端可达的门地址>")
        return {"url": url, "cross_host": True, "source": f"跨机工位声明 {cross_declared}: 对外地址(connection.json mcp.rpc_url)"}
    return {"url": loopback_gate_url(url), "cross_host": False,
            "source": "同机: config.schema lybra_dir_authority.same_host_rule 取 loopback(实例未在所属项目 project.json workstations 声明为跨机)"}


def issue_self_contained_code(
    workspace_root: str | Path,
    *,
    role: str,
    instance: str | None = None,
    ttl_seconds: int | None = None,
    gate_url: str | None = None,
    governance_root: str | None = None,
    by: str = "owner",
    reason: str = "",
) -> dict[str, Any]:
    """F23: 发码的唯一实现(门动词 lybra_enroll_code_* 与 CLI roles enroll-code 共用)。

    红线: 发码只有一份实现 —— 本函数。产出:
      - 既有 enrollment 记录(create_enrollment_code, 单次/TTL/撤销面沿用)
      - 运输通行凭证(零 scope, 注册进 gate connection.json)
      - 自包含码(encode_self_contained_code)
      - 交付文案(AIPOS-F93 件②): paste_text = `lybra roles enroll --code <自包含码> --workspace <工位目录或治理根> --verify`
        (onboarding.enroll_delivery 唯一渲染, 门与 CLI 共用)

    F24A: governance_root 显式参数优先(调用方=门动词, 已从项目注册表校验); 缺省回落
    workspace_root(记录/运输凭证所在根, 与交换/land 同根 —— 交换面不认识别的根)。
    发码必须发生在门进程内(CLI 只许薄壳): 运输凭证注册与门内存注册表同进程, 才能热重载即活。
    """
    root = _workspace_root_path(workspace_root)
    effective_ttl = int(ttl_seconds) if ttl_seconds and int(ttl_seconds) > 0 else ENROLL_DEFAULT_TTL_SECONDS
    # AIPOS-F153 件②: 码内门地址先于发码确定(推导拒 = 不建码记录、不铸运输凭证); 显式参数优先, 缺省按 same_host_rule
    # (同机 loopback, 声明跨机才用对外地址; resolve_gate_url_default 唯一实现)
    if (gate_url or "").strip():
        gate_choice = {"url": str(gate_url).strip(), "cross_host": None, "source": "显式 gate_url"}
    else:
        gate_choice = resolve_gate_url_default(root, instance=instance, governance_root=(governance_root or "").strip() or None)

    # ① 既有发码实现(单次/TTL/撤销面不动)
    enrollment = create_enrollment_code(
        root,
        role=role,
        instance=instance,
        ttl_seconds=effective_ttl,
        by=by,
        reason=reason,
        governance_root=(governance_root or "").strip() or None,
    )

    # ② 运输通行凭证(TTL 与码一致; 码即认证 —— 码过期凭证同步失效)
    transport_entry = mint_transport_token_entry(
        root, ttl_seconds=effective_ttl, code_id=enrollment["code_id"]
    )

    # ③ 自包含码(gate_url 缺省推导, 显式参数优先; governance_root 同理 —— F24A)
    resolved_gate_url = gate_choice["url"]
    # AIPOS-F50-fix1: governance_root 禁回落 workspace_root —— workspace_root 是发码门的工作区
    # (可能是 lybra), 不一定是治理根。governance_root 为空时应保持空, 让 enroll_exchange
    # 的推导失败逻辑处理 (projects=[], projects_enforced=False)。
    resolved_governance_root = (governance_root or "").strip()
    # AIPOS-F151 件③: 码内带签发声明(角色 / 实例 / 角色类), 兑换方换码前据此预检覆盖信封(不消耗码、不改凭据)。
    # 角色类经唯一实现解析(注册表根 = 码内治理根, 缺省发码门工作区); 解析不到 = 不写该键(兑换方退回换码后复核)
    from tools.aipos_cli.custom_roles import resolve_role_to_class

    declared_class = resolve_role_to_class(role, resolved_governance_root or root)
    self_contained = encode_self_contained_code(
        gate_url=resolved_gate_url,
        governance_root=resolved_governance_root,
        transport_token=transport_entry["token"],
        code=enrollment["code"],
        role=role,
        instance=instance,
        role_class=declared_class,
    )

    # ④ 交付文案(AIPOS-F93 件②: 产品命令形, 向导同一渲染源 onboarding.enroll_delivery; 门与 CLI 共用)
    from tools.aipos_cli.onboarding import enroll_delivery

    delivery = enroll_delivery(self_contained)

    return {
        "ok": True,
        "code_id": enrollment["code_id"],
        "self_contained_code": self_contained,
        "paste_text": delivery["paste_text"],
        "paste_instruction": delivery["paste_instruction"],
        "next_step": delivery["next_step"],
        "role": role,
        "instance": instance,
        "ttl_seconds": effective_ttl,
        "expires_at": enrollment.get("expires_at"),
        "gate_url": resolved_gate_url,
        "gate_url_source": gate_choice["source"],
        "governance_root": resolved_governance_root,
        "transport_token_fingerprint": transport_entry.get("fingerprint"),
        "transport_token_expires_at": transport_entry.get("expires_at"),
        "fingerprint": enrollment["fingerprint"],
        "by": by,
        "reason": reason,
    }


def _workspace_root_path(workspace_root: str | Path) -> Path:
    """Convert workspace_root to Path."""
    return Path(workspace_root).resolve()


def _enrollments_path(workspace_root: str | Path) -> Path:
    """返回 enrollment codes 存储路径: <workspace>/.lybra/enrollments.json"""
    root = _workspace_root_path(workspace_root)
    return root / ".lybra" / "enrollments.json"


def _load_enrollments(workspace_root: str | Path) -> dict[str, dict[str, Any]]:
    """加载所有注册码记录。返回 {code_id: enrollment_record}。"""
    path = _enrollments_path(workspace_root)
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            return {}
        return data
    except (json.JSONDecodeError, OSError):
        return {}


def _save_enrollments(workspace_root: str | Path, data: dict[str, dict[str, Any]]) -> None:
    """保存注册码记录,0600 权限。"""
    path = _enrollments_path(workspace_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    path.chmod(0o600)


def _generate_enrollment_code() -> str:
    """生成一个 48 字节的 URL-safe 注册码(64 字符)。"""
    return secrets.token_urlsafe(48)


def _code_fingerprint(code: str) -> str:
    """返回注册码的非秘密指纹(sha256 前 12 位)。"""
    import hashlib
    return "sha256:" + hashlib.sha256(code.encode()).hexdigest()[:12]


def enrollment_trail_path(workspace_root: Path) -> Path:
    """本项目 enrollment_log.md 位置(append-only 审计日志; 写入与 F95 工位位置读取同一口径)。"""
    from tools.aipos_cli.workspace_config import governance_paths

    return governance_paths(Path(workspace_root))["decision_log"].parent / "enrollment_log.md"


def enrollment_owner_root(
    workspace_root: str | Path,
    *,
    governance_root: str | None = None,
    projects: Any = None,
    instance: str | None = None,
) -> dict[str, Any]:
    """AIPOS-F107 件②: 接入事件(create/use/land/revoke)所属项目治理根的唯一解析口(写侧 _append_enrollment_trail 与诊断
    enrollment_whereabouts 共用)。workspace_root = 签发门工作区(码记录所在)。

    次序(先命中者为准):
      ① 码记录 governance_root(签发时经门项目注册表校验; 凭据 projects 亦由它推导)——须为已建项目(队列 + project.json)
      ② 凭据 projects(兑换铸出的 token_entry.projects, 恰一个项目名)
      ③ 实例名项目段(naming_profile.parse_instance_name 唯一切分)
    ②③按项目名经 workspace_config.resolve_home_root / resolve_project_root 解析, 且只在签发门所在 home 内解析
    (签发门工作区 = home 本身或其直接子项目; 不在则不跨 home 猜)。
    返回 {root: Path | None, project, source, reason}; root=None = 无所属项目(reason 说明), 由调用方决定落点并出声。"""
    from tools.aipos_cli.naming_profile import parse_instance_name
    from tools.aipos_cli.workspace_config import has_workspace_queue, read_project_json, resolve_home_root, resolve_project_root

    issuer = _workspace_root_path(workspace_root)
    misses: list[str] = []

    def hit(root: Path, source: str) -> dict[str, Any]:
        project = str(read_project_json(root).get("project") or root.name).strip()
        return {"root": root, "project": project, "source": source, "reason": ""}

    explicit = str(governance_root or "").strip()
    if explicit:
        root = Path(explicit).expanduser().resolve()
        if has_workspace_queue(root, established=True):
            return hit(root, "码记录 governance_root")
        misses.append(f"码记录 governance_root {root} 不是已建项目(缺队列或 project.json)")
    names: list[tuple[str, str]] = []
    if isinstance(projects, list):
        named = [str(x).strip() for x in projects if str(x).strip() and str(x).strip() != "*"]
        if len(named) == 1:
            names.append((named[0], "凭据 projects"))
        elif named:
            misses.append(f"凭据 projects 含多个项目 {named}, 不猜")
    parsed = parse_instance_name(instance or "") if instance else None
    if parsed and parsed.get("project"):
        names.append((parsed["project"], "实例名项目段"))
    elif instance:
        misses.append(f"实例名 {instance!r} 不合命名模板, 无项目段")
    if names:
        home = resolve_home_root()
        if issuer != home and issuer.parent != home:
            misses.append(f"签发门工作区 {issuer} 不在 home {home} 内, 不按项目名跨 home 解析")
        else:
            for name, source in names:
                try:
                    return hit(resolve_project_root(home, name), source)
                except (FileNotFoundError, ValueError) as exc:
                    misses.append(f"{source} {name!r}: {exc}")
    return {"root": None, "project": None, "source": "", "reason": "; ".join(misses) or "码无 governance_root / 凭据 projects / 实例名"}


# AIPOS-F95 件③b: land 事件 reason 字段的位置载荷(enroll_client 写 `host=<主机> workstation=<目录> files=[...]`)
# AIPOS-F161: 位置事件 = land(接入落地)或 relocate(`lybra project relocate-workstation` 迁移工位, append_relocation_event 唯一写入),
# 二者同一解析(本正则)、同一「最新未作废者为准」(_latest_land); 动作名 relocate 与声明 enums.schema
# workstation_transport.relocation.event_action 一致(workstation_relocate.relocation_declaration 核, 不一致 = fail-closed)。
LAND_ACTION = "land"
RELOCATE_ACTION = "relocate"
# AIPOS-F235: 改登记位置事件(redeclare; `lybra roles redeclare`, append_redeclaration_event 唯一写入)与 land / relocate 同族同解析;
# 动作名与声明 verbs.schema lybra_roles_redeclare.event_action 一致(redeclaration_declaration 核, 不一致 = fail-closed)
REDECLARE_ACTION = "redeclare"
_LAND_LINE_RE = re.compile(rf"^- (?P<ts>\S+)\s+(?P<action>{LAND_ACTION}|{RELOCATE_ACTION}|{REDECLARE_ACTION})\s+.*?\binstance=(?P<instance>\S+)\s+"
                           r".*?\breason=(?P<reason>.*)$")
_LAND_HOST_RE = re.compile(r"(?:^|\s)host=(?P<host>\S+)")
_LAND_DIR_RE = re.compile(r"(?:^|\s)workstation=(?P<dir>.+?)(?:\s+files=|$)")


def workstation_transport_declaration() -> dict[str, Any]:
    """enums.schema workstation_transport(工位位置 transport 值域 / 已支持与否 / 缺 host 缺省)唯一读取口。缺 = ValueError。"""
    from tools.schema_loader import code_repo_schema_root, load_schema

    decl = (load_schema("enums", code_repo_schema_root()).get("enums") or {}).get("workstation_transport")
    if not isinstance(decl, dict) or not isinstance(decl.get("values"), list) or not str(decl.get("missing_host") or "").strip():
        raise ValueError("enums.schema.json enums.workstation_transport(values / missing_host)未声明")
    return decl


def _latest_land(trail: Path, instance: str) -> re.Match[str] | None:
    """该日志中实例最新位置事件(land / AIPOS-F161 relocate; _LAND_LINE_RE 唯一解析; workstation_location 与诊断 enrollment_whereabouts 共用)。
    AIPOS-F121 件②: 只看未作废事件(_instance_events 唯一判定), 已作废的 land 不再定位工位。"""
    latest: re.Match[str] | None = None
    for _lineno, line in _instance_events(trail, instance)["live"]:
        match = _LAND_LINE_RE.match(line)
        if match:
            latest = match
    return latest


# AIPOS-F121 件②: 作废事件(void)——日志只追加不删行; 作废 = 追加一行指向被作废的行号区间与 code_id, 读取口据此忽略。
VOID_ACTION = "void"
_VOID_LINE_RE = re.compile(
    r"^- (?P<ts>\S+)\s+void\s+code_id=\S+\s+.*?\binstance=(?P<instance>\S+)\s+.*?\bvoids_lines=(?P<ranges>\S+)\s"
)


def _line_ranges(numbers: list[int]) -> str:
    """行号集 → 区间串 `18-22,25,27-30`(升序合并连续段; 作废行与诊断共用唯一渲染)。"""
    spans: list[list[int]] = []
    for n in sorted(set(numbers)):
        if spans and n == spans[-1][1] + 1:
            spans[-1][1] = n
        else:
            spans.append([n, n])
    return ",".join(f"{lo}-{hi}" if hi != lo else f"{lo}" for lo, hi in spans)


def _parse_line_ranges(text: str, *, where: str) -> set[int]:
    """区间串 → 行号集(_line_ranges 的逆)。形坏 = ValueError(fail-closed: 坏作废行不得被当作「未作废」静默放过)。"""
    out: set[int] = set()
    for part in text.split(","):
        bounds = part.split("-")
        if not part or len(bounds) > 2 or not all(b.isdigit() for b in bounds):
            raise ValueError(f"{where}: voids_lines 区间形坏 {part!r}(应为 18-22,25 形)")
        lo, hi = int(bounds[0]), int(bounds[-1])
        if lo < 1 or hi < lo:
            raise ValueError(f"{where}: voids_lines 区间非法 {part!r}")
        out.update(range(lo, hi + 1))
    return out


def _instance_events(trail: Path, instance: str) -> dict[str, list[tuple[int, str]]]:
    """该日志中实例的接入事件按作废状态分拣(读取口唯一判定; _latest_land / enrollment_whereabouts / void_instance_events 共用)。

    返回 {live: 未作废事件, voided: 已作废事件, voids: 指向该实例的作废行}, 元素 = (物理行号 1 起, 行文本 strip 后)。
    作废行的 voids_lines 区间内、且属该实例的事件 = 已作废; 区间外(含作废之后的重接入)仍为 live。"""
    lines = _trail_matches(trail)
    voided_numbers: set[int] = set()
    voids: list[tuple[int, str]] = []
    for lineno, line, void_match, _event in lines:
        if void_match and void_match.group("instance") == instance:
            voided_numbers |= _parse_line_ranges(void_match.group("ranges"), where=f"{trail}:{lineno}")
            voids.append((lineno, line))
    out: dict[str, list[tuple[int, str]]] = {"live": [], "voided": [], "voids": voids}
    for lineno, line, _void, match in lines:
        if not match or match.group("instance") != instance or match.group("action") == VOID_ACTION:
            continue
        out["voided" if lineno in voided_numbers else "live"].append((lineno, line))
    return out


def _trail_matches(trail: Path) -> list[tuple[int, str, re.Match[str] | None, re.Match[str] | None]]:
    """日志各行 (物理行号 1 起, strip 后行文本, 作废行匹配, 事件行匹配)。AIPOS-F177 件③: 请求级只读作用域内同一日志只读、只匹配一次
    (接入页逐实例判定共用; task_loader.request_memo), 作用域外每次现读。"""
    from tools.aipos_cli.task_loader import request_memo

    def compute() -> list[tuple[int, str, re.Match[str] | None, re.Match[str] | None]]:
        lines = [line.strip() for line in trail.read_text(encoding="utf-8").splitlines()]
        return [(lineno, line, _VOID_LINE_RE.match(line), _EVENT_LINE_RE.match(line)) for lineno, line in enumerate(lines, start=1)]

    return request_memo(("enrollment._trail_matches", str(Path(trail).resolve())), compute)


def workstation_location(workspace_root: str | Path, instance: str) -> dict[str, Any]:
    """AIPOS-F95 件③b: 实例工位位置 {host, dir} = 本项目 enrollment_log 中该实例最新 land 事件; 再按声明解析 transport。

    返回 {found, instance, host, dir, transport, supported, reason, landed_at}:
      - found=False: 日志缺 / 无该实例 land 事件 / 事件无 workstation 目录(reason 说明)
      - host 缺(F95 前存量事件)→ host=None, transport = 声明 missing_host(本机)
      - host == 本机主机名(socket.gethostname)→ local, 否则 remote; supported 读声明
    只读, 不写日志。"""
    import socket

    decl = workstation_transport_declaration()
    supported = {str(v.get("value")): bool(v.get("supported")) for v in decl["values"] if isinstance(v, dict)}
    out: dict[str, Any] = {"found": False, "instance": instance, "host": None, "dir": None, "transport": None,
                           "supported": False, "reason": "", "landed_at": None}
    trail = enrollment_trail_path(_workspace_root_path(workspace_root))
    if not trail.is_file():
        out["reason"] = f"{trail} 不存在(本项目无接入登记)"
        return out
    latest = _latest_land(trail, instance)
    if latest is None:
        out["reason"] = f"{trail.name} 无实例 {instance} 的 land 事件(该实例未在本项目接入登记)"
        return out
    reason = latest.group("reason")
    dir_match = _LAND_DIR_RE.search(reason)
    if not dir_match or not dir_match.group("dir").strip():
        out["reason"] = f"{trail.name} 中 {instance} 最新 land 事件无 workstation=<目录>: {reason[:120]}"
        return out
    host_match = _LAND_HOST_RE.search(reason)
    host = host_match.group("host") if host_match else None
    if host is None:
        transport = str(decl["missing_host"])
    else:
        transport = "local" if host == socket.gethostname() else "remote"
    if transport not in supported:
        out["reason"] = f"transport {transport!r} 不在 enums.schema workstation_transport 值域 {sorted(supported)}"
        return out
    out.update(found=True, host=host, dir=dir_match.group("dir").strip(), transport=transport,
               supported=supported[transport], landed_at=latest.group("ts"))
    return out


_EVENT_LINE_RE = re.compile(r"^- (?P<ts>\S+)\s+(?P<action>\S+)\s+code_id=(?P<code_id>\S+)\s+.*?\binstance=(?P<instance>\S+)")


# ---------------------------------------------------------------------------
# AIPOS-F161: 工位迁移位置事件(relocate)。工位位置的唯一声明来源仍是本日志(不新增路径表); 迁移 = 追加一行 relocate:
# 结构字段沿用被迁移的位置事件(code_id / role; reason 沿用 host / harness), reason 记 relocation=<forward|rollback>
# relocated_from=<旧目录> workstation=<新目录>(workstation= 在末尾, _LAND_DIR_RE 原样读)。作废(enroll-void)按 code_id 一并覆盖。
# ---------------------------------------------------------------------------

_LAND_FROM_RE = re.compile(r"(?:^|\s)relocated_from=(?P<dir>.+?)\s+workstation=")
# AIPOS-F235: redeclare 行的会话位置段(harness_host / harness_dir 在 workstation= 之前; host= 仍为工位 / 凭据落点所在机, 语义同 land)
_LAND_HARNESS_HOST_RE = re.compile(r"(?:^|\s)harness_host=(?P<harness_host>\S+)")
_LAND_HARNESS_DIR_RE = re.compile(r"(?:^|\s)harness_dir=(?P<harness_dir>\S+)")
_LAND_RELOCATION_RE = re.compile(r"(?:^|\s)relocation=(?P<relocation>\S+)")


def location_event(workspace_root: str | Path, instance: str) -> dict[str, Any]:
    """实例最新未作废位置事件(_latest_land 同一读取口)的结构字段: {found, action, ts, code_id, role, host, harness, relocation,
    relocated_from, dir, line_fields, mode, harness_host, harness_dir}。line_fields = 能沿用其 code_id / role 渲染 relocate / redeclare 行。
    AIPOS-F235: mode(子 agent land)/ harness_host / harness_dir(redeclare 写: 顾问会话所在机 / 工作目录)一并读出。只读。"""
    out: dict[str, Any] = {"found": False, "action": None, "ts": None, "code_id": None, "role": None, "host": None, "harness": None,
                           "relocation": None, "relocated_from": None, "dir": None, "line_fields": False, "mode": None,
                           "harness_host": None, "harness_dir": None}
    trail = enrollment_trail_path(_workspace_root_path(workspace_root))
    latest = _latest_land(trail, str(instance or "").strip()) if trail.is_file() else None
    if latest is None:
        return out
    reason = latest.group("reason")
    event = _EVENT_LINE_RE.match(latest.string)
    role = _LAND_ROLE_RE.search(latest.string[:latest.start("reason")])
    found = {key: (m.group(key) if (m := rx.search(reason)) else None)
             for key, rx in (("host", _LAND_HOST_RE), ("harness", _LAND_HARNESS_RE), ("relocation", _LAND_RELOCATION_RE),
                             ("mode", _LAND_MODE_RE), ("harness_host", _LAND_HARNESS_HOST_RE), ("harness_dir", _LAND_HARNESS_DIR_RE))}
    from_match, dir_match = _LAND_FROM_RE.search(reason), _LAND_DIR_RE.search(reason)
    out.update(found=True, action=latest.group("action"), ts=latest.group("ts"), code_id=event.group("code_id") if event else None,
               role=role.group("role") if role else None, **found,
               relocated_from=from_match.group("dir").strip() if from_match else None,
               dir=dir_match.group("dir").strip() if dir_match else None)
    out["line_fields"] = bool(out["code_id"] and out["role"])
    return out


def relocation_event_line(event: dict[str, Any], *, instance: str, project: str, old: str | Path, new: str | Path, by: str,
                          rollback: bool, ts: str | None = None) -> str:
    """relocate 位置事件一行的唯一渲染(经 _trail_line): host 沿用被迁移事件(缺 = 本机主机名, 迁移在治理根所在机执行)。"""
    import socket

    parts = [f"host={event.get('host') or socket.gethostname()}"]
    if event.get("harness"):
        parts.append(f"harness={event['harness']}")
    parts += [f"relocation={'rollback' if rollback else 'forward'}", f"relocated_from={old}", f"workstation={new}"]
    return _trail_line(action=RELOCATE_ACTION, code_id=str(event["code_id"]), role=str(event["role"]), instance=instance,
                       project=project, by=by, reason=" ".join(parts), ts=ts)


def append_relocation_event(workspace_root: str | Path, instance: str, *, project: str, old: str | Path, new: str | Path, by: str,
                            rollback: bool) -> Path:
    """追加 relocate 位置事件(只追加; 唯一写口 _write_trail_line)。前置: 实例有可沿用字段的最新位置事件、操作者非空无空白、
    新旧目录无空白。不满足 = ValueError(零写入)。返回日志路径。"""
    root = _workspace_root_path(workspace_root)
    event = location_event(root, instance)
    actor = str(by or "").strip()
    if not event["found"] or not event["line_fields"]:
        raise ValueError(f"实例 {instance} 在 {enrollment_trail_path(root)} 无可沿用 code_id / role 的位置事件, 不追加 relocate")
    if not actor or any(ch.isspace() for ch in actor):
        raise ValueError(f"relocate 行须带操作者(非空、无空白): {by!r}")
    if any(ch.isspace() for ch in f"{old}{new}"):
        raise ValueError(f"工位目录含空白(位置事件按空白分段): {old!r} → {new!r}")
    trail = enrollment_trail_path(root)
    _write_trail_line(trail, relocation_event_line(event, instance=instance, project=project, old=old, new=new, by=actor,
                                                   rollback=rollback))
    return trail


# ---------------------------------------------------------------------------
# AIPOS-F235: 已接入实例改登记(`lybra roles redeclare`)——顾问换 harness / 主机 / 工作目录后的正式入口, 不作废重接入。
# 登记真相 = 本日志位置事件(追加 redeclare 行, 与 land / relocate 同族同写口 _write_trail_line, 只追加不改旧行); 治理根 .lybra/role
# 实例槽经 enroll_client.write_role_file(唯一写路径)随之更新; 二者不一致时以日志为准(registered_harness 先读事件), state lint 报出
# (registration_drift)。实例名不改(凭据与历史归因按名; 名中主机段只是历史, 主机判据一律读登记); 凭据不动、不重发码。
# 授权(声明 verbs.schema lybra_roles_redeclare.authorization): 改到 F224 项目常设授权已列主机 / 拉起方式内(或只改工作目录)= 本实例自己或
# 项目顾问可做(第二档, 记录 = 本次位置事件 authorized_by=); 授权外 = 第一档: 追加 redeclare_request 行进 Owner 收件箱
# (owner_inbox 类别 enrollment_redeclare), Owner 批准后重跑同一命令 --confirm 生效。
# ---------------------------------------------------------------------------

REDECLARE_REQUEST_ACTION = "redeclare_request"
_REDECLARE_VERB = "lybra_roles_redeclare"
_REDECLARE_REJECT_CODES = ("REDECLARE_INVALID", "REDECLARE_NOT_ENROLLED", "REDECLARE_WORKSTATION", "REDECLARE_ACTOR",
                           "REDECLARE_NO_CHANGE", "REDECLARE_OWNER_DENIED")
_REDECLARE_TEXT_KEYS = ("target_line", "mode_seat", "mode_subagent", "current_line", "diff_line", "diff_none", "tier2_authorized", "tier2_dir_only", "tier1_request",
                        "owner_pending", "owner_approved", "event_line", "slot_line", "slot_none", "request_line", "invariants")
_REDECLARE_FIELDS = ("kind", "host", "dir")
_REQUEST_KEY_RE = re.compile(r"(?:^|\s)(?P<key>base|to_harness|to_host|to_dir|why)=(?P<value>\S+)")


class RedeclareRefused(ValueError):
    """改登记拒(code ∈ 声明 reject_codes; message = 拒因原文与出口)。零写入。"""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(f"{code}: {message}")
        self.code = code
        self.message = message


def redeclaration_declaration() -> dict[str, Any]:
    """verbs.schema verbs.lybra_roles_redeclare(fail-closed): event_action / request_action 与本模块常量一致; reject_codes / texts /
    exit_codes {ok, refused, owner_pending} / authorization.inbox_category 齐。"""
    from tools.schema_loader import SchemaLoadError, code_repo_schema_root, load_schema

    decl = (load_schema("verbs", code_repo_schema_root()).get("verbs") or {}).get(_REDECLARE_VERB)
    where = f"verbs.schema.json verbs.{_REDECLARE_VERB}"
    if not isinstance(decl, dict):
        raise SchemaLoadError(f"{where} 未声明")
    if decl.get("event_action") != REDECLARE_ACTION or decl.get("request_action") != REDECLARE_REQUEST_ACTION:
        raise SchemaLoadError(f"{where}.event_action / request_action 须 = {REDECLARE_ACTION} / {REDECLARE_REQUEST_ACTION}(enrollment 解析常量)")
    codes, texts, exits = decl.get("reject_codes") or {}, decl.get("texts") or {}, decl.get("exit_codes") or {}
    auth = decl.get("authorization") or {}
    if (any(not str(codes.get(k) or "").strip() for k in _REDECLARE_REJECT_CODES)
            or any(not str(texts.get(k) or "").strip() for k in _REDECLARE_TEXT_KEYS)
            or not all(isinstance((exits.get(k) or {}).get("code"), int) for k in ("ok", "refused", "owner_pending"))
            or not str(auth.get("inbox_category") or "").strip() or not str(auth.get("approve_option") or "").strip()
            or not str(auth.get("deny_option") or "").strip()):
        raise SchemaLoadError(f"{where}: reject_codes {list(_REDECLARE_REJECT_CODES)} / texts {list(_REDECLARE_TEXT_KEYS)} / "
                              "exit_codes {ok, refused, owner_pending} / authorization {inbox_category, approve_option, deny_option} 未声明齐")
    return decl


def _refuse_redeclare(code: str, detail: str) -> RedeclareRefused:
    return RedeclareRefused(code, f"{detail}。{redeclaration_declaration()['reject_codes'][code]}")


def _lybra_dir(root: Path) -> Path:
    """某根(治理根 / 工位)的 .lybra 目录(既有原语 service_mode.connection_path 的父目录, 不另拼 .lybra 路径)。"""
    return _governance_connection(Path(root)).parent


def _role_file_path(root: Path) -> Path:
    """某根的 .lybra/role 位置(enroll_client._role_file: 读口与写入器共用定位)。"""
    from tools.aipos_cli.enroll_client import _role_file

    return _role_file(_lybra_dir(root))


def _role_slot(lybra_dir: Path, instance: str) -> dict[str, Any] | None:
    """该 .lybra/role 中实例的记录(enroll_client.read_role_record 唯一分槽读口); 未登记 = None; 文件 / 槽形坏 = RoleRecordError 上抛。"""
    from tools.aipos_cli.enroll_client import RoleInstanceNotRegistered, read_role_record

    try:
        return read_role_record(lybra_dir, instance)
    except RoleInstanceNotRegistered:
        return None


def registered_harness(governance_root: str | Path, instance: str) -> dict[str, Any]:
    """AIPOS-F235: 实例当前登记的 harness {instance, kind, host, dir, source, source_label, slot_file, slot}——唯一读取口(起草缺省 /
    kickoff 时限句 / card_workstation_view / next 与 kickoff 不符出口 / 看板工位页 / workstation-plan / 改登记预演同读)。
    判序(日志是登记真相): ① 最新未作废位置事件(location_event)带 harness= = 该事件(redeclare: 会话机 harness_host、工作目录 harness_dir;
    子 agent land: 会话机 = 事件 host)→ ② 该事件工位目录(无 = 治理根)的 .lybra/role 实例记录 harness {kind, dir, host}(存量顾问接入:
    land 不带 harness=; 工位记录无 harness = 缺省 pi 工位, 治理根记录无 harness = 未登记)→ ③ 都无 = kind None。只读。"""
    from tools.aipos_cli.distribution_sync import default_harness_kind

    root = _workspace_root_path(governance_root)
    name = str(instance or "").strip()
    ev = location_event(root, name)
    out: dict[str, Any] = {"instance": name, "kind": None, "host": None, "dir": None, "source": None, "source_label": None,
                           "slot_file": None, "slot": None}
    slot_root = Path(ev["dir"]).expanduser() if ev["found"] and ev["dir"] else root
    slot = _role_slot(_lybra_dir(slot_root), name) if name else None
    if slot is not None:
        out["slot_file"] = str(_role_file_path(slot_root))
        out["slot"] = slot.get("harness") if isinstance(slot.get("harness"), dict) else None
    if ev["found"] and ev["harness"]:
        seat_session = bool(ev["harness_host"] or ev["harness_dir"]) or not ev["dir"]
        out.update(kind=ev["harness"], host=ev["harness_host"] or (ev["host"] if seat_session else None), dir=ev["harness_dir"],
                   source="event", source_label=f"{ev['action']}@{ev['ts']}")
        return out
    if slot is not None:
        record = out["slot"]
        if record and str(record.get("kind") or "").strip():
            out.update(kind=str(record["kind"]).strip(), host=str(record.get("host") or "").strip() or ev["host"],
                       dir=str(record.get("dir") or "").strip() or None, source="role_slot", source_label=out["slot_file"])
        elif slot_root.resolve() != root:  # 工位 .lybra/role 无 harness = pi 工位(既有形态)
            out.update(kind=default_harness_kind(), host=ev["host"], dir=str(slot_root), source="role_slot", source_label=out["slot_file"])
    return out


def registration_label(reg: dict[str, Any]) -> str | None:
    """登记一句(看板工位页 / workstation-plan / 预演同一渲染): <kind>@<host>[:<dir>](<来源>); 未登记 = None。"""
    if not reg.get("kind"):
        return None
    where = f"{reg.get('host') or '-'}" + (f":{reg['dir']}" if reg.get("dir") else "")
    return f"{reg['kind']}@{where}({reg.get('source_label') or reg.get('source') or '-'})"


def dispatch_harness(governance_root: str | Path, *, advisor: str | None, executor: str | None) -> dict[str, Any]:
    """AIPOS-F235 件③: 子 agent 卡的 harness 唯一判定(起草缺省 draft_writer.apply_execution_defaults、卡面缺 harness 时 loop_driver.card_harness、
    next / kickoff 不符出口 card_harness_mismatch 同读): 派发顾问(卡 created_by / draft_created_by)当前登记的 harness(registered_harness;
    须为顾问会话 kind, distribution_sync.advisor_harness_kinds)→ 否则共享子 agent 执行身份 land 事件 harness(instance_enrollment; 兜底)→
    都无 = harness None。返回 {harness, source(advisor|executor|None), advisor, advisor_registration, executor, executor_harness}。
    连接文件读不出 = ValueError 上抛(fail-closed)。"""
    from tools.aipos_cli.distribution_sync import advisor_harness_kinds

    root = _workspace_root_path(governance_root)
    adv, exe = str(advisor or "").strip() or None, str(executor or "").strip() or None
    out: dict[str, Any] = {"harness": None, "source": None, "advisor": adv, "advisor_registration": None, "executor": exe,
                           "executor_harness": None}
    if adv:
        reg = registered_harness(root, adv)
        out["advisor_registration"] = reg
        if reg["kind"] in advisor_harness_kinds():
            out.update(harness=reg["kind"], source="advisor")
    if exe:
        out["executor_harness"] = instance_enrollment(root, exe).get("harness")
        if out["harness"] is None and out["executor_harness"]:
            out.update(harness=out["executor_harness"], source="executor")
    return out


def card_dispatch_advisor(card: dict[str, Any] | None) -> str | None:
    """卡的派发顾问 = 卡面 created_by(发卡签发实例), 缺 = draft_created_by(起草实例)。"""
    fm = card or {}
    return next((str(fm.get(k) or "").strip() for k in ("created_by", "draft_created_by") if str(fm.get(k) or "").strip()), None)


def _authorization_basis(root: Path, new: dict[str, Any], dims: list[str]) -> dict[str, Any]:
    """改登记授权判定(第二档 = 本实例 / 项目顾问可做; 第一档 = 进 Owner 收件箱)。dims = 改动维度(kind / host / dir)。
    只改工作目录 = 第二档(dir_only); 改主机须 ∈ 某份生效授权(状态 active / exhausted, autonomy_policy.envelope_usage 同判)的 hosts,
    改 harness 须 ∈ 其 launch_harnesses(F224 拉起方式); 都满足的授权 = 第二档(ref = <授权>@r<修订>); 否则第一档(why = 缺的维度)。"""
    from tools.aipos_cli.autonomy_policy import authorization_ids, envelope_usage, load_authorization
    from tools.aipos_cli.clock import utc_now

    need = [d for d in dims if d in ("kind", "host")]
    if not need:
        return {"tier": 2, "basis": "dir_only", "authorization": None, "why": []}
    best_why: list[str] | None = None
    ids = authorization_ids(root)
    for aid in ids:
        auth = load_authorization(root, aid)
        if auth is None or envelope_usage(root, aid, now=utc_now()).get("state") not in ("active", "exhausted"):
            continue
        why = ([f"host:{new['host']}"] if "host" in need and new["host"] not in auth["hosts"] else []) + \
              ([f"harness:{new['kind']}"] if "kind" in need and new["kind"] not in auth["launch_harnesses"] else [])
        if not why:
            ref = f"{auth['authorization_id']}@r{auth['revision']}"
            return {"tier": 2, "basis": ref, "authorization": ref, "why": []}
        best_why = why if best_why is None or len(why) < len(best_why) else best_why
    if best_why is None:
        best_why = [f"no_authorization:{','.join(ids) or '-'}"] + [f"{'host' if d == 'host' else 'harness'}:{new[d]}" for d in need]
    return {"tier": 1, "basis": None, "authorization": None, "why": best_why}


def _project_name(root: Path) -> str:
    from tools.aipos_cli.workspace_config import read_project_json

    return str(read_project_json(root).get("project") or root.name).strip()


def redeclare_item_identity(instance: str, *, base: str, to: dict[str, Any]) -> tuple[str, str]:
    """收件箱事项 (subject, anchor) 唯一构造(改登记预演算裁决 ID 与收件箱收集 owner_inbox 同读): anchor = 实例 | 目标 | 基准位置事件时刻
    (基准事件一变 = 新事项; 同一基准同一目标重复请求 = 同一事项)。"""
    return (f"{instance}", f"{to.get('kind') or '-'}@{to.get('host') or '-'}:{to.get('dir') or '-'}|{base}")


def pending_redeclare_requests(governance_root: str | Path) -> list[dict[str, Any]]:
    """未生效的改登记请求(owner_inbox 类别 enrollment_redeclare 唯一来源): 日志中未作废的 redeclare_request 行, 其 base = 该实例当前最新
    位置事件时刻(之后已有位置事件 = 已生效 / 已过时, 不列)。每实例同一目标只列一条。返回 [{instance, ts, by, role, base, to, why, line,
    subject, anchor}]。只读。"""
    root = _workspace_root_path(governance_root)
    trail = enrollment_trail_path(root)
    if not trail.is_file():
        return []
    out: dict[tuple[str, str], dict[str, Any]] = {}
    for _lineno, line, _void, match in _trail_matches(trail):
        if not match or match.group("action") != REDECLARE_REQUEST_ACTION:
            continue
        instance = match.group("instance")
        if (_lineno, line) not in _instance_events(trail, instance)["live"]:
            continue
        reason = line.split("reason=", 1)[1] if "reason=" in line else ""
        fields = {m.group("key"): m.group("value") for m in _REQUEST_KEY_RE.finditer(reason)}
        ev = location_event(root, instance)
        if not ev["found"] or fields.get("base") != ev["ts"]:
            continue
        to = {"kind": fields.get("to_harness"), "host": fields.get("to_host"),
              "dir": None if fields.get("to_dir") in (None, "-") else fields["to_dir"]}
        subject, anchor = redeclare_item_identity(instance, base=str(fields.get("base")), to=to)
        by = line.split(" by=", 1)[1].split()[0] if " by=" in line else ""
        out[(instance, anchor)] = {"instance": instance, "ts": match.group("ts"), "by": by, "base": fields.get("base"), "to": to,
                                   "why": [w for w in str(fields.get("why") or "").split(",") if w], "line": line,
                                   "subject": subject, "anchor": anchor}
    return list(out.values())


def _redeclare_ruling(root: Path, instance: str, base: str, to: dict[str, Any]) -> dict[str, Any]:
    """该请求对应收件箱事项 ID 与 Owner 裁决(owner_inbox.item_id / read_ruling 唯一实现)。choice = 裁决 allowed_next_action。
    项目段 = 治理根目录名(收件箱按 home 根注册名枚举项目, owner_inbox.collect_project_inbox 缺省同此)。"""
    from tools.aipos_cli.owner_inbox import decision_id_for, item_id, read_ruling

    category = str(redeclaration_declaration()["authorization"]["inbox_category"])
    subject, anchor = redeclare_item_identity(instance, base=base, to=to)
    iid = item_id(root.name, category, subject, anchor)
    ruling = read_ruling(root, iid)
    return {"item_id": iid, "decision_id": decision_id_for(iid), "choice": str((ruling or {}).get("allowed_next_action") or "") or None,
            "ruling": ruling}


def _no_space(value: Any, what: str) -> str:
    text = str(value or "").strip()
    if any(ch.isspace() for ch in text):
        raise _refuse_redeclare("REDECLARE_INVALID", f"{what} 含空白(位置事件按空白分段): {text!r}")
    return text


def redeclaration_event_line(event: dict[str, Any], *, instance: str, project: str, new: dict[str, Any], old: dict[str, Any],
                             seat: bool, by: str, basis: str, ts: str | None = None) -> str:
    """redeclare 位置事件一行的唯一渲染(经 _trail_line): 结构字段 code_id / role 沿用最新位置事件; reason = host(治理席位 = 沿用事件工位机;
    子 agent = 新会话机)[mode] harness [harness_host harness_dir](治理席位)from_harness from_host from_dir authorized_by [workstation 沿用]。"""
    import socket

    parts = [f"host={(event.get('host') or socket.gethostname()) if seat else new['host']}"]
    if event.get("mode"):
        parts.append(f"mode={event['mode']}")
    parts.append(f"harness={new['kind']}")
    if seat:
        parts.append(f"harness_host={new['host']}")
        if new.get("dir"):
            parts.append(f"harness_dir={new['dir']}")
    parts += [f"from_harness={old.get('kind') or '-'}", f"from_host={old.get('host') or '-'}", f"from_dir={old.get('dir') or '-'}",
              f"authorized_by={basis}"]
    if event.get("dir"):
        parts.append(f"workstation={event['dir']}")
    return _trail_line(action=REDECLARE_ACTION, code_id=str(event["code_id"]), role=str(event["role"]), instance=instance,
                       project=project, by=by, reason=" ".join(parts), ts=ts)


def _redeclare_request_line(event: dict[str, Any], *, instance: str, project: str, new: dict[str, Any], by: str, why: list[str]) -> str:
    reason = (f"base={event['ts']} to_harness={new['kind']} to_host={new['host'] or '-'} to_dir={new.get('dir') or '-'} "
              f"why={','.join(why) or '-'}")
    return _trail_line(action=REDECLARE_REQUEST_ACTION, code_id=str(event["code_id"]), role=str(event["role"]), instance=instance,
                       project=project, by=by, reason=reason)


def redeclare_instance(governance_root: str | Path, instance: str, *, harness: str | None = None, host: str | None = None,
                       harness_dir: str | None = None, actor: str, dry_run: bool) -> dict[str, Any]:
    """AIPOS-F235 件①②: `lybra roles redeclare` 预演 / 确认的唯一实现(两阶段 verbs.schema two_phase_protocol.enrollment_log_writers)。

    适用: 本项目已接入的治理席位实例(位置事件工位 = 治理根; 实例槽在治理根 .lybra/role)与子 agent 执行身份(land mode=子 agent 模式);
    pi 工位 = 拒(REDECLARE_WORKSTATION: 迁目录走 relocate-workstation)。操作者 = 本实例或本项目已接入顾问(advisor_updates_cli.advisor_instances),
    否则 REDECLARE_ACTOR。新值校验: 治理席位经 distribution_sync.harness_role_record(唯一判定; AIPOS-F243: claude-code 与 codex 均可登记在他机, 声明 harness_host), 子 agent 须为顾问会话
    kind 且不给工作目录。无变化 = REDECLARE_NO_CHANGE。授权 = _authorization_basis: 第二档 → 确认 = 追加 redeclare 位置事件 + 更新实例槽;
    第一档 → Owner 裁决批准(_redeclare_ruling)= 同第二档生效(authorized_by=owner:<决策 ID>), 拒 = REDECLARE_OWNER_DENIED,
    未裁决 = 确认追加一行 redeclare_request(已有同一请求则不重复)进收件箱。返回 {state: apply|request|pending, written, ...}。"""
    import socket

    from tools.aipos_cli.distribution_sync import advisor_harness_kinds, harness_kind_declaration, harness_role_record

    decl = redeclaration_declaration()
    root = _workspace_root_path(governance_root)
    name = _no_space(instance, "--instance")
    by = _no_space(actor, "--actor")
    if not name or not by:
        raise _refuse_redeclare("REDECLARE_INVALID", "须给 --instance 与 --actor")
    given = {"kind": _no_space(harness, "--harness") or None, "host": _no_space(host, "--host") or None,
             "dir": _no_space(harness_dir, "--dir") or None}
    if not any(given.values()):
        raise _refuse_redeclare("REDECLARE_INVALID", "至少给 --harness / --host / --dir 之一")
    view = instance_enrollment(root, name)
    ev = location_event(root, name)
    if not view["enrolled"] or not ev["found"] or not ev["line_fields"]:
        raise _refuse_redeclare("REDECLARE_NOT_ENROLLED", f"实例 {name} 在 {enrollment_trail_path(root)} 无可沿用 code_id / role 的未作废位置事件"
                                f"({view['reason'] or '无 land 事件'}); {enrolled_instances_line(root)}")
    subagent = bool(view["governance_root_mode"])
    seat = not subagent and bool(ev["dir"]) and Path(str(ev["dir"])).expanduser().resolve() == root
    if not subagent and not seat:
        raise _refuse_redeclare("REDECLARE_WORKSTATION", f"实例 {name} 是工位实例(位置 {ev['host'] or '-'}:{ev['dir'] or '-'})")
    if by != name:
        from tools.aipos_cli.advisor_updates_cli import advisor_instances

        advisors = advisor_instances(root)
        if by not in advisors:
            raise _refuse_redeclare("REDECLARE_ACTOR", f"操作者 {by} 既不是实例 {name} 本身, 也不是本项目已接入顾问({', '.join(advisors) or '无'})")
    current = registered_harness(root, name)
    old = {k: current[k] for k in _REDECLARE_FIELDS}
    local = socket.gethostname()
    kind = given["kind"] or old["kind"]
    kinds = advisor_harness_kinds()
    if kind not in kinds:
        raise _refuse_redeclare("REDECLARE_INVALID", f"harness {kind!r} 不是顾问会话 kind({'|'.join(kinds)}); 现登记 {registration_label(current) or '无'}")
    same_session = given["kind"] in (None, old["kind"])
    # 会话机: 显式 > 新 kind 声明不登记会话机(harness_host=forbidden)= 本机 > 同 kind 沿用现登记 > 本机
    # (AIPOS-F243: 顾问会话 kind 现均 harness_host=optional; 换 kind = 换会话, 不沿用旧会话机与目录, 同机切换也显式给 --host --dir)
    pinned_local = harness_kind_declaration(kind)["harness_host"] == "forbidden"
    new_host = given["host"] or (local if pinned_local else None) or (old["host"] if same_session else None) or local
    slot_record: dict[str, Any] | None = None
    if subagent:
        if given["dir"]:
            raise _refuse_redeclare("REDECLARE_INVALID", f"实例 {name} 是子 agent 执行身份(接入模式 {view['mode']}), 无工作目录, 不给 --dir")
        new = {"kind": kind, "host": new_host, "dir": None}
    else:
        if _role_slot(_lybra_dir(root), name) is None:
            raise _refuse_redeclare("REDECLARE_NOT_ENROLLED", f"治理根 {_role_file_path(root)} 无实例 {name} 的记录(实例槽)")
        # 工作目录: 显式 > 同 kind 同会话机沿用现登记 > 无(须给的 kind 由 harness_role_record 拒并给出口)
        new_dir = given["dir"] or (old["dir"] if same_session and new_host == old["host"] else None)
        remote = new_host if new_host != local else None
        if remote and harness_kind_declaration(kind)["harness_host"] == "forbidden":
            raise _refuse_redeclare("REDECLARE_INVALID", f"harness={kind} 声明 harness_host=forbidden(不登记会话机), 不能登记到主机 {new_host}"
                                    f"(本机 {local})")
        try:
            slot_record = harness_role_record(kind, new_dir, remote)
        except ValueError as exc:
            raise _refuse_redeclare("REDECLARE_INVALID", str(exc)) from exc
        new = {"kind": kind, "host": new_host, "dir": (slot_record or {}).get("dir")}
    diffs = [{"field": k, "old": old[k], "new": new[k]} for k in _REDECLARE_FIELDS if old[k] != new[k]]
    if not diffs:
        raise _refuse_redeclare("REDECLARE_NO_CHANGE", f"实例 {name} 现登记已是 {registration_label(current)}")
    project = _project_name(root)
    auth = _authorization_basis(root, new, [d["field"] for d in diffs])
    out: dict[str, Any] = {"ok": True, "operation": "roles_redeclare", "dry_run": dry_run, "governance_root": str(root), "project": project,
                           "instance": name, "mode": "subagent" if subagent else "seat", "actor": by, "current": current, "old": old,
                           "new": new, "diffs": diffs, "tier": auth["tier"], "basis": auth["basis"], "authorization": auth["authorization"],
                           "why": auth["why"], "item_id": None, "decision_id": None, "owner_choice": None, "state": "apply",
                           "event_line": None, "request_line": None, "slot_file": str(_role_file_path(root)) if seat else None,
                           "slot_harness": slot_record, "log": str(enrollment_trail_path(root)), "written": [], "written_files": []}
    basis = auth["basis"]
    if auth["tier"] == 1:
        ruling = _redeclare_ruling(root, name, str(ev["ts"]), new)
        out.update(item_id=ruling["item_id"], decision_id=ruling["decision_id"], owner_choice=ruling["choice"])
        approve, deny = decl["authorization"]["approve_option"], decl["authorization"]["deny_option"]
        if ruling["choice"] == deny:
            raise _refuse_redeclare("REDECLARE_OWNER_DENIED", f"Owner 已裁决不批准(事项 {ruling['item_id']}, 裁决 {ruling['decision_id']})")
        if ruling["choice"] == approve:
            basis = f"owner:{ruling['decision_id']}"
        else:
            pending = any(r["instance"] == name and r["anchor"] == redeclare_item_identity(name, base=str(ev["ts"]), to=new)[1]
                          for r in pending_redeclare_requests(root))
            out["state"] = "pending" if pending else "request"
            out["request_line"] = _redeclare_request_line(ev, instance=name, project=project, new=new, by=by, why=auth["why"]).rstrip("\n")
            if not dry_run and not pending:
                _write_trail_line(enrollment_trail_path(root), out["request_line"] + "\n")
                out["written"] = [str(enrollment_trail_path(root))]
            return out
    out["basis"] = basis
    out["event_line"] = redeclaration_event_line(ev, instance=name, project=project, new=new, old=old, seat=seat, by=by,
                                                 basis=str(basis)).rstrip("\n")
    if dry_run:
        return out
    trail = enrollment_trail_path(root)
    _write_trail_line(trail, out["event_line"] + "\n")
    out["written"] = [str(trail)]
    if seat:
        from tools.aipos_cli.enroll_client import write_role_file

        role = str((_role_slot(_lybra_dir(root), name) or {}).get("role") or ev["role"])
        write_role_file(_lybra_dir(root), role, name, None, harness=slot_record, slot_by_instance=True, stamp_key="redeclared_at",
                        keep_top=True)
        out["written_files"] = [str(_role_file_path(root))]
    after = registered_harness(root, name)
    out["after"] = after
    out["ok"] = all(after[k] == new[k] for k in _REDECLARE_FIELDS)
    return out


def render_redeclaration(result: dict[str, Any]) -> list[str]:
    """改登记预演 / 确认文本(声明 texts; CLI 唯一渲染)。"""
    t = redeclaration_declaration()["texts"]
    will = "" if not result["dry_run"] else "将"
    lines = [t["target_line"].format(instance=result["instance"], mode=t[f"mode_{result['mode']}"], governance_root=result["governance_root"]),
             t["current_line"].format(current=registration_label(result["current"]) or "-")]
    lines += [t["diff_line"].format(field=d["field"], old=d["old"] or "-", new=d["new"] or "-") for d in result["diffs"]] or [t["diff_none"]]
    if result["tier"] == 2:
        lines.append(t["tier2_dir_only"] if result["basis"] == "dir_only" else t["tier2_authorized"].format(authorization=result["basis"]))
    elif result["state"] == "apply":
        lines.append(t["owner_approved"].format(decision_id=result["decision_id"], item_id=result["item_id"]))
    else:
        lines.append(t["tier1_request"].format(why=", ".join(result["why"]) or "-", item_id=result["item_id"]))
        if result["state"] == "pending":
            lines.append(t["owner_pending"].format(item_id=result["item_id"]))
        else:
            lines.append(t["request_line"].format(will=will, line=result["request_line"]))
    if result["state"] == "apply":
        lines.append(t["event_line"].format(will=will, line=result["event_line"]))
        lines.append(t["slot_line"].format(will=will, path=result["slot_file"], instance=result["instance"],
                                           harness=json.dumps(result["slot_harness"], ensure_ascii=False))
                     if result["slot_file"] else t["slot_none"])
    lines.append(t["invariants"])
    return lines


def registration_drift(governance_root: str | Path) -> list[dict[str, Any]]:
    """AIPOS-F235 件①: 实例槽与日志登记不一致(state lint 报出; 以日志为准)。对象 = 本项目已接入、最新位置事件带 harness= 且治理根 .lybra/role
    有其记录(带 harness)的实例; 比 kind / dir / host(槽内有 host 时)。返回 [{instance, event, slot, fields}]。只读。"""
    root = _workspace_root_path(governance_root)
    out: list[dict[str, Any]] = []
    for view in enrolled_instances(root):
        reg = registered_harness(root, view["instance"])
        slot = reg.get("slot")
        if reg["source"] != "event" or not slot:
            continue
        slot_view = {"kind": str(slot.get("kind") or "").strip() or None, "dir": str(slot.get("dir") or "").strip() or None,
                     "host": str(slot.get("host") or "").strip() or None}
        fields = [k for k in ("kind", "dir") if slot_view[k] != reg[k]] + (["host"] if slot_view["host"] and slot_view["host"] != reg["host"] else [])
        if fields:
            out.append({"instance": view["instance"], "event": reg["source_label"], "slot": slot_view, "registered": {k: reg[k] for k in _REDECLARE_FIELDS},
                        "slot_file": reg["slot_file"], "fields": fields})
    return out


def enrollment_whereabouts(workspace_root: str | Path, instance: str) -> dict[str, Any]:
    """AIPOS-F107 件② 只读诊断(`lybra roles enroll-where --instance <实例>`): 该实例接入事件**实际所在** log 与**应在** log。

    应在 = enrollment_owner_root(实例名项目段; 唯一解析口)的 enrollment_log; 实际 = 扫 home 下各已建项目 + 签发门工作区
    的 enrollment_log 中该实例的事件。verdict:
      in_place   应在 log 有该实例 land 事件(workstation_location 可定位)
      misplaced  应在 log 无 land, 别的 log 有(存量写在签发方 log; 按结果重接入, 新事件落所属项目)
      not_landed 各 log 均无该实例 land 事件
      owner_unresolved 解析不到所属项目(reason 说明)
    只读: 不写任何日志/记录。"""
    from tools.aipos_cli.workspace_config import _project_candidates, resolve_home_root

    issuer = _workspace_root_path(workspace_root)
    owner = enrollment_owner_root(issuer, instance=instance)
    home = resolve_home_root()
    roots = [home / name for name in _project_candidates(home)]
    if issuer not in roots:
        roots.append(issuer)
    found: list[dict[str, Any]] = []
    for root in roots:
        trail = enrollment_trail_path(root)
        if not trail.is_file():
            continue
        # AIPOS-F121 件②: 事件按作废状态分拣(_instance_events 唯一判定); actions/code_ids 只计未作废事件, 作废另列
        events = _instance_events(trail, instance)
        actions: dict[str, int] = {}
        code_ids: list[str] = []
        for _lineno, line in events["live"]:
            match = _EVENT_LINE_RE.match(line)
            actions[match.group("action")] = actions.get(match.group("action"), 0) + 1
            if match.group("code_id") not in code_ids:
                code_ids.append(match.group("code_id"))
        if not actions and not events["voided"]:
            continue
        land = _latest_land(trail, instance)
        dir_match = _LAND_DIR_RE.search(land.group("reason")) if land else None
        found.append({"log": str(trail), "project_root": str(root), "actions": actions, "code_ids": code_ids,
                      "live_lines": _line_ranges([n for n, _ in events["live"]]),
                      "voided_events": len(events["voided"]), "void_lines": [n for n, _ in events["voids"]],
                      "latest_land_at": land.group("ts") if land else None,
                      "latest_land_workstation": dir_match.group("dir").strip() if dir_match else None})
    expected = str(enrollment_trail_path(owner["root"])) if owner["root"] else None
    landed_logs = [f["log"] for f in found if f["latest_land_at"]]
    if owner["root"] is None:
        verdict = "owner_unresolved"
    elif expected in landed_logs:
        verdict = "in_place"
    elif landed_logs:
        verdict = "misplaced"
    else:
        verdict = "not_landed"
    location = workstation_location(owner["root"], instance) if owner["root"] else None
    return {"instance": instance, "issuer_workspace": str(issuer), "home": str(home),
            "owner_project": owner["project"], "owner_source": owner["source"], "owner_reason": owner["reason"],
            "expected_log": expected, "found": found, "verdict": verdict, "workstation_location": location}


# ---------------------------------------------------------------------------
# AIPOS-F143 件③④: 执行者接入模式声明(roles.schema executor_modes) + 本项目已接入实例判定(唯一实现;
# 发卡核身份 draft_writer.publish_identity_refusals、执行方式声明写前核验 workspace_config.set_project_execution、
# 起草缺省 harness 推导同读此处, 禁另建身份表)。
# ---------------------------------------------------------------------------

_LAND_MODE_RE = re.compile(r"(?:^|\s)mode=(?P<mode>\S+)")
_LAND_HARNESS_RE = re.compile(r"(?:^|\s)harness=(?P<harness>\S+)")
_LAND_ROLE_RE = re.compile(r"\srole=(?P<role>\S+)")  # AIPOS-F146: land 行结构字段 role=(_trail_line 渲染, reason 之前)


def executor_mode_declaration() -> dict[str, Any]:
    """roles.schema executor_modes(执行者接入模式: 缺省 / 各模式落点 / 允许角色类 / 拒因)唯一读取口。缺 / 形不合 = SchemaLoadError。"""
    from tools.schema_loader import SchemaLoadError, code_repo_schema_root, load_schema

    decl = load_schema("roles", code_repo_schema_root()).get("executor_modes")
    values = decl.get("values") if isinstance(decl, dict) else None
    codes = decl.get("reject_codes") if isinstance(decl, dict) else None
    landings = {"workstation", "governance_root"}
    if (not isinstance(values, dict) or decl.get("default") not in values
            or not all(isinstance(v, dict) and v.get("credential_landing") in landings
                       and isinstance(v.get("allowed_role_classes"), list) and v["allowed_role_classes"] for v in values.values())
            or not isinstance(codes, dict)
            or not all(isinstance(codes.get(k), str) and codes[k] for k in (
                "EXECUTOR_MODE_UNKNOWN", "EXECUTOR_MODE_ROLE_CLASS", "EXECUTOR_MODE_TARGET",
                "EXECUTOR_MODE_INSTANCE_REQUIRED", "EXECUTOR_MODE_HARNESS"))):
        raise SchemaLoadError("roles.schema.json executor_modes(default ∈ values / values.<模式>{credential_landing, allowed_role_classes} / "
                              "reject_codes)未声明齐")
    return decl


def governance_root_executor_modes() -> tuple[str, ...]:
    """凭据落治理根的执行者接入模式(声明 credential_landing=governance_root; 向导 / enroll / 起草同读)。"""
    return tuple(name for name, spec in executor_mode_declaration()["values"].items()
                 if spec["credential_landing"] == "governance_root")


def subagent_land_detail(*, host: str, harness: str, mode: str, files: list[str]) -> str:
    """子 agent 模式 land 事件 reason 载荷(enroll_client 写; instance_enrollment 读): 无 workstation= 段 = 无工位位置。"""
    return f"host={host} mode={mode} harness={harness} files={files}"


def _governance_connection(governance_root: Path) -> Path:
    """治理根连接文件路径(既有原语 service_mode.connection_path, 不另拼 .lybra 路径)。"""
    from tools.aipos_cli.service_mode import connection_path

    return connection_path(Path(governance_root))


def _connection_bound(governance_root: Path, instance: str) -> bool:
    """治理根连接文件 .lybra/connection.json 是否有该实例的可用条目(挑选判据唯一实现 token_resolver.select_token_entry, 只按实例)。
    文件不在 = False; 读不出 / 形坏 = ValueError 上抛(fail-closed, 不当作「未绑定」)。只看字段, 永不取出 token 值。"""
    from tools.aipos_cli.token_resolver import TokenResolutionError, load_connection_tokens, select_token_entry

    conn = _governance_connection(Path(governance_root))
    if not conn.is_file():
        return False
    tokens = load_connection_tokens(conn)
    try:
        select_token_entry(tokens, agent_instance=instance, source=str(conn))
    except TokenResolutionError:
        return False
    return True


def instance_enrollment(governance_root: str | Path, instance: str) -> dict[str, Any]:
    """AIPOS-F143 件③: 实例是否为本项目已接入实例——唯一判定。

    已接入 = 本项目 enrollment_log 有该实例未作废的 land 事件(_latest_land, 与 workstation_location 同一读取口)
    或 治理根连接文件有该实例可用凭据条目(_connection_bound → select_token_entry; 子 agent 执行者既有形)。
    返回 {instance, enrolled, via: [land|connection], role, mode, harness, host, governance_root_mode,
    workstation: workstation_location 结果, reason}:
    mode / harness / host 取自最新 land 事件 reason(子 agent 模式 = mode=subagent harness=<顾问会话 kind>; pi 工位 = 无 mode, 有 workstation=)。
    AIPOS-F146: role = 最新 land 事件行的 role= 结构字段(门签码时记下的角色; 子 agent 执行者不写 .lybra/role, 其身份源即此处);
    governance_root_mode = land 事件 mode 是凭据落治理根的执行者接入模式(roles.schema executor_modes credential_landing=governance_root,
    唯一读取口 governance_root_executor_modes; 无 mode = False, 不读声明)——无工位的已接入实例(子 agent 执行者)判定只此一处。
    连接文件读不出 = ValueError 上抛(fail-closed)。只读。"""
    root = _workspace_root_path(governance_root)
    name = str(instance or "").strip()
    out: dict[str, Any] = {"instance": name, "enrolled": False, "via": [], "role": None, "mode": None, "harness": None, "host": None,
                           "governance_root_mode": False, "workstation": {"found": False, "reason": "未接入"}, "reason": ""}
    if not name:
        out["reason"] = "实例名为空"
        return out
    trail = enrollment_trail_path(root)
    land = _latest_land(trail, name) if trail.is_file() else None
    if land is not None:
        out["via"].append("land")
        reason = land.group("reason")
        for key, rx in (("mode", _LAND_MODE_RE), ("harness", _LAND_HARNESS_RE), ("host", _LAND_HOST_RE)):
            match = rx.search(reason)
            out[key] = match.group(key) if match else None
        role_match = _LAND_ROLE_RE.search(land.string[:land.start("reason")])
        out["role"] = role_match.group("role") if role_match else None
        out["governance_root_mode"] = bool(out["mode"]) and out["mode"] in governance_root_executor_modes()
    if _connection_bound(root, name):
        out["via"].append("connection")
    out["enrolled"] = bool(out["via"])
    # AIPOS-F146: 工位位置恒为 workstation_location 结果(未接入时其拒因即「无 land 事件」原文, 等待提示 / 诊断同读此键)
    out["workstation"] = workstation_location(root, name)
    if not out["enrolled"]:
        out["reason"] = (f"{trail.name} 无 {name} 的未作废 land 事件, 治理根 .lybra/connection.json 亦无其可用凭据条目"
                         "(该实例未在本项目接入)")
    return out


def enrolled_instances(governance_root: str | Path) -> list[dict[str, Any]]:
    """本项目全部已接入实例(按实例名排序; 每项 = instance_enrollment 结果)。候选 = enrollment_log 中有 land 事件的实例
    ∪ 治理根连接文件中带 agent_instance 的条目; 逐个经 instance_enrollment 判定(作废 / retired 的不列)。只读。"""
    from tools.aipos_cli.token_resolver import TOKEN_ENTRY_FIELDS, load_connection_tokens

    root = _workspace_root_path(governance_root)
    names: set[str] = set()
    trail = enrollment_trail_path(root)
    if trail.is_file():
        for line in trail.read_text(encoding="utf-8").splitlines():
            event = _EVENT_LINE_RE.match(line.strip())  # 候选只取事件行实例名; land 是否在册由 instance_enrollment(_latest_land)判
            if event and event.group("action") == "land":
                names.add(event.group("instance"))
    conn = _governance_connection(root)
    if conn.is_file():
        field = TOKEN_ENTRY_FIELDS["agent_instance"]
        names |= {str(e.get(field)).strip() for e in load_connection_tokens(conn) if isinstance(e, dict) and str(e.get(field) or "").strip()}
    views = [instance_enrollment(root, n) for n in sorted(names)]
    return [v for v in views if v["enrolled"]]


def enrolled_instances_line(governance_root: str | Path) -> str:
    """拒因末尾「本项目已接入实例: …」一行的唯一渲染(发卡核身份与 set-execution 共用): 实例[来源+工位+模式]。
    连接文件读不出 = 如实写入该行(不吞, 不抛断拒因)。"""
    try:
        views = enrolled_instances(governance_root)
    except ValueError as exc:
        return f"本项目已接入实例: (列不出: {exc})"
    listed = ", ".join(f"{v['instance']}[{'+'.join(v['via'])}{' 工位' if v['workstation'].get('found') else ''}"
                       f"{' ' + str(v['mode']) if v['mode'] else ''}]" for v in views)
    return f"本项目已接入实例: {listed or '(无)'}"


def has_enrollment_registry(governance_root: str | Path) -> bool:
    """本项目有无接入登记 = 本项目 enrollment_log 存在(接入事件的唯一登记处; 经产品接入的项目, 向导第 5 步顾问 enroll 即由门写入
    land 事件)。无 = 无从判「已接入」(裸治理根 / 夹具 / 未经产品接入), 发卡核身份不适用(行为不变)。只看登记处是否存在,
    「某实例是否已接入」仍由 instance_enrollment 判(land 事件 / 治理根连接文件绑定)。"""
    return enrollment_trail_path(_workspace_root_path(governance_root)).is_file()


# AIPOS-F143 件②: 执行方式声明(project.json execution)唯一写入口——读取口 workspace_config.project_execution; 写前核各实例为本项目
# 已接入实例(instance_enrollment, 与发卡核身份同一判定)。写入经 workspace_config.update_project_json 唯一写路径。

def execution_instance_refusals(project_root: str | Path, execution: dict[str, Any]) -> list[str]:
    """AIPOS-F143 件②: 执行方式声明里各实例的接入核验(判定唯一实现 enrollment.instance_enrollment, 与发卡核身份同一判定): 未接入 / 审计者与 pi 执行者
    无工位位置 = 拒因(带已接入实例清单)。空列表 = 全部通过。"""
    refusals: list[str] = []
    for key, needs_workstation in (("subagent_executor", False), ("auditor", True), ("pi_executor", True)):
        name = execution.get(key)
        if not name:
            continue
        try:
            view = instance_enrollment(project_root, name)
        except ValueError as exc:  # 治理根连接文件读不出 = 接入状态不可得(fail-closed, 记为拒因)
            refusals.append(f"{key}={name}: 接入状态不可得: {exc}")
            continue
        if not view["enrolled"]:
            refusals.append(f"{key}={name}: {view['reason']}")
        elif needs_workstation and not view["workstation"].get("found"):
            refusals.append(f"{key}={name}: 已接入但无工位位置({view['workstation'].get('reason')}); 审计者 / pi 执行者须为 pi 工位")
    if refusals:
        refusals.append(enrolled_instances_line(project_root))
    return refusals


def set_project_execution(
    project_root: str | Path,
    *,
    subagent_executor: str,
    auditor: str,
    pi_executor: str | None = None,
    pi_allowed_task_modes: list[str] | tuple[str, ...] = (),
    default_mode: str | None = None,
    subagent_max_wait: float | None = None,
    dry_run: bool = True,
) -> dict[str, Any]:
    """AIPOS-F143 件②: `lybra project set-execution` 的唯一实现——整段写 project.json execution(未给的可选键 = 声明缺省; 其余键原样保留)。
    校验 = 唯一读取口 project_execution(写前以将写内容预检 + 写后复核, 经 update_project_json verify); 写前另核各实例为本项目已接入实例
    (execution_instance_refusals; 不过 = EXECUTION_INSTANCE_NOT_ENROLLED 零写入)。dry_run(缺省)= 只预演 diff 零写入。
    返回 {project_json, dry_run, changed, written, diff, execution}。"""
    from tools.aipos_cli.workspace_config import (
        ExecutionDeclarationError,
        execution_declaration,
        project_execution,
        project_json_path,
        update_project_json,
    )

    decl = execution_declaration()
    section: dict[str, Any] = {
        "default_mode": default_mode or decl["schema"]["default_mode"].get("default"),
        "subagent_executor": str(subagent_executor or "").strip(),
        "pi_allowed_task_modes": [str(m).strip() for m in pi_allowed_task_modes],
        "pi_executor": (str(pi_executor).strip() or None) if pi_executor is not None else None,
        "auditor": str(auditor or "").strip(),
    }
    staged: dict[str, Any] = {}

    def _mutate(data: dict[str, Any]) -> None:
        previous = data.get("execution")
        # AIPOS-F169: pi_models 由同族 set-model 管, 整段重声明执行方式时原样保留(不随 set-execution 清掉 Owner 选定的模型)
        if isinstance(previous, dict) and previous.get("pi_models") is not None:
            section["pi_models"] = previous["pi_models"]
        # AIPOS-F185 件①: 子 agent 等交回上限 —— 给了 = 写; 未给 = 原值保留(同 pi_models, 不随整段重声明清掉)
        if subagent_max_wait is not None:
            section["subagent_max_wait"] = subagent_max_wait
        elif isinstance(previous, dict) and previous.get("subagent_max_wait") is not None:
            section["subagent_max_wait"] = previous["subagent_max_wait"]
        data["execution"] = section
        staged["view"] = project_execution(project_root, project=data)  # 形不合 = 抛, 零写入

    probe: dict[str, Any] = {"execution": {**section, **({"subagent_max_wait": subagent_max_wait} if subagent_max_wait is not None else {})}}
    view = project_execution(project_root, project=probe)
    refusals = execution_instance_refusals(project_root, view or {})
    if refusals:
        raise ExecutionDeclarationError("EXECUTION_INSTANCE_NOT_ENROLLED", refusals)
    outcome = update_project_json(project_root, _mutate, verify=project_execution, dry_run=dry_run)
    return {**outcome, "project_json": str(project_json_path(project_root)), "execution": staged.get("view") or view}


def _pi_model_affected(project_root: str | Path, execution: dict[str, Any], roles: list[str],
                       before: dict[str, Any], after: dict[str, Any]) -> list[dict[str, Any]]:
    """AIPOS-F169: set-model 影响的工位(按角色: auditor = execution.auditor, executor = execution.pi_executor)与下次拉起的模型变化。
    位置只经 workstation_location(拉起 / check-workstation 同一读取口, 只读本项目 enrollment_log 位置事件); 读不出 = 如实写入条目(不吞)。"""
    from tools.aipos_cli.workspace_config import pi_model_for_role

    out: list[dict[str, Any]] = []
    for role in roles:
        instance = execution.get("auditor") if role == "auditor" else execution.get("pi_executor")
        entry: dict[str, Any] = {"role": role, "instance": instance,
                                 "before": pi_model_for_role(project_root, role, project=before),
                                 "after": pi_model_for_role(project_root, role, project=after)}
        if not instance:
            entry["note"] = "未声明 pi 执行工位(execution.pi_executor): 执行类卡由子 agent 做, 本声明暂不影响任何拉起"
        else:
            try:
                loc = workstation_location(project_root, instance)  # 只读位置事件(不读连接文件)
            except (ValueError, OSError) as exc:
                loc = {"found": False, "reason": f"工位位置读不出: {exc}"}
            entry["workstation"] = {k: loc.get(k) for k in ("found", "host", "dir", "transport", "reason")}
            if loc.get("transport") == "remote":
                entry["note"] = "跨机工位: 可用性按本机 pi 清单核, 工位所在机的 pi 配置未核(在该机 pi --list-models 核)"
        out.append(entry)
    return out


def set_project_pi_model(
    project_root: str | Path,
    *,
    model: str,
    owner_ref: str,
    role: str | None = None,
    dry_run: bool = True,
) -> dict[str, Any]:
    """AIPOS-F169: `lybra project set-model` 的唯一实现——只写 project.json execution.pi_models(role 缺 = default, 给 = by_role.<角色>;
    owner_ref = 本次切换的 Owner 指示摘要或决定记录), execution 其余键原样保留。写前: 须已声明 execution 段(PI_MODEL_EXECUTION_UNDECLARED);
    模型须在本机 pi 已配置模型中(harness_launch.harness_model_catalog 唯一实现; 不在 = PI_MODEL_NOT_CONFIGURED 并列相近可用模型,
    核不了 = PI_MODEL_CATALOG_UNAVAILABLE)。校验 = project_execution(写前预检 + 写后复核, 经 update_project_json verify)。
    dry_run(缺省)= 只预演 diff 与影响工位, 零写入。返回 {project_json, dry_run, changed, written, diff, pi_models, model, role,
    target_key, catalog, affected, effect_note}。"""
    import copy

    from tools.aipos_cli.harness_launch import harness_model_catalog, near_models
    from tools.schema_loader import SchemaLoadError
    from tools.aipos_cli.workspace_config import (
        ExecutionDeclarationError,
        pi_model_id_problem,
        pi_models_declaration,
        project_execution,
        project_json_path,
        project_pi_models,
        read_project_json,
        update_project_json,
    )

    decl = pi_models_declaration()
    model = str(model or "").strip()
    if not str(owner_ref or "").strip():
        raise ExecutionDeclarationError("PI_MODEL_OWNER_REF_REQUIRED", ["--owner-ref 须给 Owner 指示摘要或决定记录(非空)"])
    if role is not None and role not in decl["roles"]:
        raise ExecutionDeclarationError("EXECUTION_DECLARATION_INVALID", [f"--role {role!r} 不在 {decl['roles']}"])
    problem = pi_model_id_problem(model)
    if problem:
        raise ExecutionDeclarationError("EXECUTION_DECLARATION_INVALID", [problem])
    before = read_project_json(project_root)
    execution = project_execution(project_root, project=before)  # 现有段形不合 = 抛(先修声明)
    if execution is None:
        raise ExecutionDeclarationError("PI_MODEL_EXECUTION_UNDECLARED", [
            f"{project_json_path(project_root)} 无 execution 段; 先 lybra project set-execution 声明执行方式"])
    try:
        catalog = harness_model_catalog("pi")
    except (ValueError, SchemaLoadError) as exc:  # 清单声明不可用 = 核不了(fail-closed)
        catalog = {"ok": False, "detail": f"模型清单声明不可用: {exc}"}
    if not catalog["ok"]:
        raise ExecutionDeclarationError("PI_MODEL_CATALOG_UNAVAILABLE", [catalog["detail"]])
    if model not in catalog["models"]:
        near = near_models(model, catalog["models"], int(decl["near_match_limit"]))
        raise ExecutionDeclarationError("PI_MODEL_NOT_CONFIGURED", [
            f"{model} 不在本机 pi 已配置模型中({catalog['detail']})",
            f"相近可用模型: {', '.join(near)}" if near else f"无相近模型; 已配置: {', '.join(catalog['models'])}"])
    target_key = f"by_role.{role}" if role else "default"
    staged: dict[str, Any] = {}

    def _mutate(data: dict[str, Any]) -> None:
        section = data["execution"]
        current = section.get("pi_models")
        pm = dict(current) if isinstance(current, dict) else {}
        by_role = pm.get("by_role") or {}
        by_role = dict(by_role) if isinstance(by_role, dict) else by_role  # 形不合 = 原样留给 verify 报 EXECUTION_DECLARATION_INVALID
        if role and isinstance(by_role, dict):
            by_role[role] = model
        elif not role:
            pm["default"] = model
        pm["by_role"] = by_role
        pm["owner_ref"] = str(owner_ref).strip()
        section["pi_models"] = pm
        staged["after"] = copy.deepcopy(data)

    outcome = update_project_json(project_root, _mutate, verify=project_execution, dry_run=dry_run)
    after = staged["after"]
    roles = [role] if role else list(decl["roles"])
    return {**outcome, "project_json": str(project_json_path(project_root)), "pi_models": project_pi_models(project_root, project=after),
            "model": model, "role": role, "target_key": target_key, "catalog": {k: catalog[k] for k in ("command", "detail")},
            "affected": _pi_model_affected(project_root, execution, roles, before, after), "effect_note": str(decl["effect_note"])}


def _append_enrollment_trail(
    workspace_root: Path,
    *,
    action: str,
    code_id: str,
    role: str,
    instance: str | None,
    by: str,
    reason: str,
    governance_root: str | None = None,
    projects: Any = None,
) -> Path:
    """Append-only 审计日志。

    AIPOS-F107 件②: 写入该码/凭据/实例**所属项目**治理根的 enrollment_log(enrollment_owner_root 唯一解析口), 不写签发方
    治理根; 签发方不另记指针(签发留痕 = 签发门注册表 .lybra/enrollments.json 的码记录 + 所属项目 log 的 create 行 by=)。
    无所属项目(未绑项目的门级凭据 / 实例名无可解析项目段)→ 落签发门工作区自身 log, 行内 project=(未归属) 出声。
    每行带 project=<所属项目>。"""
    owner = enrollment_owner_root(workspace_root, governance_root=governance_root, projects=projects, instance=instance)
    trail = enrollment_trail_path(owner["root"] or _workspace_root_path(workspace_root))
    line = _trail_line(action=action, code_id=code_id, role=role, instance=instance,
                       project=owner["project"] if owner["root"] else None, by=by, reason=reason)
    _write_trail_line(trail, line)
    return trail


def _trail_line(*, action: str, code_id: str, role: str, instance: str | None, project: str | None, by: str,
                reason: str, extra: str = "", ts: str | None = None) -> str:
    """接入日志一行的唯一渲染(create/use/land/revoke/void 共用)。extra = 动作专属结构字段(void 的 voids_lines/voids_code_ids),
    置于 project 与 by 之间(reason 是行尾自由文本, 结构字段不得在其后)。"""
    inst_str = f"instance={instance}" if instance else "instance=(any)"
    project_str = f"project={project}" if project else "project=(未归属)"
    extra_str = f"  {extra}" if extra else ""
    return (f"- {ts or iso_z()}  {action}  code_id={code_id}  role={role}  {inst_str}  {project_str}{extra_str}  "
            f"by={by}  reason={reason or '(none)'}\n")


def _write_trail_line(trail: Path, line: str) -> None:
    """接入日志唯一写口: 只追加; 首建带声明 frontmatter(AIPOS-F94 N6, 过治理仓提交门 B②)。
    AIPOS-F140 件③: 委托产品治理文档追加唯一写口 governance_add.append_governance_doc_line, 头 = governance_doc_frontmatter。"""
    from tools.aipos_cli.governance_add import append_governance_doc_line

    append_governance_doc_line(trail, line, title="Enrollment Codes Log (append-only)")


def void_instance_events(
    workspace_root: str | Path,
    instance: str,
    *,
    by: str,
    reason: str,
    dry_run: bool,
    log: str | Path | None = None,
    project: str | None = None,
    code_ids: list[str] | tuple[str, ...] = (),
    before: str | None = None,
) -> dict[str, Any]:
    """AIPOS-F121 件②(`lybra roles enroll-void`): 作废某实例的接入事件——向事件所在 enrollment_log 追加 void 行, 不删不改任何行。

    定位 = enrollment_whereabouts 同一扫描(home 下各已建项目 + 签发门工作区; 事件写在哪个项目的 log 就在哪个 log 作废,
    存量写在签发方 log 的事件亦然)。
    AIPOS-F153 件①(gap #108): 作用域必须显式——恰给 log(日志路径)或 project(项目名)之一, 只在该日志作废; 缺省(两者都不给)
    = 拒(原「按实例跨所有日志一刀切」会连同所属项目新 land 一并作废), 拒因列出含该实例未作废事件的各日志作出口。
    可再限定 code_ids(只作废这些 code_id 的事件; 给了而范围内无其事件 = 拒)与 before(只作废时间戳早于该 ISO 时刻的事件)。
    范围内命中的事件追加一行:
      `- <ts>  void  code_id=-  role=<被作废事件角色>  instance=<实例>  project=<该 log 项目>  voids_lines=<行号区间>
       voids_code_ids=<code_id,...>  by=<操作者>  reason=<理由>`
    读取口(_instance_events: workstation_location / enroll-where)忽略 voids_lines 区间内该实例的事件; 作废之后的重接入不受影响。
    拒(ValueError, 不写): 实例/操作者/理由缺或形坏; 作用域缺 / 两者都给 / 不在扫描范围; 未知实例(各 log 均无该实例事件);
    --before 形坏; 范围内无未作废事件。dry_run=True: 只返回将追加的行与将作废的各行(行号 + 动作 + code_id + 时刻), 零写入。"""
    from tools.aipos_cli.autonomy_policy import _parse_iso
    from tools.aipos_cli.workspace_config import read_project_json

    instance = str(instance or "").strip()
    by = str(by or "").strip()
    reason = str(reason or "").strip()
    log_arg = str(log or "").strip()
    project_arg = str(project or "").strip()
    wanted_codes = [str(c).strip() for c in code_ids if str(c).strip()]
    before_arg = str(before or "").strip()
    if not instance or any(ch.isspace() for ch in instance):
        raise ValueError(f"--instance 缺或含空白: {instance!r}")
    if not by or any(ch.isspace() for ch in by):
        raise ValueError(f"--actor 缺或含空白(作废行须带操作者): {by!r}")
    if not reason or "\n" in reason or "\r" in reason:
        raise ValueError("--reason 缺或含换行(作废行须带单行理由)")
    if log_arg and project_arg:
        raise ValueError("--log 与 --project 只能给一个(作废作用域 = 恰一个日志)")
    cutoff = None
    if before_arg:
        cutoff = _parse_iso(before_arg)
        if cutoff is None:
            raise ValueError(f"--before 不是 ISO 时刻: {before_arg!r}(如 2026-10-08T16:00:00Z)")
    report = enrollment_whereabouts(workspace_root, instance)
    if not report["found"]:
        raise ValueError(f"未知实例 {instance}: home {report['home']} 下各项目与签发门工作区的 enrollment_log 均无该实例事件, 拒绝作废")
    candidates: list[dict[str, Any]] = []
    for item in report["found"]:
        root = Path(item["project_root"])
        candidates.append({"log": Path(item["log"]), "project": str(read_project_json(root).get("project") or root.name).strip(),
                           "live": len(_instance_events(Path(item["log"]), instance)["live"])})
    listing = "; ".join(f"--log {c['log']}(--project {c['project']}, 未作废事件 {c['live']} 个)" for c in candidates)
    if not log_arg and not project_arg:
        raise ValueError(f"作废须显式作用域(--log <日志路径> 或 --project <项目>), 缺省不跨日志一刀切; 实例 {instance} 事件所在: {listing}")
    if log_arg:
        target = Path(log_arg).expanduser().resolve()
        scoped = [c for c in candidates if c["log"].resolve() == target]
        if not scoped:
            raise ValueError(f"--log {log_arg} 不是含实例 {instance} 事件的 enrollment_log(扫描范围: home 下各项目与签发门工作区); 可选: {listing}")
    else:
        scoped = [c for c in candidates if c["project"] == project_arg]
        if not scoped:
            raise ValueError(f"--project {project_arg} 的 enrollment_log 无实例 {instance} 事件; 可选: {listing}")
    if len(scoped) > 1:
        raise ValueError(f"--project {project_arg} 对应多个 enrollment_log({[str(c['log']) for c in scoped]}), 改用 --log 指明其一")
    chosen = scoped[0]
    trail = chosen["log"]
    live = _instance_events(trail, instance)["live"]
    selected: list[dict[str, Any]] = []
    for lineno, line in live:
        match = _EVENT_LINE_RE.match(line)
        if wanted_codes and match.group("code_id") not in wanted_codes:
            continue
        if cutoff is not None:
            stamp = _parse_iso(match.group("ts"))
            if stamp is None:
                raise ValueError(f"{trail}:{lineno}: 事件时间戳形坏 {match.group('ts')!r}, 无法按 --before 判定(fail-closed)")
            if stamp >= cutoff:
                continue
        selected.append({"line": lineno, "action": match.group("action"), "code_id": match.group("code_id"),
                         "ts": match.group("ts"), "role": line.split("role=", 1)[1].split()[0] if "role=" in line else "-"})
    missing_codes = [c for c in wanted_codes if c not in {s["code_id"] for s in selected}]
    if wanted_codes and missing_codes:
        raise ValueError(f"--code-id {','.join(missing_codes)} 在 {trail} 的作用域内无实例 {instance} 的未作废事件, 拒绝作废(核对 code_id 或用 enroll-where 查看)")
    if not selected:
        scope_text = "".join([f" code_id∈{wanted_codes}" if wanted_codes else "", f" 早于 {before_arg}" if before_arg else ""])
        raise ValueError(f"{trail} 中实例 {instance} 无范围内未作废事件{scope_text}(已全部作废或不在范围), 拒绝作废")
    codes = list(dict.fromkeys(s["code_id"] for s in selected))
    roles = sorted({s["role"] for s in selected if s["role"] != "-"})
    ranges = _line_ranges([s["line"] for s in selected])
    rendered = _trail_line(action=VOID_ACTION, code_id="-", role=",".join(roles) or "-", instance=instance,
                           project=chosen["project"], by=by, reason=reason,
                           extra=f"voids_lines={ranges}  voids_code_ids={','.join(codes)}")
    planned = [{"log": str(trail), "project": chosen["project"], "line": rendered.rstrip("\n"), "voids_lines": ranges,
                "event_count": len(selected), "code_id_count": len(codes), "code_ids": codes, "events": selected,
                "left_live": len(live) - len(selected)}]
    if not dry_run:
        for item in planned:
            _write_trail_line(Path(item["log"]), item["line"] + "\n")
    return {"ok": True, "operation": "roles_enroll_void", "dry_run": dry_run, "written": not dry_run, "instance": instance,
            "scope": {"log": str(trail), "project": chosen["project"], "code_ids": wanted_codes, "before": before_arg or None},
            "entries": planned}


def governance_seat_instance_refusal(role: str, instance: str | None, project_root: str | Path) -> str | None:
    """AIPOS-F134 件②: 治理席位类(顾问/规划方, roles.schema class_groups.governance_seat)注册码必须带实例——其凭据落治理根 .lybra/,
    同一治理根可接入多个顾问实例; 码不带实例 = 兑换出角色级条目, 落盘(enroll_client.upsert_token_entry)按角色匹配会把同角色既有条目置
    retired(互覆)。角色类唯一解析 custom_roles.resolve_role_to_class(自定义角色经门注册表; 注册表读不出照抛 = fail-closed)。
    返回拒因(带出口)或 None(放行: 已带实例 / 工位类 / 角色类不可解析由既有 UNKNOWN_ROLE 判)。工位类与 svc 角色级条目语义不变。"""
    if str(instance or "").strip():
        return None
    from tools.aipos_cli.custom_roles import resolve_role_to_class, role_classes_in_group

    role_class = resolve_role_to_class(role, project_root)
    if role_class not in role_classes_in_group("governance_seat"):
        return None
    return (f"角色 {role!r}(类 {role_class})是治理席位类, 注册码必须带实例(--instance <角色前缀>.<项目>.<机器>): 同一治理根可接入多个顾问实例, "
            "不带实例的凭据按角色匹配会把同角色既有凭据置 retired。出口: 重发码并给 --instance")


def create_enrollment_code(
    workspace_root: str | Path,
    *,
    role: str,
    instance: str | None = None,
    ttl_seconds: int | None = None,
    by: str = "owner",
    reason: str = "",
    governance_root: str | None = None,
) -> dict[str, Any]:
    """创建一个注册码。
    
    Args:
        workspace_root: 工作区根路径
        role: 要绑定的角色名(如 executor, auditor, 或自定义角色)
        instance: 可选的实例名(如 exec.lybra.mac1);None 表示不绑定实例
        ttl_seconds: 过期时间(秒);None 表示永不过期
        by: 签发者(owner 或授权引用)
        reason: 签发原因
    
    Returns:
        {
            "code_id": str,
            "code": str,  # 明文注册码,仅此处返回一次
            "fingerprint": str,
            "role": str,
            "instance": str | None,
            "status": "pending",
            "created_at": str (ISO8601),
            "expires_at": str | None (ISO8601),
            "by": str,
            "reason": str
        }
    """
    root = _workspace_root_path(workspace_root)
    # AIPOS-F134 件②: 治理席位类码必须带实例(判定唯一实现 governance_seat_instance_refusal, 门 dry-run 预检同用)
    refusal = governance_seat_instance_refusal(role, instance, str(governance_root or "").strip() or root)
    if refusal:
        raise ValueError(refusal)
    code = _generate_enrollment_code()
    code_id = f"enroll_{secrets.token_hex(8)}"
    now = utc_now().replace(microsecond=0)
    expires_at = (now + timedelta(seconds=ttl_seconds)) if ttl_seconds else None
    
    record = {
        "code": code,
        "fingerprint": _code_fingerprint(code),
        "role": role,
        "instance": instance,
        # AIPOS-F78B 件④c: 签给哪个治理根的工位(门注册表唯一口径, enroll-list --workspace-root 按此过滤)
        "governance_root": str(governance_root or "").strip() or None,
        "status": "pending",
        "created_at": now.isoformat().replace("+00:00", "Z"),
        "expires_at": expires_at.isoformat().replace("+00:00", "Z") if expires_at else None,
        "used_at": None,
        "revoked_at": None,
        "by": by,
        "reason": reason,
    }
    
    enrollments = _load_enrollments(root)
    enrollments[code_id] = record
    _save_enrollments(root, enrollments)
    
    _append_enrollment_trail(
        root,
        action="create",
        code_id=code_id,
        role=role,
        instance=instance,
        by=by,
        reason=reason,
        governance_root=record["governance_root"],
    )
    
    return {
        "code_id": code_id,
        "code": code,  # 明文 code,仅此处返回
        "fingerprint": record["fingerprint"],
        "role": role,
        "instance": instance,
        "status": "pending",
        "created_at": record["created_at"],
        "expires_at": record["expires_at"],
        "by": by,
        "reason": reason,
    }


def get_enrollment_status(workspace_root: str | Path, code: str) -> tuple[EnrollmentStatus, dict[str, Any] | None]:
    """根据注册码查询其状态。
    
    Returns:
        (status, record)
        - status: "pending" | "used" | "revoked" | "expired"
        - record: 完整记录(含 code_id),如果找不到则为 None
    """
    enrollments = _load_enrollments(workspace_root)
    now = utc_now()
    
    for code_id, rec in enrollments.items():
        if rec.get("code") == code:
            # 检查状态
            if rec.get("status") == "revoked":
                return "revoked", {**rec, "code_id": code_id}
            if rec.get("status") == "used":
                return "used", {**rec, "code_id": code_id}
            # 检查是否过期
            expires_str = rec.get("expires_at")
            if expires_str:
                expires_at = datetime.fromisoformat(expires_str.replace("Z", "+00:00"))
                if now > expires_at:
                    return "expired", {**rec, "code_id": code_id}
            return "pending", {**rec, "code_id": code_id}
    
    return "expired", None  # 找不到视为已过期(防止暴力枚举)


def mark_enrollment_used(
    workspace_root: str | Path,
    code: str,
    *,
    token_entry: dict[str, Any] | None = None,
    grace_seconds: int = ENROLL_LANDING_GRACE_SECONDS,
) -> dict[str, Any]:
    """标记注册码为已使用(F23: 交换与落盘原子 —— grace 窗口机制)。

    首次调用: status=pending → used, 记 used_at / grace_until / minted_token_entry;
    窗口内重试(同码): 幂等返回既有记录(同一 token, 不重铸);
    landed 后 / grace 过期后: ValueError(码已彻底消费)。

    Args:
        token_entry: 首次兑换时铸出的 token entry(存入记录供免费重试返回同一 token)

    Returns:
        更新后的记录(含 code_id)

    Raises:
        ValueError: 如果 code 不是 pending 且不在免费重试窗口内
    """
    root = _workspace_root_path(workspace_root)
    enrollments = _load_enrollments(root)
    now = utc_now().replace(microsecond=0)

    # 找到对应记录(按明文 code 匹配)
    code_id = None
    record = None
    for cid, rec in enrollments.items():
        if rec.get("code") == code:
            code_id, record = cid, rec
            break
    if record is None:
        raise ValueError("Enrollment code not found")

    status, _ = get_enrollment_status(root, code)

    if status == "pending":
        record["status"] = "used"
        record["used_at"] = now.isoformat().replace("+00:00", "Z")
        record["landed_at"] = None
        record["grace_until"] = (
            now + timedelta(seconds=max(1, int(grace_seconds)))
        ).isoformat().replace("+00:00", "Z")
        if token_entry is not None:
            record["minted_token_entry"] = token_entry
        _save_enrollments(root, enrollments)
        _append_enrollment_trail(
            root,
            action="use",
            code_id=code_id,
            role=record["role"],
            instance=record.get("instance"),
            by="(agent-exchange)",
            reason=(
                f"enrollment code exchanged for token"
                f" (landing grace until {record['grace_until']})"
            ),
            governance_root=record.get("governance_root"),
            projects=(record.get("minted_token_entry") or {}).get("projects"),
        )
        return {**record, "code_id": code_id}

    if status == "used":
        # F23 验收⑦: grace 窗口内同码免费重试(落盘前中断 → 码不白烧)
        if not record.get("landed_at") and record.get("minted_token_entry"):
            grace_until = record.get("grace_until")
            in_grace = False
            if grace_until:
                try:
                    in_grace = now <= datetime.fromisoformat(grace_until.replace("Z", "+00:00"))
                except ValueError:
                    in_grace = False
            if in_grace:
                return {**record, "code_id": code_id, "retry": True}
            reason = f"Enrollment code is used (landing grace expired at {grace_until}; token was minted but workstation never landed it)"
        elif record.get("landed_at"):
            reason = f"Enrollment code is used (already landed at {record['landed_at']})"
        else:
            reason = "Enrollment code is used"
        raise ValueError(reason)

    raise ValueError(f"Enrollment code is {status}, cannot use")


def land_enrollment(
    workspace_root: str | Path,
    code: str,
    *,
    landed_detail: str = "",
) -> dict[str, Any]:
    """F23 验收⑦/⑧: 工位落盘成功后确认 —— 关闭 grace 窗口, 码彻底消费。

    幂等: 已 landed 再调不报错(返回既有记录, retry=True)。

    Returns:
        更新后的记录(含 code_id)

    Raises:
        ValueError: code 不存在 / 尚未 exchange(必须先 exchange 再 land)/ 已吊销或过期
    """
    root = _workspace_root_path(workspace_root)
    enrollments = _load_enrollments(root)
    now = utc_now().replace(microsecond=0)

    code_id = None
    record = None
    for cid, rec in enrollments.items():
        if rec.get("code") == code:
            code_id, record = cid, rec
            break
    if record is None:
        raise ValueError("Enrollment code not found")

    if record.get("landed_at"):
        return {**record, "code_id": code_id, "retry": True}

    status, _ = get_enrollment_status(root, code)
    if status != "used":
        raise ValueError(
            f"Enrollment code is {status}; land requires a prior exchange (used). "
            "Call lybra_roles_enroll_exchange first."
        )

    record["landed_at"] = now.isoformat().replace("+00:00", "Z")
    if landed_detail:
        record["landed_detail"] = str(landed_detail)[:400]
    _save_enrollments(root, enrollments)
    _append_enrollment_trail(
        root,
        action="land",
        code_id=code_id,
        role=record["role"],
        instance=record.get("instance"),
        by="(agent-enroll)",
        reason=landed_detail or "workstation landed .lybra/ config",
        governance_root=record.get("governance_root"),
        projects=(record.get("minted_token_entry") or {}).get("projects"),
    )
    return {**record, "code_id": code_id}


def revoke_enrollment_code(
    workspace_root: str | Path,
    code_id: str,
    *,
    by: str = "owner",
    reason: str = "",
) -> dict[str, Any]:
    """吊销一个注册码。幂等:已吊销的再次吊销不报错。
    
    Returns:
        更新后的记录
    
    Raises:
        ValueError: 如果 code_id 不存在
    """
    root = _workspace_root_path(workspace_root)
    enrollments = _load_enrollments(root)
    if code_id not in enrollments:
        raise ValueError(f"Enrollment code_id not found: {code_id}")
    
    rec = enrollments[code_id]
    if rec.get("status") != "revoked":
        now = utc_now().replace(microsecond=0)
        rec["status"] = "revoked"
        rec["revoked_at"] = now.isoformat().replace("+00:00", "Z")
        _save_enrollments(root, enrollments)
        
        _append_enrollment_trail(
            root,
            action="revoke",
            code_id=code_id,
            role=rec["role"],
            instance=rec.get("instance"),
            by=by,
            reason=reason,
            governance_root=rec.get("governance_root"),
            projects=(rec.get("minted_token_entry") or {}).get("projects"),
        )
    
    return {**rec, "code_id": code_id}


def list_enrollment_codes(workspace_root: str | Path, *, include_code: bool = False) -> list[dict[str, Any]]:
    """列出所有注册码。
    
    Args:
        workspace_root: 工作区根路径
        include_code: 是否包含明文 code(默认 False,仅返回 fingerprint)
    
    Returns:
        注册码列表,每项含 code_id 和状态
    """
    enrollments = _load_enrollments(workspace_root)
    now = utc_now()
    result = []
    
    for code_id, rec in enrollments.items():
        # 动态计算状态(考虑过期)
        status = rec.get("status", "pending")
        if status == "pending":
            expires_str = rec.get("expires_at")
            if expires_str:
                expires_at = datetime.fromisoformat(expires_str.replace("Z", "+00:00"))
                if now > expires_at:
                    status = "expired"
        
        item = {
            "code_id": code_id,
            "fingerprint": rec.get("fingerprint", ""),
            "role": rec.get("role", ""),
            "instance": rec.get("instance"),
            "governance_root": rec.get("governance_root"),
            "status": status,
            "landed": bool(rec.get("landed_at")),
            "created_at": rec.get("created_at"),
            "expires_at": rec.get("expires_at"),
            "used_at": rec.get("used_at"),
            "landed_at": rec.get("landed_at"),
            "grace_until": rec.get("grace_until"),
            "revoked_at": rec.get("revoked_at"),
            "by": rec.get("by", ""),
            "reason": rec.get("reason", ""),
        }
        if include_code:
            item["code"] = rec.get("code", "")
        result.append(item)
    
    return result


# AIPOS-316: Guard against direct invocation
from tools.aipos_cli._cli_entry_guard import check_direct_invocation
check_direct_invocation(__name__)
