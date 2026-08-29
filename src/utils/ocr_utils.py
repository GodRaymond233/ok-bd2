from difflib import SequenceMatcher

_T2S_CONVERTER = None


def _to_simplified(value: str) -> str:
    """Collapse traditional/simplified variants so OCR script mixing cannot break matching."""
    global _T2S_CONVERTER
    if _T2S_CONVERTER is None:
        from opencc import OpenCC

        _T2S_CONVERTER = OpenCC("t2s")
    return _T2S_CONVERTER.convert(value)


def normalize_ocr_text(value: object, *, alnum_only: bool = False) -> str:
    """Normalize OCR text while preserving the punctuation used by regex callers."""
    lowered = str(value).lower()
    if alnum_only:
        return "".join(character for character in lowered if character.isalnum())
    return "".join(lowered.split())


def fuzzy_substring_match(
    normalized_text: str,
    normalized_keyword: str,
    minimum_ratio: float,
) -> bool:
    """Match a normalized keyword against similarly sized windows of OCR text."""
    if not normalized_text or not normalized_keyword:
        return False
    if normalized_keyword in normalized_text:
        return True

    ratio = max(0.01, min(1.0, float(minimum_ratio)))
    keyword_length = len(normalized_keyword)
    minimum_window = max(1, round(keyword_length * ratio))
    maximum_window = max(minimum_window, round(keyword_length / ratio))
    maximum_window = min(maximum_window, len(normalized_text))

    for window_length in range(minimum_window, maximum_window + 1):
        for start in range(len(normalized_text) - window_length + 1):
            window = normalized_text[start : start + window_length]
            if SequenceMatcher(None, normalized_keyword, window).ratio() >= ratio:
                return True
    return False


def keyword_match_count(
    text: object,
    keywords: list[str] | tuple[str, ...],
    *,
    alnum_only: bool = False,
    fuzzy_ratio: float | None = None,
) -> int:
    """Count unique configured keywords present in OCR text.

    The game client is Traditional Chinese while keywords are authored in Simplified
    Chinese, and OCR output can mix scripts within one box, so both sides are folded
    to Simplified before matching (BUG-20260829-06).
    """
    normalized_text = _to_simplified(normalize_ocr_text(text, alnum_only=alnum_only))
    matches = 0
    for keyword in keywords:
        normalized_keyword = _to_simplified(
            normalize_ocr_text(keyword, alnum_only=alnum_only)
        )
        if fuzzy_ratio is None:
            matched = bool(normalized_keyword and normalized_keyword in normalized_text)
        else:
            matched = fuzzy_substring_match(
                normalized_text,
                normalized_keyword,
                fuzzy_ratio,
            )
        matches += int(matched)
    return matches
