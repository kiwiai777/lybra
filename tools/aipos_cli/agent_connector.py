"""AIPOS-248 — agent-side connector (the pull half): ``lybra agent fetch|watch``.

Design (card: tools/mcp_server/AIPOS-248_agent_connector_micro_plan.md):

- **Lybra is ALWAYS the connected party.** Every ask is a STATELESS pull over the
  existing read tool (``lybra_queue_list`` via the AIPOS-203 ``GateClient``). The gate
  records NOTHING about agent presence — no liveness-as-truth, no heartbeat; the TUI
  ``/agents`` view stays "as recorded — not live" (AIPOS-234).
- **The loop host is the AGENT-side process** (red line 1 / R hook 2): ``watch`` is a
  foreground, BOUNDED client loop — agent-launched + foreground + bounded, all three,
  none optional — that exits on the first hit or at ``--max-wait``. Lybra never pushes,
  schedules, or wakes an agent.
- **Role-agnostic thin client** (red line 4): ``--role`` passes through to the token
  source (connection.json role table or env var); nothing in this module is specific to
  the executor role. The claimable predicate is an ADVISORY pre-filter (R hook 3): the
  enforcement stays in the gate validator. The mirror errs WIDE when in doubt — too
  wide means the gate refuses loudly (safe); too narrow means silently missed tasks
  (worse). The listing is a suggestion; the gate is the truth.
- **fetch/watch NEVER claim** (red line 3): the only tool this module ever calls is the
  read tool. Claiming stays the agent's explicit act through the unchanged supervised
  chain (dry-run -> Owner OOB confirm).
- **No jitter** on the interval (R hook 6): single-user local gate — there is no
  thundering herd to spread; jitter would only make the cadence harder to audit.
- **on/off is not a state anywhere** (derived red line 6): "on" is the agent running
  this loop; "off" is not running it. Nothing is registered, recorded, or displayed.
"""

from __future__ import annotations

import json
import time
from typing import Any, Callable

from tools.aipos_cli.confirm_client import GateClient, GateError, load_owner_token

# Client-side loop guards (R hook 6): 60s default, 15s hard floor (protect the gate;
# below the floor is an ERROR, never silently raised), 30min bounded watch, error
# backoff doubling up to 5min and resetting on success.
DEFAULT_INTERVAL_SECONDS = 60.0
MIN_INTERVAL_SECONDS = 15.0
DEFAULT_MAX_WAIT_SECONDS = 1800.0
BACKOFF_CAP_SECONDS = 300.0

# The three stateless-pull outcomes (card §3-Q3).
STATE_HELD = "held"
STATE_CLAIMABLE = "claimable"
STATE_NONE = "none"

# R-b (Owner-ruled v1.0 requirement): between-task context hygiene. Lybra cannot reach
# into an agent's memory, so this is a TAUGHT rule (SKILL.md) plus this client-side
# hint printed whenever new tasks are offered.
_HYGIENE_HINT = "提示:若你刚完成/return 过上一单,先清任务上下文(cc: /clear)再认领——一单一净上下文。"


def _metadata(task: dict[str, Any]) -> dict[str, Any]:
    metadata = task.get("metadata")
    return metadata if isinstance(metadata, dict) else {}


def actor_matches(task: dict[str, Any], actor: str) -> bool:
    """ADVISORY mirror of the gate validator's PRE-CLAIM actor-match set
    (validator.py:361-365): ``{assigned_to, agent_instance, claimed_by}``. This is the
    "who is authorized to claim this pending task" set — used ONLY for `claimable`
    classification below.

    Mirror discipline (R hook 3): this is a pre-filter for PRESENTATION only — the gate
    dry-run re-validates every claim. Kept deliberately as the validator's WIDE set
    (claimed_by included even for pending tasks): too wide is refused loudly by the
    gate; too narrow would silently hide claimable work.
    """
    metadata = _metadata(task)
    return actor in {
        metadata.get("assigned_to"),
        metadata.get("agent_instance"),
        metadata.get("claimed_by"),
    }


def _is_holder(task: dict[str, Any], actor: str) -> bool:
    """F-248-o3-1 fix: for an ALREADY-CLAIMED task, the holder is `claimed_by` — full
    stop. NOT `assigned_to`/`agent_instance` (those are pre-claim authorization/
    authoring fields that can diverge from who actually claimed it).

    Ground truth, source-verified: `queue_mutation.py:203` (`_prepare_claim`) writes
    `claimed_by = actor` as the ONLY post-claim identity field it sets; the gate's own
    holder check (`validator.py:420-427`) matches `current_actor` against `claimed_by`
    exclusively and BLOCKS ("Task is claimed by another actor") on any other match —
    this is not advisory there, it is the enforced truth. The prior implementation
    reused the wide `actor_matches` (assigned_to-inclusive) mirror for BOTH held and
    claimable classification; that conflated "who was assigned" with "who actually
    holds it" and produced a false positive (O3 F-248-o3-1: an actor matching only
    `assigned_to` on a task actually claimed by someone else was told "you already
    hold this").
    """
    metadata = _metadata(task)
    return bool(metadata.get("claimed_by")) and actor == metadata.get("claimed_by")


def classify(tasks: list[dict[str, Any]], actor: str) -> dict[str, Any]:
    """Classify one stateless pull into the three outcomes of card §3-Q3.

    held: the actor is the RECORDED claimant (`claimed_by`) of a claimed task ->
    one-session-one-task discipline comes FIRST (new tasks are deliberately suppressed
    to keep the discipline in view). Deliberately NOT assigned_to/agent_instance (see
    `_is_holder`, F-248-o3-1).
    claimable: pending tasks whose recorded assignment matches the actor (advisory).
    none: neither.
    """
    held = [
        task
        for task in tasks
        if str(task.get("queue_state") or "") == "claimed" and _is_holder(task, actor)
    ]
    claimable = [
        task
        for task in tasks
        if str(task.get("queue_state") or "") == "pending" and actor_matches(task, actor)
    ]
    if held:
        state = STATE_HELD
    elif claimable:
        state = STATE_CLAIMABLE
    else:
        state = STATE_NONE
    return {"state": state, "held": held, "claimable": claimable}


def _task_line(task: dict[str, Any]) -> str:
    metadata = _metadata(task)
    title = str(task.get("title") or metadata.get("title") or "").strip()
    assigned = str(metadata.get("assigned_to") or "").strip()
    parts = [str(task.get("task_id") or "(no task_id)")]
    if assigned:
        parts.append(f"assigned_to={assigned}")
    if title:
        parts.append(title)
    return "  - " + "  ".join(parts)


def render(result: dict[str, Any], *, watching: bool = False) -> str:
    """Render one pull result. Each state carries its own P-A guidance line (R-c):
    where you are / what to type next — never a bare data dump."""
    state = result["state"]
    if state == STATE_HELD:
        ids = ", ".join(str(t.get("task_id")) for t in result["held"])
        return (
            f"你已持有 {ids} —— 一 session 一 task,先 return/complete 再接新活(新任务列表已抑制)。\n"
            "→ 下一步:完成后走 queue_return dry-run → Owner confirm(OOB);"
            "然后 /clear 清任务上下文再回来接单。"
        )
    if state == STATE_CLAIMABLE:
        lines = ["可认领任务(建议列表——列表是建议,门才是真相):"]
        lines += [_task_line(task) for task in result["claimable"]]
        lines.append(_HYGIENE_HINT)
        lines.append(
            "→ 下一步:选定一单,走 lybra_queue_claim_dry_run(actor=你,带 active_session_id)。"
            "认领结果由门(gate)判定,你不预判、不自行 confirm:"
            "若此单落在你的预授权信封内(Owner 事先签发的 autonomy 策略),claim 会自动放行、"
            "无需报 Owner(应答 autonomy_mode=PreAuthorized,记录指回策略);"
            "否则回落逐单——把 dry-run 结果报 Owner,由 Owner OOB confirm(应答 autonomy_mode=Supervised)。"
            "是否在信封内以 gate 应答为准。"
        )
        return "\n".join(lines)
    if watching:
        return "暂无可认领任务。\n→ 下一步:继续按 interval 询问(有界,到 max-wait 即退);或 /lybra off 离线。"
    return "暂无可认领任务。\n→ 下一步:稍后再 fetch,或起 `lybra agent watch` 在界内等待;或 /lybra off 离线。"


def _to_json(result: dict[str, Any]) -> str:
    def _slim(task: dict[str, Any]) -> dict[str, Any]:
        metadata = _metadata(task)
        return {
            "task_id": task.get("task_id"),
            "queue_state": task.get("queue_state"),
            "assigned_to": metadata.get("assigned_to"),
            "agent_instance": metadata.get("agent_instance"),
            "claimed_by": metadata.get("claimed_by"),
        }

    return json.dumps(
        {
            "state": result["state"],
            "held": [_slim(t) for t in result["held"]],
            "claimable": [_slim(t) for t in result["claimable"]],
        },
        ensure_ascii=False,
    )


def _connect(args: Any) -> GateClient:
    token = load_owner_token(
        connection_json=args.connection_json, role=args.role, token_env=args.token_env
    )
    client = GateClient(args.gate_url, token)
    client.initialize()
    return client


def fetch_once(client: GateClient, actor: str) -> dict[str, Any]:
    """One STATELESS pull: a fresh read-tool call, classified. No other tool is ever
    called from this module (red line 3 — presenting is not claiming)."""
    return classify(client.queue_tasks(), actor)


def run_fetch(args: Any) -> int:
    try:
        client = _connect(args)
        result = fetch_once(client, args.actor)
    except (GateError, ValueError, OSError, json.JSONDecodeError) as exc:
        print(f"lybra agent fetch: {exc}")
        return 2
    print(_to_json(result) if args.json else render(result))
    return 0


def run_watch(
    args: Any,
    *,
    sleeper: Callable[[float], None] = time.sleep,
    clock: Callable[[], float] = time.monotonic,
) -> int:
    """Foreground, BOUNDED client-side loop (agent-launched + foreground + bounded —
    the non-daemon triad, R hook 2). Exits 0 on the first hit AND on max-wait timeout
    (the agent decides whether to re-enter). Connection failures print honestly and
    back off (doubling, capped, reset on success) — never a silent retry."""
    interval = float(args.interval)
    max_wait = float(args.max_wait)
    if interval < MIN_INTERVAL_SECONDS:
        print(
            f"lybra agent watch: --interval {interval:g}s is below the {MIN_INTERVAL_SECONDS:g}s floor "
            "(gate-protection guard; the floor is an error, never silently raised)."
        )
        return 2
    if max_wait <= 0:
        print("lybra agent watch: --max-wait must be positive (the loop must be bounded).")
        return 2

    start = clock()
    client: GateClient | None = None
    backoff = interval
    while True:
        try:
            if client is None:
                client = _connect(args)
            result = fetch_once(client, args.actor)
            backoff = interval  # success resets the error backoff
        except (GateError, ValueError, OSError, json.JSONDecodeError) as exc:
            print(f"lybra agent watch: pull failed ({exc}); retrying in {backoff:g}s")
            client = None  # reconnect next round (the transport session may be gone)
            if clock() - start + backoff >= max_wait:
                print("watch 超时(max-wait):期间连接持续失败。\n→ 下一步:检查 gate(lybra serve)后重进 watch。")
                return 2
            sleeper(backoff)
            backoff = min(backoff * 2, BACKOFF_CAP_SECONDS)
            continue
        if result["state"] != STATE_NONE:
            print(render(result, watching=True))
            return 0
        if clock() - start + interval >= max_wait:
            print("watch 超时(max-wait):暂无可认领任务。\n→ 下一步:由你(agent)决定是否重进 watch,或 /lybra off 离线。")
            return 0
        sleeper(interval)


def run_agent_command(args: Any) -> int:
    if args.agent_command == "fetch":
        return run_fetch(args)
    if args.agent_command == "watch":
        return run_watch(args)
    print("usage: lybra agent {fetch|watch} … (see --help)")
    return 2
