"""Every user-facing line of the relay probe, in both supported languages (zh / en), in one table.

Language: --lang > environment variable LYBRA_LANG > "zh" (same variable and default as the Lybra CLI; the relay
does not import the gate's i18n module because relay/ must stay independent of gate internals).
Texts never interpolate credential values; callers pass paths, key names, counts and protocol codes only.
"""
from __future__ import annotations

import os

SUPPORTED = ("zh", "en")
DEFAULT_LANG = "zh"
LANG_ENV = "LYBRA_LANG"

TEXTS: dict[str, dict[str, str]] = {
    "zh": {
        "bad_lang": "语言取值 {value!r} 不支持(可用: zh / en)。",
        "cfg_no_home": "拒启动: 环境变量 HOME 未设置, 无法推导缺省路径; 请设置 HOME 或显式给 --config-dir / --probe-root。",
        "cfg_unknown_channel": "拒启动: 未知渠道 {channel!r}(可用: {known})。",
        "cfg_bad_line": "拒启动: 凭据文件 {path} 第 {line} 行不是 KEY=VALUE 格式(值不显示)。",
        "cfg_missing_file": "拒启动: 凭据文件不存在: {path}\n  请按 docs/relay-probe-setup.zh-CN.md 建好该文件并 chmod 600。",
        "cfg_not_regular_file": "拒启动: {path} 不是普通文件(不接受符号链接 / 目录)。",
        "cfg_wrong_owner": "拒启动: {path} 不属于当前用户。",
        "cfg_mode_too_open": "拒启动: 凭据文件 {path} 权限为 {mode}, 过宽(组 / 其他人可读写)。\n  请执行: chmod 600 {path}",
        "cfg_missing_keys": "拒启动: 凭据文件 {path} 缺键或值为空: {keys}",
        "cfg_unknown_keys": "拒启动: 凭据文件 {path} 含未声明的键: {keys}(允许: {allowed})",
        "cfg_file_exists": "拒绝覆盖: {path} 已存在; 确要重新登录覆盖请加 --force。",
        "lock_busy": "拒启动: 本渠道已有探针在运行(锁 {path}, 进程 {holder})。同一机器人同一时间只能有一个有效连接。",
        "run_start": "探针启动: 渠道 {channel}, 只收不发; 记录目录 {dir}。按 Ctrl-C 停止。",
        "run_done": "探针已停止(退出码 {code})。查看汇总: python3 -m relay.probe summarize --channel {channel}",
        "check_ok": "凭据文件检查通过: {path}(权限 600; 键: {keys}; 值不显示)",
        "login_only_clawbot": "渠道 {channel} 不需要扫码登录(凭据由管理后台给出, 写进凭据文件即可)。",
        "net_refused": "拒绝连接: {error}",
        "net_failed": "网络请求失败: {error}",
        "probe_crashed": "探针异常退出: {error}",
        "summarize_empty": "还没有记录: {dir}",
        "frame_not_json": "第 {seq} 帧不是 JSON(已原样落盘)。",
        "attachment_failed": "附件下载失败: {path}(msgid {msgid}): {error}",
        "attachment_saved": "附件已保存: {path}(msgid {msgid}), 下载 {size} 字节, 解密 {status}, 文件 {name}",
        "attachment_crashed": "附件处理异常: {error}",
        "wecom_connected": "已连上企业微信长连接, 正在订阅(aibot_subscribe)。",
        "wecom_subscribed": "订阅成功, 开始接收。只做心跳(ping), 不回复任何消息。",
        "wecom_subscribe_timeout": "订阅 15 秒无应答, 重连。",
        "wecom_auth_failed": "订阅被拒(errcode={errcode}, errmsg={errmsg}): 请核对 BotID / Secret 是否为长连接模式所给; 不自动重试(订阅有频率限制)。",
        "wecom_heartbeat_lost": "连续 {missed} 次心跳无应答, 重连。",
        "wecom_closed": "服务端关闭连接(code={code}, reason={reason}), 重连。",
        "wecom_socket_error": "连接出错: {error}, 重连。",
        "wecom_connect_failed": "第 {attempt} 次连接失败: {error}",
        "wecom_message": "收到消息 #{seq}: msgtype={msgtype}, chattype={chattype}, 附件 {media} 个(正文不显示)。",
        "wecom_event": "收到事件 #{seq}: {eventtype}",
        "wecom_other_frame": "收到其他帧 #{seq}: cmd={cmd}",
        "wecom_replaced": "收到 disconnected_event: 另一个连接接管了这个机器人, 本探针停止(不重连, 以免把对方踢下线)。",
        "probe_stopped": "探针结束: 消息 {messages} 条, 事件 {events} 个。",
        "clawbot_polling": "ClawBot 长轮询中(getupdates), 只收不发。",
        "clawbot_lifecycle_failed": "生命周期通知 {endpoint} 失败: {error}(不影响接收)",
        "clawbot_poll_failed": "第 {attempt} 次拉取失败: {error}",
        "clawbot_session_expired": "会话已过期(-14): 请重新执行 python3 -m relay.probe login --channel clawbot --force 扫码。",
        "clawbot_bad_media_key": "消息 {msgid} 的媒体密钥无法解析: {error}(原始帧已落盘)",
        "clawbot_message": "收到消息 #{seq}: 条目类型 [{types}], 附件 {media} 个(正文不显示)。",
        "clawbot_stopped": "探针结束: 消息 {messages} 条, 空轮询 {empty} 次。",
        "clawbot_qr_unavailable": "取不到登录二维码(服务端未返回二维码)。",
        "clawbot_qr_link": "请用手机微信扫码确认(微信需 8.0.69+ 且已开通 ClawBot 插件)。二维码链接(在电脑浏览器打开后用手机微信扫码, 或直接在手机微信里打开):\n  {link}",
        "clawbot_qr_poll_failed": "查询扫码状态失败: {error}",
        "clawbot_qr_no_token": "扫码已确认但服务端未给出凭据, 请重试。",
        "clawbot_login_saved": "登录成功, 凭据已写入 {path}(权限 600, 值不显示)。",
        "clawbot_qr_scanned": "已扫码, 请在手机上确认。",
        "clawbot_qr_verify_prompt": "请输入手机微信上显示的验证码: ",
        "clawbot_qr_verify_blocked": "验证码错误次数过多, 请稍后重新登录。",
        "clawbot_qr_already_bound": "服务端返回 binded_redirect(该 ClawBot 已绑定到其他客户端), 未签发新凭据。",
        "clawbot_qr_expired": "二维码已过期, 重新获取。",
        "clawbot_qr_timeout": "等待扫码超时, 请重新执行 login。",
        "sum_header": "渠道 {channel} 探针汇总 — 目录 {dir}\n  原始帧 {frames} 条, 业务消息 {messages} 条, 附件 {attachments} 个",
        "sum_other": "  其他帧: {items}",
        "sum_messages_title": "逐条消息(正文只显示前 20 字):",
        "sum_undocumented": "(官方文档未列的类型)",
        "sum_preview": "正文: {text}",
        "sum_attachment": "附件 {path}: 解密 {decrypt}, 文件名 {name}, 大小 {size}, 类型 {kind}, sha256 {sha}, 错误 {error}",
        "sum_fields_title": "按消息类型列出出现过的字段:",
        "sum_questions_title": "四个问题:",
        "sum_q_forward": "合并转发是否展开为逐条: 官方文档未列的消息类型 = {undocumented}; 含多个条目的消息(#序号:条目数) = {multi}",
        "sum_q_images": "图片是否可下载: 图片附件 {total} 个, 下载并解密成功 {ok} 个",
        "sum_q_file": "文件: 文件名 {name}, 实际大小 {size}, 消息声明大小 {declared}, 解密 {decrypt}",
        "sum_q_no_file": "文件: 没收到文件附件",
        "sum_q_title": "群名 / 标题类字段: {fields}",
    },
    "en": {
        "bad_lang": "Unsupported language {value!r} (use zh or en).",
        "cfg_no_home": "Refusing to start: HOME is not set, so default paths cannot be derived; set HOME or pass --config-dir / --probe-root.",
        "cfg_unknown_channel": "Refusing to start: unknown channel {channel!r} (known: {known}).",
        "cfg_bad_line": "Refusing to start: credential file {path} line {line} is not KEY=VALUE (value not shown).",
        "cfg_missing_file": "Refusing to start: credential file not found: {path}\n  Create it as described in docs/relay-probe-setup.md and chmod 600 it.",
        "cfg_not_regular_file": "Refusing to start: {path} is not a regular file (symlinks / directories are not accepted).",
        "cfg_wrong_owner": "Refusing to start: {path} is not owned by the current user.",
        "cfg_mode_too_open": "Refusing to start: credential file {path} has mode {mode}, too open (group/other access).\n  Run: chmod 600 {path}",
        "cfg_missing_keys": "Refusing to start: credential file {path} is missing keys or has empty values: {keys}",
        "cfg_unknown_keys": "Refusing to start: credential file {path} has undeclared keys: {keys} (allowed: {allowed})",
        "cfg_file_exists": "Not overwriting: {path} exists; pass --force to log in again and replace it.",
        "lock_busy": "Refusing to start: a probe for this channel is already running (lock {path}, pid {holder}). A bot allows one live connection at a time.",
        "run_start": "Probe started: channel {channel}, receive-only; recording to {dir}. Press Ctrl-C to stop.",
        "run_done": "Probe stopped (exit code {code}). Summary: python3 -m relay.probe summarize --channel {channel}",
        "check_ok": "Credential file OK: {path} (mode 600; keys: {keys}; values not shown)",
        "login_only_clawbot": "Channel {channel} needs no QR login (its credentials come from the admin console; put them in the credential file).",
        "net_refused": "Connection refused by policy: {error}",
        "net_failed": "Network request failed: {error}",
        "probe_crashed": "Probe crashed: {error}",
        "summarize_empty": "Nothing recorded yet: {dir}",
        "frame_not_json": "Frame #{seq} is not JSON (stored as received).",
        "attachment_failed": "Attachment download failed: {path} (msgid {msgid}): {error}",
        "attachment_saved": "Attachment saved: {path} (msgid {msgid}), downloaded {size} bytes, decrypt {status}, file {name}",
        "attachment_crashed": "Attachment handling crashed: {error}",
        "wecom_connected": "Connected to the WeCom long connection; subscribing (aibot_subscribe).",
        "wecom_subscribed": "Subscribed; receiving. Heartbeats (ping) only, no replies to any message.",
        "wecom_subscribe_timeout": "No subscribe answer within 15 s; reconnecting.",
        "wecom_auth_failed": "Subscribe refused (errcode={errcode}, errmsg={errmsg}): check that BotID / Secret are the long-connection ones; not retrying (subscribe is rate-limited).",
        "wecom_heartbeat_lost": "{missed} heartbeats unanswered; reconnecting.",
        "wecom_closed": "Server closed the connection (code={code}, reason={reason}); reconnecting.",
        "wecom_socket_error": "Connection error: {error}; reconnecting.",
        "wecom_connect_failed": "Connect attempt {attempt} failed: {error}",
        "wecom_message": "Message #{seq}: msgtype={msgtype}, chattype={chattype}, {media} attachment(s) (content not shown).",
        "wecom_event": "Event #{seq}: {eventtype}",
        "wecom_other_frame": "Other frame #{seq}: cmd={cmd}",
        "wecom_replaced": "disconnected_event received: another connection took over this bot; stopping (no reconnect, so the other side is not kicked off).",
        "probe_stopped": "Probe finished: {messages} message(s), {events} event(s).",
        "clawbot_polling": "ClawBot long polling (getupdates), receive-only.",
        "clawbot_lifecycle_failed": "Lifecycle notice {endpoint} failed: {error} (receiving is unaffected)",
        "clawbot_poll_failed": "Poll attempt {attempt} failed: {error}",
        "clawbot_session_expired": "Session expired (-14): run python3 -m relay.probe login --channel clawbot --force and scan again.",
        "clawbot_bad_media_key": "Media key of message {msgid} cannot be parsed: {error} (raw frame is stored)",
        "clawbot_message": "Message #{seq}: item types [{types}], {media} attachment(s) (content not shown).",
        "clawbot_stopped": "Probe finished: {messages} message(s), {empty} empty poll(s).",
        "clawbot_qr_unavailable": "Could not get a login QR code (server returned none).",
        "clawbot_qr_link": "Scan with WeChat on your phone and confirm (WeChat 8.0.69+ with the ClawBot plugin enabled). QR link (open it in a desktop browser and scan, or open it inside WeChat on the phone):\n  {link}",
        "clawbot_qr_poll_failed": "QR status check failed: {error}",
        "clawbot_qr_no_token": "Scan confirmed but the server issued no credential; try again.",
        "clawbot_login_saved": "Logged in; credentials written to {path} (mode 600, values not shown).",
        "clawbot_qr_scanned": "Scanned; confirm on the phone.",
        "clawbot_qr_verify_prompt": "Enter the verification code shown in WeChat on your phone: ",
        "clawbot_qr_verify_blocked": "Too many wrong verification codes; log in again later.",
        "clawbot_qr_already_bound": "Server answered binded_redirect (this ClawBot is bound to another client); no new credential issued.",
        "clawbot_qr_expired": "QR code expired; fetching a new one.",
        "clawbot_qr_timeout": "Timed out waiting for the scan; run login again.",
        "sum_header": "Probe summary for channel {channel} — {dir}\n  {frames} raw frame(s), {messages} message(s), {attachments} attachment(s)",
        "sum_other": "  Other frames: {items}",
        "sum_messages_title": "Messages (text shows the first 20 characters only):",
        "sum_undocumented": "(type not in the official docs)",
        "sum_preview": "text: {text}",
        "sum_attachment": "attachment {path}: decrypt {decrypt}, name {name}, size {size}, type {kind}, sha256 {sha}, error {error}",
        "sum_fields_title": "Fields seen, per message type:",
        "sum_questions_title": "The four questions:",
        "sum_q_forward": "Is a merged forward expanded into items: undocumented message types = {undocumented}; messages with several items (#seq:items) = {multi}",
        "sum_q_images": "Are images downloadable: {total} image attachment(s), {ok} downloaded and decrypted",
        "sum_q_file": "File: name {name}, actual size {size}, size declared in the message {declared}, decrypt {decrypt}",
        "sum_q_no_file": "File: no file attachment received",
        "sum_q_title": "Group name / title-like fields: {fields}",
    },
}


class LangError(ValueError):
    pass


def resolve_lang(flag: str | None, env: dict[str, str] | None = None) -> str:
    source = os.environ if env is None else env
    value = (flag or source.get(LANG_ENV) or DEFAULT_LANG).strip()
    if value not in SUPPORTED:
        raise LangError(TEXTS[DEFAULT_LANG]["bad_lang"].format(value=value))
    return value


def translator(lang: str):
    table = TEXTS[lang]

    def t(key: str, **fields: object) -> str:
        return table[key].format(**fields)

    return t
