from pathlib import Path, PurePosixPath

OFFLINE_TEMPLATE_ROOT_NAME = "train-source-screenshots"
OFFLINE_TEMPLATE_REFERENCE_RESOLUTIONS = {
    "image": (1280, 720),
    "root": (1920, 1080),
}


def offline_template_reference_resolution(
    template_path: str | Path,
) -> tuple[int, int]:
    """Return the source-screen resolution represented by an offline template."""
    normalized = str(template_path).replace("\\", "/")
    parts = PurePosixPath(normalized).parts
    folded = tuple(part.casefold() for part in parts)
    root_name = OFFLINE_TEMPLATE_ROOT_NAME.casefold()

    if root_name in folded:
        root_index = folded.index(root_name)
        is_image_asset = (
            root_index + 1 < len(folded) and folded[root_index + 1] == "image"
        )
    else:
        is_image_asset = bool(folded and folded[0] == "image")

    key = "image" if is_image_asset else "root"
    return OFFLINE_TEMPLATE_REFERENCE_RESOLUTIONS[key]


def offline_template_scale(
    template_path: str | Path,
    frame_width: int,
    frame_height: int,
) -> float:
    """Scale a template from its source-screen resolution to the captured client."""
    reference_width, reference_height = offline_template_reference_resolution(template_path)
    return min(
        max(1, int(frame_width)) / reference_width,
        max(1, int(frame_height)) / reference_height,
    )
