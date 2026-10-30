#!/usr/bin/env python3
"""AIPOS-R6H 靶② / AIPOS-F151 件②: 签码 + 接入一体(enroll-deliver)的唯一实现 —— 注册码只在本进程内。

入口(唯一): `lybra roles enroll-deliver --role <角色> --instance <实例> --governance-root <治理根> --workspace <工位或治理根>
[--executor-mode …] [--harness …] [--harness-dir …] [--harness-host …] [--ssh <目标>] [--dry-run | --confirm]`
(两阶段声明 verbs.schema two_phase_protocol.roles_writers: 缺省预演, --confirm 才签码接入)。
在治理根所在机运行; 门客户端凭据 = `lybra roles` 的 connection.json(与 `roles enroll-code` 同一取法), 只作本机 HTTP Authorization。
原模块入口 `python3 -m tools.aipos_cli.enroll_deliver`(同机本地发码 + bootstrap 兑换 + 手写 connection.json = enroll 的第二份实现)
已收为上述子命令: 模块 main 只打印对应的 `lybra roles enroll-deliver` 命令并非零退出(AIPOS-F151)。

流程(enroll_deliver):
  ① 本机预检(先于签码, 拒 = 不签码): ssh 目标合法; 治理根是本机已建项目; 角色类可解析(custom_roles.resolve_role_to_class);
     enroll_client.enroll_preflight —— 执行者模式 / harness / 子 agent 判据 / 工位类不落治理根 / 覆盖信封(与 enroll 换码前预检同一实现)
  ② 门动词 lybra_enroll_code_dry_run(门侧校验签码参数: 角色 / 实例 / 治理根注册表 / Owner 授权引用)——预演到此为止, 不出码
  ③ --confirm: lybra_enroll_code_dry_run → lybra_enroll_code_confirm 签码(发码唯一实现 enrollment.issue_self_contained_code 在门进程内);
     同机 = 直调 enroll_client.enroll(code=…, verify=True)(兑换 + 落盘 + land + 连通验证);
     跨机 = 码经 ssh stdin 交远端 `python3 -m tools.aipos_cli.enroll_client --code-stdin`(AIPOS-F113: 不进远端 argv), pi 工位再远端分发
  ④ 失败即经门动词 lybra_roles_enroll_revoke 吊销该码; 吊销失败 = EnrollDeliverRollbackError(非零, 带手工清理出口)
注册码与凭据明文永不打印、不落盘、不进任何 argv / 环境变量(输出只含 code_id 与指纹; 异常文本经 confined_worker.redact_transcript 脱敏)。
"""
from __future__ import annotations

import json
import shlex
import subprocess
import sys
from pathlib import Path
from typing import Any

from tools.sandbox_runtime.confined_worker import redact_transcript

# AIPOS-F113: 同机判定 / loopback 规范化复用 enroll_client 唯一实现(本模块仍导出同名供既有调用方)
from tools.aipos_cli.enroll_client import is_same_host, normalize_gate_url_for_same_host  # noqa: E402,F401

#: 门签码两动词(roles enroll-code 与 enroll-deliver 同用, verbs.schema lybra_enroll_code_dry_run / _confirm)
ENROLL_CODE_DRY_RUN_VERB = "lybra_enroll_code_dry_run"
ENROLL_CODE_CONFIRM_VERB = "lybra_enroll_code_confirm"
ENROLL_REVOKE_VERB = "lybra_roles_enroll_revoke"


class EnrollDeliverRollbackError(RuntimeError):
    """AIPOS-F113 件③: 失败后回滚(吊销注册码)本身也失败 —— 非零退出, 消息含远端/门侧手工清理出口。"""


class GateVerbRefused(RuntimeError):
    """门动词返回 ok≠true(带门原文, 已截断; 门回应不含码明文——dry_run 不出码, confirm 失败无码)。"""

    def __init__(self, verb: str, response: dict[str, Any]) -> None:
        self.verb = verb
        self.response = response
        super().__init__(f"gate rejected {verb}: {json.dumps(response, ensure_ascii=False)[:800]}")


def gate_enroll_code(client: Any, params: dict[str, Any], *, confirm: bool) -> dict[str, Any]:
    """AIPOS-F151 件①②: CLI 侧签码的唯一门调用(`lybra roles enroll-code` 与 enroll-deliver 共用)。

    预演(confirm=False)= 只调 lybra_enroll_code_dry_run(门侧校验 + 预览, 不出码); 执行(confirm=True)= dry_run → lybra_enroll_code_confirm
    (owner_confirmation_token 为公开字面常量, 非秘密; AIPOS-328 顾问自确认)。门拒 = GateVerbRefused。
    返回 {"dry_run": <dry_run 回应>, "confirm": <confirm 回应 | None>}。"""
    args = {k: v for k, v in params.items() if v not in (None, "")}
    dry = client.call_tool(ENROLL_CODE_DRY_RUN_VERB, args)
    if not dry.get("ok"):
        raise GateVerbRefused(ENROLL_CODE_DRY_RUN_VERB, dry)
    if not confirm:
        return {"dry_run": dry, "confirm": None}
    conf = client.call_tool(ENROLL_CODE_CONFIRM_VERB, {
        "dry_run_token": dry.get("dry_run_token"),
        "owner_confirmation_token": "OWNER_CONFIRMED",
        "actor": args.get("actor") or "cli:roles-enroll-code",
    })
    if not conf.get("ok"):
        raise GateVerbRefused(ENROLL_CODE_CONFIRM_VERB, conf)
    return {"dry_run": dry, "confirm": conf}


def validate_workspace_root(workspace_root: str, role: str, registry_root: str | Path | None = None) -> None:
    """工位目录守卫(AIPOS-F22 F23⑧): 目标是治理工作区(结构识别 enroll_client.is_governance_workspace, AIPOS-F88)时,
    非治理席位类拒绝。判定委托 enroll_client._governance_target_refusal(enroll / enroll-deliver 预检同一实现, AIPOS-F151);
    角色类唯一解析 custom_roles.resolve_role_to_class(解析不到 = UnknownRoleClass, ValueError 子类; 注册表根缺省 = workspace_root)。"""
    from tools.aipos_cli.custom_roles import resolve_role_to_class
    from tools.aipos_cli.enroll_client import _governance_target_refusal, is_governance_workspace

    role_class = str(resolve_role_to_class(role, registry_root or workspace_root, required=True))
    target = Path(workspace_root).expanduser()
    if is_governance_workspace(target) and _governance_target_refusal(target, role, role_class):
        raise ValueError(
            f"workspace_root cannot be governance repo (结构识别: 含声明的队列根) for role class '{role_class}': {workspace_root}. "
            "Use product repo or agent workstation path. (Only planner/advisor roles may use governance workspace.)"
        )


def resolve_local_governance_root(value: str | Path | None) -> Path:
    """--governance-root(项目名或绝对路径)→ 本机已建治理根(结构判据 has_workspace_queue(established); 项目名经 home 根唯一解析梯)。
    不是本机已建治理根 = ValueError(enroll-deliver 在治理根所在机运行: 覆盖信封预检读本机治理根的声明)。"""
    from tools.aipos_cli.workspace_config import has_workspace_queue, resolve_home_root, resolve_project_root

    raw = str(value or "").strip()
    if not raw:
        raise ValueError("--governance-root 必填(项目名或治理根绝对路径; 同机直递缺治理根会落错项目, gap #106)")
    candidate = Path(raw).expanduser()
    if candidate.is_absolute():
        root = candidate.resolve()
        if not has_workspace_queue(root, established=True):
            raise ValueError(f"PROJECT_NOT_ESTABLISHED: --governance-root {root} 不是本机已建治理根(须有 project.json 与声明的队列根); "
                             "enroll-deliver 在治理根所在机运行")
        return root
    try:
        return resolve_project_root(resolve_home_root(), raw)
    except FileNotFoundError as exc:
        raise ValueError(f"{exc}(--governance-root {raw!r}; enroll-deliver 在治理根所在机运行)") from exc


def _redact(text: str, secrets_: list[str | None]) -> str:
    """AIPOS-F113: 本机输出/日志/异常文本里的凭据与注册码明文 → «redacted:<指纹>»(复用 confined_worker.redact_transcript 唯一实现)。"""
    needles = [str(x) for x in secrets_ if x]
    clean, _hits = redact_transcript(str(text), needles)
    return clean


def remote_command(argv: list[str]) -> str:
    """AIPOS-F113 件②: ssh 把远端命令交给远端登录 shell 解释 —— 每个参数逐个 shlex.quote(禁字符串直拼)。"""
    return shlex.join([str(a) for a in argv])


def ssh_argv(ssh_target: str, argv: list[str]) -> list[str]:
    """本机 ssh 调用的 argv: `ssh -- <目标> <已转义远端命令>`。

    `--` 终止 ssh 选项解析, 且拒绝以 '-' 开头的目标(防 `-oProxyCommand=…` 类选项注入, fail-closed)。
    远端命令只含非秘密参数; 注册码经 stdin 送达(见 _deliver_ssh)。
    """
    target = str(ssh_target or "").strip()
    if not target or target.startswith("-") or any(ch.isspace() for ch in target):
        raise ValueError(f"--ssh 目标非法(空 / 以 '-' 开头 / 含空白): {ssh_target!r}")
    return ["ssh", "--", target, remote_command(argv)]


def _run_remote(ssh_target: str, argv: list[str], *, stdin_text: str, stage: str, secrets_: list[str | None]) -> dict[str, Any]:
    """经 ssh 执行一条远端命令(参数已转义, 秘密只走 stdin), 解析其 --json 输出; 非零/非 JSON/ok≠true 一律抛错(已脱敏)。"""
    result = subprocess.run(ssh_argv(ssh_target, argv), input=stdin_text, capture_output=True, text=True, timeout=60)
    if result.returncode != 0:
        raise RuntimeError(
            f"远端 {stage} 失败(ssh 退出码 {result.returncode}): "
            f"{_redact((result.stderr or '').strip() or (result.stdout or '').strip(), secrets_)[:800]}"
        )
    try:
        payload = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"远端 {stage} 输出不是 JSON({exc}): {_redact(result.stdout, secrets_)[:400]}") from exc
    if not isinstance(payload, dict) or payload.get("ok") is not True:
        raise RuntimeError(f"远端 {stage} 返回 ok≠true: {_redact(json.dumps(payload, ensure_ascii=False), secrets_)[:800]}")
    return payload


def _enroll_args(harness_kind: str | None, harness_dir: str | None, harness_host: str | None, executor_mode: str | None) -> list[str]:
    """接入参数(与 `lybra roles enroll` / enroll_client 同名同义)→ 远端 argv 片段(只含给了的项)。"""
    out: list[str] = []
    for flag, value in (("--harness", harness_kind), ("--harness-dir", harness_dir), ("--harness-host", harness_host),
                        ("--executor-mode", executor_mode)):
        if str(value or "").strip():
            out += [flag, str(value).strip()]
    return out


def _deliver_local(code: str, *, workspace: Path, harness_kind: str | None, harness_dir: str | None, harness_host: str | None,
                   executor_mode: str | None, policy: str | None) -> dict[str, Any]:
    """同机: 直调 enroll 唯一实现(兑换 + 落盘 + land + --verify); 码以函数参数在本进程内传递, 不进 argv / 环境 / 文件。"""
    from tools.aipos_cli.enroll_client import enroll

    return enroll(code=code, gate_url="", workspace_root=workspace, policy=policy, verify=True, harness_kind=harness_kind,
                  harness_dir=harness_dir or None, harness_host=harness_host, executor_mode=executor_mode)


def _deliver_ssh(code: str, *, ssh_target: str, gate_url: str, workspace: str, harness_root: str | None, role: str,
                 policy: str | None, harness_kind: str | None, harness_dir: str | None, harness_host: str | None,
                 executor_mode: str | None, distribute: bool, force: bool, secrets_: list[str | None],
                 stage_box: dict[str, str]) -> dict[str, Any]:
    """跨机(AIPOS-F113): 码只走 ssh stdin 交远端 enroll_client --code-stdin(远端命令逐参数 shlex.quote); pi 工位再远端分发(无秘密参数)。"""
    enroll_argv = [
        "python3", "-m", "tools.aipos_cli.enroll_client",
        "--code-stdin",
        "--gate-url", gate_url,
        "--workspace", workspace,
        *(["--policy", policy] if str(policy or "").strip() else []),
        "--landed-host", ssh_target,  # AIPOS-F95 件③b: land 事件 host = ssh 目标(跨机工位, loop 以它为拉起目标)
        *_enroll_args(harness_kind, harness_dir, harness_host, executor_mode),
        "--json",
    ]
    stage_box["stage"] = "enroll"
    enroll_result = _run_remote(ssh_target, enroll_argv, stdin_text=code + "\n", stage="enroll", secrets_=secrets_)
    distribute_result = None
    if distribute and harness_root:
        distribute_argv = ["python3", "-m", "tools.distribute_tools", harness_root, role, "--json", *(["--force"] if force else [])]
        stage_box["stage"] = "distribute"
        stage_box["distribute_cmd"] = shlex.join(ssh_argv(ssh_target, distribute_argv))
        distribute_result = _run_remote(ssh_target, distribute_argv, stdin_text="", stage="distribute", secrets_=secrets_)
    return {"enroll": enroll_result, "distribution": distribute_result}


def enroll_deliver(
    *,
    client: Any,
    role: str,
    governance_root: str | Path,
    workspace: str | Path,
    owner_authorization_ref: str,
    instance: str | None = None,
    confirm: bool = False,
    ssh_target: str | None = None,
    harness_kind: str | None = None,
    harness_dir: str | None = None,
    harness_host: str | None = None,
    executor_mode: str | None = None,
    gate_url: str | None = None,
    ttl_seconds: int | None = None,
    reason: str = "",
    actor: str = "cli:roles-enroll-deliver",
    harness_root: str | None = None,
    policy: str | None = None,
    force: bool = False,
) -> dict[str, Any]:
    """AIPOS-F151 件②: 签码 + 接入一体的唯一实现(见模块说明 ①–④)。client = 已持门客户端凭据的 GateClient(或同接口替身)。

    confirm=False(缺省)= 预演: 本机预检 + 门 lybra_enroll_code_dry_run, 零写入、不出码; confirm=True = 签码 + 接入。
    返回结果只含 code_id / 指纹 / 落点 / 预检结论; 注册码明文永不出现在返回值、输出或异常文本里。"""
    from tools.aipos_cli.custom_roles import resolve_role_to_class
    from tools.aipos_cli.enroll_client import EnrollLandedError, enroll_preflight
    from tools.aipos_cli.enrollment import executor_mode_declaration, governance_root_executor_modes

    # ① 本机预检(先于签码)
    ssh = None
    if ssh_target is not None:  # 给了 --ssh(含空串)即校验: 非法目标 fail-closed, 先于签码(不白铸码)
        ssh_argv(ssh_target, ["true"])
        ssh = str(ssh_target).strip()
    auth_ref = str(owner_authorization_ref or "").strip()
    if not auth_ref:
        raise ValueError("--owner-authorization-ref 必填(签码是 Owner 授权的门动词; 工位码通常填覆盖该工位的信封 ID)")
    role = str(role or "").strip()
    instance = str(instance or "").strip() or None
    gov = resolve_local_governance_root(governance_root)
    role_class = str(resolve_role_to_class(role, gov, required=True))
    ws_text = str(workspace or "").strip()
    if not ws_text:
        raise ValueError("--workspace 必填(工位目录; 治理席位与子 agent 执行者 = 治理根)")
    ws_local = None if ssh else Path(ws_text).expanduser().resolve()
    # AIPOS-F253 件①: 会话目录相撞的出口 = 同一条命令换 --harness-dir(先预演)
    retry_argv = ["lybra", "roles", "enroll-deliver", "--role", role, *(["--instance", instance] if instance else []),
                  "--governance-root", str(gov), "--workspace", ws_text, *(["--harness", str(harness_kind)] if harness_kind else []),
                  "--harness-dir", "DIR_SLOT", *(["--harness-host", str(harness_host)] if harness_host else []),
                  "--owner-authorization-ref", auth_ref, "--dry-run"]
    pre = enroll_preflight(workspace_root=ws_local or Path(ws_text), governance_root=str(gov),
                           declared={"role": role, "instance": instance, "role_class": role_class},
                           harness_kind=harness_kind, harness_dir=harness_dir, harness_host=harness_host,
                           executor_mode=executor_mode, check_target=ssh is None, stage="issue",
                           session_retry=shlex.join(retry_argv).replace("DIR_SLOT", "{dir}"))
    mode = pre["mode"]
    seat_mode = mode in governance_root_executor_modes()
    default_mode = str(executor_mode_declaration()["default"])
    envelope = pre["envelope"] or {}
    target = {
        "mode": "ssh" if ssh else "local",
        "ssh_target": ssh,
        "role": role,
        "role_class": role_class,
        "instance": instance,
        "governance_root": str(gov),
        "workspace_root": ws_text if ssh else str(ws_local),
        "executor_mode": mode,
        "harness": pre["harness_record"] if not ssh else
        {k: v for k, v in (("kind", harness_kind), ("dir", harness_dir), ("host", harness_host)) if v} or None,
        "envelope": {"required": envelope.get("required"), "policy_id": envelope.get("policy_id"), "reason": envelope.get("reason")},
    }
    params = {
        "role": role,
        "instance": instance,
        "ttl": ttl_seconds,
        "gate_url": gate_url,
        "governance_root": str(gov),
        "owner_authorization_ref": auth_ref,
        "reason": reason or f"enroll-deliver {instance or role} → {target['workspace_root']}",
        "actor": actor,
    }
    credential_fp = getattr(client, "token_fingerprint", None)
    distribute = bool(ssh) and not seat_mode and str(harness_kind or "").strip() in ("", "pi") and mode == default_mode

    # ② 预演: 门侧校验签码参数, 不出码、零写入
    if not confirm:
        issued = gate_enroll_code(client, params, confirm=False)
        return {
            "ok": True, "operation": "enroll-deliver", "phase": "dry_run", "dry_run": True, **target,
            "gate_preview": issued["dry_run"].get("preview"),
            "credential_fingerprint": credential_fp,
            "will": [
                f"门签码(lybra_enroll_code_confirm): 角色 {role}(类 {role_class}) 实例 {instance or '-'} 治理根 {gov}"
                f"(有效期 {(issued['dry_run'].get('preview') or {}).get('ttl_seconds')} 秒)",
                (f"经 ssh {ssh} 把码由 stdin 交远端 enroll_client --code-stdin 落 {ws_text}" + (" 并远端分发工位件" if distribute else ""))
                if ssh else f"本进程直调 enroll 兑换落盘到 {ws_local}(--verify 连通验证)",
                "注册码只在本进程内: 不打印、不落盘、不进任何 argv",
            ],
        }

    # ③ 签码 + 接入
    issued = gate_enroll_code(client, params, confirm=True)
    conf = issued["confirm"]
    code = str(conf.get("self_contained_code") or "")
    code_id = str(conf.get("code_id") or "")
    code_fp = conf.get("fingerprint")
    # 脱敏针: 注册码 + 门客户端凭据(门 / 远端报错文本可能回显; 原样永不出本进程)
    secrets_: list[str | None] = [code, *getattr(client, "redaction_needles", lambda: [])()]
    if not code or not code_id:
        raise RuntimeError(f"门签码回应缺码或 code_id: {_redact(json.dumps(conf, ensure_ascii=False), secrets_)[:400]}")
    stage_box: dict[str, str] = {"stage": "enroll"}
    try:
        if ssh:
            delivered = _deliver_ssh(code, ssh_target=ssh, gate_url=str(conf.get("gate_url") or gate_url or ""), workspace=ws_text,
                                     harness_root=harness_root or ws_text, role=role, policy=policy, harness_kind=harness_kind,
                                     harness_dir=harness_dir, harness_host=harness_host, executor_mode=executor_mode,
                                     distribute=distribute, force=force, secrets_=secrets_, stage_box=stage_box)
            enroll_result, distribution = delivered["enroll"], delivered["distribution"]
        else:
            enroll_result = _deliver_local(code, workspace=ws_local, harness_kind=harness_kind, harness_dir=harness_dir,
                                           harness_host=harness_host, executor_mode=executor_mode, policy=policy)
            distribution = None
    except EnrollLandedError as exc:
        # 码已兑换并落地(本进程 enroll: --verify 不通已回滚凭据 / 非 pi harness 件交付失败): 不吊销(码已消费), 按原出口处理
        raise RuntimeError(f"enroll-deliver 失败(注册码 {code_id} 已兑换落地, 无须吊销): "
                           f"{_redact(f'{exc.__class__.__name__}: {exc}', secrets_)}") from exc
    except Exception as exc:
        _rollback(client, code_id=code_id, code_fp=code_fp, auth_ref=auth_ref, exc=exc, secrets_=secrets_, ssh=ssh, workspace=ws_text,
                  instance=instance, role=role, stage_box=stage_box)
        raise  # _rollback 总是抛出; 此行只为静态可读
    finally:
        code = ""  # noqa: F841 —— 码引用尽早释放(不进返回值)

    keep = ("files_written", "landed", "verify", "policy_derivation", "harness", "executor_mode", "subagent_harness", "warnings",
            "minimum_bootable_set", "harness_delivery", "next_step", "rotated", "lybra_dir")
    return {
        "ok": True, "operation": "enroll-deliver", "phase": "confirm", "dry_run": False, **target,
        "enrollment": {"code_id": code_id, "fingerprint": code_fp, "expires_at": conf.get("expires_at"),
                       "gate_url": conf.get("gate_url"), "governance_root": conf.get("governance_root")},
        "credential_fingerprint": credential_fp,
        "token": {"fingerprint": enroll_result.get("fingerprint"), "scopes": enroll_result.get("scopes", [])},
        "enroll": {k: enroll_result.get(k) for k in keep if k in enroll_result},
        "distribution": distribution,
    }


def _rollback(client: Any, *, code_id: str, code_fp: Any, auth_ref: str, exc: BaseException, secrets_: list[str | None],
              ssh: str | None, workspace: str, instance: str | None, role: str, stage_box: dict[str, str]) -> None:
    """失败 → 经门吊销该码(AIPOS-F113 件③ fail-closed); 吊销失败 = EnrollDeliverRollbackError(出口)。总是抛出。"""
    primary = _redact(f"{exc.__class__.__name__}: {exc}", secrets_)
    stage = stage_box.get("stage", "enroll")
    who = f"agent_instance={instance or '(无实例, role=' + role + ')'}"
    if ssh:
        where = (
            f"远端 enroll 已成功(凭据已落 {workspace}/.lybra/connection.json), 失败在分发阶段: 可直接重跑分发 "
            f"`{stage_box.get('distribute_cmd', '')}`; 若要撤回该工位, 在远端删除该文件中 {who} 的 token 条目并在门侧吊销该 token"
            if stage == "distribute" else
            f"远端 enroll 未确认成功: 检查远端 {workspace}/.lybra/connection.json, 若已写入 {who} 的 token 条目则删除该条目"
        )
        label, manual = f"enroll-deliver SSH 失败({stage})", f"② 远端手工清理(ssh {ssh}): {where}"
    else:
        where = f"检查 {workspace}/.lybra/connection.json, 若已写入 {who} 的 token 条目则删除该条目后重跑"
        label, manual = "enroll-deliver 失败", f"② {where}"
    try:
        revoked = client.call_tool(ENROLL_REVOKE_VERB, {"code_id": code_id, "owner_authorization_ref": auth_ref,
                                                         "reason": f"enroll-deliver rollback ({stage} failed)"})
        if not revoked.get("ok"):
            raise RuntimeError(f"门拒绝吊销: {_redact(json.dumps(revoked, ensure_ascii=False), secrets_)[:400]}")
    except Exception as rb_exc:
        raise EnrollDeliverRollbackError(
            f"{label}: {primary}\n"
            f"且回滚失败: 注册码 {code_id}(指纹 {code_fp}) 未能吊销({_redact(f'{rb_exc.__class__.__name__}: {rb_exc}', secrets_)})。\n"
            f"出口: ① 手工吊销该码: 门动词 {ENROLL_REVOKE_VERB} {{code_id: {code_id}}}"
            f"(或在门工作区 lybra roles enroll-revoke {code_id} --owner-authorization-ref <owner授权引用>); {manual}。"
        ) from rb_exc
    state = f" 远端状态: {where}。" if ssh else ""
    raise RuntimeError(f"{label}: {primary}\n已回滚: 注册码 {code_id}(指纹 {code_fp}) 已经门吊销。{state}") from exc


def build_parser() -> "argparse.ArgumentParser":
    """旧模块入口的参数解析器(AIPOS-F110 技能步骤命令过 argparse 夹具; main 据此把旧参数换算成 `lybra roles enroll-deliver` 命令)。"""
    import argparse

    parser = argparse.ArgumentParser(
        description="AIPOS-R6H(已收为 `lybra roles enroll-deliver`, AIPOS-F151): 本入口只打印对应的新命令",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--role", required=True, help="Role name (executor/auditor/advisor)")
    parser.add_argument("--instance", help="Agent instance (e.g., exec.lybra.mac1)")
    parser.add_argument("--target-workspace", required=True, help="Target workspace root (→ --workspace)")
    parser.add_argument("--target-harness", required=True, help="Target harness root (pi workstation = the workspace root)")
    parser.add_argument("--gate-url", required=True, help="Gate URL (→ --gate-url)")
    parser.add_argument("--owner-policy-ref", required=True, help="Owner policy reference (→ --owner-authorization-ref)")
    # AIPOS-F113: Owner 凭据禁进 argv(原 --owner-token <明文> 退役)
    parser.add_argument("--owner-token-stdin", action="store_true", help="(retired) the new command reads the gate credential from connection.json")
    parser.add_argument("--connection-json", help="Path to connection.json (→ lybra roles --connection-json)")
    parser.add_argument("--owner-authorization-ref", help="Owner authorization ref (→ --owner-authorization-ref)")
    parser.add_argument("--governance-root", help="Governance root (project name or absolute path; → --governance-root)")
    parser.add_argument("--ssh", help="SSH target for remote delivery (user@host)")
    parser.add_argument("--ttl", type=int, default=3600, help="Enrollment code TTL in seconds (default: 3600)")
    parser.add_argument("--force", action="store_true", help="Force overwrite existing files")
    parser.add_argument("--json", action="store_true", help="Output JSON")
    return parser


def equivalent_command(args: Any) -> str:
    """旧参数 → 等价的 `lybra roles enroll-deliver` 命令(不含任何凭据; 缺治理根留占位)。"""
    parts = ["lybra", "roles"]
    if args.connection_json:
        parts += ["--connection-json", args.connection_json]
    parts += ["enroll-deliver", "--role", args.role]
    if args.instance:
        parts += ["--instance", args.instance]
    parts += ["--governance-root", args.governance_root or "<治理根>", "--workspace", args.target_workspace]
    if args.ssh:
        parts += ["--ssh", args.ssh]
    parts += ["--gate-url", args.gate_url, "--ttl", str(args.ttl),
              "--owner-authorization-ref", args.owner_authorization_ref or args.owner_policy_ref]
    return shlex.join([str(p) for p in parts])


def main(argv: list[str] | None = None) -> int:
    """旧入口已收为 `lybra roles enroll-deliver`(AIPOS-F151 件②): 不签码、不接入, 只打印等价新命令(先预演, 再加 --confirm)并退出 2。"""
    args = build_parser().parse_args(argv)
    command = equivalent_command(args)
    message = ("python3 -m tools.aipos_cli.enroll_deliver 已收为 `lybra roles enroll-deliver`(AIPOS-F151: 两阶段, 注册码只在进程内)。"
               "请在治理根所在机改跑(先预演, 确认后同一条加 --confirm):")
    if args.json:
        print(json.dumps({"ok": False, "error": "RETIRED_ENTRY", "message": message,
                          "command": f"{command} --dry-run", "confirm_command": f"{command} --confirm"}, ensure_ascii=False, indent=2))
    else:
        print(f"{message}\n  {command} --dry-run\n  {command} --confirm", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main())
