"""Mouse navigation shared by the independent cartridge challenges."""

from time import monotonic

import cv2

from src.tasks.HomeRewardTasks import HomePageTask

HOME_COLLECTION_ENTRY = (.881, .908)
COLLECTION_SCROLL_POINT = (.52, .55)


def red_silhouette(frame):
    blue, green, red = cv2.split(frame[:, :, :3])
    contrast = cv2.subtract(red, cv2.max(blue, green))
    return cv2.bitwise_and(cv2.inRange(red, 151, 255), cv2.inRange(contrast, 66, 255))


class CartridgeChallengeTask(HomePageTask):
    waits_for_login = True

    @staticmethod
    def _on_collection(labels):
        return any(text == "游戏卡珍藏集" and .07 < x < .37 and y < .10
                   for text, x, y, _ in labels)

    def _enter_collection(self):
        self.info_set("状态", "打开游戏卡珍藏集")
        self.operate_click(*HOME_COLLECTION_ENTRY, after_sleep=.5)
        deadline = monotonic() + 10
        while monotonic() < deadline:
            frame, labels = self._read()
            if self._on_collection(labels):
                return frame, labels
            self.sleep(.4)
        return None

    def _card_center(self, frame, card):
        cover = self._match(frame, card)
        if not self._passes(cover, card):
            return None
        height, width = frame.shape[:2]
        x, y = cover.center
        return x / width, y / height

    def _enter_cartridge(self, frame, labels, card, arrived):
        if not self._on_collection(labels):
            if not self._frame_confirms_home(frame, self.name) and not self.prepare_home(self.name):
                return None
            collection = self._enter_collection()
            if collection is None:
                return None
            frame, labels = collection
        for scroll in (0, 3, 3, -3, -3, -3, -3):
            if not self._on_collection(labels):
                return None
            if scroll:
                self.scroll_client(COLLECTION_SCROLL_POINT, scroll, count=1, after_sleep=.7)
                frame, labels = self._read()
                if not self._on_collection(labels):
                    return None
            first = self._card_center(frame, card)
            if first is None:
                continue
            self.sleep(.35)
            frame, labels = self._read()
            if not self._on_collection(labels):
                return None
            second = self._card_center(frame, card)
            if second is not None and max(abs(a - b) for a, b in zip(first, second)) <= .008:
                self.info_set("状态", "载入目标卡带")
                self.operate_click(*second, after_sleep=.5)
                return self._wait_cartridge(arrived)
        return None

    def _wait_cartridge(self, arrived):
        deadline = monotonic() + 25
        while monotonic() < deadline:
            frame, labels = self._read()
            if arrived(frame, labels):
                return frame, labels
            self.sleep(.5)
        return None
