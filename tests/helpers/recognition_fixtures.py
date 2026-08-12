"""Strict loaders for full-HD recognition regression fixtures."""

from pathlib import Path

import cv2
import numpy as np

FHD_BGR_SHAPE = (1080, 1920, 3)


def load_fhd_bgr(path: str | Path) -> np.ndarray:
    """Load one fixture as the exact 1920×1080 uint8 BGR frame Vision receives."""

    fixture_path = Path(path)
    frame = cv2.imread(str(fixture_path), cv2.IMREAD_COLOR)
    assert frame is not None, f"Unable to read recognition fixture: {fixture_path}"
    assert frame.dtype == np.uint8, (
        f"Recognition fixture must be uint8 BGR: {fixture_path} ({frame.dtype})"
    )
    assert frame.shape == FHD_BGR_SHAPE, (
        f"Recognition fixture must be 1920×1080 BGR: {fixture_path} ({frame.shape})"
    )
    return frame
