"""Chinese names for ok-script's global settings, which it keys and describes in English.

The Qt window translates them through ok-script's translation files; the desktop client
gets the names from the core instead, like the task fields that BD2 already names in
Chinese.  Option values stay as stored; ``option_labels`` only names them.
"""

GROUP_LABELS = {
    "Basic Options": "基本设置",
    "Notification": "通知",
    "App Launcher": "启动器",
}

# key: (label, description)
FIELD_TEXT = {
    "Auto Start Game When App Starts": (
        "打开客户端时自动启动游戏",
        "通过 Starter 启动，和开始任务时一样。",
    ),
    "Minimize Window to System Tray when Closing": (
        "关闭窗口时最小化到托盘",
        "",
    ),
    "Mute Game while in Background": ("游戏在后台时静音", ""),
    "Auto Resize Game Window": (
        "连接时自动调整窗口",
        "连上游戏时，画面不是支持的大小就自动调到最接近的 16:9。",
    ),
    "Exit App when Game Exits": ("游戏退出时关闭客户端", ""),
    "Use DirectML": (
        "用显卡加速识别（DirectML）",
        "文字识别用显卡计算。改了之后重新打开客户端生效。",
    ),
    "Trigger Interval": (
        "触发任务检查间隔",
        "自动登录等触发任务每次检查之间等待的毫秒数，调大可以降低占用。",
    ),
    "Start/Stop": ("开始 / 停止快捷键", "全局快捷键。"),
    "Kill Launcher After Start": (
        "游戏启动后关闭 Starter",
        "游戏窗口出现后结束 Starter 进程。",
    ),
    "Launch with DX11": ("用 DX11 启动", ""),
    "Enable Blur": (
        "模糊游戏内 UID 等区域",
        "在游戏画面上盖住 UID 等固定区域，减轻 OLED 烧屏。",
    ),
    "Blur Algorithm": ("模糊方式", ""),
    "Blur Interval": ("模糊刷新间隔", "两次更新遮盖之间的秒数。"),
    "System Notification": (
        "Windows 通知",
        "任务完成、出错或游戏启动失败时，在屏幕右下角弹出通知。",
    ),
    "Discord Notification": ("Discord", "发到 Discord 频道的 Webhook。"),
    "Discord Webhook": ("Webhook 地址", ""),
    "Telegram Notification": ("Telegram", "通过 Telegram Bot 发给你。"),
    "Telegram Bot Token": ("Bot Token", ""),
    "Telegram Chat ID": ("Chat ID", ""),
    "Enterprise WeChat Webhook Notification": ("企业微信群机器人", "发到企业微信群。"),
    "Enterprise WeChat Webhook URL": ("Webhook 地址", ""),
    "QQ Bot API Notification": ("QQ 频道机器人", "通过 QQ 机器人 API 发到频道。"),
    "QQ Bot API App ID": ("App ID", ""),
    "QQ Bot API Token": ("Token", ""),
    "QQ Bot API Channel ID": ("频道 ID", ""),
    "QQ Desktop Notification (Not Reliable)": (
        "QQ 桌面通知",
        "会操作电脑上的 QQ 窗口，可能打断你正在做的事，也可能发送失败。关闭后这一项不再显示。",
    ),
    "QQ Desktop Nickname": ("QQ 昵称", "接收通知的联系人。"),
    "WeChat Desktop Notification (Not Reliable)": (
        "微信桌面通知",
        "会操作电脑上的微信窗口，可能打断你正在做的事，也可能发送失败。关闭后这一项不再显示。",
    ),
    "WeChat Desktop Nickname": ("微信昵称", "接收通知的联系人。"),
    "Auto Start {app_name}": ("开机时打开启动器", "登录 Windows 后自动打开启动器。"),
    "Auto Update": ("更新方式", "启动器如何安装新版本。"),
    "SMTP Notification": ("邮件（SMTP）", "通过你的邮箱服务器发送通知邮件。"),
    "SMTP Host": ("服务器地址", "例如 smtp.qq.com。"),
    "SMTP Port": ("端口", "通常是 587 或 465。"),
    "SMTP Username": ("用户名", "登录邮箱服务器用的账号，一般就是邮箱地址。"),
    "SMTP Password": ("授权码", "在邮箱设置里生成的授权码，通常不是登录密码。"),
    "SMTP Use TLS": ("加密连接", "587 端口用 STARTTLS，465 端口用 SSL。"),
    "SMTP Default Sender": ("发件地址", ""),
    "SMTP Default Recipient": ("收件地址", "多个地址用英文逗号隔开。"),
}

OPTION_LABELS = {
    "Auto Update": {
        "Manual Update": "手动更新",
        "Automatic Update(Release Only)": "自动更新（只装正式版）",
        "Automatic Update (Pre-release)": "自动更新（包括预发布版）",
    },
    "Use DirectML": {"Auto": "自动", "Yes": "是", "No": "否"},
    "Start/Stop": {"None": "不使用"},
    "Blur Algorithm": {"Blur": "模糊", "Inpaint": "修补"},
}

# Tokens and webhook addresses: the client masks them until asked to show them.
SECRET_KEYS = {
    "Discord Webhook",
    "Telegram Bot Token",
    "Enterprise WeChat Webhook URL",
    "QQ Bot API Token",
    "SMTP Password",
}

# QQ and WeChat desktop notifications drive those programs' windows (OCR, posted clicks,
# typed text, Esc) and ok-script itself calls them unreliable; the client does not offer
# them (user decision 2026-10-23).  Saved values still apply, so a switch that is on stays
# visible to be turned off; its nickname is a sub-setting that only exists while it is on.
SHOWN_WHILE_ON = {
    "QQ Desktop Notification (Not Reliable)",
    "WeChat Desktop Notification (Not Reliable)",
}

# DX11 stays out as in Qt: the Starter launch cannot pass it (src/config.py).
OMITTED_KEYS = {
    "Launch with DX11",
    # MCP has its own confirmation and runtime control, not a plain config switch.
    "Enable MCP Server",
}

# Notification channels the client can send a test message through, with the settings
# each provider reads, in the provider's argument order.
TEST_CHANNELS = {
    "discord": ("Discord Notification", ("Discord Webhook",)),
    "telegram": ("Telegram Notification", ("Telegram Bot Token", "Telegram Chat ID")),
    "wecom": ("Enterprise WeChat Webhook Notification", ("Enterprise WeChat Webhook URL",)),
    "qqbot": (
        "QQ Bot API Notification",
        ("QQ Bot API App ID", "QQ Bot API Token", "QQ Bot API Channel ID"),
    ),
    "smtp": (
        "SMTP Notification",
        (
            "SMTP Host",
            "SMTP Port",
            "SMTP Username",
            "SMTP Password",
            "SMTP Use TLS",
            "SMTP Default Sender",
            "SMTP Default Recipient",
        ),
    ),
}
# A mail server may take no login, and an unticked box reads as empty.
OPTIONAL_TEST_KEYS = {"SMTP Username", "SMTP Password", "SMTP Use TLS"}


def describe_group(group):
    """Add Chinese names to one ``/api/settings`` group and drop the omitted fields."""
    fields = []
    for field in group["fields"]:
        key = field["key"]
        if key in OMITTED_KEYS or (key in SHOWN_WHILE_ON and not field.get("value")):
            continue
        label, description = FIELD_TEXT.get(key, (key, field.get("description") or ""))
        field = dict(field, label=label, description=description)
        if key in OPTION_LABELS:
            field["option_labels"] = OPTION_LABELS[key]
        if key in SECRET_KEYS:
            field["secret"] = True
        fields.append(field)
    name = group["name"]
    return dict(group, label=GROUP_LABELS.get(name, name), fields=fields)
