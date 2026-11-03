"""Relay probe entry: `python3 -m relay.probe <run|login|check|summarize> --channel <wecom|clawbot>`.

Receive-only: records exactly what a channel delivers (raw frames + attachments) so the real design can be decided
from facts. Runs as its own process with its own credentials; the gate never imports it and it never calls the gate.
Setup and the Owner's test checklist: docs/relay-probe-setup.zh-CN.md (docs/relay-probe-setup.md).
"""
from __future__ import annotations

import argparse
import os
import signal
import sys
import threading
import time
from pathlib import Path

from relay import config
from relay.channels import EXIT_CONFIG, EXIT_LOCKED, EXIT_NETWORK, ProbeContext, adapter
from relay.net import HttpError, NetPolicyError
from relay.store import LockBusy, ProbeStore
from relay.summarize import summarize
from relay.texts import LangError, resolve_lang, translator


def _say(line: str) -> None:
    print(line, flush=True)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python3 -m relay.probe",
                                     description="Lybra relay probe: receive-only channel recorder (no replies).")
    parser.add_argument("--lang", choices=("zh", "en"), default=None, help="output language (default: $LYBRA_LANG, else zh)")
    sub = parser.add_subparsers(dest="command", required=True)

    def common(p: argparse.ArgumentParser, *, probe_root: bool = True, config_dir: bool = True) -> None:
        p.add_argument("--channel", required=True, choices=sorted(config.CHANNELS))
        if config_dir:
            p.add_argument("--config-dir", type=Path, default=None,
                           help="credential directory (default: $HOME/.config/lybra/relay)")
        if probe_root:
            p.add_argument("--probe-root", type=Path, default=None,
                           help="probe output root (default: $HOME/.lybra/relay/probe)")

    run = sub.add_parser("run", help="connect and record (receive-only)")
    common(run)
    run.add_argument("--max-seconds", type=float, default=None, help="stop after this many seconds (default: until Ctrl-C)")
    run.add_argument("--ws-url", default=None, help="wecom: override the WebSocket URL (wss://; ws:// only for loopback fixtures)")
    run.add_argument("--heartbeat", type=float, default=None, help="wecom: heartbeat interval seconds (default 30)")
    run.add_argument("--api-base", default=None, help="clawbot: override the iLink API base URL")
    run.add_argument("--cdn-base", default=None, help="clawbot: override the media CDN base URL")
    run.add_argument("--no-lifecycle-notify", action="store_true",
                     help="clawbot: skip notifystart/notifystop (lifecycle signals the official client sends)")

    login = sub.add_parser("login", help="clawbot: QR login, saves the issued token to the credential file (mode 600)")
    common(login)
    login.add_argument("--force", action="store_true", help="overwrite an existing credential file")
    login.add_argument("--api-base", default=None, help="override the iLink API base URL")

    check = sub.add_parser("check", help="validate the credential file (presence, mode 600, keys) without connecting")
    common(check, probe_root=False)

    summ = sub.add_parser("summarize", help="summarize what was received (text shows first 20 characters only)")
    common(summ, config_dir=False)
    return parser


def _dirs(args: argparse.Namespace) -> tuple[Path | None, Path | None]:
    config_dir = getattr(args, "config_dir", None)
    probe_root = getattr(args, "probe_root", None)
    if hasattr(args, "config_dir") and config_dir is None:
        config_dir = config.default_config_dir()
    if hasattr(args, "probe_root") and probe_root is None:
        probe_root = config.default_probe_root()
    return config_dir, probe_root


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        lang = resolve_lang(args.lang)
    except LangError as exc:
        print(str(exc), file=sys.stderr)
        return EXIT_CONFIG
    t = translator(lang)
    os.umask(0o077)
    try:
        spec = config.channel_spec(args.channel)
        config_dir, probe_root = _dirs(args)
        if args.command == "check":
            creds = config.load_credentials(config_dir, spec)
            _say(t("check_ok", path=str(config.credential_path(config_dir, spec)), keys=", ".join(sorted(creds))))
            return 0
        if args.command == "summarize":
            channel_dir = probe_root / args.channel
            if not channel_dir.is_dir():
                _say(t("summarize_empty", dir=str(channel_dir)))
                return 0
            _say(summarize(args.channel, channel_dir, t))
            return 0
        if args.command == "login":
            module = adapter(args.channel)
            if not hasattr(module, "login"):
                _say(t("login_only_clawbot", channel=args.channel))
                return EXIT_CONFIG
            store = ProbeStore(probe_root, args.channel, echo=None)
            ctx = ProbeContext(channel=args.channel, store=store, creds={}, t=t, say=_say, stop=threading.Event(),
                               options={"config_dir": config_dir, "force": args.force, "api_base": args.api_base})
            return module.login(ctx)
        creds = config.load_credentials(config_dir, spec)
    except config.ConfigError as exc:
        print(t("cfg_" + exc.code, **exc.fields), file=sys.stderr)
        return EXIT_CONFIG
    except NetPolicyError as exc:
        print(t("net_refused", error=str(exc)), file=sys.stderr)
        return EXIT_CONFIG
    except HttpError as exc:
        print(t("net_failed", error=exc.what), file=sys.stderr)
        return EXIT_NETWORK

    store = ProbeStore(probe_root, args.channel, echo=_say)
    lock = store.lock()
    try:
        lock.acquire()
    except LockBusy as exc:
        print(t("lock_busy", path=str(exc.path), holder=exc.holder), file=sys.stderr)
        return EXIT_LOCKED
    stop = threading.Event()

    def _on_signal(signum, _frame) -> None:
        stop.set()

    signal.signal(signal.SIGINT, _on_signal)
    signal.signal(signal.SIGTERM, _on_signal)
    deadline = time.monotonic() + args.max_seconds if args.max_seconds is not None else None
    ctx = ProbeContext(channel=args.channel, store=store, creds=creds, t=t, say=_say, stop=stop, deadline=deadline,
                       options={"ws_url": args.ws_url, "heartbeat": args.heartbeat, "api_base": args.api_base,
                                "cdn_base": args.cdn_base, "no_lifecycle_notify": args.no_lifecycle_notify})
    _say(t("run_start", channel=args.channel, dir=str(store.channel_dir)))
    try:
        code = adapter(args.channel).run(ctx)
    except NetPolicyError as exc:
        store.log(t("net_refused", error=str(exc)))
        code = EXIT_CONFIG
    except Exception as exc:  # record why the probe died, then re-raise (no silent swallow)
        store.log(t("probe_crashed", error=f"{type(exc).__name__}: {exc}"))
        raise
    finally:
        lock.release()
    _say(t("run_done", code=code, channel=args.channel))
    return code


if __name__ == "__main__":
    sys.exit(main())
