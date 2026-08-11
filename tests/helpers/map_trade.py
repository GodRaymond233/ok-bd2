"""Shared fixtures for map-trade tests (extracted from test_map_trade)."""

import numpy as np

from src.tasks.map_trade.models import CollectionMapRole


class FakeTask:
    def __init__(self):
        self.config = {"跑图跑商 OCR 阈值": 0.2}
        self.clicks = []
        self.infos = []

    def operate_click(self, x, y, after_sleep=0):
        self.clicks.append((x, y, after_sleep))

    def capture_frame(self):
        return np.zeros((720, 1280, 3), dtype=np.uint8)

    def info_set(self, *args):
        self.infos.append(args)

    def sleep(self, *_args):
        return None


def _seed_action_records(store, card_id, target_key):
    """Persist the durable role-required records before a target commit."""

    actions = (
        ("吸收",) if target_key == CollectionMapRole.MAIN_AREA.value else ("吸收", "召集", "压制")
    )
    for action in actions:
        store.arm_action(card_id, target_key, action)
        store.mark_action_local_done(card_id, target_key, action, pending=False)


def _seed_battle_supplements(store, card_id, target_key):
    """Persist the summon/suppress records when a test seeds absorb itself."""

    for action in ("召集", "压制"):
        store.arm_action(card_id, target_key, action)
        store.mark_action_local_done(card_id, target_key, action, pending=False)
