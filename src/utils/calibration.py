from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ReferenceCalibration:
    """One explicit client-resolution calibration used across BD2 tasks.

    All reference pixel coordinates in the project are calibrated against a
    known resolution and converted to relative coordinates at runtime.  Keep
    the canonical calibration objects here so task modules never redefine the
    numbers themselves.
    """

    width: int
    height: int

    @property
    def size(self) -> tuple[int, int]:
        return self.width, self.height


HD_720 = ReferenceCalibration(1280, 720)
FHD_1080 = ReferenceCalibration(1920, 1080)
QHD_1440 = ReferenceCalibration(2560, 1440)
