from src.utils.ocr_utils import normalize_ocr_text

HOME_GACHA_OCR_REFERENCE_ROI = (110, 993, 95, 54)
HOME_GACHA_OCR_RELATIVE_ROI = (
    110 / 1920,
    993 / 1080,
    205 / 1920,
    1047 / 1080,
)
HOME_GACHA_OCR_KEYWORD = "抽抽乐"
HOME_ANNOUNCEMENT_CLEAR_REFERENCE_POINT = (169, 615)
HOME_ANNOUNCEMENT_CLEAR_RELATIVE_POINT = (
    HOME_ANNOUNCEMENT_CLEAR_REFERENCE_POINT[0] / 1920,
    HOME_ANNOUNCEMENT_CLEAR_REFERENCE_POINT[1] / 1080,
)


def home_gacha_ocr_matches(text: object) -> bool:
    return normalize_ocr_text(HOME_GACHA_OCR_KEYWORD) in normalize_ocr_text(text)


def home_confirmation_passes(
    *,
    button_found: bool,
    brightness_ratio: float,
    brightness_threshold: float,
    gacha_ocr_text: object,
) -> bool:
    """Require all three same-frame signals before confirming the global home page."""
    return (
        bool(button_found)
        and float(brightness_ratio) >= float(brightness_threshold)
        and home_gacha_ocr_matches(gacha_ocr_text)
    )


def home_temporary_announcement_detected(
    *,
    button_found: bool,
    brightness_ratio: float,
    brightness_threshold: float,
    gacha_ocr_text: object,
) -> bool:
    """Detect a dimmed global home page covered by a temporary announcement."""
    return (
        bool(button_found)
        and float(brightness_ratio) < float(brightness_threshold)
        and home_gacha_ocr_matches(gacha_ocr_text)
    )
