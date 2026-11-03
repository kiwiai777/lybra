# External communication gateway · receive-only probe: setup and test checklist

[中文](relay-probe-setup.zh-CN.md)

## What this is

This is the first step of the external communication gateway. A **receive-only** probe records exactly what the bots receive, and the real design will be based on those results.

- The probe is a separate subsystem in the Lybra product repo, `relay/`. It runs as its own process with its own credentials. The gate never imports it, and it never connects to the gate or runs a gate command.
- The probe never replies to a message. It only sends the frames the protocols require: subscribe and heartbeat for WeCom; login, polling and lifecycle notices for ClawBot.
- Received text and attachments stay on this machine under `~/.lybra/relay/probe/` (directories 700, files 600), outside every git repo.
- Credentials stay on this machine under `~/.config/lybra/relay/` (mode 600). The Owner puts them there; advisors and agents never handle them. The probe never prints a credential value or writes one to a log.

## Preparation

- Run on the machine that holds the governance root. You need Python 3.10+ and outbound https / wss. No third-party package is needed.
- After this card is merged, go to the Lybra product repo checkout: `cd <Lybra product repo>`. Run every command below from there.
- Output is in Chinese by default. Add `--lang en`, or set `LYBRA_LANG=en`, for English.

## 1. WeCom smart bot (long-connection mode)

1. Create the bot. In the WeCom client, open Workbench → Smart Bot (智能机器人) → Create bot → Create manually, then choose "Create in API mode" at the bottom of the page.
   - Any name works. Under "members who can use it", select only the Owner.
   - In the API settings, set the connection type to long connection (使用长连接).
   - Note the **Bot ID**, click "Get Secret" and note the **Secret**, then save.
   - The long-connection Secret is not the callback-URL mode's Token / EncodingAESKey. Don't mix them.
   - If Smart Bot is missing from the Workbench, update the client or ask the WeCom admin to enable it.
   - The UI steps come from third-party integration guides and may change between versions. The protocol reference is the official [long-connection document](https://developer.work.weixin.qq.com/document/path/101463).
2. Store the credentials. Create the file in an editor rather than with `echo`, so the Secret stays out of shell history:
   ```bash
   mkdir -p ~/.config/lybra/relay && chmod 700 ~/.config/lybra/relay
   nano ~/.config/lybra/relay/wecom.env
   chmod 600 ~/.config/lybra/relay/wecom.env
   ```
   The file has two lines. Replace the angle-bracket parts with the real values:
   ```text
   WECOM_BOT_ID=<Bot ID>
   WECOM_BOT_SECRET=<Secret>
   ```
3. Check the credentials. This makes no network call. It checks that the file exists, that its mode is 600 and that both keys are present, and it prints no values:
   ```bash
   python3 -m relay.probe check --channel wecom
   ```
4. Start the probe. It runs in the foreground; press Ctrl-C to stop it. For long runs, start it inside tmux or screen:
   ```bash
   python3 -m relay.probe run --channel wecom
   ```
   When you see "subscribed; receiving", the probe is connected.
5. A bot can have only one live connection at a time.
   - If another program (for example cc-connect) is connected to the same bot, whichever side connects later kicks the other off.
   - When the probe is kicked off, it receives `disconnected_event` and exits with code 5. It never kicks the other side off in return.
   - A second probe on the same machine is refused by the single-instance lock (exit code 3).

## 2. Weixin ClawBot

1. Enable ClawBot. Update WeChat on the phone to Android 8.0.69 / iOS 8.0.70 or later, then go to Me → Settings → Plugins (我 → 设置 → 插件) and enable ClawBot.
   - ClawBot is rolling out gradually. If the entry is missing, this account has not received it yet.
   - This path comes from launch-day press reports, not from a Tencent developer document.
2. Log in by QR code. Run this on the machine:
   ```bash
   python3 -m relay.probe login --channel clawbot
   ```
   - The terminal prints a QR link. Open it in a desktop browser, then scan the QR code with WeChat on the phone and confirm. You can also open the link inside WeChat on the phone.
   - If asked for a verification code, type the code shown on the phone into the terminal.
   - On success, the credentials are written to `~/.config/lybra/relay/clawbot.env` (mode 600). The terminal does not show the values.
   - An existing file is never overwritten. Add `--force` to log in again.
3. Start the probe:
   ```bash
   python3 -m relay.probe run --channel clawbot
   ```
   - When the session expires, the probe asks you to log in again and exits with code 6. Run `login --channel clawbot --force` and scan again.
4. About the channel:
   - The probe uses the official WeChat ClawBot channel: the iLink protocol of Tencent's own plugin `@tencent-weixin/openclaw-weixin`. Tencent publishes the protocol document at [Tencent/openclaw-weixin](https://github.com/Tencent/openclaw-weixin/blob/main/docs/protocol.md).
   - No iPad protocol, PC injection, web protocol or UI automation is involved. The probe identifies itself to the server as `bot_agent=LybraRelayProbe/0.1`.
   - The research found no Tencent terms on whether a non-OpenClaw client may use this protocol directly: nothing allows it and nothing forbids it. If the Owner does not accept that uncertainty, test only WeCom for now.
   - ClawBot supports direct chats only. It cannot be added to a group.

## 3. The four samples to send

Send each sample on both channels, in a direct chat with the bot (the WeCom smart bot in WeCom, ClawBot in WeChat).

1. **Merged forward (chat history) with a screenshot**: in a group, multi-select several messages (at least one screenshot), choose "merge and forward", and send it to the bot.
2. **Item-by-item forward**: multi-select 3 messages (text and an image), choose "forward one by one", and send them to the bot.
3. **Single image**: send one screenshot.
4. **File**: send one PDF or Excel file.

Tips:

- Wait at least 10 seconds between samples, and note the order and time you sent them.
- Prefer non-sensitive content. The raw text stays on this machine, but there is no need to take the risk.
- WeCom can only forward WeCom conversations. Customer-group messages that live in personal WeChat can only be forwarded to ClawBot.

## 4. Where the results are and how to read them

Records go to `~/.lybra/relay/probe/<channel>/<date>/`:

| File | Content |
|---|---|
| `frames.jsonl` | Every received frame. The `raw` field holds the frame text exactly as received. |
| `sent.jsonl` | Protocol frames the probe sent (command name and id only, no content) |
| `attachments.jsonl` + `attachments/` | Attachments. Both the encrypted original and the decrypted file are kept, each with its sha256. |
| `probe.log` | Status lines |

Run the summary command:

```bash
python3 -m relay.probe summarize --channel wecom
python3 -m relay.probe summarize --channel clawbot
```

The summary lists each message with its type, item breakdown and attachments (whether they downloaded and decrypted, file name, size, sha256). Message text shows the first 20 characters only. It also lists every field seen per message type, and answers the four questions:

1. Is a merged forward expanded into individual items?
2. Can images be downloaded?
3. What are the file names and sizes?
4. Is there a group-name or title field?

Paste the summarize output to the advisor. `frames.jsonl` and the attachments hold the full text, so keep them on this machine.

## 5. Exit codes

| Code | Meaning |
|---|---|
| 0 | Normal stop (Ctrl-C or `--max-seconds` reached) |
| 2 | Refused to start: credential file missing, mode not 600, missing keys, or disallowed URL |
| 3 | A probe for this channel is already running (single-instance lock) |
| 4 | Authentication failed (WeCom subscribe refused, or the ClawBot credential is invalid) |
| 5 | WeCom: another connection took over (`disconnected_event`) |
| 6 | ClawBot: session expired; run `login` again |
| 7 | Network kept failing |

To stop the probe, press Ctrl-C in the foreground. In the background, run `kill <pid>`; the pid is in `~/.lybra/relay/probe/<channel>/probe.lock`. When testing is done, the Owner deletes `~/.lybra/relay/probe/` and `~/.config/lybra/relay/` themselves.
