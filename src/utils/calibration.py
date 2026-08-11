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


def reference_rect_to_relative_roi(
    rect: tuple[int, int, int, int],
    reference: ReferenceCalibration,
) -> tuple[float, float, float, float]:
    """Convert a reference ``(x, y, width, height)`` rect to an LTRB ROI.

    ``relative_roi_frame`` consumes fractional ``(left, top, right, bottom)``
    bounds. Keep this conversion named and centralized so reference rectangle
    widths/heights cannot accidentally be passed as right/bottom coordinates.
    """

    x, y, width, height = rect
    return (
        x / reference.width,
        y / reference.height,
        (x + width) / reference.width,
        (y + height) / reference.height,
    )
