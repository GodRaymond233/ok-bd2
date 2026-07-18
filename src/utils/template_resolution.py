from pathlib import Path, PurePosixPath

OFFLINE_TEMPLATE_ROOT_NAME = "train-source-screenshots"
OFFLINE_GREEN_TEMPLATE_DIR = "green"
OFFLINE_TEMPLATE_REFERENCE_RESOLUTIONS = {
    "image": (1280, 720),
    "root": (1920, 1080),
}
OFFLINE_TEMPLATE_REFERENCE_SCALES = {
    "image": 1.25,
    "root": 1.0,
}


def _offline_template_parts(template_path: str | Path) -> tuple[str, ...]:
    normalized = str(template_path).replace("\\", "/")
    return tuple(part.casefold() for part in PurePosixPath(normalized).parts)


def _offline_template_group(template_path: str | Path) -> str:
    folded = _offline_template_parts(template_path)
    root_name = OFFLINE_TEMPLATE_ROOT_NAME.casefold()

    if root_name in folded:
        root_index = folded.index(root_name)
        is_image_asset = (
            root_index + 1 < len(folded) and folded[root_index + 1] == "image"
        )
    else:
        is_image_asset = bool(folded and folded[0] == "image")

    return "image" if is_image_asset else "root"


def offline_template_reference_resolution(
    template_path: str | Path,
) -> tuple[int, int]:
    """Return the source-screen resolution represented by an offline template."""
    return OFFLINE_TEMPLATE_REFERENCE_RESOLUTIONS[_offline_template_group(template_path)]


def offline_template_requires_green_mask(template_path: str | Path) -> bool:
    """Return whether a template is marked as green-screen by its folder."""
    folded = _offline_template_parts(template_path)
    root_name = OFFLINE_TEMPLATE_ROOT_NAME.casefold()
    green_name = OFFLINE_GREEN_TEMPLATE_DIR.casefold()

    if root_name in folded:
        relative_parts = folded[folded.index(root_name) + 1 :]
    else:
        relative_parts = folded

    return len(relative_parts) >= 3 and relative_parts[:2] == ("image", green_name)


def offline_template_scale(
    template_path: str | Path,
    frame_width: int,
    frame_height: int,
    reference_scale: float | None = None,
) -> float:
    """Scale a template from its calibrated 1080p baseline to the client."""
    group = _offline_template_group(template_path)
    if group == "image":
        baseline_scale = OFFLINE_TEMPLATE_REFERENCE_SCALES[group]
    else:
        baseline_scale = (
            max(0.01, float(reference_scale))
            if reference_scale is not None
            else OFFLINE_TEMPLATE_REFERENCE_SCALES[group]
        )
    client_scale = min(
        max(1, int(frame_width)) / 1920,
        max(1, int(frame_height)) / 1080,
    )
    return baseline_scale * client_scale
