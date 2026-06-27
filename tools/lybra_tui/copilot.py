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
# AIPOS-208: draft_validator is a READ-ONLY validator (it writes nothing and is NOT one of the
# write helpers the AIPOS-206 guard test forbids here). Importing it lets the copilot guarantee
# card conformance in-memory (single source for the field contract), so a copilot-authored card is
# gated-publishable + claimable by construction, not by LLM luck.
from tools.aipos_cli.draft_validator import (
    DRAFT_REQUIRED_FIELDS,
    FORBIDDEN_RUNTIME_FIELDS,
    RECOMMENDED_FIELDS,
    TASK_ID_PATTERN,
    draft_slug,
)

_QUEUE_LIST = "lybra_queue_list"
_TASK_PREVIEW = "lybra_task_preview"
_VALIDATE = "lybra_validate"

# Template defaults the copilot fills for required/recommended fields the LLM need not invent.
# status MUST be pending (validator); created_by marks copilot authorship (DG-8 / N4 evidence).
_CARD_DEFAULTS = {
    "status": "pending",
    "created_by": "copilot",
    "needs_owner": "false",
    "artifact_policy": "formal_write",
    "task_mode": "docs",
    "priority": "low",
    "model_tier": "L2",
    "task_type": "one_shot",
    "polling_mode": "agent_polling",
    "claim_policy": "assigned_agent_only",
    "report_mode": "forum_reply",
    "recurrence": "none",
}
# The semantic fields the LLM is asked to fill (everything else is templated/derived/Owner-supplied).
_LLM_CARD_FIELDS = ("task_id", "title", "task_mode", "priority", "output_target", "assigned_to", "body")


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


@dataclass
class Usage:
    """READ-ONLY token telemetry captured from an LLM /chat/completions response.

    Pure observability surfaced on ``ChatReply`` so the TUI can show honest up/down token
    counts in the thinking line. Carries NO secret, NO file path, NO token; capturing it
    writes nothing and does not change the copilot's role/scopes. ``prompt_tokens`` is the
    egress (↑) count, ``completion_tokens`` the answer (↓) count.
    """

    prompt_tokens: int | None = None
    completion_tokens: int | None = None


def _usage_from_payload(payload: Any) -> Usage | None:
    """Extract OpenAI-compatible ``usage`` from a response payload. Read-only, never raises."""
    usage = payload.get("usage") if isinstance(payload, dict) else None
    if not isinstance(usage, dict):
        return None

    def _int(value: Any) -> int | None:
        return int(value) if isinstance(value, (int, float)) else None

    return Usage(
        prompt_tokens=_int(usage.get("prompt_tokens")),
        completion_tokens=_int(usage.get("completion_tokens")),
    )


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
        # AIPOS-222 (read-only telemetry): the usage block from the LAST /chat/completions
        # response, if the provider returned one. Pure observability — it is captured from the
        # HTTP response and surfaced on ChatReply; it writes NOTHING, confirms nothing, and does
        # not touch the role/scopes credential. None until the first completion (or if the
        # provider omits `usage`).
        self.last_usage: Usage | None = None

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
        # READ-ONLY telemetry: capture token usage from the response (if present) for display.
        self.last_usage = _usage_from_payload(payload)
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
    # AIPOS-208 structured task-card authoring (None for a free-form planning draft):
    task_id: str | None = None
    conformant: bool = False  # passes the in-memory field contract (gated-publishable shape)
    blocking_reasons: list[str] = field(default_factory=list)
    needs_bundle: bool = False  # no matching existing context_bundle → Owner must specify at proceed
    context_bundle: str | None = None
    draft_rel_path: str | None = None  # slug-aligned target under 5_tasks/drafts/ (Owner lands here)
    fields: dict[str, Any] = field(default_factory=dict)  # raw assembled fields (for re-finalize)


_SYSTEM_PROMPT = (
    "You are the Lybra Planning Copilot: a read-only planning advisor. You may read the "
    "workspace truth provided and converse, but you CANNOT write files, confirm, or "
    "publish — the Owner does that through the gate. Produce a clear task/plan DRAFT for "
    "the Owner to review."
)

# AIPOS-222: a SEPARATE conversational system prompt for the read-only `chat()` turn. The
# copilot answers in natural language as a planning advisor; it never writes/confirms/publishes.
# When the discussion is concrete enough to act on, it OFFERS to generate a project-init task-card
# draft — the Owner consents via `/draft` or an affirmative reply. The offer is a suggestion only;
# producing the card is a separate consent step (draft_task_card), never done inside chat().
_CHAT_SYSTEM_PROMPT = (
    "You are the Lybra Planning Copilot: a read-only conversational planning advisor. Answer the "
    "Owner's question in clear, natural language, grounded in the workspace truth you are given. "
    "You CANNOT write files, confirm, or publish — only the Owner does that through the gate, so "
    "never claim to have created, saved, or published anything. When the discussion has enough "
    "concrete detail to act on, END your answer by briefly OFFERING to generate a project-init "
    "task-card draft for the Owner to review (the Owner will consent). Do not output a task card "
    "yourself; just converse and, when ready, offer."
)

# AIPOS-222: conversational chat accumulates turns, so the LLM egress (truth snapshot + chat)
# would grow unbounded. After each chat turn, if l3_chat exceeds this, auto-compact (trim L3 chat
# ONLY — never L0/L1 truth) down to the last KEEP_LAST turns. Reuses CopilotMemory.compact.
CHAT_KEEP_LAST = 20


@dataclass
class ChatReply:
    """Read-only conversational reply. DATA ONLY — no file path, no write, no token.

    Returned by ``CopilotSession.chat``. ``compacted`` is True iff this turn triggered an
    auto-compaction of L3 chat (so the TUI can surface a subtle "earlier turns compacted" line).
    """

    content: str
    compacted: bool = False
    # AIPOS-222 read-only telemetry: the token usage of the underlying LLM call, if the provider
    # reported it (else None → the TUI falls back to a char-based ~estimate, never a fake number).
    usage: "Usage | None" = None


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

    def _build_messages(self, intent: str, truth: dict[str, Any], *, system_prompt: str = _SYSTEM_PROMPT) -> list[dict[str, str]]:
        # Egress: the truth snapshot + chat are sent to the configured external LLM.
        messages = [
            {"role": "system", "content": system_prompt},
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

    # --- AIPOS-222: read-only conversational chat (NL answer; no card, no write) ---

    def chat(self, intent: str) -> ChatReply:
        """Read-only conversational turn. Answers the Owner in natural language.

        Uses the SAME read-only gate read-tools as ``draft()`` (``rehydrate_truth`` → ``_read``),
        calls the LLM (sync), and records BOTH turns in ``CopilotMemory`` (``truth=False``). It
        writes NO file, calls NO write/confirm/publish tool, and holds NO owner token — it is a
        pure planning conversation. After recording, it auto-compacts L3 chat (trimming chat ONLY,
        never L0/L1 truth) when the transcript grows past ``CHAT_KEEP_LAST``, and reports whether a
        compaction happened so the TUI can surface a subtle notice.

        Returns DATA ONLY (``ChatReply``). The card-generation step is a SEPARATE consent action
        (``draft_task_card``); chat never produces or lands a card.
        """
        truth = self.rehydrate_truth()  # RF-5: re-read truth via read-tools (read-only)
        self.memory.record_chat("user", intent)
        content = self._llm.complete(
            self._build_messages(intent, truth, system_prompt=_CHAT_SYSTEM_PROMPT)
        )
        # READ-ONLY telemetry: read the usage the LLM client captured from its HTTP response (if
        # any). This only READS an attribute on the client — it writes no file, calls no tool, and
        # leaves the role/scopes credential untouched. None when the provider omits usage.
        usage = getattr(self._llm, "last_usage", None)
        self.memory.record_chat("assistant", content)
        compacted = False
        if len(self.memory.l3_chat) > CHAT_KEEP_LAST:
            self.memory.compact(keep_last=CHAT_KEEP_LAST)  # trims L3 chat ONLY; truth untouched
            compacted = True
        return ChatReply(content=content, compacted=compacted, usage=usage)

    # --- AIPOS-208: chat-to-task — structured, conformant task-card authoring ---

    def available_context_bundles(self) -> list[str]:
        """Read-only: the context_bundle values already in use across the queue (path-B for R4 'send')."""
        bundles: set[str] = set()
        data = self._read(_QUEUE_LIST).get("data")
        tasks = data.get("tasks") if isinstance(data, dict) else None
        for task in tasks or []:
            meta = task.get("metadata") if isinstance(task.get("metadata"), dict) else {}
            value = meta.get("context_bundle") or task.get("context_bundle")
            if value:
                bundles.add(str(value))
        return sorted(bundles)

    def _suggest_bundle(self, assigned_to: str | None, available: list[str]) -> str | None:
        # copilot NEVER invents a bundle: suggest one that already exists, else None (Owner specifies).
        if assigned_to and assigned_to in available:
            return assigned_to
        return available[0] if available else None

    @staticmethod
    def _llm_card_fields(raw: str) -> dict[str, Any]:
        text = raw.strip()
        if text.startswith("```"):
            text = text.split("```", 2)[1] if "```" in text[3:] else text.strip("`")
            text = text[text.find("{"):] if "{" in text else text
        start, end = text.find("{"), text.rfind("}")
        if start == -1 or end == -1:
            return {}
        try:
            obj = json.loads(text[start : end + 1])
        except json.JSONDecodeError:
            return {}
        return obj if isinstance(obj, dict) else {}

    def _assemble_card(self, fields: dict[str, Any], context_bundle: str | None) -> tuple[str, dict[str, Any], str]:
        """Assemble a conformant card text from LLM fields + template defaults. Pure (no write)."""
        meta: dict[str, Any] = dict(_CARD_DEFAULTS)
        for key in _LLM_CARD_FIELDS:
            value = fields.get(key)
            if value not in (None, ""):
                meta[key] = value
        meta["project"] = self.project
        meta["agent_instance"] = fields.get("agent_instance") or "agent-01"
        meta["assigned_to"] = fields.get("assigned_to") or "dev_claude"
        if context_bundle:
            meta["context_bundle"] = context_bundle
        body = str(fields.get("body") or "").strip()
        # render frontmatter (required first, then recommended) — forbidden runtime fields never added
        ordered = [*DRAFT_REQUIRED_FIELDS, *RECOMMENDED_FIELDS]
        lines = ["---"]
        for key in ordered:
            if key in meta and meta[key] not in (None, ""):
                lines.append(f"{key}: {meta[key]}")
        lines.append("---")
        content = "\n".join(lines) + "\n" + body + "\n"
        return content, meta, body

    def _conformance(self, meta: dict[str, Any], body: str) -> list[str]:
        """In-memory field-contract check (mirrors draft_validator's field rules; no fs access).

        The full validate (collision/path) runs at draft_publish_dry_run; this guarantees the
        gated-publishable shape up front so the Owner never previews an unpublishable card.
        """
        reasons: list[str] = []
        for f in DRAFT_REQUIRED_FIELDS:
            if meta.get(f) in (None, ""):
                reasons.append(f"Missing required field: {f}")
        task_id = meta.get("task_id")
        if task_id and not TASK_ID_PATTERN.fullmatch(str(task_id)):
            reasons.append("Invalid task_id format or path-unsafe task_id")
        if meta.get("status") not in (None, "pending"):
            reasons.append("Draft status must be pending")
        for f in FORBIDDEN_RUNTIME_FIELDS:
            if meta.get(f) not in (None, ""):
                reasons.append(f"Draft contains forbidden runtime-state field: {f}")
        if not body.strip():
            reasons.append("Missing required field: body")
        return reasons

    def _proposal_from_fields(self, intent: str, fields: dict[str, Any], truth: dict[str, Any], *, context_bundle: str | None, available: list[str]) -> DraftProposal:
        assigned_to = str(fields.get("assigned_to") or "") or None
        bundle = context_bundle or self._suggest_bundle(assigned_to, available)
        content, meta, body = self._assemble_card(fields, bundle)
        reasons = self._conformance(meta, body)
        task_id = str(meta.get("task_id") or "") or None
        rel = None
        if task_id and TASK_ID_PATTERN.fullmatch(task_id):
            try:
                rel = f"5_tasks/drafts/{draft_slug(task_id)}.md"
            except ValueError:
                rel = None
        return DraftProposal(
            intent=intent, content=content, project=self.project,
            truth_reread=True, truth_snapshot_keys=sorted(truth.keys()),
            task_id=task_id, conformant=not reasons, blocking_reasons=reasons,
            needs_bundle=bundle is None, context_bundle=bundle,
            draft_rel_path=rel, fields=dict(fields),
        )

    def draft_task_card(self, intent: str, *, task_id: str | None = None) -> DraftProposal:
        """chat-to-task: turn a natural-language ask into a conformant task card. Zero file write.

        RF-5 welded (truth re-read first). The LLM supplies semantics; copilot.py guarantees the
        gated-publishable structure. If no existing context_bundle matches, the gap is surfaced to
        the Owner (needs_bundle) rather than invented or left to fail validation.
        """
        truth = self.rehydrate_truth(task_id=task_id)
        self.memory.record_chat("user", intent)
        prompt = (
            "Return ONLY a JSON object (no prose, no code fence) with these keys for a Lybra task "
            f"card: {list(_LLM_CARD_FIELDS)}. task_id like 'AIPOS-DOC-1'; task_mode one of "
            "code/docs/test/research; priority one of low/medium/high; output_target a path; body a "
            "one-paragraph description. Base it on the request and the workspace truth.\nRequest: " + intent
        )
        raw = self._llm.complete(self._build_messages(prompt, truth))
        self.memory.record_chat("assistant", raw)
        fields = self._llm_card_fields(raw)
        if task_id:
            fields.setdefault("task_id", task_id)
        return self._proposal_from_fields(intent, fields, truth, context_bundle=None, available=self.available_context_bundles())

    def finalize_card(self, proposal: DraftProposal, *, context_bundle: str | None = None, task_id: str | None = None) -> DraftProposal:
        """Re-assemble a card with Owner-supplied overrides (e.g. a bundle ref). Pure (no write)."""
        fields = dict(proposal.fields)
        if task_id:
            fields["task_id"] = task_id
        truth = {k: None for k in proposal.truth_snapshot_keys}  # keys only; not re-reading here
        bundle = context_bundle or proposal.context_bundle
        return self._proposal_from_fields(proposal.intent, fields, truth, context_bundle=bundle, available=self.available_context_bundles())


def build_llm(config: LLMConfig | None) -> LLMCompleter | None:
    return LLMClient(config) if config is not None else None


__all__ = [
    "LLMConfig",
    "LLMClient",
    "LLMCompleter",
    "Usage",
    "CopilotMemory",
    "ChatTurn",
    "ChatReply",
    "CHAT_KEEP_LAST",
    "DraftProposal",
    "CopilotSession",
    "build_llm",
]
