def draw_close_glyph(frame, center, size=27, value=60):
    """在 frame 上画一个叉号，模拟弹窗右上角的关闭按钮。"""
    x, y = center
    half = size // 2
    thickness = max(2, size // 5)
    for offset in range(-half, half + 1):
        for shift in range(-(thickness // 2), thickness // 2 + 1):
            row = min(frame.shape[0] - 1, max(0, y + offset))
            left = min(frame.shape[1] - 1, max(0, x + offset + shift))
            right = min(frame.shape[1] - 1, max(0, x - offset + shift))
            frame[row, left] = value
            frame[row, right] = value
    return frame
