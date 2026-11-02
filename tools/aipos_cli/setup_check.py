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

_DECL_KEYS = ("marks", "items", "texts", "probe_timeout_seconds", "requirements", "pi", "gate")
_WHERE = "config.schema.json setup_check.texts"
#: AIPOS-F267: 看板「设置自检」页复用结果的秒数缺省(config.schema setup_check.board.cache_seconds 缺省时用, 与声明同值)
BOARD_CACHE_DEFAULT_SECONDS = 60


class SetupCheckError(ValueError):
    """声明缺 / 形坏(fail-closed)。"""


def setup_check_declaration() -> dict[str, Any]:
    """config.schema setup_check(唯一读取口)。缺键 = SetupCheckError。AIPOS-F277: 用户可见文案(标签 / 出口 / 详情 / 人读行)= texts 双语块
    ({zh, en}, 结构与占位经 i18n.surface_texts 核); 结果里的文字为双语渲染值(i18n.Localized: str 本身 = 缺省语言, 看板 / JSON 行为不变),
    CLI 出声按用户语言取(render_setup_check / preflight_step 的 lang)。"""
    from tools.aipos_cli.i18n import surface_texts
    from tools.schema_loader import code_repo_schema_root, load_schema

    decl = load_schema("config", code_repo_schema_root()).get("setup_check")
    if not isinstance(decl, dict) or any(k not in decl for k in _DECL_KEYS):
        raise SetupCheckError(f"config.schema.json setup_check 未声明齐 {list(_DECL_KEYS)}")
    labels = surface_texts(decl["texts"], None, where=_WHERE)["labels"]
    unknown = [i for i in decl["items"] if i not in labels]
    if unknown:
        raise SetupCheckError(f"config.schema.json setup_check.items 无标签: {unknown}")
    return decl


def _t(decl: dict[str, Any], key: str, /, **values: Any) -> str:
    """texts 双语块一条文案 → 双语渲染值(i18n.block_localize)。"""
    from tools.aipos_cli.i18n import block_localize

    return block_localize(decl["texts"], key, values, where=_WHERE)


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
    return {"id": item_id, "label": _t(decl, f"labels.{item_id}"), "status": status, "mark": decl["marks"][status],
            "detail": detail, "exit": exit_line if status != "ok" else None}


def _version_of(decl: dict[str, Any], argv: list[str], timeout: float) -> tuple[str | None, str]:
    """跑 [可执行, *参数] 取版本输出首行; 起不来 / 超时 / 非零退出 = (None, 原因)。"""
    exe = shutil.which(argv[0])
    if not exe:
        return None, _t(decl, "details.not_on_path", exe=argv[0])
    command = " ".join(argv)
    try:
        proc = subprocess.run([exe, *argv[1:]], stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        return None, _t(decl, "details.timeout", command=command, seconds=f"{timeout:g}")
    except OSError as exc:
        return None, _t(decl, "details.cannot_start", command=command, error=f"{type(exc).__name__}: {exc}")
    out = ((proc.stdout or "").strip() or (proc.stderr or "").strip()).splitlines()
    if proc.returncode != 0:
        return None, _t(decl, "details.exit_nonzero", command=command, code=proc.returncode,
                        tail=out[-1][:200] if out else _t(decl, "details.no_output"))
    return (out[0].strip() if out else ""), exe


def _check_python(decl: dict[str, Any]) -> dict[str, Any]:
    req = decl["requirements"]["python"]
    have = tuple(sys.version_info[:3])
    shown = ".".join(map(str, have))
    want = parse_version(req["min"]) or ()
    exit_line = _t(decl, "exits.python", min=req["min"])
    if have < want:
        return _item(decl, "python", "fail", _t(decl, "details.below", have=shown, where=sys.executable, min=req["min"]), exit_line)
    return _item(decl, "python", "ok", _t(decl, "details.ok_required", have=shown, where=sys.executable, min=req["min"]))


def _check_versioned(decl: dict[str, Any], item_id: str, timeout: float) -> dict[str, Any]:
    req = decl["requirements"][item_id]
    exit_line = _t(decl, f"exits.{item_id}", min=req.get("min", ""))
    out, where = _version_of(decl, [str(a) for a in req["argv"]], timeout)
    if out is None:
        return _item(decl, item_id, "fail", where, exit_line)
    have = parse_version(out)
    if req.get("min"):
        want = parse_version(req["min"]) or ()
        if have is None or have < want:
            return _item(decl, item_id, "fail", _t(decl, "details.below", have=out, where=where, min=req["min"]), exit_line)
        return _item(decl, item_id, "ok", _t(decl, "details.ok_required", have=out, where=where, min=req["min"]))
    return _item(decl, item_id, "ok", _t(decl, "details.ok_plain", have=out, where=where))


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
    missing_exit = _t(decl, "exits.pi_missing", command=install["command"], url=install["url"])
    version, where = _version_of(decl, [exe, *[str(a) for a in spec["version_args"]]], timeout)
    if version is None:
        return [_item(decl, "pi", "fail", where, missing_exit),
                _item(decl, "pi_models", "warn", _t(decl, "details.pi_skipped"), _t(decl, "exits.pi_skipped"))]
    pi_item = _item(decl, "pi", "ok", _t(decl, "details.ok_plain", have=version, where=where))
    catalog = harness_model_catalog(str(spec["harness"]))
    if catalog["ok"] and catalog["models"]:
        models = catalog["models"]
        more = _t(decl, "details.models_more", count=len(models)) if len(models) > 5 else ""
        # AIPOS-F272: 完整清单随结果带出(看板「设 pi 模型」候选复用本次结果, 不另起 pi 子进程)
        return [pi_item, {**_item(decl, "pi_models", "ok", _t(decl, "details.models_ok", count=len(models), shown=", ".join(models[:5]), more=more)),
                          "models": list(models)}]
    # 核不了的原因原文来自 harness_launch.harness_model_catalog(车道外, 单语; AIPOS-F277 已知局限)
    detail = catalog["detail"] if not catalog["ok"] else _t(decl, "details.models_zero", command=" ".join(catalog["command"]))
    return [pi_item, _item(decl, "pi_models", "fail", detail, _t(decl, "exits.pi_models"))]


def _probe_gate(decl: dict[str, Any], url: str, timeout: float) -> tuple[bool, str]:
    """对门基址 + probe_path 发一次 GET(不带凭据、不经代理); 任何 HTTP 应答 = 可达。"""
    import urllib.error
    import urllib.request

    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(urllib.request.Request(url, method="GET"), timeout=timeout) as resp:
            return True, f"HTTP {resp.status}"
    except urllib.error.HTTPError as exc:
        return True, _t(decl, "details.http_answered", code=exc.code)
    except (urllib.error.URLError, OSError, ValueError) as exc:
        reason = getattr(exc, "reason", exc)
        return False, f"{type(reason).__name__}: {reason}"


def _check_gate(decl: dict[str, Any], workspace_root: Path | None, gate_url: str | None) -> dict[str, Any]:
    from tools.aipos_cli.confirm_client import GateAddressError, resolve_gate_base_url

    spec = decl["gate"]
    try:
        base = resolve_gate_base_url(workspace_root=workspace_root, explicit_url=gate_url)
    except (GateAddressError, ValueError, OSError) as exc:
        return _item(decl, "gate", "warn", _t(decl, "details.gate_unresolved", error=exc), _t(decl, "exits.gate"))
    source = (_t(decl, "details.source_explicit") if gate_url else
              (_t(decl, "details.source_workspace", workspace=workspace_root) if workspace_root else _t(decl, "details.source_default")))
    probe = base.rstrip("/") + str(spec["probe_path"])
    ok, how = _probe_gate(decl, probe, float(spec["timeout_seconds"]))
    if ok:
        return _item(decl, "gate", "ok", _t(decl, "details.gate_ok", base=base, source=source, probe_path=spec["probe_path"], how=how))
    return _item(decl, "gate", "warn", _t(decl, "details.gate_down", base=base, source=source, how=how), _t(decl, "exits.gate"))


def _resident_path_problem(decl: dict[str, Any], st: dict[str, Any]) -> tuple[str, str] | None:
    """AIPOS-F268 件②: 已装单元的 PATH(只读解析单元文件, gate_resident.unit_environment)不含 pi 所在目录 = (详情, 出口)。
    pi 在本进程 PATH 里也找不到 = 不对照(pi 项本身已报 ✗); 单元 PATH 已含能找到 pi 的目录 = 无问题。"""
    from tools.aipos_cli.gate_resident import path_contains

    unit_env = st.get("unit_environment")
    exe = tool_executable(decl, "pi")
    found = shutil.which(exe)
    if unit_env is None:
        return (_t(decl, "details.resident_env_unreadable", pi=found or exe),
                _t(decl, "exits.resident_path", pi_dir=os.path.dirname(found) if found else exe,
                   unit_path_value=_t(decl, "details.resident_path_unreadable_value")))
    unit_path_value = unit_env.get("PATH")
    if unit_path_value and shutil.which(exe, path=unit_path_value):
        return None
    if not found:
        return None
    pi_dir = os.path.dirname(found)
    if unit_path_value and path_contains(unit_path_value, pi_dir):
        return None
    shown = unit_path_value if unit_path_value else _t(decl, "details.resident_path_absent")
    exit_line = _t(decl, "exits.resident_path", pi_dir=pi_dir, unit_path_value=shown)
    return _t(decl, "details.resident_path_missing", pi_dir=pi_dir, shown=shown), exit_line


def _check_resident(decl: dict[str, Any]) -> dict[str, Any]:
    from tools.aipos_cli.gate_resident import resident_status

    st = resident_status()
    if not st.get("supported"):
        return _item(decl, "gate_resident", "warn", st.get("detail"), _t(decl, "exits.resident"))
    hand = st.get("handwritten") or []
    if st.get("installed"):
        state = _t(decl, "details.resident_running" if st.get("active") else "details.resident_stopped")
        owner = _t(decl, "details.resident_managed" if st.get("managed") else "details.resident_unmanaged")
        detail = _t(decl, "details.resident_installed", unit_path=st["unit_path"], owner=owner, state=state)
        exits = [] if st.get("active") else [_t(decl, "exits.resident_not_running")]
        path_problem = _resident_path_problem(decl, st)
        if path_problem:
            detail = _join(decl, [detail, path_problem[0]])
            exits.append(path_problem[1])
        return _item(decl, "gate_resident", "warn" if exits else "ok", detail, _join(decl, exits) if exits else None)
    if hand:
        names = ", ".join(f"{h['name']}({h['path']})" for h in hand)
        return _item(decl, "gate_resident", "ok", _t(decl, "details.resident_handwritten", names=names))
    return _item(decl, "gate_resident", "warn", _t(decl, "details.resident_absent", unit_path=st["unit_path"]), _t(decl, "exits.resident"))


def _join(decl: dict[str, Any], parts: list[str]) -> str:
    """多段详情 / 出口以 details.detail_sep 相连, 各语言各连一份(双语渲染值不因拼接丢语言)。"""
    from tools.aipos_cli.i18n import localize, supported_languages

    sep = _t(decl, "details.detail_sep")
    names = [f"p{i}" for i in range(len(parts))]
    template = "{sep}".join("{" + n + "}" for n in names)
    return localize({code: template for code in supported_languages()}, {"sep": sep, **dict(zip(names, parts))},
                    where=f"{_WHERE}.details.detail_sep")


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


def render_setup_check(result: dict[str, Any], lang: str | None = None) -> str:
    """人读唯一出口: 每项一行(标记 标签: 详情), 非 ✓ 项下一行给出口; 末行汇总。AIPOS-F277: lang = 用户语言(i18n.resolve_language;
    缺省 = 缺省语言, 与改前逐字相同)。"""
    from tools.aipos_cli.i18n import lines_in

    decl = setup_check_declaration()
    lines = []
    for it in result["items"]:
        lines.append(_t(decl, "render.item", mark=it["mark"], label=it["label"], detail=it["detail"]))
        if it.get("exit"):
            lines.append(_t(decl, "render.exit", exit=it["exit"]))
    c = result["counts"]
    lines.append(_t(decl, "render.summary_blocking" if result["blocking"] else "render.summary_clear", **c))
    return "\n".join(lines_in(lines, lang))


def preflight_step(result: dict[str, Any] | None) -> dict[str, Any]:
    """onboarding 向导「前置检查」摘要(唯一出口): 未运行 = 只给命令; 运行了 = 每项一行 + ✗ 项出口 + 阻断标记。
    AIPOS-F277: title / lines 为双语渲染值(向导人读输出按用户语言取)。"""
    decl = setup_check_declaration()
    command = "lybra setup check"
    title = _t(decl, "render.preflight_title")
    if result is None:
        return {"title": title, "command": command, "ran": False, "blocking": False, "blocking_items": [],
                "gate_reachable": False, "lines": [_t(decl, "render.preflight_not_run", command=command)]}
    lines = [_t(decl, "render.item", mark=it["mark"], label=it["label"], detail=it["detail"])
             if not it.get("exit") else _join_exit(decl, it) for it in result["items"]]
    return {"title": title, "command": command, "ran": True, "blocking": bool(result["blocking"]),
            "blocking_items": list(result["blocking"]), "counts": dict(result["counts"]), "lines": lines,
            "gate_reachable": any(it["id"] == "gate" and it["status"] == "ok" for it in result["items"])}


def _join_exit(decl: dict[str, Any], it: dict[str, Any]) -> str:
    """向导前置检查一行: 「标记 标签: 详情  → 出口: …」(各语言各一份)。"""
    from tools.aipos_cli.i18n import localize, supported_languages

    item = _t(decl, "render.item", mark=it["mark"], label=it["label"], detail=it["detail"])
    tail = _t(decl, "render.preflight_exit", exit=it["exit"])
    return localize({code: "{item}{tail}" for code in supported_languages()}, {"item": item, "tail": tail},
                    where=f"{_WHERE}.render.preflight_exit")
