"""Task-local, offline illustrated guides for the Fluent task cards."""

from pathlib import Path

from PySide6.QtCore import QSignalBlocker, QSize, Qt, QUrl
from PySide6.QtGui import (
    QDesktopServices,
    QFont,
    QImage,
    QPixmap,
    QTextBlockFormat,
    QTextCharFormat,
    QTextCursor,
    QTextDocument,
)
from PySide6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QScrollArea,
    QTextBrowser,
    QVBoxLayout,
    QWidget,
)
from qfluentwidgets import PushButton, qconfig

from src.ui.quest_theme import APP_FONT_FAMILIES, palette

GUIDE_ROOT = Path(__file__).resolve().parents[2] / "docs" / "guides"
GUIDES = {
    "MapCollectionTask": ("每周跑图", "collection", "选择卡带，按周进度完成采集。"),
    "MapTradeTask": ("每日跑商", "trade", "先确认买卖范围，再开始每日跑商。"),
    "QuickHuntTask": ("快速狩猎", "quick-hunt", "安排米饭用途，按需补充属性圣石。"),
}


def size_dialog(dialog, width, height):
    screen = dialog.screen().availableGeometry()
    dialog.resize(min(width, screen.width() - 48), min(height, screen.height() - 80))


class GuideBrowser(QTextBrowser):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setOpenLinks(False)
        self.setFrameShape(QTextBrowser.NoFrame)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        font = QFont()
        font.setFamilies(list(APP_FONT_FAMILIES))
        font.setPointSize(11)
        self.document().setDefaultFont(font)
        self.document().setDocumentMargin(26)

    def fit_images(self):
        available = max(80, self.viewport().width() - 60)
        block = self.document().begin()
        while block.isValid():
            iterator = block.begin()
            while not iterator.atEnd():
                fragment = iterator.fragment()
                if fragment.isValid() and fragment.charFormat().isImageFormat():
                    fmt = fragment.charFormat().toImageFormat()
                    image = self.document().resource(QTextDocument.ImageResource, QUrl(fmt.name()))
                    if isinstance(image, (QImage, QPixmap)) and not image.isNull():
                        width = min(image.width(), available)
                        height = width * image.height() / image.width()
                        if (fmt.width() != width or fmt.height() != height
                                or fmt.anchorHref() != fmt.name()):
                            fmt.setWidth(width)
                            fmt.setHeight(height)
                            # Qt's Markdown importer drops the link around linked images.
                            # Restore an image anchor so QTextBrowser handles real clicks.
                            fmt.setAnchor(True)
                            fmt.setAnchorHref(fmt.name())
                            cursor = QTextCursor(self.document())
                            cursor.setPosition(fragment.position())
                            cursor.setPosition(fragment.position() + fragment.length(),
                                               QTextCursor.KeepAnchor)
                            cursor.setCharFormat(fmt)
                iterator += 1
            block = block.next()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.fit_images()


class GuideImageDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("guideImage")
        self.setWindowTitle("查看说明图片")
        self.setWindowFlag(Qt.WindowContextHelpButtonHint, False)
        size_dialog(self, 1100, 760)
        self._pixmap = QPixmap()
        self._fit = True
        layout = QVBoxLayout(self)
        bar = QHBoxLayout()
        self.caption = QLabel()
        bar.addWidget(self.caption, 1)
        for title, callback in (("适应窗口", self.fit), ("原始大小", self.actual_size)):
            button = PushButton(title)
            button.clicked.connect(callback)
            bar.addWidget(button)
        layout.addLayout(bar)
        self.area = QScrollArea()
        self.area.setAlignment(Qt.AlignCenter)
        self.label = QLabel()
        self.area.setWidget(self.label)
        layout.addWidget(self.area, 1)

    def open_image(self, path):
        self._pixmap = QPixmap(str(path))
        self.caption.setText("点击“原始大小”查看细节，可滚动浏览")
        self._fit = True
        self.show()
        self.render()
        self.raise_()
        self.activateWindow()

    def fit(self):
        self._fit = True
        self.render()

    def actual_size(self):
        self._fit = False
        self.render()

    def render(self):
        if self._pixmap.isNull():
            self.label.setText("图片未找到，请重新安装当前版本。")
            self.label.adjustSize()
            return
        pixmap = self._pixmap
        if self._fit:
            pixmap = pixmap.scaled(self.area.viewport().size() - QSize(16, 16),
                                  Qt.KeepAspectRatio, Qt.SmoothTransformation)
        self.label.setPixmap(pixmap)
        self.label.resize(pixmap.size())

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if hasattr(self, "area"):
            self.render()


class TaskGuideDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("taskGuide")
        self.setWindowFlag(Qt.WindowContextHelpButtonHint, False)
        self.setMinimumSize(560, 440)
        size_dialog(self, 1060, 820)
        self._markdown = ""
        self._sections = []
        self._image_dialog = None
        layout = QVBoxLayout(self)
        layout.setContentsMargins(28, 24, 28, 18)
        layout.setSpacing(16)
        titles = QVBoxLayout()
        eyebrow = QLabel("OK-BD2  /  任务指南")
        eyebrow.setObjectName("guideEyebrow")
        titles.addWidget(eyebrow)
        self.title = QLabel()
        self.title.setObjectName("guideTitle")
        titles.addWidget(self.title)
        layout.addLayout(titles)
        body = QHBoxLayout()
        body.setSpacing(18)
        self.sidebar = QWidget()
        side = QVBoxLayout(self.sidebar)
        side.setContentsMargins(0, 12, 0, 0)
        label = QLabel("本页目录")
        label.setObjectName("guideEyebrow")
        side.addWidget(label)
        self.contents = QListWidget()
        self.contents.setObjectName("guideContents")
        self.contents.setFixedWidth(164)
        self.contents.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.contents.currentRowChanged.connect(self.jump_to_section)
        side.addWidget(self.contents, 1)
        body.addWidget(self.sidebar)
        self.browser = GuideBrowser()
        self.browser.setObjectName("guideBrowser")
        self.browser.anchorClicked.connect(self.open_link)
        self.browser.verticalScrollBar().valueChanged.connect(self.sync_contents)
        self.browser.verticalScrollBar().rangeChanged.connect(self.sync_contents)
        body.addWidget(self.browser, 1)
        layout.addLayout(body, 1)
        footer = QHBoxLayout()
        hint = QLabel("图片可点击放大  ·  可保持此窗口打开，对照调整任务配置")
        hint.setObjectName("guideSubtitle")
        hint.setWordWrap(True)
        footer.addWidget(hint, 1)
        close = PushButton("关闭说明")
        close.clicked.connect(self.close)
        footer.addWidget(close)
        layout.addLayout(footer)
        qconfig.themeChanged.connect(self.apply_theme)
        self.apply_theme()

    def set_guide(self, guide):
        self._sections = []
        title, slug, _description = guide
        self.setWindowTitle(f"{title} · 使用说明")
        self.title.setText(title)
        self.browser.document().setBaseUrl(QUrl.fromLocalFile(str(GUIDE_ROOT) + "/"))
        try:
            self._markdown = (GUIDE_ROOT / f"{slug}.md").read_text(encoding="utf-8")
        except OSError:
            self._markdown = "## 说明暂不可用\n\n请重新安装当前版本以恢复本地说明文件。"
        self.apply_theme()
        self.contents.blockSignals(True)
        self.contents.clear()
        self._sections = []
        block = self.browser.document().begin()
        while block.isValid():
            if block.blockFormat().headingLevel() == 2:
                self.contents.addItem(block.text())
                self._sections.append(block.position())
            block = block.next()
        self.contents.setCurrentRow(0)
        self.contents.blockSignals(False)
        self.browser.verticalScrollBar().setValue(0)
        self.sync_contents()

    def apply_theme(self, *_args):
        p = palette()
        self.setStyleSheet(f"""
            QDialog#taskGuide, QDialog#guideImage {{
                background: {p['bg_panel']}; color: {p['ink']}; }}
            QLabel {{ color: {p['ink']}; background: transparent; }}
            QLabel#guideEyebrow {{ color: {p['accent']}; font-size: 12px; }}
            QLabel#guideTitle {{ font-size: 28px; font-weight: 700; }}
            QLabel#guideSubtitle {{ color: {p['ink_dim']}; font-size: 12px; }}
            QListWidget#guideContents {{ background: transparent; border: none;
                color: {p['ink_dim']}; font-size: 14px; outline: none; }}
            QListWidget#guideContents::item {{ padding: 12px 8px; border-radius: 6px; }}
            QListWidget#guideContents::item:selected {{ background: {p['accent_soft']};
                color: {p['accent']}; }}
            QTextBrowser#guideBrowser {{ background: {p['card']}; color: {p['ink']};
                border: 1px solid {p['line']}; border-radius: 10px;
                selection-background-color: {p['accent']}; }}
            QScrollBar:vertical {{ background: transparent; width: 8px; margin: 4px 0; }}
            QScrollBar::handle:vertical {{ background: {p['line_strong']};
                border-radius: 4px; min-height: 28px; }}
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0; }}
            QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {{ background: none; }}
        """)
        bar = self.browser.verticalScrollBar()
        position = bar.value()
        with QSignalBlocker(bar):
            self.browser.setCurrentCharFormat(QTextCharFormat())
            self.browser.setMarkdown(self._markdown)
            # Keep Markdown formatting and give paragraphs room to breathe.
            block = self.browser.document().begin()
            while block.isValid():
                cursor = QTextCursor(block)
                fmt = block.blockFormat()
                fmt.setTopMargin(22 if fmt.headingLevel() else 6)
                fmt.setBottomMargin(10)
                fmt.setLineHeight(150, QTextBlockFormat.ProportionalHeight.value)
                cursor.setBlockFormat(fmt)
                block = block.next()
            self.browser.fit_images()
            bar.setValue(position)
        self.sync_contents()

    def jump_to_section(self, row):
        if 0 <= row < len(self._sections):
            document = self.browser.document()
            block = document.findBlock(self._sections[row])
            top = document.documentLayout().blockBoundingRect(block).top()
            bar = self.browser.verticalScrollBar()
            with QSignalBlocker(bar):
                bar.setValue(max(0, int(top) - 24))

    def sync_contents(self, *_args):
        if not self._sections:
            return
        bar = self.browser.verticalScrollBar()
        document = self.browser.document()
        row = 0
        if bar.maximum() > 0 and bar.value() == bar.maximum():
            row = len(self._sections) - 1
        else:
            for index, position in enumerate(self._sections):
                block = document.findBlock(position)
                top = document.documentLayout().blockBoundingRect(block).top()
                if top > bar.value() + 26:
                    break
                row = index
        if row != self.contents.currentRow():
            # Updating the reading marker must not jump the document again.
            with QSignalBlocker(self.contents):
                self.contents.setCurrentRow(row)
            self.contents.scrollToItem(self.contents.item(row))

    def open_link(self, url):
        if url.scheme() in {"http", "https"}:
            QDesktopServices.openUrl(url)
            return
        path = Path(self.browser.document().baseUrl().resolved(url).toLocalFile())
        if path.suffix.lower() not in {".png", ".jpg", ".jpeg", ".webp"}:
            return
        if self._image_dialog is None:
            self._image_dialog = GuideImageDialog(self)
        self._image_dialog.open_image(path)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if hasattr(self, "sidebar"):
            self.sidebar.setVisible(self.width() >= 800)


def install_task_guides():
    from ok.ui.qt.tasks.TaskCard import TaskCard

    if getattr(TaskCard, "_bd2_guides_installed", False):
        return
    original_init = TaskCard.__init__
    original_show = TaskCard.show_instructions

    def init(self, task, onetime):
        guide = GUIDES.get(type(task).__name__)
        if guide:
            task.instructions = guide[2]
        original_init(self, task, onetime)
        if guide:
            self.instructions_button.setText("使用说明")

    def show_instructions(self):
        guide = GUIDES.get(type(self.task).__name__)
        if guide is None:
            return original_show(self)
        window = self.window()
        dialog = getattr(window, "_bd2_task_guide", None)
        if dialog is None:
            dialog = TaskGuideDialog(window)
            window._bd2_task_guide = dialog
        dialog.set_guide(guide)
        dialog.show()
        dialog.raise_()
        dialog.activateWindow()

    TaskCard.__init__ = init
    TaskCard.show_instructions = show_instructions
    TaskCard._bd2_guides_installed = True
