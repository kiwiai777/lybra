"""`python3 -m relay.probe summarize --channel <ch>`: what did the bot actually receive?

Reads only the probe directory. Per message: time, type, item breakdown, attachments (download / decrypt result,
file name, size, sha256), text preview (first 20 characters only). Per type: every field path seen. Then the four
questions the probe exists to answer (merged-forward expansion, image download, file name/size, group/title fields).
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from relay.store import iter_jsonl

PREVIEW_CHARS = 20
WECOM_DOCUMENTED_TYPES = ("text", "image", "mixed", "voice", "file", "video")
ILINK_ITEM_TYPES = {1: "text", 2: "image", 3: "voice", 4: "file", 5: "video", 11: "tool_call_start", 12: "tool_call_result"}
TITLE_HINTS = ("title", "name", "chatname", "group", "chatid", "roomid")


@dataclass
class Msg:
    seq: int
    received_at: str
    msgid: str
    label: str                    # message type label
    items: list[str] = field(default_factory=list)
    preview: str = ""
    body: dict = field(default_factory=dict)
    documented: bool = True


def preview(text: str) -> str:
    text = " ".join(str(text).split())
    return text if len(text) <= PREVIEW_CHARS else text[:PREVIEW_CHARS] + "…"


def field_paths(node: Any, prefix: str = "") -> set[str]:
    paths: set[str] = set()
    if isinstance(node, dict):
        for key, value in node.items():
            here = f"{prefix}.{key}" if prefix else key
            paths.add(here)
            paths |= field_paths(value, here)
    elif isinstance(node, list):
        for item in node:
            paths |= field_paths(item, prefix + "[]")
    return paths


def _wecom_messages(frames: list[dict]) -> tuple[list[Msg], dict[str, int]]:
    msgs: list[Msg] = []
    other: dict[str, int] = {}
    for rec in frames:
        if rec.get("kind") != "ws_text":
            other[rec.get("kind", "?")] = other.get(rec.get("kind", "?"), 0) + 1
            continue
        try:
            frame = json.loads(rec["raw"])
        except (json.JSONDecodeError, TypeError):
            other["not_json"] = other.get("not_json", 0) + 1
            continue
        cmd = frame.get("cmd") if isinstance(frame, dict) else None
        body = frame.get("body") if isinstance(frame, dict) and isinstance(frame.get("body"), dict) else {}
        if cmd == "aibot_msg_callback":
            mtype = str(body.get("msgtype"))
            m = Msg(seq=rec["seq"], received_at=rec["received_at"], msgid=str(body.get("msgid") or ""), label=mtype,
                    body=body, documented=mtype in WECOM_DOCUMENTED_TYPES)
            texts: list[str] = []
            if mtype == "text":
                texts.append((body.get("text") or {}).get("content", ""))
            elif mtype == "voice":
                texts.append((body.get("voice") or {}).get("content", ""))
            elif mtype == "mixed":
                for item in (body.get("mixed") or {}).get("msg_item") or []:
                    itype = str(item.get("msgtype"))
                    m.items.append(itype)
                    if itype == "text":
                        texts.append((item.get("text") or {}).get("content", ""))
            if "quote" in body:
                m.items.append("quote:" + str((body.get("quote") or {}).get("msgtype")))
            m.preview = preview(" ".join(t for t in texts if t))
            msgs.append(m)
        elif cmd == "aibot_event_callback":
            event = body.get("event") if isinstance(body.get("event"), dict) else {}
            key = f"event:{event.get('eventtype')}"
            other[key] = other.get(key, 0) + 1
        elif cmd:
            other[f"cmd:{cmd}"] = other.get(f"cmd:{cmd}", 0) + 1
        else:
            other["response(subscribe/ping)"] = other.get("response(subscribe/ping)", 0) + 1
    return msgs, other


def _clawbot_messages(frames: list[dict]) -> tuple[list[Msg], dict[str, int]]:
    msgs: list[Msg] = []
    other: dict[str, int] = {}
    for rec in frames:
        try:
            data = json.loads(rec["raw"])
        except (json.JSONDecodeError, TypeError):
            other["not_json"] = other.get("not_json", 0) + 1
            continue
        if not isinstance(data, dict):
            continue
        if data.get("ret") not in (0, None) or data.get("errcode") not in (0, None):
            key = f"error:ret={data.get('ret')},errcode={data.get('errcode')}"
            other[key] = other.get(key, 0) + 1
        for msg in data.get("msgs") or []:
            if not isinstance(msg, dict):
                continue
            items = [it for it in (msg.get("item_list") or []) if isinstance(it, dict)]
            names = [ILINK_ITEM_TYPES.get(it.get("type"), f"type{it.get('type')}") for it in items]
            texts = [str((it.get("text_item") or {}).get("text", "")) for it in items if it.get("type") == 1]
            texts += [str((it.get("voice_item") or {}).get("text", "")) for it in items if it.get("type") == 3]
            for it in items:
                if isinstance(it.get("ref_msg"), dict):
                    names.append("ref_msg")
            label = names[0] if len(set(names)) == 1 and names else ("+".join(names) if names else "empty")
            documented = all(n in ILINK_ITEM_TYPES.values() or n == "ref_msg" for n in names)
            msgs.append(Msg(seq=rec["seq"], received_at=rec["received_at"], msgid=str(msg.get("message_id") or ""),
                            label=label, items=names, preview=preview(" ".join(t for t in texts if t)), body=msg,
                            documented=documented))
    return msgs, other


EXTRACTORS: dict[str, Callable[[list[dict]], tuple[list[Msg], dict[str, int]]]] = {
    "wecom": _wecom_messages,
    "clawbot": _clawbot_messages,
}


def load(channel_dir: Path) -> tuple[list[dict], list[dict]]:
    frames: list[dict] = []
    attachments: list[dict] = []
    for day in sorted(p for p in channel_dir.iterdir() if p.is_dir()) if channel_dir.is_dir() else []:
        frames.extend(iter_jsonl(day / "frames.jsonl"))
        attachments.extend(iter_jsonl(day / "attachments.jsonl"))
    return frames, attachments


def summarize(channel: str, channel_dir: Path, t: Callable[..., str]) -> str:
    frames, attachments = load(channel_dir)
    msgs, other = EXTRACTORS[channel](frames)
    by_msg: dict[str, list[dict]] = {}
    for att in attachments:
        by_msg.setdefault(str(att.get("msgid")), []).append(att)
    out: list[str] = [t("sum_header", channel=channel, dir=str(channel_dir), frames=len(frames), messages=len(msgs),
                        attachments=len(attachments))]
    if other:
        out.append(t("sum_other", items=", ".join(f"{k}×{v}" for k, v in sorted(other.items()))))
    out.append("")
    out.append(t("sum_messages_title"))
    for m in msgs:
        items = f" [{', '.join(m.items)}]" if m.items else ""
        flag = "" if m.documented else " " + t("sum_undocumented")
        out.append(f"  #{m.seq} {m.received_at} {m.label}{items}{flag} msgid={m.msgid}")
        if m.preview:
            out.append("      " + t("sum_preview", text=m.preview))
        for att in by_msg.get(m.msgid, []):
            out.append("      " + t("sum_attachment", path=att.get("path"), decrypt=att.get("decrypt"),
                                    name=att.get("content_disposition_filename") or att.get("declared_name") or "-",
                                    size=att.get("decrypted_size") if att.get("decrypted_size") is not None else att.get("encrypted_size"),
                                    sha=att.get("decrypted_sha256") or att.get("encrypted_sha256") or "-",
                                    kind=att.get("sniffed_type") or "-", error=att.get("error") or "-"))
    out.append("")
    out.append(t("sum_fields_title"))
    by_type: dict[str, set[str]] = {}
    for m in msgs:
        by_type.setdefault(m.label, set()).update(field_paths(m.body))
    for label in sorted(by_type):
        out.append(f"  {label}:")
        for path in sorted(by_type[label]):
            out.append(f"    {path}")
    out.append("")
    out.append(t("sum_questions_title"))
    undocumented = sorted({m.label for m in msgs if not m.documented})
    multi = [m for m in msgs if len(m.items) > 1]
    out.append("  1. " + t("sum_q_forward", undocumented=", ".join(undocumented) or "-",
                            multi=", ".join(f"#{m.seq}:{len(m.items)}" for m in multi) or "-"))
    images = [a for a in attachments if "image" in str(a.get("path"))]
    ok = [a for a in images if a.get("decrypt") in ("ok", "not_encrypted")]
    out.append("  2. " + t("sum_q_images", total=len(images), ok=len(ok)))
    files = [a for a in attachments if "file" in str(a.get("path"))]
    if files:
        for a in files:
            out.append("  3. " + t("sum_q_file", name=a.get("content_disposition_filename") or a.get("declared_name") or "-",
                                    size=a.get("decrypted_size") if a.get("decrypted_size") is not None else a.get("encrypted_size"),
                                    declared=a.get("declared_size") or "-", decrypt=a.get("decrypt")))
    else:
        out.append("  3. " + t("sum_q_no_file"))
    hints = sorted({p for paths in by_type.values() for p in paths if any(h in p.rsplit(".", 1)[-1].lower() for h in TITLE_HINTS)})
    out.append("  4. " + t("sum_q_title", fields=", ".join(hints) or "-"))
    return "\n".join(out)
