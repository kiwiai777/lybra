"""AIPOS-F57 / AIPOS-F92 — 从 0 接新项目全流程固化(onboarding guide generator)。

把"从项目注册到首卡结案"做成产品命令序列, 全程零手工编辑。AIPOS-F92 起按单门 home 根约定(AIPOS-226 §1.3)重排:
  ① 建项目(顾问): `lybra project new` 落在 resolve_home_root 解析出的 home 根下(打印结果与来源; 门只扫描 home 根),
     同时写首份阶段快照「项目创建」(首次 finalize 不被阶段门拦)
  ② 声明产品仓(顾问): `lybra project set-repos`(project.json repos/code_repo, 经声明校验)
  ③ Owner 一次性动作 ①: 用中央凭据库的 Owner 凭据为新项目签发顾问注册码(项目范围 = 新项目)
  ④ Owner 一次性动作 ②: 一条 `lybra envelope mint --confirm` 签三张信封(驱动方 / 执行实例 / 审计实例)
  ⑤ 顾问凭码 enroll 到治理根(得 advisor 凭据), 产品经 distribution 声明把顾问技能交付到 Claude Code 会话目录 .claude/skills
  ⑥ 顾问为执行 / 审计工位发注册码(顾问凭据)
  ⑦ 工位 enroll + sync 分发 + 稳态复核
  ⑧ 工位自检 → 起 pi → /go(工位零门: 认领由驱动方经产品完成)
  ⑨ 首卡: 发卡 → `lybra loop` 推进到结案
Owner 动作只有 ③④ 两条(每条在 guide 中以 owner_action 标出); 其余由新顾问执行。顾问会话目录可与治理根不同:
所有命令显式带治理根(--workspace-root / --home-root / --governance-root), 不依赖 cwd。

每步失败都报错带路且给出可执行出口。禁任何项目名/路径硬编码(项目无关性)。

单源纪律:
  - home 根 = workspace_config.resolve_home_root_with_source(与 project new / 门同一优先级梯)
  - 实例名 = naming_profile.default_instance_name(roles.schema naming.template + 角色前缀)
  - 可启动最小集清单 = distribution.schema#minimum_bootable_set(单源)
  - harness 维度 = distribution.schema#harness_semantics(--harness 取值由声明推导)

AIPOS-F85 件①(F84 G1)不变量 = guide 产出的每条 `lybra ...` 命令占位换合法值后过 aipos_cli.build_parser(),
pi 内斜杠命令只许 distribution 声明中扩展所注册者(夹具 tests/test_aipos_f85_onboarding_guide_generic.py 守)。
"""
from __future__ import annotations

import json
import os
import shlex
import socket
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

#: guide 中由上一步输出填入的占位(F85 夹具登记同一组; 新占位须同步登记)
ADVISOR_CODE = "<ADVISOR_CODE>"
EXECUTOR_CODE = "<EXECUTOR_CODE>"
AUDITOR_CODE = "<AUDITOR_CODE>"
OWNER_WORKSPACE_PLACEHOLDER = "<OWNER_WORKSPACE>"
REPO_PLACEHOLDER = "<REPO_NAME>=<REPO_ABS_PATH>"
DRAFT_JSON_PLACEHOLDER = "<CARD_DRAFT_JSON>"
DRAFT_PATH_PLACEHOLDER = "<DRAFT_PATH>"
TASK_ID_PLACEHOLDER = "<TASK_ID>"
#: Claude Code harness kind(distribution.schema harness_semantics.kinds 的键; 顾问会话)
ADVISOR_HARNESS = "claude-code"


# ---------------------------------------------------------------------------
# 引号工具
# ---------------------------------------------------------------------------

def _shell_quote(s: str) -> str:
    """Shell-quote a string for copy-paste commands."""
    return shlex.quote(s)


def _shell_path(s: str) -> str:
    """Shell-quote a path but keep a leading ``~/`` unquoted so the shell still expands it.

    AIPOS-F85: 缺省工位 ``~/<项目>-workstation`` 经 shlex.quote 会变成 ``'~/...'``(引号内 ``~`` 不展开 → cd 落到字面目录)。
    占位(``<...>``)原样保留, 由上一步输出替换。
    """
    if s.startswith("<") and s.endswith(">"):
        return s
    if s == "~":
        return s
    if s.startswith("~/"):
        rest = s[2:]
        return "~/" + shlex.quote(rest) if rest else "~/"
    return shlex.quote(s)


def _cmd(*parts: str) -> str:
    return " ".join(parts)


# ---------------------------------------------------------------------------
# AIPOS-F93 件②: 注册码交付文案唯一渲染(向导 Step 5/7、发码 paste_text 与门 enroll_code 动词说明 / next_step、CLI enroll-code
# 打印同此; 原「会话斜杠 enroll 命令」随 lybra-loop 扩展退役, 禁再出现)。项目无关: 只拼产品命令形。
# ---------------------------------------------------------------------------

ENROLL_WORKSPACE_PLACEHOLDER = "<工位目录或治理根>"
ENROLL_DELIVERY_INSTRUCTION = (
    "把注册码交给接收方, 由其在任一 shell 执行下面这条(--workspace = 其工位目录; 顾问凭据落治理根; "
    "完整接入步骤见 lybra onboarding guide):"
)
ENROLL_NEXT_STEP = (
    "接收方执行 paste_text 那条 lybra roles enroll 命令(替换 --workspace 占位)→ 产品完成兑换/落盘/连通验证(--verify)"
)


def render_enroll_command(code: str, workspace: str = ENROLL_WORKSPACE_PLACEHOLDER, *extra: str) -> str:
    """注册码兑换命令的唯一渲染: `lybra roles enroll --code <码> --workspace <工位或治理根> [extra…] --verify`。
    workspace 由调用方按需 shell 引用(向导传已引用路径; 发码侧传占位, 由接收方替换)。"""
    return _cmd("lybra", "roles", "enroll", "--code", code, "--workspace", workspace, *extra, "--verify")


def enroll_delivery(code: str) -> dict[str, str]:
    """发码后的交付文案(门 lybra_enroll_code_confirm / lybra_roles_enroll_code 与 CLI roles enroll-code 共用一份):
    paste_text = 兑换命令(工位占位), paste_instruction = 说明 + 命令, next_step。"""
    paste_text = render_enroll_command(code)
    return {
        "paste_text": paste_text,
        "paste_instruction": f"{ENROLL_DELIVERY_INSTRUCTION}\n{paste_text}",
        "next_step": ENROLL_NEXT_STEP,
    }


# ---------------------------------------------------------------------------
# 推导(全部经既有单源)
# ---------------------------------------------------------------------------

def _owner_workspace(home: Path, explicit: str | None) -> str:
    """Owner 凭据所在的门工作区(中央凭据库 connection.json 所在): 显式 > home 根 + 活动项目(既有 resolve_active_project 梯)。
    解析不到 = 占位(guide 照常生成, 由 Owner 替换)。"""
    if explicit:
        return str(Path(explicit).expanduser())
    from tools.aipos_cli.workspace_config import resolve_active_project

    try:
        return str(home / resolve_active_project(home))
    except (ValueError, FileNotFoundError, OSError):
        return OWNER_WORKSPACE_PLACEHOLDER


def _gate_url(explicit: str | None, owner_connection: str) -> tuple[str, str]:
    """门地址: 显式 > LYBRA_GATE_URL > Owner 中央凭据库 connection.json 的 mcp.rpc_url(非秘密字段) > 声明缺省端口。"""
    if explicit:
        return explicit, "显式 --gate-url"
    env = os.environ.get("LYBRA_GATE_URL", "").strip()
    if env:
        return env, "环境变量 LYBRA_GATE_URL"
    if not owner_connection.startswith("<"):
        try:
            conn = json.loads(Path(owner_connection).read_text(encoding="utf-8"))
            rpc = str(((conn.get("mcp") or {}).get("rpc_url")) or "").strip()
            if rpc:
                return (rpc[:-len("/mcp")] if rpc.endswith("/mcp") else rpc), f"{owner_connection} mcp.rpc_url"
        except (OSError, json.JSONDecodeError):
            pass
    from tools.aipos_cli.workspace_config import DEFAULT_MCP_HOST, DEFAULT_MCP_PORT

    return f"http://{DEFAULT_MCP_HOST}:{DEFAULT_MCP_PORT}", "config.schema 缺省"


def _instances(project: str, host: str) -> dict[str, str]:
    from tools.aipos_cli.naming_profile import _registry_prefix_mapping, default_instance_name

    prefixes = _registry_prefix_mapping()
    return {role: default_instance_name(prefixes[role], project=project, host=host) for role in ("advisor", "executor", "auditor")}


# ---------------------------------------------------------------------------
# guide 生成
# ---------------------------------------------------------------------------

def generate_onboarding_guide(
    project_name: str,
    *,
    home_root: str | None = None,
    gate_url: str | None = None,
    code_repo: str | None = None,
    actor: str | None = None,
    workspace_dir: str | None = None,
    repos: list[str] | None = None,
    default_repo: str | None = None,
    advisor_dir: str | None = None,
    auditor_dir: str | None = None,
    host_segment: str | None = None,
    owner_workspace: str | None = None,
    owner_connection_json: str | None = None,
    envelope_days: int = 30,
    max_tasks: int = 50,
) -> dict[str, Any]:
    """生成从 0 接新项目全流程的分步指南(结构化: steps[] + metadata)。

    每步: step_number, title, actor(owner|advisor|workstation), owner_action(Owner 一次性动作序号或 None), command/commands,
    purpose, check, on_fail, creates。参数:
      - workspace_dir: 执行工位目录(兼容旧参数名; 缺省 ~/<项目>-executor); auditor_dir: 审计工位(缺省 ~/<项目>-auditor)
      - advisor_dir: 顾问 Claude Code 会话目录(顾问技能交付落点基准; 缺省 ~/<项目>)
      - repos: ["<仓名>=<绝对路径>", ...](缺省: code_repo 给了 = 单仓 <项目>=<code_repo>; 都缺 = 占位)
      - owner_workspace / owner_connection_json: Owner 凭据所在(门的中央凭据库; 缺省 home 根/活动项目)
    """
    from tools.aipos_cli.workspace_config import resolve_home_root_with_source

    home, home_source = resolve_home_root_with_source(explicit_root=home_root)
    gov = home / project_name
    gov_s = str(gov)
    host = (host_segment or socket.gethostname().split(".")[0]).strip()
    inst = _instances(project_name, host)
    owner_ws = _owner_workspace(home, owner_workspace)
    owner_conn = str(Path(owner_connection_json).expanduser()) if owner_connection_json else (
        f"{owner_ws}/.lybra/connection.json" if not owner_ws.startswith("<") else "<OWNER_WORKSPACE>/.lybra/connection.json")
    gate, gate_source = _gate_url(gate_url, owner_conn)
    advisor_ws = advisor_dir or f"~/{project_name}"
    exec_ws = workspace_dir or f"~/{project_name}-executor"
    audit_ws = auditor_dir or f"~/{project_name}-auditor"
    _actor = actor or inst["advisor"]
    now = datetime.now(timezone.utc)
    expires = (now + timedelta(days=int(envelope_days))).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    policies = {
        "driver": f"pol_{project_name}_loop_1",
        "executor": f"pol_{project_name}_exec_1",
        "auditor": f"pol_{project_name}_audit_1",
    }
    repo_items = list(repos or ([f"{project_name}={code_repo}"] if code_repo else [REPO_PLACEHOLDER]))
    repo_default = default_repo or (repo_items[0].split("=", 1)[0] if len(repo_items) > 1 and "=" in repo_items[0] and not repo_items[0].startswith("<") else None)

    steps: list[dict[str, Any]] = []
    gq, hq = _shell_quote(gov_s), _shell_path(str(home))

    # ── Step 1: 建项目(顾问) ─────────────────────────────────────────
    steps.append({
        "step_number": 1,
        "actor": "advisor",
        "owner_action": None,
        "title": "建项目(home 根下的治理根 + project.json + 首份阶段快照)",
        "command": _cmd("lybra", "project", "new", _shell_quote(project_name), "--home-root", hq, "--actor", _shell_quote(_actor)),
        "purpose": (
            f"在 home 根 {home}(来源: {home_source})下建 {project_name} 的治理根 {gov_s}: 队列 / 记录 / 治理文档 / project.json, "
            "并经 governance add stage 写首份阶段快照「项目创建」。门只扫描 home 根发现项目; 要放别处只能改 home 根本身"
            "(--home-root > LYBRA_HOME_ROOT > ~/.lybra/config.json home_root > 缺省 ~/.lybra/projects), 不登记 home 根外的治理根"
        ),
        "check": "输出 'Created project root:' 与 'stage snapshot:' 行; 验证: lybra project list --home-root <home 根> 出现项目名",
        "on_fail": {
            "PROJECT_EXISTS": "项目根已存在且非空; lybra project list 确认后跳到 Step 2",
            "PROJECT_NAME_EMPTY": "项目名不能为空; 给一个非空项目名重跑本步",
            "home 根不对": "home 根只经优先级梯解析(见目的); 换 --home-root 重跑本步",
        },
        "creates": f"{gov_s}/(project.json, 5_tasks/, governance/, stage_archive/<日期>_项目创建.md)",
    })

    # ── Step 2: 声明产品仓(顾问) ─────────────────────────────────────
    repo_parts = ["lybra", "project", "set-repos", _shell_quote(project_name), "--home-root", hq]
    for item in repo_items:
        repo_parts += ["--repo", item if item.startswith("<") else _shell_quote(item)]
    if repo_default:
        repo_parts += ["--default", _shell_quote(repo_default)]
    steps.append({
        "step_number": 2,
        "actor": "advisor",
        "owner_action": None,
        "title": "声明产品仓(project.json repos / code_repo)",
        "command": _cmd(*repo_parts),
        "purpose": "把产品仓清单写进 project.json(repos {default, items} + code_repo 别名), 经 config.schema project_json.repos 声明校验; 卡 lane.repo 写仓名, 建工作树 / finalize 按此解析",
        "check": "输出 'Declared repos in <project.json>' 与每个仓一行; 多于一个仓须 --default",
        "on_fail": {
            "REPOS_CONFLICT": "路径须为绝对路径、default 须在仓名内; 改参数重跑(project.json 未改动)",
            "PROJECT_NOT_ESTABLISHED": "Step 1 未完成; 先跑 Step 1",
        },
        "creates": f"{gov_s}/project.json#repos",
    })

    # ── Step 3: Owner ① 签发顾问注册码 ───────────────────────────────
    owner_roles = ["lybra", "roles", "--workspace-root", _shell_path(owner_ws), "--connection-json", _shell_path(owner_conn)]
    step3 = _cmd(*owner_roles, "enroll-code", "--role", "advisor", "--instance", _shell_quote(inst["advisor"]),
                 "--governance-root", _shell_quote(project_name), "--token-role", "owner", "--gate-url", _shell_quote(gate),
                 "--ttl", "86400", "--owner-authorization-ref", _shell_quote(f"onboarding-{project_name}"),
                 "--reason", _shell_quote(f"Onboarding {project_name} advisor"))
    steps.append({
        "step_number": 3,
        "actor": "owner",
        "owner_action": 1,
        "title": "【Owner 动作 ①】签发顾问注册码(凭据项目范围 = 新项目)",
        "command": step3,
        "purpose": (
            f"Owner 用门的中央凭据库({owner_conn})里的 Owner 凭据, 为 {project_name} 签发一次性顾问注册码: 码内嵌门地址 {gate}"
            f"(来源: {gate_source})与治理根, 兑换出的顾问凭据 projects=[{project_name}]。把输出的 LYBRAENROLL1.* 码交给新顾问(Step 5)"
        ),
        "check": "输出 'Generated SELF-CONTAINED enrollment code' 与 Code ID / Governance root 行; 码只显示一次",
        "on_fail": {
            "UNKNOWN_GOVERNANCE_ROOT": "门在 home 根下找不到该项目(Step 1 未完成或门的 home 根不同); 确认门与 Step 1 用同一 home 根",
            "no usable role token": "--connection-json 须指向持 Owner 凭据的中央凭据库(门 serve 所读的 connection.json)",
            "gate call failed": "门未运行或地址不通: lybra serve status; 此为门侧故障, 报 Owner",
        },
        "creates": "一个一次性顾问注册码(24h 有效)",
    })

    # ── Step 4: Owner ② 一条命令签三张信封 ───────────────────────────
    step4 = _cmd(
        "lybra", "envelope", "mint", "--confirm",
        "--workspace-root", gq, "--connection-json", _shell_path(owner_conn),
        "--policy-id", policies["driver"], "--agent-or-role", _shell_quote(inst["advisor"]),
        "--policy-id", policies["executor"], "--agent-or-role", _shell_quote(inst["executor"]),
        "--policy-id", policies["auditor"], "--agent-or-role", _shell_quote(inst["auditor"]),
        "--max-tasks", str(int(max_tasks)), "--task-mode", "code", "--expires-at", _shell_quote(expires),
        "--decision-summary", _shell_quote(f"Onboarding {project_name}: driver/executor/auditor autonomy envelopes"),
        "--actor", "owner",
    )
    steps.append({
        "step_number": 4,
        "actor": "owner",
        "owner_action": 2,
        "title": "【Owner 动作 ②】签三张信封(驱动方 / 执行 / 审计)",
        "command": step4,
        "purpose": (
            f"一条命令经门 owner_decision_record envelope 路径签三张 PreAuthorized 信封并真实落盘到 {gov_s}/5_tasks/policies/: "
            f"{policies['driver']} 覆盖驱动方 {inst['advisor']}(lybra loop 一阶段认领 / 交回 / 派审 / 裁决 / 结案), "
            f"{policies['executor']} 覆盖 {inst['executor']}, {policies['auditor']} 覆盖 {inst['auditor']}(审计卡按被审卡判定)"
        ),
        "check": "输出三行 'signed <policy_id> covers <实例>' 与各自 'wrote 5_tasks/policies/<id>.md' / 'wrote 5_tasks/records/owner_decisions/...'(以门生记录为准)",
        "on_fail": {
            "PROJECT_SCOPE_DENIED": "所用凭据的 projects 不含本项目; 用 Owner 凭据(roles.schema owner project_scope=cross_project)",
            "OWNER_CONFIRMATION_REQUIRED / scope denied": "签信封须 Owner 凭据(owner_confirm); --connection-json 指向中央凭据库",
            "already exists": "该 policy_id 已签过(看输出已签几张); 未签的换新 policy_id 重跑",
        },
        "creates": f"{gov_s}/5_tasks/policies/{{{policies['driver']},{policies['executor']},{policies['auditor']}}}.md + 三份 owner_decisions 记录",
    })

    # ── Step 5: 顾问 enroll(治理根)+ 技能交付 ────────────────────────
    sync_adv = _cmd("lybra", "sync", "--harness-root", gq, "--workspace-root", gq)
    step5 = [
        "# 顾问凭 Step 3 的码 enroll: 凭据落治理根 .lybra/(loop 按治理根取驱动方凭据), 顾问技能交付到会话目录 .claude/skills/",
        render_enroll_command(ADVISOR_CODE, gq, "--harness", ADVISOR_HARNESS, "--harness-dir", _shell_path(advisor_ws)),
        "# 稳态复核: plan 为空 = 技能已齐",
        f"{sync_adv} --dry-run",
    ]
    steps.append({
        "step_number": 5,
        "actor": "advisor",
        "owner_action": None,
        "title": "顾问 enroll 到治理根 + 顾问技能交付到 Claude Code 会话目录",
        "command": "\n".join(step5),
        "purpose": (
            f"新顾问(在会话目录 {advisor_ws} 起的 Claude Code)凭码兑换 advisor 凭据, 落 {gov_s}/.lybra/(connection.json + role); "
            f"产品按 distribution 声明(advisor-skills, harness={ADVISOR_HARNESS})把顾问技能经同一分发引擎交付到 {advisor_ws}/.claude/skills/<技能名>/"
        ),
        "check": "enroll 输出 '✓ Enrollment successful' 与 '✓ claude-code 件已交付: N 个文件'; sync --dry-run 输出 'up-to-date: 0 file(s) to fetch/render'",
        "on_fail": {
            "code 过期/已用": "请 Owner 重跑 Step 3",
            "--harness-dir 须为已存在的绝对目录": "先建会话目录(或改 --harness-dir 为实际 Claude Code 会话目录)",
            "件交付失败": "凭据已落, 重跑 lybra sync --harness-root <治理根>(勿重跑 enroll)",
        },
        "creates": f"{gov_s}/.lybra/connection.json, {gov_s}/.lybra/role, {advisor_ws}/.claude/skills/",
        "note": "顾问会话目录与治理根可不同: 本 guide 每条命令都显式带治理根, 不依赖 cwd; 技能交付后在会话目录重启 Claude Code 会话即加载",
    })

    # ── Step 6: 顾问发工位注册码 ─────────────────────────────────────
    adv_roles = ["lybra", "roles", "--workspace-root", gq, "enroll-code"]
    step6 = [
        _cmd(*adv_roles, "--role", role, "--instance", _shell_quote(inst[role]), "--governance-root", _shell_quote(project_name),
             "--gate-url", _shell_quote(gate), "--ttl", "86400", "--owner-authorization-ref", policies[role],
             "--reason", _shell_quote(f"Onboarding {project_name} {role}"))
        for role in ("executor", "auditor")
    ]
    steps.append({
        "step_number": 6,
        "actor": "advisor",
        "owner_action": None,
        "title": "顾问为执行 / 审计工位发注册码",
        "commands": step6,
        "command": step6[0],
        "purpose": f"顾问用自己的凭据(治理根 .lybra/connection.json, projects=[{project_name}])为两个工位各发一个一次性注册码; 授权依据 = Step 4 的信封",
        "check": "每条输出 'Generated SELF-CONTAINED enrollment code'; 两个码分别用于 Step 7",
        "on_fail": {
            "no usable role token": "Step 5 未完成(治理根无顾问凭据); 先跑 Step 5",
            "UNKNOWN_GOVERNANCE_ROOT": "门找不到项目; 核对门与 Step 1 同一 home 根",
        },
        "creates": "两个一次性工位注册码",
    })

    # ── Step 7: 工位 enroll + sync ──────────────────────────────────
    step7: list[str] = []
    for label, ws, code in (("执行", exec_ws, EXECUTOR_CODE), ("审计", audit_ws, AUDITOR_CODE)):
        wq = _shell_path(ws)
        sync_ws = f"lybra sync --harness-root {wq} --workspace-root {gq}"
        step7 += [
            f"# {label}工位: enroll(落 .lybra + .pi 接线) → 按分发声明落齐工位件 → 稳态复核",
            render_enroll_command(code, wq),
            sync_ws,
            f"{sync_ws} --dry-run",
        ]
    steps.append({
        "step_number": 7,
        "actor": "advisor",
        "owner_action": None,
        "title": "执行 / 审计工位 enroll + sync 分发 + 稳态复核",
        "command": "\n".join(step7),
        "purpose": f"在工位 {exec_ws} / {audit_ws} 兑换凭据(owner_policy_ref 由 Step 4 信封推导), 按分发声明落齐技能 / 扩展 / 章程, 复核稳态; 工位只同步分发, 不敲门动词",
        "check": (
            "enroll 输出 '✓ Enrollment successful' 与 '✓ owner_policy_ref 已推导'; 工位 .lybra/connection.json 含 lybra_bin 且 "
            "workspace_root == governance_root; sync 工位行 synced(首个工位会补挂 .pi/extensions/go.ts); "
            "--dry-run 输出 'up-to-date: 0 file(s) to fetch/render'、无 would-prune 行"
        ),
        "on_fail": {
            "code 过期/已用": "请顾问重跑 Step 6 为该工位发新码",
            "401 Unauthorized": "注册码无效或过期(或发码门 ≠ 本门); 重跑 Step 6 发新码后重试 enroll --verify",
            "推导 owner_policy_ref 失败 / role 缺 owner_policy_ref": "Step 4 信封未落或 agent_or_role 与工位实例不符; 核对 Step 4 输出",
            ".pi/ 接线缺失": "enroll 落 .pi 接线, sync 补挂声明内缺失挂载; 仍缺 = 报 bug(附 sync --json 输出)",
            "connection.json 缺 lybra_bin": "enroll 按运行中的 lybra 部署位写入; 用部署的 lybra 命令重跑 enroll",
            "workspace_root 写成 harness root": "workspace_root 须等于码内治理根; 重跑 enroll 校正",
            "sync 把工位记为 skipped": f"--workspace-root 须为本项目治理根 {gov_s}",
            "--dry-run 的 plan 或 prune 非空": "再跑一次不带 --dry-run 的 sync 后复核; 仍非空 = 报 bug, 附 --dry-run --json 输出",
        },
        "creates": f"{exec_ws}/.lybra + .pi + AGENTS.md, {audit_ws}/.lybra + .pi + AGENTS.md, 工位父根 _distributed/",
    })

    # ── Step 8: 工位自检 → 起 pi → /go ───────────────────────────────
    step8 = [
        "# 工位自检(只读, 缺项逐项点名; 可在 pi 外任一终端跑)",
        _cmd("lybra", "onboarding", "check", _shell_quote(project_name), "--step", "8", "--home-root", hq,
             "--workspace-dir", _shell_path(exec_ws)),
        "# 在工位目录起 pi(Pi 编码代理), pi 内无参 /go 开工(只查本实例已认领的卡; 认领由驱动方经产品完成)",
        f"cd {_shell_path(exec_ws)}",
        "pi",
        "/go",
    ]
    steps.append({
        "step_number": 8,
        "actor": "workstation",
        "owner_action": None,
        "title": "工位自检 → 起 pi → /go(每张卡 Owner 在工位敲 /go, 非一次性动作)",
        "command": "\n".join(step8),
        "purpose": "验证工位可启动最小集完整, 在工位目录起 pi; 首卡由顾问发卡、驱动方经 lybra loop 认领并建工作树后 /go 开工(审计工位同法)",
        "check": "onboarding check 输出 '✓ Step 8 prerequisites satisfied'; /go 发出开工提示或提示'无已认领卡'(驱动方尚未认领 = 正常)",
        "on_fail": {
            "缺项报错": "按输出的缺项逐项修复(lybra_bin 悬空 → 重跑 Step 7 enroll; owner_policy_ref 缺失 → 核对 Step 4 信封)",
            "/go 不是已知命令": "go 扩展未落到工位; 回 Step 7 重跑 sync 并用 --dry-run 复核稳态",
            "/go 报 工作树尚未建立": "认领与建树由驱动方完成, 工位不自领; 等驱动方 lybra loop 认领后再 /go",
        },
        "creates": "运行中的 pi 会话 + 开工提示",
    })

    # ── Step 9: 首卡(顾问, 产品命令) ─────────────────────────────────
    step9 = [
        "# 发卡: 卡稿 JSON 写 project / assigned_to(执行实例) / lane.repo(Step 2 仓名) / audit 等; 校验后发布到 pending",
        _cmd("lybra", "--workspace-root", gq, "draft", "create", "--from-json", DRAFT_JSON_PLACEHOLDER),
        _cmd("lybra", "--workspace-root", gq, "draft", "publish", "--path", DRAFT_PATH_PLACEHOLDER),
        "# 推进: 驱动方信封一段式认领(建工作树)→ 等工位交回 → 派审 → 等审计报告 → 裁决 → finalize → 结案",
        _cmd("lybra", "loop", "--task-id", TASK_ID_PLACEHOLDER, "--workspace-root", gq, "--envelope", policies["driver"]),
    ]
    steps.append({
        "step_number": 9,
        "actor": "advisor",
        "owner_action": None,
        "title": "首卡: 发卡 → lybra loop 推进到结案",
        "command": "\n".join(step9),
        "purpose": f"顾问只用产品命令推进: lybra loop 以信封 {policies['driver']} 驱动整张卡, 工位只在 /go 后写产物(RETURN / 审计报告)",
        "check": "lybra loop 退出码 0 且输出 'done: <卡ID> 已结案(closure 记录存在)'",
        "on_fail": {
            "exit 5(无信封)": "Step 4 未落或信封不覆盖驱动方 / 本卡; 按输出的申领出口请 Owner 签",
            "exit 3(等待超时)": "工位尚未交回产物; 工位 /go 开工后重跑同一条 lybra loop",
            "exit 2(门拒)": "照输出原文处理; loop 已回读门生记录, 不会重复执行已落步骤",
        },
        "creates": "卡在 completed 队列 + claim/return/dispatch/verdict/finalization/closure 记录",
    })

    owner_steps = [s["step_number"] for s in steps if s.get("owner_action")]
    return {
        "project_name": project_name,
        "home_root": str(home),
        "home_root_source": home_source,
        "governance_root": gov_s,
        "gate_url": gate,
        "gate_url_source": gate_source,
        "owner_workspace": owner_ws,
        "owner_connection_json": owner_conn,
        "instances": inst,
        "policies": policies,
        "advisor_dir": advisor_ws,
        "workstations": {"executor": exec_ws, "auditor": audit_ws},
        "generated_at": now.isoformat(),
        "total_steps": len(steps),
        "owner_actions": owner_steps,
        "steps": steps,
        "summary": (
            f"从 0 接新项目 {project_name} 全流程({len(steps)} 步, 零手工编辑; Owner 一次性动作 = Step {' / '.join(map(str, owner_steps))}):\n"
            + "\n".join(f"  Step {s['step_number']}: {s['title']}" for s in steps)
        ),
    }


def format_guide_text(guide: dict[str, Any]) -> str:
    """将结构化 guide 格式化为可读文本(终端输出用)。"""
    lines: list[str] = []
    lines.append(f"═══ 从 0 接新项目: {guide['project_name']} ═══")
    lines.append(f"home 根: {guide['home_root']}(来源: {guide.get('home_root_source', '?')})")
    lines.append(f"治理根: {guide.get('governance_root', '')}")
    lines.append(f"门地址: {guide['gate_url']}(来源: {guide.get('gate_url_source', '?')})")
    lines.append(f"Owner 凭据: {guide.get('owner_connection_json', '')}")
    lines.append(f"生成时间: {guide['generated_at']}")
    owner = guide.get("owner_actions") or []
    lines.append(f"共 {guide['total_steps']} 步, 全程零手工编辑; Owner 一次性动作 {len(owner)} 条: " + ", ".join(f"Step {n}" for n in owner))
    lines.append("")

    for step in guide["steps"]:
        who = {"owner": "Owner", "advisor": "顾问", "workstation": "工位"}.get(step.get("actor", ""), step.get("actor", ""))
        lines.append(f"── Step {step['step_number']}: {step['title']} [执行方: {who}] ──")
        lines.append(f"目的: {step['purpose']}")
        lines.append("")

        if "commands" in step and len(step["commands"]) > 1:
            lines.append("命令(逐条执行):")
            for i, cmd in enumerate(step["commands"], 1):
                lines.append(f"  {i}. {cmd}")
        else:
            lines.append("命令:")
            for cmd_line in step["command"].split("\n"):
                lines.append(f"  {cmd_line}")

        lines.append("")
        lines.append(f"验证: {step['check']}")

        if step.get("on_fail"):
            lines.append("失败出口:")
            for err, fix in step["on_fail"].items():
                lines.append(f"  [{err}] → {fix}")

        if step.get("note"):
            lines.append(f"注: {step['note']}")

        lines.append(f"产物: {step['creates']}")
        lines.append("")

    lines.append("═══ 完成: 从项目注册到首卡结案, 零手工编辑 ═══")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# 前置自检(lybra onboarding check; 步号与上面 guide 同一编号)
# ---------------------------------------------------------------------------

def validate_step_prerequisites(
    step_number: int,
    *,
    project_name: str,
    home_root: str | None = None,
    workspace_dir: str | None = None,
) -> dict[str, Any]:
    """验证进入某一步之前的产物是否齐(负夹具 / 运行时前置检查)。步号 = generate_onboarding_guide 的步号。

    ≥1 项目根 + project.json; ≥3 产品仓声明; ≥5 信封; ≥6 顾问凭据(治理根 .lybra/connection.json);
    ≥8 工位 connection.json(lybra_bin / workspace_root==governance_root)+ .pi 接线 + role#owner_policy_ref。
    返回 {ok: bool, missing: [str], guidance: [str]}。
    """
    from tools.aipos_cli.workspace_config import resolve_home_root

    project_root = resolve_home_root(explicit_root=home_root) / project_name
    ws = Path(workspace_dir or f"~/{project_name}-executor").expanduser()

    missing: list[str] = []
    guidance: list[str] = []

    if step_number >= 1:
        if not project_root.is_dir():
            missing.append("project_root")
            guidance.append(f"Step 1 未完成: 项目目录不存在 {project_root}; 先跑 lybra project new")
        elif not (project_root / "project.json").is_file():
            missing.append("project.json")
            guidance.append("project.json 缺失; 重跑 Step 1")

    if step_number >= 3 and (project_root / "project.json").is_file():
        from tools.aipos_cli.workspace_config import CardRepoUnresolved, project_repos

        try:
            repos = project_repos(project_root)
            if not repos["declared"] and repos["code_repo"] is None:
                missing.append("repos")
                guidance.append("Step 2 未完成: project.json 无产品仓声明; 跑 lybra project set-repos")
        except CardRepoUnresolved as exc:
            missing.append("repos")
            guidance.append(f"产品仓声明不合规: {exc}; 重跑 Step 2")

    if step_number >= 5:
        policies_dir = project_root / "5_tasks" / "policies"
        if not policies_dir.is_dir() or not list(policies_dir.glob("pol_*.md")):
            missing.append("envelopes")
            guidance.append("Step 4 未完成: 无信封文件; Owner 跑 Step 4 的 lybra envelope mint --confirm")

    if step_number >= 6 and project_root.is_dir():
        if not (project_root / ".lybra" / "connection.json").is_file():
            missing.append("advisor_credential")
            guidance.append("Step 5 未完成: 治理根无 .lybra/connection.json(顾问凭据); 跑 Step 5 的 lybra roles enroll")

    if step_number >= 8:
        conn_json = ws / ".lybra" / "connection.json"
        if not conn_json.is_file():
            missing.append("connection.json")
            guidance.append(f"Step 7 未完成: {conn_json} 不存在; 跑 lybra roles enroll")
        else:
            try:
                data = json.loads(conn_json.read_text(encoding="utf-8"))
                if not data.get("lybra_bin"):
                    missing.append("lybra_bin")
                    guidance.append("connection.json 缺 lybra_bin; 重跑 Step 7 enroll")
                ws_root = data.get("workspace_root", "")
                gov_root = data.get("governance_root", "")
                if ws_root and gov_root and Path(ws_root).resolve() != Path(gov_root).resolve():
                    missing.append("workspace_root_mismatch")
                    guidance.append("workspace_root != governance_root; 重跑 Step 7 enroll")
            except (OSError, json.JSONDecodeError):
                missing.append("connection.json_invalid")
                guidance.append("connection.json 格式错误; 重跑 Step 7")
        if not (ws / ".pi" / "settings.json").is_file():
            missing.append(".pi/settings.json")
            guidance.append(".pi 接线缺失; Step 7 enroll 应自动落, 重跑 Step 7")
        role_file = ws / ".lybra" / "role"
        if role_file.is_file():
            try:
                role_data = json.loads(role_file.read_text(encoding="utf-8"))
                if not role_data.get("owner_policy_ref"):
                    missing.append("owner_policy_ref")
                    guidance.append("role 文件缺 owner_policy_ref; 核对 Step 4 信封是否覆盖本工位实例后重跑 lybra sync")
            except (OSError, json.JSONDecodeError):
                missing.append("role_invalid")
                guidance.append("role 文件格式错误; 重跑 Step 7")

    return {
        "ok": not missing,
        "missing": missing,
        "guidance": guidance,
    }
