from __future__ import annotations

# 「应用更新」卡片状态标签的稳定可读宽度。上游 UpdateCard 把 status_label 放在
# addStretch(1) 占位符之前且 stretch=0，布局把富余宽度全给占位符，标签宽度被钉在
# sizeHint：中文短句 70px 折两行、错误长文最多 294px 折五行。离屏实验
# （.local-dev/experiments/update_card_layout/run.py）证实定宽 260 在 750/950/1250
# 三档窗口宽度下都稳定可读，而 setStretchFactor 方案在 750px 时会把标签挤到 14px
# 一字一行，不可用。
UPDATE_CARD_STATUS_MIN_WIDTH = 260

# 单行宽度超过该值的长文本（错误详情、双语指引）才启用 260 定宽；短状态保持自然
# 宽度，避免与版本下拉同行时在 600px 最小窗口宽度下把右侧按钮挤出视野。
UPDATE_CARD_STATUS_SINGLE_LINE_MAX = 280

# pyappify.get_version_list 要求启动器 PYAPPIFY_VERSION >= 1.2.2 才支持“检查更新”
# （pyappify/__init__.py 的 _is_supported_pyappify_version）；更老的启动器只会抛
# RuntimeError。提前拦截并给出下载重装指引，避免把英文异常直接甩给用户。
MIN_CHECK_UPDATES_LAUNCHER_VERSION = (1, 2, 2)

PATCH_MARKER = "_ok_bd2_update_card_ui_enabled"


def parse_launcher_version(version) -> tuple[int, ...] | None:
    """解析启动器版本号为全数字元组；无法解析（如开发环境未注入）时返回 None。"""
    if not isinstance(version, str):
        return None
    parts = version.lstrip("v").split(".")
    if not parts or not all(part.isdigit() for part in parts):
        return None
    return tuple(int(part) for part in parts)


def launcher_supports_update_check(version) -> bool | None:
    """启动器是否支持检查更新；版本不可解析时返回 None（不拦截，交由上游处理）。"""
    parsed = parse_launcher_version(version)
    if parsed is None:
        return None
    return parsed >= MIN_CHECK_UPDATES_LAUNCHER_VERSION


def _too_old_message(version) -> str:
    return (
        f"启动器版本 {version} 过旧，无法检查更新；请点击「下载」重新安装最新版以升级启动器。"
        f"Launcher {version} is too old to check for updates; click Download to reinstall."
    )


def install_update_card_ui() -> None:
    from ok.ui.qt.about.UpdateCard import UpdateCard

    if getattr(UpdateCard, PATCH_MARKER, False):
        return

    original_init = UpdateCard.__init__
    original_check_for_updates = UpdateCard.check_for_updates
    original_set_status = UpdateCard._set_status

    def stable_init(self, *args, **kwargs):
        original_init(self, *args, **kwargs)
        self.status_label.setMinimumWidth(0)

    def stable_check_for_updates(self):
        version = getattr(self.pyappify_module, "pyappify_version", None)
        if launcher_supports_update_check(version) is False:
            self._show_error(_too_old_message(version))
            return
        original_check_for_updates(self)

    def stable_set_status(self, message, error=False):
        original_set_status(self, message, error)
        metrics = self.status_label.fontMetrics()
        if metrics.horizontalAdvance(message) > UPDATE_CARD_STATUS_SINGLE_LINE_MAX:
            self.status_label.setMinimumWidth(UPDATE_CARD_STATUS_MIN_WIDTH)
        else:
            self.status_label.setMinimumWidth(0)

    UpdateCard.__init__ = stable_init
    UpdateCard.check_for_updates = stable_check_for_updates
    UpdateCard._set_status = stable_set_status
    setattr(UpdateCard, PATCH_MARKER, True)
