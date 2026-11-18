import unittest

from src.tasks.main_story import (
    BATTLE_TURN_BUTTON_KEYWORDS,
    BATTLE_TURN_CLICKS,
    BATTLE_TURN_INTERVAL,
    BATTLE_TURN_POINT,
    CLICK_RETRY_ATTEMPTS,
    LIST_BUTTON_ROI,
    PREPARE_ACTIONS,
    PREPARE_RETRY_ROUNDS,
    PREPARE_STATES,
    REWARD_POPUP_HINT_POINT,
    REWARD_POPUP_HINT_ROI,
    SKIP_CONFIRM_OK_POINTS,
    SKIP_CONFIRM_OK_ROIS,
    SKIP_CONFIRM_ROUNDS,
    STORY_ADVANCE_CLICKS,
    STORY_ADVANCE_POINT,
    STORY_SUBTITLE_MIN_CHARS,
    STORY_SUBTITLE_ROIS,
    MainStoryFeatureMixin,
    MainStoryState,
    classify_main_story_state,
)
from src.tasks.task_vision_mixin import REFERENCE_HEIGHT, REFERENCE_WIDTH


class ClassifyMainStoryStateTest(unittest.TestCase):

    def test_victory_on_leave_button(self):
        self.assertIs(
            classify_main_story_state("", "", "离开", ""),
            MainStoryState.VICTORY,
        )

    def test_victory_tolerates_spaced_ocr(self):
        self.assertIs(
            classify_main_story_state("", "", "离 开", ""),
            MainStoryState.VICTORY,
        )

    def test_battle_button_wins_over_empty_leave_roi(self):
        # 列表页这两块 ROI 在画面上重叠，读不到「离开」时必须走列表分支。
        self.assertIs(
            classify_main_story_state("", "战斗", "", ""),
            MainStoryState.LIST_BATTLE,
        )

    def test_in_stage_entry(self):
        self.assertIs(
            classify_main_story_state("进行快速探索", "", "", ""),
            MainStoryState.IN_STAGE,
        )

    def test_list_auto(self):
        self.assertIs(
            classify_main_story_state("", "自动进行", "", ""),
            MainStoryState.LIST_AUTO,
        )

    def test_battle_keyword_not_swallowed_by_auto_text(self):
        # 「自动进行」里不含「战斗」，两者不能互相误判。
        self.assertIs(
            classify_main_story_state("", "自动进行", "", ""),
            MainStoryState.LIST_AUTO,
        )

    def test_unknown_button_text(self):
        self.assertIs(
            classify_main_story_state("", "领取奖励", "", ""),
            MainStoryState.LIST_OTHER_BUTTON,
        )

    def test_empty_everything_is_busy(self):
        self.assertIs(classify_main_story_state("", "", "", ""), MainStoryState.BUSY)

    def test_ocr_noise_still_classifies(self):
        # 实战里「进」常被识别成形近字，允许一字之差。
        self.assertIs(
            classify_main_story_state("", "自动逬行", "", ""),
            MainStoryState.LIST_AUTO,
        )

    def test_whitespace_is_ignored(self):
        self.assertIs(
            classify_main_story_state(" 进行 快速 探索 ", "", "", ""),
            MainStoryState.IN_STAGE,
        )

    def test_values_are_stable_strings(self):
        # 状态值会写进 GUI 状态面板，改名属于破坏性改动。
        self.assertEqual(MainStoryState.IN_STAGE.value, "关卡内")
        self.assertEqual(MainStoryState.LIST_BATTLE.value, "列表-战斗")
        self.assertEqual(MainStoryState.VICTORY.value, "战斗结算")

    # --- 2026-11-16 实机失败回归：战斗内 HUD 落在列表按钮 ROI 里 ---

    def test_battle_hud_autobattle_hint_is_not_battle_button(self):
        # 实机日志：按钮 ROI 读到「结束自动战斗esc」，含「战斗」二字但不是按钮。
        self.assertIs(
            classify_main_story_state("", "结束自动战斗esc", "结束自动战斗esc", ""),
            MainStoryState.IN_BATTLE,
        )

    def test_battle_hud_turn_battle_is_not_unknown_button(self):
        # 2026-11-18 00:03 卡住的那一帧：按钮=turn.battle，离开=turn.1」battle。
        # 现在要认成「待开回合」，去点 BATTLE，而不是干等。
        self.assertIs(
            classify_main_story_state("", "turn.battle", "turn.1」battle", ""),
            MainStoryState.IN_BATTLE_TURN,
        )

    def test_battle_hud_turn_counter_only(self):
        self.assertIs(
            classify_main_story_state("", "TURN.1BATTLE", "", ""),
            MainStoryState.IN_BATTLE_TURN,
        )

    def test_battle_hud_turn_signal_in_leave_roi_only(self):
        # 两块 ROI 有重叠，按钮那块没读到时，离开那块读到 TURN 也算。
        self.assertIs(
            classify_main_story_state("", "", "turn.2 battle", ""),
            MainStoryState.IN_BATTLE_TURN,
        )

    def test_battle_hud_auto_hint_is_not_turn(self):
        # 自动战斗在跑时那块 ROI 只有「结束自动战斗」：不能当成待开回合去乱点。
        self.assertIsNot(
            classify_main_story_state("", "结束自动战斗esc", "", ""),
            MainStoryState.IN_BATTLE_TURN,
        )

    def test_battle_hud_turn_does_not_swallow_list_battle(self):
        self.assertIs(
            classify_main_story_state("", "战斗", "", ""),
            MainStoryState.LIST_BATTLE,
        )

    def test_in_battle_turn_value_is_stable(self):
        self.assertEqual(MainStoryState.IN_BATTLE_TURN.value, "战斗-待开回合")

    def test_battle_hud_does_not_swallow_list_battle(self):
        # 列表页那个按钮是中文「战斗」，不能被 HUD 规则吃掉。
        self.assertIs(
            classify_main_story_state("", "战斗", "", ""),
            MainStoryState.LIST_BATTLE,
        )

    def test_battle_hud_does_not_swallow_auto(self):
        self.assertIs(
            classify_main_story_state("", "自动进行", "", ""),
            MainStoryState.LIST_AUTO,
        )

    def test_battle_hud_does_not_swallow_victory(self):
        self.assertIs(
            classify_main_story_state("", "", "离开", ""),
            MainStoryState.VICTORY,
        )

    def test_in_battle_value_is_stable(self):
        self.assertEqual(MainStoryState.IN_BATTLE.value, "战斗内")

    # --- 2026-11-16 奖励弹窗回归 ---

    def test_reward_popup_detected(self):
        self.assertIs(
            classify_main_story_state("", "", "", "可获得奖励 点击画面即可返回"),
            MainStoryState.REWARD_POPUP,
        )

    def test_list_reward_preview_label_is_not_a_popup(self):
        # 2026-11-17 实机：列表页中部常驻「可获得奖励」奖励预览标签，位置和弹窗
        # 标题 ROI 完全重叠；只认这段文字会把整张列表页当弹窗、点空地到超时。
        self.assertIs(
            classify_main_story_state("", "自动进行", "自动进行", "可获得奖励"),
            MainStoryState.LIST_AUTO,
        )

    def test_reward_popup_wins_over_auto_button(self):
        # 真弹窗帧：底部提示「点击画面即可返回」+ 背后仍可见的「自动进行」。
        self.assertIs(
            classify_main_story_state(
                "", "自动进行", "自动进行", "可获得奖励 点击画面即可返回",
            ),
            MainStoryState.REWARD_POPUP,
        )

    def test_reward_popup_hint_without_reward_title(self):
        self.assertIs(
            classify_main_story_state("", "", "", "点击画面即可返回"),
            MainStoryState.REWARD_POPUP,
        )

    def test_reward_popup_tolerates_one_wrong_character(self):
        self.assertIs(
            classify_main_story_state("", "", "", "点击画面即可返凹"),
            MainStoryState.REWARD_POPUP,
        )

    def test_reward_popup_does_not_swallow_battle(self):
        self.assertIs(
            classify_main_story_state("", "战斗", "战斗", ""),
            MainStoryState.LIST_BATTLE,
        )

    def test_reward_popup_does_not_swallow_victory(self):
        self.assertIs(
            classify_main_story_state("", "", "离开", ""),
            MainStoryState.VICTORY,
        )

    def test_reward_popup_does_not_swallow_in_stage(self):
        self.assertIs(
            classify_main_story_state("进行快速探索", "", "", ""),
            MainStoryState.IN_STAGE,
        )

    def test_reward_popup_value_is_stable(self):
        self.assertEqual(MainStoryState.REWARD_POPUP.value, "奖励弹窗")

    # --- 2026-11-17 终局「完成主线任务」回归 ---

    def test_all_clear_detected(self):
        # 实机文案：两行「完成主线任务极难」+「已完成极难难度的所有探索。」
        self.assertIs(
            classify_main_story_state(
                "", "", "", "", "完成主线任务极难 已完成极难难度的所有探索。",
            ),
            MainStoryState.ALL_CLEAR,
        )

    def test_all_clear_single_line_is_enough(self):
        # OCR 只读到一行时也要能认出来，否则会掉进 BUSY 白等到超时。
        self.assertIs(
            classify_main_story_state("", "", "", "", "已完成极难难度的所有探索"),
            MainStoryState.ALL_CLEAR,
        )

    def test_all_clear_tolerates_ocr_noise(self):
        self.assertIs(
            classify_main_story_state("", "", "", "", "完成主线任务 极 难"),
            MainStoryState.ALL_CLEAR,
        )

    def test_all_clear_wins_over_empty_screen(self):
        # 该提示出现时画面上没有任何按钮文字，必须优先于 BUSY 判定。
        self.assertIsNot(
            classify_main_story_state("", "", "", "", "完成主线任务困难"),
            MainStoryState.BUSY,
        )

    def test_all_clear_does_not_swallow_list_auto(self):
        # 万一同帧误命中，也不该抢走正常的列表页推进。
        self.assertIs(
            classify_main_story_state("", "自动进行", "自动进行", "", ""),
            MainStoryState.LIST_AUTO,
        )

    def test_all_clear_value_is_stable(self):
        self.assertEqual(MainStoryState.ALL_CLEAR.value, "主线已完成")


class PrepareFlowStateTest(unittest.TestCase):
    """2026-11-17 新增：从大厅「开始难度」开跑的这一段界面。

    实机截图（3840x2160）逐张 OCR 得到这里用的文案，顺序是
    追踪条开始难度 → 难度确认弹窗 → 剧情过场 → 快速探索确认弹窗。
    """

    def test_quest_start_detected(self):
        self.assertIs(
            classify_main_story_state(
                "", "", "", "",
                quest_start_text="主线 开始极难难度",
            ),
            MainStoryState.QUEST_START,
        )

    def test_quest_start_accepts_hard_mode(self):
        self.assertIs(
            classify_main_story_state(
                "", "", "", "",
                quest_start_text="主线 开始困难难度",
            ),
            MainStoryState.QUEST_START,
        )

    def test_quest_start_needs_both_words(self):
        # 「主线」这两个字追踪条、任务面板上到处都是，单独命中不能当成开跑入口。
        self.assertIs(
            classify_main_story_state("", "", "", "", quest_start_text="主线"),
            MainStoryState.BUSY,
        )

    def test_difficulty_dialog_detected(self):
        self.assertIs(
            classify_main_story_state(
                "", "", "", "",
                difficulty_dialog_text=(
                    "极难难度?难度仅适用于主线任务战斗。是否开始主线任务？"
                ),
            ),
            MainStoryState.DIFFICULTY_DIALOG,
        )

    def test_fast_explore_dialog_detected(self):
        self.assertIs(
            classify_main_story_state(
                "", "", "", "",
                fast_explore_text=(
                    "完成普通难度后，可省略剧情快速进行探索。是否前往快速探索界面？"
                ),
            ),
            MainStoryState.FAST_EXPLORE_DIALOG,
        )

    def test_cutscene_detected(self):
        # 实机 OCR 出来是小写「f2」。
        self.assertIs(
            classify_main_story_state("", "", "", "", cutscene_text="f2"),
            MainStoryState.CUTSCENE,
        )

    def test_cutscene_hint_tolerates_ocr_noise(self):
        # 右上角这块 ROI 压着追踪条，「F2」常被读成「Fz」「F2 」之类。
        self.assertIs(
            classify_main_story_state("", "", "", "", cutscene_text="F z"),
            MainStoryState.CUTSCENE,
        )

    def test_unrelated_hud_text_is_not_cutscene(self):
        # 5.png 实机：快速探索弹窗背后露出追踪条「主线」，被这块 ROI 读到了，
        # 之前的「读到文字就短路」直接把它算成过场，弹窗于是永远认不出来。
        self.assertIs(
            classify_main_story_state("", "", "", "", cutscene_text="主线"),
            MainStoryState.BUSY,
        )

    def test_prepare_states_do_not_swallow_list_auto(self):
        self.assertIs(
            classify_main_story_state(
                "", "自动进行", "", "",
                quest_start_text="主线 开始极难难度",
            ),
            MainStoryState.LIST_AUTO,
        )

    def test_skip_confirm_by_title(self):
        self.assertIs(
            classify_main_story_state(
                "", "", "", "", skip_confirm_text="跳过对话",
            ),
            MainStoryState.SKIP_CONFIRM,
        )

    def test_skip_confirm_by_question(self):
        # 实测问句 OCR 置信度只有 0.95，偶尔读成问号结尾，也要认出来。
        self.assertIs(
            classify_main_story_state(
                "", "", "", "", skip_confirm_text="是否结束剧情观看?",
            ),
            MainStoryState.SKIP_CONFIRM,
        )

    def test_skip_confirm_in_game_variant(self):
        # 7.png 实机：剧情演出里点 F2 之后弹的框，正文是「可于剧情菜单重新查看。」
        self.assertIs(
            classify_main_story_state(
                "", "", "", "",
                skip_confirm_text="跳过对话 跳过对话。 可于剧情菜单重新查看 取消 确认",
            ),
            MainStoryState.SKIP_CONFIRM,
        )

    def test_skip_confirm_wins_over_cutscene(self):
        # 剧情演出里点跳过时，右上角「F2」提示还留在弹窗背后：必须先认弹窗，
        # 否则会一直去点跳过图标（7.png 实机就是这种画面）。
        self.assertIs(
            classify_main_story_state(
                "", "", "", "",
                cutscene_text="f2",
                skip_confirm_text="跳过对话 可于剧情菜单重新查看 确认",
            ),
            MainStoryState.SKIP_CONFIRM,
        )

    def test_difficulty_title_is_not_skip_confirm(self):
        # 3.png 实机：难度弹窗的标题「极难难度」就落在跳对话标题那块 ROI 上。
        self.assertIs(
            classify_main_story_state(
                "", "", "", "",
                difficulty_dialog_text="极难难度?难度仅适用于主线任务战斗。是否开始主线任务？",
                skip_confirm_text="极难难度",
            ),
            MainStoryState.DIFFICULTY_DIALOG,
        )

    def test_story_dialog_detected_by_subtitle(self):
        # 2026-11-17 23:42 实机日志里读到的真实对白。
        self.assertIs(
            classify_main_story_state(
                "", "", "", "",
                story_text="都以为自己能全身而退",
            ),
            MainStoryState.STORY_DIALOG,
        )

    def test_story_dialog_does_not_swallow_buttons(self):
        for button in ("自动进行", "战斗"):
            self.assertIn(
                classify_main_story_state(
                    "", button, "", "",
                    story_text="都以为自己能全身而退",
                ),
                (MainStoryState.LIST_AUTO, MainStoryState.LIST_BATTLE),
            )

    def test_short_tracker_text_is_not_story(self):
        # 追踪条上只有「主线」两个字，凑不够一句对白。
        self.assertIs(
            classify_main_story_state("", "", "", "", story_text="主线"),
            MainStoryState.BUSY,
        )

    def test_story_dialog_value_is_stable(self):
        self.assertEqual(MainStoryState.SKIP_CONFIRM.value, "跳过对话确认")
        self.assertEqual(MainStoryState.STORY_DIALOG.value, "剧情演出")

    def test_prepare_state_values_are_stable(self):
        self.assertEqual(MainStoryState.QUEST_START.value, "追踪条-开始难度")
        self.assertEqual(MainStoryState.DIFFICULTY_DIALOG.value, "难度确认弹窗")
        self.assertEqual(MainStoryState.CUTSCENE.value, "剧情过场")
        self.assertEqual(
            MainStoryState.FAST_EXPLORE_DIALOG.value, "快速探索确认弹窗",
        )


class PrepareActionsTest(unittest.TestCase):
    """大厅开跑三步的点击表与「多重试几次」的安全冗余。"""

    @staticmethod
    def _inside(point, bounds):
        x1, y1, x2, y2 = bounds
        return (
            x1 * REFERENCE_WIDTH <= point[0] <= x2 * REFERENCE_WIDTH
            and y1 * REFERENCE_HEIGHT <= point[1] <= y2 * REFERENCE_HEIGHT
        )

    def test_table_covers_prepare_states(self):
        self.assertEqual(set(PREPARE_ACTIONS), set(PREPARE_STATES))

    def test_fallback_points_inside_their_rois(self):
        for state, (bounds, name, keywords, point, label) in PREPARE_ACTIONS.items():
            self.assertTrue(self._inside(point, bounds), state)
            self.assertTrue(keywords, state)
            self.assertTrue(name and label, state)

    def test_skip_confirm_points_inside_ok_buttons(self):
        # 全黑过场里的「确认」(1020,767)-(1067,792) 和剧情演出里的
        # 「确认」(1021,616)-(1070,642)——两种位置各一个兜底点，都要落在自己的 ROI 里。
        self.assertEqual(len(SKIP_CONFIRM_OK_ROIS), len(SKIP_CONFIRM_OK_POINTS))
        for fallback, bounds in zip(SKIP_CONFIRM_OK_POINTS, SKIP_CONFIRM_OK_ROIS):
            self.assertTrue(self._inside(fallback, bounds), fallback)

    def test_skip_confirm_ok_rois_exclude_cancel(self):
        # 「取消」实测文字框到 x≈900 为止，ROI 从 990 起才不会连它一起点。
        for bounds in SKIP_CONFIRM_OK_ROIS:
            self.assertGreater(bounds[0] * REFERENCE_WIDTH, 900, bounds)

    def test_story_fallback_point_inside_subtitle_band(self):
        self.assertTrue(
            any(self._inside(STORY_ADVANCE_POINT, roi) for roi in STORY_SUBTITLE_ROIS),
        )

    def test_story_subtitle_rois_are_small_enough_for_ocr(self):
        # PaddleOCR 会把长边缩到 960：ROI 实际像素宽度必须明显小于这条线，
        # 否则小字会被缩没（2026-11-17 22:46 奖励弹窗就栽在这上面）。
        for roi in STORY_SUBTITLE_ROIS:
            width_px = (roi[2] - roi[0]) * REFERENCE_WIDTH * 2  # 实机 3840 = 参考 1920 × 2
            self.assertLess(width_px, 960, roi)

    def test_story_subtitle_rois_cover_tall_dialogues(self):
        # 2026-11-18 实机：全屏 CG 上的对白正文在参考坐标 903~948，比关卡里的
        # 演出高一行多。旧 ROI 从 925 起，只切到字的下半截，OCR 读成 "["，
        # 任务因此判定「界面持续无法识别」停机。至少一条 ROI 要完整框住这段。
        band_top, band_bottom = 903, 948
        covered = [
            roi for roi in STORY_SUBTITLE_ROIS
            if roi[1] * REFERENCE_HEIGHT <= band_top
            and roi[3] * REFERENCE_HEIGHT >= band_bottom
        ]
        self.assertTrue(covered, f"没有一条字幕 ROI 能完整框住高位对白: {STORY_SUBTITLE_ROIS}")

    def test_story_subtitle_min_chars_is_sane(self):
        self.assertGreaterEqual(STORY_SUBTITLE_MIN_CHARS, 3)

    def test_retries_are_not_single_shot(self):
        # 用户要求：不要一次不过就判失败。一次点击内部要重试，
        # 整轮失败后还要能重来，剧情演出更要给足点击次数。
        self.assertGreaterEqual(CLICK_RETRY_ATTEMPTS, 3)
        self.assertGreaterEqual(PREPARE_RETRY_ROUNDS, 2)
        self.assertGreaterEqual(SKIP_CONFIRM_ROUNDS, 2)
        self.assertGreaterEqual(STORY_ADVANCE_CLICKS, 30)
        # 一场战斗几十个回合很正常，BATTLE 的点击上限必须给得够宽。
        self.assertGreaterEqual(BATTLE_TURN_CLICKS, 50)

    def test_battle_turn_point_inside_pill_button(self):
        # 实测「TURN. N | BATTLE」长条 (1600,952)-(1841,1018)，
        # 兜底点必须是「BATTLE」文字中心，且落在列表按钮那块 ROI 里。
        self.assertEqual(BATTLE_TURN_BUTTON_KEYWORDS, ("battle",))
        self.assertTrue(self._inside(BATTLE_TURN_POINT, LIST_BUTTON_ROI))
        # 长条左边是「切换视角 / 切换画面」，兜底点不能飘过去。
        self.assertGreater(BATTLE_TURN_POINT[0], 1700)
        self.assertLess(BATTLE_TURN_POINT[0], 1841)
        self.assertTrue(952 <= BATTLE_TURN_POINT[1] <= 1018)

    def test_battle_turn_interval_is_not_zero(self):
        # 同一帧上连点没有意义：一回合的动画要时间，留出间隔再点下一回合。
        self.assertGreaterEqual(BATTLE_TURN_INTERVAL, 1.0)


class CutsceneBarsTest(unittest.TestCase):
    """没读到「F2」时的第二判据：上下黑边 + 中间有画面。"""

    @staticmethod
    def _frame(top, middle, bottom):
        height, width = 100, 40
        band = 8  # int(100 * 0.08)
        frame = [[[middle] * 3 for _ in range(width)] for _ in range(height)]
        for y in range(band):
            for x in range(width):
                frame[y][x] = [top] * 3
                frame[height - 1 - y][x] = [bottom] * 3
        return _Array(frame)

    def test_black_bars_detected(self):
        self.assertTrue(
            MainStoryFeatureMixin._main_story_cutscene_bars(
                self._frame(5, 120, 5),
            )
        )

    def test_full_bright_frame_is_not_cutscene(self):
        self.assertFalse(
            MainStoryFeatureMixin._main_story_cutscene_bars(
                self._frame(120, 120, 120),
            )
        )

    def test_black_bars_with_black_middle_is_not_cutscene(self):
        # 黑屏读条也不能当成过场，否则会一直点跳过去。
        self.assertFalse(
            MainStoryFeatureMixin._main_story_cutscene_bars(
                self._frame(5, 5, 5),
            )
        )

    def test_broken_frame_is_not_cutscene(self):
        self.assertFalse(
            MainStoryFeatureMixin._main_story_cutscene_bars(object()),
        )


class _Array:
    """最小 numpy 替身：只支持 frame.shape / frame[a:b] 和 .mean()。"""

    def __init__(self, rows):
        self._rows = rows
        self.shape = (len(rows),)

    def __getitem__(self, item):
        return _Array(self._rows[item])

    def mean(self):
        values = [channel for row in self._rows for pixel in row for channel in pixel]
        return sum(values) / len(values)


class _Crop:
    """只提供 _main_story_box_point 需要的 shape/size，避免测试依赖 numpy。"""

    def __init__(self, height, width):
        self.shape = (height, width, 3)
        self.size = height * width * 3


class _Box:
    def __init__(self, x, y, width, height):
        self.x, self.y, self.width, self.height = x, y, width, height


class RewardClickPointTest(unittest.TestCase):
    """奖励弹窗点击点的换算。2026-11-17 22:46 实机就是这里算不出点而卡住。"""

    def test_hint_point_falls_inside_hint_roi(self):
        x1, y1, x2, y2 = REWARD_POPUP_HINT_ROI
        px, py = REWARD_POPUP_HINT_POINT
        self.assertGreaterEqual(px, x1 * REFERENCE_WIDTH)
        self.assertLessEqual(px, x2 * REFERENCE_WIDTH)
        self.assertGreaterEqual(py, y1 * REFERENCE_HEIGHT)
        self.assertLessEqual(py, y2 * REFERENCE_HEIGHT)

    def test_box_center_maps_to_roi_center(self):
        # ROI 内的正中像素必须换算回 ROI 的正中参考坐标，漏加 ROI 偏移就会挂。
        bounds = REWARD_POPUP_HINT_ROI
        crop = _Crop(180, 600)
        point = MainStoryFeatureMixin._main_story_box_point(
            bounds, crop, _Box(250, 40, 100, 100),
        )
        x1, y1, x2, y2 = bounds
        self.assertAlmostEqual(point[0], (x1 + x2) / 2 * REFERENCE_WIDTH, places=3)
        self.assertAlmostEqual(point[1], (y1 + y2) / 2 * REFERENCE_HEIGHT, places=3)

    def test_box_point_is_scaled_by_reference_not_frame(self):
        # 4K 帧裁出的 crop 是参考坐标的 2 倍宽，换算结果只应是参考坐标。
        bounds = REWARD_POPUP_HINT_ROI
        point = MainStoryFeatureMixin._main_story_box_point(
            bounds, _Crop(180, 600), _Box(0, 0, 600, 180),
        )
        x1, y1, x2, y2 = bounds
        self.assertAlmostEqual(point[0], (x1 + x2) / 2 * REFERENCE_WIDTH, places=3)
        self.assertAlmostEqual(point[1], (y1 + y2) / 2 * REFERENCE_HEIGHT, places=3)

    def test_empty_crop_falls_back_to_roi_center(self):
        bounds = REWARD_POPUP_HINT_ROI
        x1, y1, x2, y2 = bounds
        point = MainStoryFeatureMixin._main_story_box_point(
            bounds, None, _Box(0, 0, 0, 0),
        )
        self.assertAlmostEqual(point[0], (x1 + x2) / 2 * REFERENCE_WIDTH, places=3)
        self.assertAlmostEqual(point[1], (y1 + y2) / 2 * REFERENCE_HEIGHT, places=3)


class _NamedBox(_Box):
    def __init__(self, x, y, width, height, name):
        super().__init__(x, y, width, height)
        self.name = name


class BattleTurnPointTest(unittest.TestCase):
    """战斗「BATTLE」按钮点哪儿：读到 BATTLE 文字框就用框中心，否则用实测兜底点。"""

    @staticmethod
    def _helper(boxes):
        class _Dummy(MainStoryFeatureMixin):
            def _main_story_ocr_boxes(self, frame, bounds, name, threshold):
                return boxes, _Crop(120, 490)  # LIST_BUTTON_ROI 在 4K 下的裁剪尺寸

        return _Dummy()

    def test_uses_battle_text_box_center(self):
        point = self._helper(
            [_NamedBox(300, 40, 120, 30, "BATTLE")],
        )._main_story_battle_turn_point(None, 0.2)
        x1, y1, x2, y2 = LIST_BUTTON_ROI
        self.assertAlmostEqual(
            point[0], (x1 + 360 / 490 * (x2 - x1)) * REFERENCE_WIDTH, places=3,
        )
        self.assertAlmostEqual(
            point[1], (y1 + 55 / 120 * (y2 - y1)) * REFERENCE_HEIGHT, places=3,
        )

    def test_turn_label_alone_falls_back_to_measured_point(self):
        # 只读到「TURN.1」时不算数：兜底点仍是实测的 BATTLE 文字中心。
        point = self._helper(
            [_NamedBox(10, 40, 120, 30, "TURN.1"), _NamedBox(300, 40, 120, 30, "？")],
        )._main_story_battle_turn_point(None, 0.2)
        self.assertEqual(point, BATTLE_TURN_POINT)

    def test_no_boxes_falls_back_to_measured_point(self):
        point = self._helper([])._main_story_battle_turn_point(None, 0.2)
        self.assertEqual(point, BATTLE_TURN_POINT)


if __name__ == "__main__":
    unittest.main()
