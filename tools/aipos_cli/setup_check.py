"""AIPOS-F263 件①: `lybra setup check [--json]` 安装自检。

声明唯一出处 config.schema setup_check(检查项序 / 标签 / 版本要求 / pi 安装指引 / 门探测 / 出口); 本模块:
  - run_setup_check: 结构化结果(看板 P3 设置自检页直接复用: AIPOS-F267 board_pages.setup_check_entry 缓存后渲染)
  - board_cache_seconds: 看板复用结果的秒数(AIPOS-F267, 声明 setup_check.board)
  - render_setup_check: 人读唯一出口
  - preflight_step: onboarding 向导「前置检查」摘要(✗ 项阻断后续并给出口)
  - tool_executable: 检查项的可执行名唯一读取口(pi = enums harness launch.argv[0]; node / git = requirements.<id>.argv[0];
                     AIPOS-F268 gate_resident 单元 PATH 对照同读)
只读: 不写文件、不连门写、不读 token / 密钥文件内容(门可达只对门基址发一次 GET 声明路径, 永不探 /mcp)。
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

from tools.aipos_cli._cli_entry_guard import check_direct_invocation

check_direct_invocation(__name__)

_DECL_KEYS = ("marks", "items", "labels", "probe_timeout_seconds", "requirements", "pi", "gate", "resident_exit",
              "resident_path_exit", "resident_path_absent")
#: AIPOS-F267: 看板「设置自检」页复用结果的秒数缺省(config.schema setup_check.board.cache_seconds 缺省时用, 与声明同值)
BOARD_CACHE_DEFAULT_SECONDS = 60


class SetupCheckError(ValueError):
    """声明缺 / 形坏(fail-closed)。"""


def setup_check_declaration() -> dict[str, Any]:
    """config.schema setup_check(唯一读取口)。缺键 = SetupCheckError。"""
    from tools.schema_loader import code_repo_schema_root, load_schema

    decl = load_schema("config", code_repo_schema_root()).get("setup_check")
    if not isinstance(decl, dict) or any(k not in decl for k in _DECL_KEYS):
        raise SetupCheckError(f"config.schema.json setup_check 未声明齐 {list(_DECL_KEYS)}")
    unknown = [i for i in decl["items"] if i not in decl["labels"]]
    if unknown:
        raise SetupCheckError(f"config.schema.json setup_check.items 无标签: {unknown}")
    return decl


def board_cache_seconds(decl: dict[str, Any] | None = None) -> int:
    """AIPOS-F267: 看板复用 run_setup_check 结果的秒数 = setup_check.board.cache_seconds(缺键 = BOARD_CACHE_DEFAULT_SECONDS;
    写了但非正整数 = SetupCheckError, fail-closed)。"""
    board = (decl if decl is not None else setup_check_declaration()).get("board")
    if board is None or "cache_seconds" not in board:
        return BOARD_CACHE_DEFAULT_SECONDS
    value = board["cache_seconds"] if isinstance(board, dict) else None
    if not isinstance(value, int) or isinstance(value, bool) or value < 1:
        raise SetupCheckError(f"config.schema.json setup_check.board.cache_seconds 须为正整数, 得到 {value!r}")
    return value


def parse_version(text: str) -> tuple[int, ...] | None:
    """版本串(v22.3.1 / git version 2.43.0 / 3.10 / 0.85.1)首个数字段 → 整数元组; 无 = None。"""
    m = re.search(r"(\d+)(?:\.(\d+))?(?:\.(\d+))?", str(text or ""))
    return tuple(int(g) for g in m.groups() if g is not None) if m else None


def _item(decl: dict[str, Any], item_id: str, status: str, detail: str, exit_line: str | None = None) -> dict[str, Any]:
    return {"id": item_id, "label": decl["labels"][item_id], "status": status, "mark": decl["marks"][status],
            "detail": detail, "exit": exit_line if status != "ok" else None}


def _version_of(argv: list[str], timeout: float) -> tuple[str | None, str]:
    """跑 [可执行, *参数] 取版本输出首行; 起不来 / 超时 / 非零退出 = (None, 原因)。"""
    exe = shutil.which(argv[0])
    if not exe:
        return None, f"{argv[0]} 不在 PATH"
    try:
        proc = subprocess.run([exe, *argv[1:]], stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        return None, f"`{' '.join(argv)}` 超时({timeout:g}s)"
    except OSError as exc:
        return None, f"`{' '.join(argv)}` 起不来: {type(exc).__name__}: {exc}"
    out = ((proc.stdout or "").strip() or (proc.stderr or "").strip()).splitlines()
    if proc.returncode != 0:
        return None, f"`{' '.join(argv)}` exit {proc.returncode}: {out[-1][:200] if out else '(无输出)'}"
    return (out[0].strip() if out else ""), exe


def _check_python(decl: dict[str, Any]) -> dict[str, Any]:
    req = decl["requirements"]["python"]
    have = tuple(sys.version_info[:3])
    shown = ".".join(map(str, have))
    want = parse_version(req["min"]) or ()
    exit_line = str(req["exit"]).replace("{min}", str(req["min"]))
    if have < want:
        return _item(decl, "python", "fail", f"{shown}({sys.executable})低于要求 ≥{req['min']}", exit_line)
    return _item(decl, "python", "ok", f"{shown}({sys.executable}; 要求 ≥{req['min']})")


def _check_versioned(decl: dict[str, Any], item_id: str, timeout: float) -> dict[str, Any]:
    req = decl["requirements"][item_id]
    exit_line = str(req["exit"]).replace("{min}", str(req.get("min", "")))
    out, where = _version_of([str(a) for a in req["argv"]], timeout)
    if out is None:
        return _item(decl, item_id, "fail", where, exit_line)
    have = parse_version(out)
    if req.get("min"):
        want = parse_version(req["min"]) or ()
        if have is None or have < want:
            return _item(decl, item_id, "fail", f"{out}({where})低于要求 ≥{req['min']}", exit_line)
        return _item(decl, item_id, "ok", f"{out}({where}; 要求 ≥{req['min']})")
    return _item(decl, item_id, "ok", f"{out}({where})")


def tool_executable(decl: dict[str, Any], item_id: str) -> str:
    """检查项的可执行名(唯一读取口): pi = enums.schema harness.<pi.harness>.launch.argv[0](与 loop 拉起同一声明);
    requirements 里带 argv 的项(node / git)= argv[0]。未声明 = SetupCheckError(fail-closed)。"""
    if item_id == "pi":
        from tools.aipos_cli.autonomy_policy import launchable_harnesses

        harness = str(decl["pi"]["harness"])
        template = launchable_harnesses().get(harness) or {}
        argv = template.get("argv") if isinstance(template.get("argv"), list) else []
        if not argv:
            raise SetupCheckError(f"enums.schema harness {harness} launch.argv 未声明, 无从核 pi")
        return str(argv[0])
    req = decl["requirements"].get(item_id)
    argv = req.get("argv") if isinstance(req, dict) else None
    if not isinstance(argv, list) or not argv:
        raise SetupCheckError(f"config.schema setup_check.requirements.{item_id}.argv 未声明, 无从得出可执行名")
    return str(argv[0])


def _check_pi(decl: dict[str, Any], timeout: float) -> list[dict[str, Any]]:
    from tools.aipos_cli.harness_launch import harness_model_catalog

    spec = decl["pi"]
    exe = tool_executable(decl, "pi")
    install = spec["install"]
    missing_exit = str(spec["exit_missing"]).replace("{command}", str(install["command"])).replace("{url}", str(install["url"]))
    version, where = _version_of([exe, *[str(a) for a in spec["version_args"]]], timeout)
    if version is None:
        return [_item(decl, "pi", "fail", where, missing_exit),
                _item(decl, "pi_models", "warn", str(spec["skipped_without_pi"]), str(spec["skipped_exit"]))]
    pi_item = _item(decl, "pi", "ok", f"{version}({where})")
    catalog = harness_model_catalog(str(spec["harness"]))
    if catalog["ok"] and catalog["models"]:
        models = catalog["models"]
        shown = ", ".join(models[:5]) + (f" 等 {len(models)} 个" if len(models) > 5 else "")
        # AIPOS-F272: 完整清单随结果带出(看板「设 pi 模型」候选复用本次结果, 不另起 pi 子进程)
        return [pi_item, {**_item(decl, "pi_models", "ok", f"{len(models)} 个已配置模型: {shown}"), "models": list(models)}]
    detail = catalog["detail"] if not catalog["ok"] else f"`{' '.join(catalog['command'])}` 列出 0 个模型"
    return [pi_item, _item(decl, "pi_models", "fail", detail, str(spec["exit_models"]))]


def _probe_gate(url: str, timeout: float) -> tuple[bool, str]:
    """对门基址 + probe_path 发一次 GET(不带凭据、不经代理); 任何 HTTP 应答 = 可达。"""
    import urllib.error
    import urllib.request

    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(urllib.request.Request(url, method="GET"), timeout=timeout) as resp:
            return True, f"HTTP {resp.status}"
    except urllib.error.HTTPError as exc:
        return True, f"HTTP {exc.code}(门已应答)"
    except (urllib.error.URLError, OSError, ValueError) as exc:
        reason = getattr(exc, "reason", exc)
        return False, f"{type(reason).__name__}: {reason}"


def _check_gate(decl: dict[str, Any], workspace_root: Path | None, gate_url: str | None) -> dict[str, Any]:
    from tools.aipos_cli.confirm_client import GateAddressError, resolve_gate_base_url

    spec = decl["gate"]
    try:
        base = resolve_gate_base_url(workspace_root=workspace_root, explicit_url=gate_url)
    except (GateAddressError, ValueError, OSError) as exc:
        return _item(decl, "gate", "warn", f"门地址推导不出: {exc}", str(spec["exit"]))
    source = "显式" if gate_url else (f"工作区 {workspace_root}" if workspace_root else "环境 / 缺省")
    probe = base.rstrip("/") + str(spec["probe_path"])
    ok, how = _probe_gate(probe, float(spec["timeout_seconds"]))
    if ok:
        return _item(decl, "gate", "ok", f"{base}(地址来源: {source}; GET {spec['probe_path']} → {how})")
    return _item(decl, "gate", "warn", f"{base} 不可达(地址来源: {source}; {how})", str(spec["exit"]))


def _resident_path_problem(decl: dict[str, Any], st: dict[str, Any]) -> tuple[str, str] | None:
    """AIPOS-F268 件②: 已装单元的 PATH(只读解析单元文件, gate_resident.unit_environment)不含 pi 所在目录 = (详情, 出口)。
    pi 在本进程 PATH 里也找不到 = 不对照(pi 项本身已报 ✗); 单元 PATH 已含能找到 pi 的目录 = 无问题。"""
    from tools.aipos_cli.gate_resident import path_contains

    unit_env = st.get("unit_environment")
    exe = tool_executable(decl, "pi")
    found = shutil.which(exe)
    if unit_env is None:
        return (f"单元文件读不出环境, 无从核 PATH 是否含 pi({found or exe})",
                str(decl["resident_path_exit"]).replace("{pi_dir}", os.path.dirname(found) if found else exe)
                .replace("{unit_path_value}", "读不出"))
    unit_path_value = unit_env.get("PATH")
    if unit_path_value and shutil.which(exe, path=unit_path_value):
        return None
    if not found:
        return None
    pi_dir = os.path.dirname(found)
    if unit_path_value and path_contains(unit_path_value, pi_dir):
        return None
    shown = unit_path_value if unit_path_value else str(decl["resident_path_absent"])
    exit_line = str(decl["resident_path_exit"]).replace("{pi_dir}", pi_dir).replace("{unit_path_value}", shown)
    return f"单元 PATH 不含 pi 所在目录 {pi_dir}(单元 PATH: {shown})", exit_line


def _check_resident(decl: dict[str, Any]) -> dict[str, Any]:
    from tools.aipos_cli.gate_resident import resident_status

    st = resident_status()
    if not st.get("supported"):
        return _item(decl, "gate_resident", "warn", str(st.get("detail")), str(decl["resident_exit"]))
    hand = st.get("handwritten") or []
    if st.get("installed"):
        state = "在跑" if st.get("active") else "未在跑"
        owner = "本命令生成" if st.get("managed") else "同名但非本命令生成"
        detail = f"{st['unit_path']}({owner}; {state})"
        exits = [] if st.get("active") else ["单元已装但未在跑: 看 `lybra serve status` 与系统服务日志"]
        path_problem = _resident_path_problem(decl, st)
        if path_problem:
            detail += f"; {path_problem[0]}"
            exits.append(path_problem[1])
        return _item(decl, "gate_resident", "warn" if exits else "ok", detail, "; ".join(exits) or None)
    if hand:
        names = ", ".join(f"{h['name']}({h['path']})" for h in hand)
        return _item(decl, "gate_resident", "ok", f"检测到已有手写常驻单元 {names}; lybra serve install 不接管")
    return _item(decl, "gate_resident", "warn", f"未安装({st['unit_path']} 不存在)", str(decl["resident_exit"]))


def run_setup_check(*, workspace_root: Path | None = None, gate_url: str | None = None) -> dict[str, Any]:
    """安装自检唯一实现: 按声明 items 序逐项判定, 返回结构化结果(看板 P3 复用)。
    {ok, verdict, counts{ok,fail,warn}, blocking[ids], items[{id,label,status,mark,detail,exit}], declaration}。"""
    from tools.aipos_cli.clock import iso_z, utc_now

    decl = setup_check_declaration()
    timeout = float(decl["probe_timeout_seconds"])
    found: dict[str, dict[str, Any]] = {}
    found["python"] = _check_python(decl)
    for item_id in ("node", "git"):
        found[item_id] = _check_versioned(decl, item_id, timeout)
    for it in _check_pi(decl, timeout):
        found[it["id"]] = it
    found["gate"] = _check_gate(decl, workspace_root, gate_url)
    found["gate_resident"] = _check_resident(decl)
    missing = [i for i in decl["items"] if i not in found]
    if missing:
        raise SetupCheckError(f"config.schema setup_check.items 声明了未实现的检查项: {missing}")
    items = [found[i] for i in decl["items"]]
    counts = {k: sum(1 for it in items if it["status"] == k) for k in ("ok", "fail", "warn")}
    blocking = [it["id"] for it in items if it["status"] == "fail"]
    return {"operation": "setup_check", "ok": not blocking, "verdict": "PASS" if not blocking else "BLOCK", "counts": counts,
            "blocking": blocking, "items": items, "workspace_root": str(workspace_root) if workspace_root else None,
            "checked_at": iso_z(utc_now())}


def render_setup_check(result: dict[str, Any]) -> str:
    """人读唯一出口: 每项一行(标记 标签: 详情), 非 ✓ 项下一行给出口; 末行汇总。"""
    lines = []
    for it in result["items"]:
        lines.append(f"{it['mark']} {it['label']}: {it['detail']}")
        if it.get("exit"):
            lines.append(f"    出口: {it['exit']}")
    c = result["counts"]
    tail = (f"结论: 有 {c['fail']} 项 ✗(阻断), {c['warn']} 项 ⚠, {c['ok']} 项 ✓" if result["blocking"]
            else f"结论: 无 ✗ 项({c['ok']} 项 ✓, {c['warn']} 项 ⚠ 提示)")
    lines.append(tail)
    return "\n".join(lines)


def preflight_step(result: dict[str, Any] | None) -> dict[str, Any]:
    """onboarding 向导「前置检查」摘要(唯一出口): 未运行 = 只给命令; 运行了 = 每项一行 + ✗ 项出口 + 阻断标记。"""
    command = "lybra setup check"
    if result is None:
        return {"title": "本机安装自检", "command": command, "ran": False, "blocking": False, "blocking_items": [],
                "gate_reachable": False, "lines": [f"本次未运行自检(--skip-preflight 或调用方未给结果); 开始 Step 1 前先跑 `{command}`"
                                                   "(或 `lybra onboarding check <项目> --step 0`), 有 ✗ 项先按出口处理"]}
    lines = [f"{it['mark']} {it['label']}: {it['detail']}" + (f"  → 出口: {it['exit']}" if it.get("exit") else "")
             for it in result["items"]]
    return {"title": "本机安装自检", "command": command, "ran": True, "blocking": bool(result["blocking"]),
            "blocking_items": list(result["blocking"]), "counts": dict(result["counts"]), "lines": lines,
            "gate_reachable": any(it["id"] == "gate" and it["status"] == "ok" for it in result["items"])}
