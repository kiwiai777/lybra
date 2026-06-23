"""AIPOS-206 — Planning Copilot (DG-11) core logic (pure, no Textual).

The Planning Copilot is an Owner-side, read-only planning advisor (DL-20260623-08:
R1 plan-then-execute role separation, R4 per-project session, R6 file memory model).
Structural red lines, enforced here in code (not by policy):

- **Read-only credential (copilot-side ★A1).** The copilot connects with the `copilot`
  service role (scopes []). Read tools are exposed by default, so it can observe; every
  write/confirm/publish op is SCOPE_DENIED at the gate. This module never calls a
  write/confirm/publish tool.

- **DRAFT-write boundary.** The LLM loop returns DRAFT *data* (``DraftProposal``) only.
  This module writes NO files and imports no write helper. Landing a draft under
  ``5_tasks/drafts/`` and feeding it to the AIPOS-204 publish gate is the Owner's
  "proceed" action, done by the TUI layer (``TuiSession.land_draft`` + publish), never
  by the copilot.

- **File memory L0-L3 + three disciplines (R6).** L0 = truth snapshot (read via gate
  read-tools, never edited here); L1 = derived index; L3 = chat. Disciplines:
  (a) ``compact`` never touches L0/L1 (only trims L3 chat); (b) the truth body is
  re-read via read-tools before every draft ([RF-5]); (c) persisted chat is marked
  ``truth=False``. LLM digest is deferred (not implemented here).

- **Secrets fingerprint-only.** The LLM api key is held for in-process use and surfaced
  fingerprint-only; the raw key never enters a prompt, log, record, or persisted context.

- **Egress disclosure.** Planning sends workspace content (the hydrated truth snapshot +
  chat) to the configured external LLM provider (``base_url``). This is the inherent
  output of the planning feature; configuring a provider is informed consent. It is
  orthogonal to the (closed) truth-write path. External web fetch is NOT here (AIPOS-206b).
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Callable, Protocol
from urllib import request as _request

from tools.aipos_cli.confirm_client import GateClient, load_owner_token, token_fingerprint

_QUEUE_LIST = "lybra_queue_list"
_TASK_PREVIEW = "lybra_task_preview"
_VALIDATE = "lybra_validate"


# --- LLM access (bare HTTP, no third-party SDK) ------------------------------------


@dataclass
class LLMConfig:
    """Configurable LLM endpoint. base_url + api_key are the only required knobs."""

    base_url: str
    api_key: str  # raw: held for in-process use only, never logged/recorded/prompted
    model: str = "gpt-4o-mini"
    timeout: float = 60.0

    @property
    def key_fingerprint(self) -> str:
        return token_fingerprint(self.api_key)


class LLMCompleter(Protocol):
    def complete(self, messages: list[dict[str, str]]) -> str: ...


class LLMClient:
    """OpenAI-compatible chat client over stdlib urllib (bare HTTP, proxy bypassed).

    No third-party SDK, so the gate core stays zero-dependency and this module is
    testable in the core CI lane. The raw api key is sent only in the Authorization
    header to the configured provider; it is never logged or returned.
    """

    def __init__(self, config: LLMConfig) -> None:
        self._config = config
        self._opener = _request.build_opener(_request.ProxyHandler({}))

    @property
    def key_fingerprint(self) -> str:
        return self._config.key_fingerprint

    def complete(self, messages: list[dict[str, str]]) -> str:
        body = json.dumps({"model": self._config.model, "messages": messages}).encode("utf-8")
        headers = {
            "Authorization": f"Bearer {self._config.api_key}",
            "Content-Type": "application/json",
        }
        url = self._config.base_url.rstrip("/") + "/chat/completions"
        req = _request.Request(url, data=body, headers=headers, method="POST")
        with self._opener.open(req, timeout=self._config.timeout) as response:
            payload = json.loads(response.read().decode("utf-8"))
        choices = payload.get("choices") if isinstance(payload, dict) else None
        if not isinstance(choices, list) or not choices:
            raise RuntimeError("LLM response had no choices")
        message = choices[0].get("message") if isinstance(choices[0], dict) else {}
        return str(message.get("content") or "")


# --- file memory model L0-L3 (R6) --------------------------------------------------


@dataclass
class ChatTurn:
    role: str
    content: str
    truth: bool = False  # discipline (c): persisted chat is always non-truth


@dataclass
class CopilotMemory:
    """L0 truth snapshot / L1 derived index / L3 chat. L2 hydrate = what is fed to the LLM."""

    l0_truth: dict[str, Any] = field(default_factory=dict)  # read via read-tools, never edited here
    l1_index: dict[str, Any] = field(default_factory=dict)  # derived, read-only
    l3_chat: list[ChatTurn] = field(default_factory=list)

    def record_chat(self, role: str, content: str) -> ChatTurn:
        turn = ChatTurn(role=role, content=content, truth=False)
        self.l3_chat.append(turn)
        return turn

    def compact(self, *, keep_last: int = 20) -> None:
        """Discipline (a): compact NEVER touches L0/L1 — it only trims L3 chat.

        By construction this method assigns to ``l3_chat`` alone; L0/L1 are untouched.
        """
        if keep_last >= 0 and len(self.l3_chat) > keep_last:
            self.l3_chat = self.l3_chat[-keep_last:]


@dataclass
class DraftProposal:
    """A planning DRAFT produced by the copilot. DATA ONLY — no file path, no write.

    The Owner's TUI "proceed" action lands this under 5_tasks/drafts/ and publishes it
    through the AIPOS-204 gate; the copilot never writes it.
    """

    intent: str
    content: str
    project: str
    truth_reread: bool  # True iff truth was re-read via read-tools just before drafting (RF-5)
    truth_snapshot_keys: list[str] = field(default_factory=list)


_SYSTEM_PROMPT = (
    "You are the Lybra Planning Copilot: a read-only planning advisor. You may read the "
    "workspace truth provided and converse, but you CANNOT write files, confirm, or "
    "publish — the Owner does that through the gate. Produce a clear task/plan DRAFT for "
    "the Owner to review."
)


class CopilotSession:
    """Read-only planning session over the gate + an LLM. Single-project (R4).

    Holds a read-only GateClient (the `copilot` role). Exposes only read + draft; there
    is deliberately no confirm/publish/file-write method on this class.
    """

    def __init__(self, *, client: GateClient, llm: LLMCompleter, project: str, memory: CopilotMemory | None = None) -> None:
        self._client = client
        self._llm = llm
        self.project = project
        self.memory = memory or CopilotMemory()

    @classmethod
    def connect(
        cls,
        gate_url: str,
        *,
        llm: LLMCompleter,
        project: str,
        connection_json: str | None = None,
        token_env: str | None = None,
        role: str = "copilot",
    ) -> "CopilotSession":
        token = load_owner_token(connection_json=connection_json, role=role, token_env=token_env)
        client = GateClient(gate_url, token)
        client.initialize()
        return cls(client=client, llm=llm, project=project)

    @property
    def token_fingerprint(self) -> str:
        return self._client.token_fingerprint

    # --- read path: state via gate read-tools, never direct truth file reads ---

    def _read(self, tool: str, arguments: dict[str, Any] | None = None) -> dict[str, Any]:
        return self._client.call_tool(tool, arguments or {})

    def rehydrate_truth(self, *, task_id: str | None = None) -> dict[str, Any]:
        """Discipline (b) / [RF-5]: re-read the truth body via read-tools before drafting.

        L0 is rebuilt from the gate read-tools (not from stale context, not by reading
        truth files directly). Returns the fresh L0 snapshot.
        """
        snapshot: dict[str, Any] = {"queue": self._read(_QUEUE_LIST)}
        if task_id:
            snapshot["task"] = self._read(_TASK_PREVIEW, {"task_id": task_id})
        self.memory.l0_truth = snapshot
        return snapshot

    def _build_messages(self, intent: str, truth: dict[str, Any]) -> list[dict[str, str]]:
        # Egress: the truth snapshot + chat are sent to the configured external LLM.
        messages = [
            {"role": "system", "content": _SYSTEM_PROMPT},
            {"role": "system", "content": f"project: {self.project}\nworkspace truth (read-only):\n{json.dumps(truth, sort_keys=True)[:20000]}"},
        ]
        for turn in self.memory.l3_chat:
            messages.append({"role": turn.role, "content": turn.content})
        messages.append({"role": "user", "content": intent})
        return messages

    def draft(self, intent: str, *, task_id: str | None = None) -> DraftProposal:
        """Produce a DRAFT proposal. RF-5 welded: truth is re-read first, every time.

        Returns DATA ONLY. Writes nothing. The Owner lands/publishes it via the TUI.
        """
        truth = self.rehydrate_truth(task_id=task_id)
        self.memory.record_chat("user", intent)
        content = self._llm.complete(self._build_messages(intent, truth))
        self.memory.record_chat("assistant", content)
        return DraftProposal(
            intent=intent,
            content=content,
            project=self.project,
            truth_reread=True,
            truth_snapshot_keys=sorted(truth.keys()),
        )


def build_llm(config: LLMConfig | None) -> LLMCompleter | None:
    return LLMClient(config) if config is not None else None


__all__ = [
    "LLMConfig",
    "LLMClient",
    "LLMCompleter",
    "CopilotMemory",
    "ChatTurn",
    "DraftProposal",
    "CopilotSession",
    "build_llm",
]
