import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import cv2
import numpy as np
from ok.task.exceptions import FinishedException, TaskDisabledException

from src.interaction.BD2Interaction import InputTargetChanged
from src.tasks.BaseBD2Task import (
    SCENE_HOME_MAX_CLICKS,
    TEMPLATE_DIR,
    TOP_LEFT_BACK_CHEVRON_RATIO,
    BaseBD2Task,
)
from src.tasks.trigger.AutoLoginTask import AutoLoginTask
from src.utils.home_confirmation import (
    HOME_ANNOUNCEMENT_CLEAR_RELATIVE_POINT,
    HOME_DIMMED_P95_THRESHOLD_DEFAULT,
    HOME_LEFT_COLUMN_OCR_RELATIVE_ROI,
    home_confirmation_passes,
    home_gacha_ocr_with_fallback,
    home_left_column_hits,
    home_temporary_announcement_detected,
    modal_backdrop_point,
    modal_close_glyph,
    modal_close_point,
    modal_panel_bbox,
    scene_home_button_point,
)
from src.utils.vision_models import MatchResult
from tests.helpers.modal_glyph import draw_close_glyph


def home_task(kind=BaseBD2Task, boxes=()):
    task = object.__new__(kind)
    task.config = {}
    task.info = {}
    task.clicks = []
    task.info_set = task.info.__setitem__
    task.log_info = Mock()
    task.log_warning = Mock()
    task.sleep = Mock()
    task._save_flow_diagnostic = Mock()
    task.operate_click = lambda x, y, **_: task.clicks.append((x, y))
    task._sleep_after_recognition = Mock()
    task._quick_vision = lambda: SimpleNamespace(
        ocr_text=lambda *_, **kw: "", ocr_boxes=lambda *_, **kw: boxes
    )
    task.ocr = lambda **_: boxes
    return task


class HomeConfirmationTest(unittest.TestCase):
    def test_home_and_temporary_announcement_require_their_own_three_signals(self):
        for text, votes, required, bright, gacha, home, announcement in (
            ("我的小屋 经营管理格鲁TALK 街机游戏", 3, 2, 253, "抽抽乐", True, False),
            ("经营管理格鲁TALK 街機遊戲", 1, 2, 253, "抽抽乐", False, False),
            ("我的小屋", 1, 1, 253, "抽抽乐", True, False),
            ("设置 公告", 0, 1, 253, "抽抽乐", False, False),
            ("我的小屋 格鲁TALK", 2, 2, 126, "抽抽乐", False, True),
            ("我的小屋 格鲁TALK", 2, 2, 185, "", False, False),
            ("我的小屋", 1, 2, 126, "抽抽乐", False, False),
            ("我的小屋 格鲁TALK", 2, 2, 126, "", False, False),
        ):
            with self.subTest(text=text, bright=bright, gacha=gacha):
                hits = home_left_column_hits(text)
                self.assertEqual(votes, hits)
                signals = dict(
                    left_hits=hits,
                    required_left_hits=required,
                    brightness=bright,
                    brightness_threshold=HOME_DIMMED_P95_THRESHOLD_DEFAULT,
                    gacha_ocr_text=gacha,
                )
                self.assertEqual(home, home_confirmation_passes(**signals))
                self.assertEqual(announcement, home_temporary_announcement_detected(**signals))

    def test_gacha_ocr_stops_at_first_match_or_exhausts_scales(self):
        for texts, selected, expected_text in (
            (("", "抽 抽 乐"), 2.0, "抽 抽 乐"),
            (("其他文字", "", ""), None, "其他文字"),
        ):
            with self.subTest(texts=texts):
                read = Mock(side_effect=texts)
                result = home_gacha_ocr_with_fallback(read)
                attempts = tuple(zip((1.0, 2.0, 3.0), texts))
                self.assertEqual(attempts, result.attempts)
                self.assertEqual([s for s, _ in attempts], [c.args[0] for c in read.call_args_list])
                self.assertEqual(
                    (selected is not None, selected, expected_text),
                    (result.matched, result.selected_scale, result.text),
                )


class ReturnHomeTest(unittest.TestCase):
    def _task(self, home_results, *, house_match=None, scene_points=None):
        task = home_task()
        task.config.update(
            {
                "自动返回主页最大步数": 1,
                "自动返回主页失败冷却秒数": 30.0,
                "返回主页动作确认秒数": 0.0,
            }
        )
        task.capture_frame = Mock(return_value=np.zeros((100, 100, 3), np.uint8))
        results = iter(home_results)
        points = iter(scene_points or ())

        def signals(*_):
            task._test_state = next(results)
            task._scene_home_point = next(points, None)
            return task._test_state[0], 0, 255.0, ""

        def scan(frame, observation=None):
            if observation is None:
                signals(frame)
            return task._test_state

        task._read_home_confirmation_signals = signals
        task._home_scan = scan
        task._match_house_button = lambda _: house_match
        task._click_top_left_back = lambda *_, **kw: False
        task.dismiss_home_announcement = lambda *_: False
        task._auto_return_home_cooldown_until = 0.0
        return task, task.clicks

    def test_return_click_needs_stable_evidence_and_a_fresh_home_frame(self):
        hint, home, unknown = (False, True), (True, False), (False, False)
        house = MatchResult(0.95, (40, 20), (20, 20))
        point = (0.9363, 0.0535)
        for name, frames, match, points, steps, success, clicks in (
            ("already home", [hint, home], house, [], 1, True, []),
            ("lost house", [hint, unknown], house, [], 1, False, []),
            ("still away", [hint] * 3 + [unknown], house, [], 1, False, [(0.5, 0.3)]),
            ("restaurant", [hint] * 3 + [home], None, [point] * 4, 1, True, [point]),
            (
                "moving control",
                [hint] * 3,
                None,
                [point, (0.9, 0.0535), (0.9, 0.0535)],
                1,
                False,
                [],
            ),
            (
                "click cap",
                [hint] * 40,
                None,
                [point] * 40,
                12,
                False,
                [point] * SCENE_HOME_MAX_CLICKS,
            ),
            ("unrecognized scene", [unknown], None, [], 4, False, []),
        ):
            with self.subTest(name=name):
                task, actual = self._task(frames, house_match=match, scene_points=points)
                task.config["自动返回主页最大步数"] = steps
                task.send_key = Mock()
                self.assertEqual(success, task.auto_return_main_home())
                self.assertEqual(clicks, actual)
                task.send_key.assert_not_called()
                if not success:
                    captures = task.capture_frame.call_count
                    self.assertGreater(task._auto_return_home_cooldown_until, 0.0)
                    self.assertFalse(task.auto_return_main_home())
                    self.assertEqual(captures, task.capture_frame.call_count)
                    task.log_warning.assert_called_once()
                    self.assertTrue(task.log_warning.call_args.kwargs["notify"])

    def test_daily_recovery_observes_home_in_cooldown_and_propagates_stop(self):
        task, clicks = self._task([(True, False)])
        task._auto_return_home_cooldown_until = float("inf")
        self.assertTrue(task.recover_daily_home())
        self.assertEqual([], clicks)
        for error in (TaskDisabledException, FinishedException):
            for boundary in ("capture_frame", "operate_click"):
                with self.subTest(error=error, boundary=boundary):
                    task, clicks = self._task(
                        [(False, True)] * 6, scene_points=[(0.9363, 0.0535)] * 6
                    )
                    setattr(task, boundary, Mock(side_effect=error))
                    with self.assertRaises(error):
                        task.recover_daily_home()
                    self.assertEqual([], clicks)
                    task.log_warning.assert_not_called()
                    self.assertEqual(0.0, task._auto_return_home_cooldown_until)

    def test_dimmed_home_cleanup_requires_fresh_bright_confirmation(self):
        for clears in (True, False):
            with self.subTest(clears=clears):
                task, clicks = self._task([])
                del task._home_scan
                del task.dismiss_home_announcement
                del task._read_home_confirmation_signals
                dark = np.full((1080, 1920, 3), 80, dtype=np.uint8)
                bright = np.full_like(dark, 255)
                task.capture_frame = Mock(side_effect=[dark, bright if clears else dark])

                def read_text(_frame, _name, *, relative_roi, **_kwargs):
                    if relative_roi == HOME_LEFT_COLUMN_OCR_RELATIVE_ROI:
                        return "我的小屋 格鲁TALK"
                    if _name == "返回主页公告":
                        return "更新 抢先看"
                    return "抽抽乐"

                task._quick_vision = lambda: SimpleNamespace(ocr_text=read_text)
                task._bd2_ocr_boxes = lambda *_args, **_kwargs: []
                task._sleep_after_recognition = lambda: None
                self.assertEqual(clears, task.auto_return_main_home())
                self.assertEqual([HOME_ANNOUNCEMENT_CLEAR_RELATIVE_POINT], clicks)
                self.assertEqual(2, task.capture_frame.call_count)


HOUSE_WIDTH, HOUSE_HEIGHT = 3840, 2160
TEMPLATE_NAME = "return_home_house_button_alpha.png"
# 实机 4K 帧上的图标 bbox（房子+齿轮），模板即由此 1:2 下采样得到。
ICON_BOX = (3334, 76, 3422, 160)
# 「我的小屋 → 分享设置」按钮（白色胶囊，含深色齿轮图标与文字）。
PILL_BOX = (3205, 60, 3570, 142)


def _background():
    return np.full((HOUSE_HEIGHT, HOUSE_WIDTH, 3), 32, dtype=np.uint8)


def _frame_with_icon():
    frame = _background()
    template = cv2.imread(str(TEMPLATE_DIR / TEMPLATE_NAME), cv2.IMREAD_UNCHANGED)
    assert template is not None
    left, top, right, bottom = ICON_BOX
    scaled = cv2.resize(template, (right - left, bottom - top), interpolation=cv2.INTER_CUBIC)
    alpha = scaled[:, :, 3:4].astype(np.float32) / 255.0
    target = frame[top:bottom, left:right].astype(np.float32)
    frame[top:bottom, left:right] = (
        scaled[:, :, :3].astype(np.float32) * alpha + target * (1.0 - alpha)
    ).astype(np.uint8)
    return frame


def _frame_with_share_pill():
    """模拟「我的小屋」右上角：白色圆角胶囊 + 深色文字/齿轮。"""
    frame = _background()
    left, top, right, bottom = PILL_BOX
    cv2.rectangle(frame, (left + 30, top), (right - 30, bottom), (232, 232, 226), -1)
    cv2.circle(frame, (left + 30, (top + bottom) // 2), (bottom - top) // 2, (232, 232, 226), -1)
    cv2.circle(frame, (right - 30, (top + bottom) // 2), (bottom - top) // 2, (232, 232, 226), -1)
    # 左侧深色圆形里的白色双齿轮（旧模板就是在这里/胶囊白底上误命中）。
    cv2.circle(frame, (left + 36, (top + bottom) // 2), 26, (46, 46, 46), -1)
    for offset in (-10, 10):
        cv2.circle(
            frame, (left + 36 + offset, (top + bottom) // 2 + offset // 2), 11, (240, 240, 240), -1
        )
    for word_x in (150, 230, 310):
        cv2.rectangle(
            frame,
            (left + word_x, top + 26),
            (left + word_x + 56, bottom - 26),
            (60, 60, 60),
            -1,
        )
    return frame


class ReturnControlImageTest(unittest.TestCase):
    def test_house_and_scene_controls_reject_similar_hud_elements(self):
        task = home_task()
        result = task._match_house_button(_frame_with_icon())
        self.assertIsNotNone(result)
        for axis in (0, 1):
            center = result.position[axis] + result.size[axis] // 2
            self.assertAlmostEqual((ICON_BOX[axis] + ICON_BOX[axis + 2]) / 2, center, delta=6)
        self.assertIsNone(task._match_house_button(_frame_with_share_pill()))
        frame = _background()
        frame[36:126, 3270:3610] = (202, 211, 219)
        frame[58:96, 3405:3480] = 50
        with patch(
            "src.utils.task_vision.match_template",
            return_value=MatchResult(0.96, (3542, 6), (84, 106)),
        ):
            self.assertIsNone(task._match_house_button(frame))

        def detect(name, scale=1.0):
            frame = cv2.imread(str(Path(__file__).parent / "fixtures" / "scene_home_button" / name))
            self.assertIsNotNone(frame, name)
            if scale != 1.0:
                frame = cv2.resize(frame, None, fx=scale, fy=scale, interpolation=cv2.INTER_CUBIC)
            return scene_home_button_point(frame)

        bright = detect("restaurant_bright_1080p.png")
        self.assertIsNotNone(bright)
        self.assertAlmostEqual(0.9365, bright[0], delta=0.006)
        self.assertAlmostEqual(0.0555, bright[1], delta=0.006)
        for scale in (1.0, 1.5, 2.0):
            with self.subTest(share_settings_scale=scale):
                self.assertIsNone(detect("my_home_720p.png", scale))
        self.assertIsNone(detect("restaurant_720p.png"))
        smaller, larger = (detect("restaurant_720p.png", s) for s in (1.5, 2.0))
        self.assertIsNotNone(smaller)
        self.assertIsNotNone(larger)
        self.assertAlmostEqual(smaller[0], larger[0], delta=0.004)
        self.assertAlmostEqual(smaller[1], larger[1], delta=0.006)

    def test_back_bar_click_targets_the_chevron_at_each_resolution(self):
        template = cv2.imread(str(TEMPLATE_DIR / "back_return_bar_arrow.png"))
        self.assertIsNotNone(template)
        for width, height in ((2560, 1440), (1920, 1080), (1280, 720)):
            with self.subTest(width=width):
                frame = np.full((height, width, 3), (36, 34, 30), np.uint8)
                w, h = (max(8, round(v * width / 1920)) for v in template.shape[1::-1])
                frame[18 : 18 + h, 20 : 20 + w] = cv2.resize(
                    template, (w, h), interpolation=cv2.INTER_AREA
                )
                task = home_task()
                self.assertTrue(task._click_top_left_back(frame, 1))
                self.assertEqual(1, len(task.clicks))
                x, y = task.clicks[0]
                rx, ry = TOP_LEFT_BACK_CHEVRON_RATIO
                ex, ey = 20 + round(w * rx), 18 + round(h * ry)
                tolerance = max(3.0, ex * 0.08)
                self.assertAlmostEqual(x * width, ex, delta=tolerance)
                self.assertAlmostEqual(y * height, ey, delta=tolerance)


HEIGHT, WIDTH = 1080, 1920
# 实机「登录加成」帧在 1920x1080 下的面板 bbox（X 中心实测 1480,285）。
REAL_PANEL = (424, 178, 1081, 641)
# 实机「公告板」帧在 3840x2160 下的面板 bbox（X 中心换算 2656,531）。
BIG_HEIGHT, BIG_WIDTH = 2160, 3840
BOARD_PANEL = (1120, 460, 1600, 1220)
BOARD_CLOSE = (2656, 531)


def _dimmed_frame(value: int = 70, size=(HEIGHT, WIDTH)):
    return np.full((size[0], size[1], 3), value, dtype=np.uint8)


def _popup_frame(bbox=REAL_PANEL, close=(1480, 285), with_glyph=True):
    frame = _dimmed_frame()
    left, top, width, height = bbox
    frame[top : top + height, left : left + width] = 255
    if with_glyph:
        draw_close_glyph(frame, close)
    return frame


def _board_frame(with_glyph=True):
    frame = _dimmed_frame(size=(BIG_HEIGHT, BIG_WIDTH))
    left, top, width, height = BOARD_PANEL
    frame[top : top + height, left : left + width] = 245
    if with_glyph:
        draw_close_glyph(frame, BOARD_CLOSE, size=39)
    return frame


class HomeModalTest(unittest.TestCase):
    def test_unknown_home_panel_uses_verified_edge_and_stops_after_five_unchanged_clicks(self):
        for kind in (BaseBD2Task, AutoLoginTask):
            task = home_task(kind)
            panel = _popup_frame(with_glyph=False)
            panel[:, :40] = 30
            with patch("src.tasks.BaseBD2Task.monotonic", side_effect=range(10, 80, 2)):
                for _ in range(5):
                    self.assertTrue(task.dismiss_home_announcement(
                        panel, (False, 2, 126.0, "抽抽乐"), "测试",
                    ))
                with self.assertRaisesRegex(RuntimeError, "5 次.*未变化"):
                    task.dismiss_home_announcement(panel, (False, 2, 126.0, "抽抽乐"), "测试")
            self.assertEqual([(.01, .5)] * 5, task.clicks)
            task._save_flow_diagnostic.assert_called_once_with("home_modal_stuck")

    def test_home_modal_progress_resets_unchanged_clicks(self):
        task = home_task()
        panel = _popup_frame()
        with patch("src.tasks.BaseBD2Task.monotonic", side_effect=range(10, 80, 2)):
            for i in range(8):
                changed = panel.copy()
                changed[400:600, 600:1000] = 30 if i % 2 else 200
                self.assertTrue(task.dismiss_home_modal_popup(changed))
        self.assertEqual(8, len(task.clicks))

    def test_unsent_home_click_does_not_count_as_stuck(self):
        for interrupted in (False, True):
            task = home_task()
            task.operate_click = Mock(side_effect=InputTargetChanged) if interrupted else Mock(
                return_value=False,
            )
            with patch("src.tasks.BaseBD2Task.monotonic", side_effect=range(10, 80, 2)):
                for _ in range(8):
                    self.assertEqual(interrupted, task.dismiss_home_modal_popup(_popup_frame()))
            task._save_flow_diagnostic.assert_not_called()

    def test_panel_less_return_prompt_is_supported_but_not_other_buttons(self):
        for label, expected in (("点击画面即可返回", True), ("返回", False),
                                ("7天内不再显示", False)):
            task = home_task(boxes=[SimpleNamespace(
                name=label, x=800, y=900, width=320, height=40, confidence=.99,
            )])
            with patch("src.tasks.BaseBD2Task.monotonic", return_value=10):
                self.assertEqual(expected, task.dismiss_home_modal_popup(_dimmed_frame()))
            self.assertEqual(int(expected), len(task.clicks))

    def test_missing_panel_preserves_fullscreen_news_recovery(self):
        task = home_task()
        task.dismiss_fullscreen_news_page = Mock(return_value=True)
        self.assertTrue(task.dismiss_home_announcement(
            _dimmed_frame(), (False, 2, 126., "抽抽乐"), "测试",
        ))
        task.dismiss_fullscreen_news_page.assert_called_once()

    def test_home_modal_text_close_uses_only_explicit_close_labels(self):
        for label, expected in (("关闭", True), ("点击画面即可返回", True),
                                ("7天内不再显示", False), ("前往查看", False),
                                ("购买", False), ("确认", False)):
            with self.subTest(label=label):
                box = SimpleNamespace(name=label, x=850, y=740, width=200,
                                      height=40, confidence=.99)
                task = home_task(boxes=[box])
                with patch("src.tasks.BaseBD2Task.monotonic", return_value=10):
                    self.assertEqual(expected, task.dismiss_home_modal_popup(
                        _popup_frame(with_glyph=False),
                    ))
                self.assertEqual([(950 / WIDTH, 760 / HEIGHT)] if expected else [], task.clicks)

    def test_edge_fallback_rejects_content_and_normal_home(self):
        frame = _popup_frame(with_glyph=False)
        self.assertIsNone(modal_backdrop_point(frame, REAL_PANEL))
        frame[:, :40] = 30
        self.assertEqual((.01, .5), modal_backdrop_point(frame, REAL_PANEL))
        frame[520:550, :20] = 55
        self.assertEqual((.01, .5), modal_backdrop_point(frame, REAL_PANEL))
        frame[500:570, :40] = 200
        self.assertIsNone(modal_backdrop_point(frame, REAL_PANEL))
        task = home_task()
        self.assertFalse(task.dismiss_home_announcement(
            frame, (True, 2, 255., "抽抽乐"), "测试",
        ))
        self.assertEqual([], task.clicks)

    def test_close_glyph_geometry_in_board_and_dark_headers(self):
        point = modal_close_point(
            BOARD_PANEL,
            BIG_WIDTH,
            BIG_HEIGHT,
            frame=_board_frame(),
        )
        self.assertIsNotNone(point)
        self.assertAlmostEqual(BOARD_CLOSE[0] / BIG_WIDTH, point[0], delta=0.004)
        self.assertAlmostEqual(BOARD_CLOSE[1] / BIG_HEIGHT, point[1], delta=0.004)
        # 关键：关闭点必须落在面板顶部标题带里，而不是第一条公告上。
        _, top, _, height = BOARD_PANEL
        close_y = point[1] * BIG_HEIGHT
        self.assertLess(close_y, top + height * 0.10)

        frame = _popup_frame(with_glyph=False)
        left, top, width, _ = REAL_PANEL
        corner_x = left + width - 4
        corner_y = top + 3
        frame[corner_y : corner_y + 12, corner_x - 12 : corner_x] = 90
        self.assertIsNone(modal_close_glyph(frame, REAL_PANEL))

        body = (516, 316, 948, 512)
        frame = _popup_frame(bbox=body, with_glyph=False)
        # A pair of horizontal text strokes and two legs at the old click point.
        frame[326:329, 1398:1416] = 65
        frame[332:335, 1398:1416] = 65
        for step in range(11):
            frame[328 + step, 1405 - step // 2 : 1409 - step // 2] = 65
            frame[334 + step, 1405 + step : 1409 + step] = 65
        self.assertIsNone(modal_close_glyph(frame, body))
        draw_close_glyph(frame, (1480, 288), value=250)
        x, y = modal_close_glyph(frame, body)
        self.assertAlmostEqual(1480 / WIDTH, x, delta=0.002)
        self.assertAlmostEqual(288 / HEIGHT, y, delta=0.002)

    def test_panel_prefers_close_glyph_and_rejects_unsafe_missing_control(self):
        for present in (True, False):
            task = home_task()
            with patch("src.tasks.BaseBD2Task.monotonic", return_value=10):
                self.assertEqual(present, task.dismiss_home_modal_popup(
                    _popup_frame(with_glyph=present),
                ))
            self.assertEqual(int(present), len(task.clicks))
            if present:
                x, y = task.clicks[0]
                self.assertAlmostEqual(1480 / WIDTH, x, delta=.004)
                self.assertAlmostEqual(285 / HEIGHT, y, delta=.004)

    def test_dark_centered_panel_on_dimmed_backdrop_uses_fallback_and_hits_close_glyph(self):
        frame = _dimmed_frame(45)
        left, top, width, height = (424, 316, 1040, 512)
        # Dark-brown popup header surrounds the mid-tone card body; white X sits in the header.
        frame[250:830, 420:1505] = 60
        frame[top : top + height, left : left + width] = 135
        draw_close_glyph(frame, (1480, 288), value=250)
        bbox = modal_panel_bbox(frame)
        self.assertIsNotNone(bbox)
        close = modal_close_glyph(frame, bbox)
        self.assertIsNotNone(close)
        self.assertAlmostEqual(1480 / WIDTH, close[0], delta=0.004)
        self.assertAlmostEqual(288 / HEIGHT, close[1], delta=0.004)
        task = home_task()
        self.assertTrue(task.dismiss_home_modal_popup(frame))
        self.assertEqual(1, len(task.clicks))

        mid_backdrop = _dimmed_frame(95)
        mid_backdrop[top : top + height, left : left + width] = 135
        self.assertIsNone(modal_panel_bbox(mid_backdrop))
        self.assertIsNone(modal_panel_bbox(_dimmed_frame(70)))

    def test_announcement_waits_for_a_recognized_control(self):
        for kind, frame in (
            (BaseBD2Task, _popup_frame(with_glyph=False)),
            (AutoLoginTask, _popup_frame(with_glyph=False)),
            (AutoLoginTask, _dimmed_frame()),
        ):
            with self.subTest(task=kind.__name__, brightness=frame.max()):
                task = home_task(kind)
                task._last_clear_click_at = 0.0
                task._home_bright_since = 1.0
                self.assertTrue(
                    task.dismiss_home_announcement(
                        frame,
                        (False, 2, 126.0, "抽抽乐"),
                        kind.__name__,
                    )
                )
                self.assertEqual([], task.clicks)

    def test_dimmed_dialog_accepts_only_cancel_and_limits_retries(self):
        for brightness, labels, results in (
            (30, ("取消", "确认"), [True] * 4 + [False] * 2),
            (240, ("取消", "确认"), [False]),
            (30, ("确认",), [False]),
            (30, ("确定",), [False]),
            (30, ("取消分享",), [False]),
        ):
            with self.subTest(brightness=brightness, labels=labels):
                boxes = [
                    SimpleNamespace(
                        name=name,
                        x=1644 if name.startswith("取消") else 2078,
                        y=1362,
                        width=115,
                        height=71,
                        confidence=0.99,
                    )
                    for name in labels
                ]
                task = home_task(boxes=boxes)
                frame = _dimmed_frame(brightness, size=(2160, 3840))
                with patch("src.tasks.BaseBD2Task.monotonic", side_effect=range(100, 140, 2)):
                    self.assertEqual(results, [task.dismiss_blocking_modal(frame) for _ in results])
                self.assertEqual(sum(results), len(task.clicks))
                for x, y in task.clicks:
                    self.assertAlmostEqual((1644 + 115 / 2) / 3840, x, delta=0.002)
                    self.assertAlmostEqual((1362 + 71 / 2) / 2160, y, delta=0.002)

    def test_fullscreen_news_limits_clicks_across_pages_and_cycles(self):
        news = "新闻／公告 全部 公告事项 更新 活动 开发者笔记 查看详情"
        home = "我的小屋 好友 公会 格鲁TALK 街机游戏 抽抽乐"
        frame = _dimmed_frame(250)
        for pages, expected, flag in (
            ([news] * 5, [True] * 3 + [False] * 2, "_news_page_back_exhausted"),
            ([news, home] * 3 + [news], [True, False] * 3 + [False], "_news_page_loop_detected"),
        ):
            with self.subTest(flag=flag):
                task = home_task()
                task.back = Mock()
                with patch("src.tasks.BaseBD2Task.monotonic", side_effect=range(10, 300, 5)):
                    results = [task.dismiss_fullscreen_news_page(frame, ocr_text=p) for p in pages]
                    self.assertEqual(expected, results)
                    self.assertTrue(getattr(task, flag))
                    self.assertFalse(task.dismiss_fullscreen_news_page(frame, ocr_text=home))
                    self.assertFalse(task._news_page_back_exhausted)
                self.assertEqual(3, len(task.clicks))
                task.back.assert_not_called()
