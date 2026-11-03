# 对外沟通关口 · 实测探针操作清单(只收不发)

[English](relay-probe-setup.md)

## 这是什么

对外沟通关口的第一步:先用一个**只收不发**的探针,把机器人实际收到的内容原样记下来,再按真实结果定正式设计。

- 探针是 Lybra 产品仓里的独立子系统 `relay/`,单独的进程、单独的凭据。门不引用它,它也不连门、不调任何门命令。
- 探针不回复任何消息。它只发协议要求的帧:企业微信发订阅和心跳,ClawBot 发登录、拉取和生命周期通知。
- 收到的原文和附件只存在本机 `~/.lybra/relay/probe/` 下(目录权限 700,文件权限 600),不在任何 git 仓里。
- 凭据只放在本机 `~/.config/lybra/relay/` 下(权限 600)。由 Owner 自己放,顾问和 agent 不经手。探针在任何输出和日志里都不显示凭据值。

## 准备

- 在治理根所在的机器上运行。需要 Python 3.10 以上,能访问外网(https / wss)。不需要装任何第三方包。
- 本卡合入后,先进入 Lybra 产品仓检出目录:`cd <Lybra 产品仓>`。下面的命令都在这个目录里执行。
- 输出语言默认中文。想看英文就加 `--lang en`,或设环境变量 `LYBRA_LANG=en`。

## 一、企业微信智能机器人(长连接模式)

1. 建机器人。打开企业微信客户端,进入「工作台 → 智能机器人 → 创建机器人 → 手动创建」,选页面最下方的「API 模式创建」。
   - 名称随意,「可使用成员」只选 Owner 自己。
   - 在 API 配置里,把连接方式设为「使用长连接」。
   - 记下页面上的 **Bot ID**,点「获取 Secret」记下 **Secret**,再保存。
   - 长连接的 Secret 和回调 URL 模式的 Token / EncodingAESKey 是两套凭据,不能混用。
   - 工作台里找不到「智能机器人」时,先升级客户端,或请企业管理员确认已开通。
   - 界面步骤来自第三方接入文档(界面可能随版本变化)。协议依据是官方文档[智能机器人长连接](https://developer.work.weixin.qq.com/document/path/101463)。
2. 放凭据。用编辑器建文件,不要用 `echo`,免得 Secret 进 shell 历史:
   ```bash
   mkdir -p ~/.config/lybra/relay && chmod 700 ~/.config/lybra/relay
   nano ~/.config/lybra/relay/wecom.env
   chmod 600 ~/.config/lybra/relay/wecom.env
   ```
   文件内容就两行,把尖括号部分换成实际值:
   ```text
   WECOM_BOT_ID=<Bot ID>
   WECOM_BOT_SECRET=<Secret>
   ```
3. 检查凭据。这一步不联网,只检查文件在不在、权限对不对、键全不全,不显示任何值:
   ```bash
   python3 -m relay.probe check --channel wecom
   ```
4. 启动。探针在前台运行,按 Ctrl-C 停止。要长时间挂着,就在 tmux 或 screen 里跑:
   ```bash
   python3 -m relay.probe run --channel wecom
   ```
   看到「订阅成功,开始接收」就说明连上了。
5. 注意:同一个机器人同一时间只能有一个有效连接。
   - 如果别的程序(比如 cc-connect)也连着这个机器人,后连上的一方会把先连上的踢下线。
   - 探针被踢时会收到 `disconnected_event`,随即退出(退出码 5),不会反过来去踢对方。
   - 同一台机器上第二个探针会被单实例锁拒绝(退出码 3)。

## 二、个人微信 ClawBot

1. 开通 ClawBot。手机微信要升级到 Android 8.0.69 / iOS 8.0.70 及以上,再进「我 → 设置 → 插件」开通 ClawBot。
   - ClawBot 是灰度放量的,找不到入口说明还没轮到这个账号。
   - 开通路径来自发布当日的报道,不是腾讯开发文档。
2. 扫码登录。在本机执行:
   ```bash
   python3 -m relay.probe login --channel clawbot
   ```
   - 终端会打印一条二维码链接。在电脑浏览器里打开它,再用手机微信扫码并确认。也可以直接在手机微信里打开这条链接。
   - 如果提示输入验证码,就在终端输入手机上显示的验证码。
   - 登录成功后,凭据自动写入 `~/.config/lybra/relay/clawbot.env`(权限 600,终端不显示凭据值)。
   - 文件已存在时不会覆盖;要重新登录就加 `--force`。
3. 启动:
   ```bash
   python3 -m relay.probe run --channel clawbot
   ```
   - 会话过期时探针提示重新登录并退出(退出码 6)。这时重跑 `login --channel clawbot --force` 再扫一次码。
4. 通道说明:
   - 探针走的是微信官方 ClawBot 通道,即腾讯发布的 `@tencent-weixin/openclaw-weixin` 插件所用的 iLink 协议。协议文档由腾讯公开在 [Tencent/openclaw-weixin](https://github.com/Tencent/openclaw-weixin/blob/main/docs/protocol_zh_CN.md)。
   - 不用 iPad 协议、PC 注入、网页协议或界面自动化。探针向服务端如实报身份 `bot_agent=LybraRelayProbe/0.1`。
   - 调研没找到腾讯对「非 OpenClaw 客户端直接使用这套协议」的明文条款,既没有允许,也没有禁止。Owner 不接受这一点,就先只测企业微信。
   - ClawBot 只能私聊,不能拉进群。

## 三、要发的四种样本

两个渠道各发一遍,都是在私聊里发给机器人(企业微信里发给智能机器人,微信里发给 ClawBot)。

1. **合并转发(聊天记录)带截图**:在群里多选几条消息(至少含一张截图),选「合并转发」,发给机器人。
2. **逐条转发**:多选 3 条(文字加图片),选「逐条转发」,发给机器人。
3. **单图**:发一张截图。
4. **文件**:发一个 PDF 或 Excel。

建议:

- 每种之间隔 10 秒以上,并记下发送的顺序和时间。
- 样本尽量用不敏感的内容。原文只存在本机,但没必要冒这个险。
- 企业微信里只能转发企业微信自己的会话。个人微信里的客户群消息,只能在个人微信里转给 ClawBot。

## 四、结果在哪,怎么看

记录目录是 `~/.lybra/relay/probe/<渠道>/<日期>/`:

| 文件 | 内容 |
|---|---|
| `frames.jsonl` | 收到的每一帧,`raw` 字段是原文,一字不改 |
| `sent.jsonl` | 探针发出的协议帧(只记命令名和编号,不记内容) |
| `attachments.jsonl` + `attachments/` | 附件。同时保存加密原件和解密后的文件,各记 sha256 |
| `probe.log` | 运行状态 |

运行汇总命令:

```bash
python3 -m relay.probe summarize --channel wecom
python3 -m relay.probe summarize --channel clawbot
```

汇总按条列出消息类型、条目构成、附件(能否下载和解密、文件名、大小、sha256),正文只显示前 20 字。另外列出每种消息里出现过的字段,并回答四个问题:

1. 合并转发是否展开为逐条;
2. 图片能否下载;
3. 文件名和大小;
4. 有没有群名或标题类字段。

把 summarize 的输出贴给顾问即可。`frames.jsonl` 和附件里是完整原文,只留在本机。

## 五、退出码

| 码 | 含义 |
|---|---|
| 0 | 正常停止(Ctrl-C 或 `--max-seconds` 到点) |
| 2 | 拒启动:凭据文件缺失、权限不是 600、缺键,或地址不合规 |
| 3 | 本渠道已有探针在运行(单实例锁) |
| 4 | 鉴权失败(企业微信订阅被拒,或 ClawBot 凭据无效) |
| 5 | 企业微信:被另一个连接接管(`disconnected_event`) |
| 6 | ClawBot:会话过期,需要重新 `login` |
| 7 | 网络连续失败 |

停止探针:在前台按 Ctrl-C;在后台就执行 `kill <进程号>`,进程号在 `~/.lybra/relay/probe/<渠道>/probe.lock` 里。测完要删数据,由 Owner 自己删 `~/.lybra/relay/probe/` 和 `~/.config/lybra/relay/`。
