"""Chinese names for ok-script's global settings, which it keys and describes in English.

The Qt window translates them through ok-script's translation files; the desktop client
gets the names from the core instead, like the task fields that BD2 already names in
Chinese.  Option values stay as stored; ``option_labels`` only names them.
"""

GROUP_LABELS = {
    "Basic Options": "基本设置",
    "Notification": "通知",
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
}

OPTION_LABELS = {
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
}

# QQ and WeChat desktop notifications drive those programs' windows (OCR, posted clicks,
# typed text, Esc) and ok-script itself calls them unreliable; the client leaves them
# out (user decision 2026-10-23).  Saved values still apply; the Qt window shows them.
# DX11 stays out as in Qt: the Starter launch cannot pass it (src/config.py).
OMITTED_KEYS = {
    "QQ Desktop Notification (Not Reliable)",
    "QQ Desktop Nickname",
    "WeChat Desktop Notification (Not Reliable)",
    "WeChat Desktop Nickname",
    "Launch with DX11",
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
}


def describe_group(group):
    """Add Chinese names to one ``/api/settings`` group and drop the omitted fields."""
    fields = []
    for field in group["fields"]:
        key = field["key"]
        if key in OMITTED_KEYS:
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
