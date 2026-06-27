from ok import Box


class ScreenPosition:
    def __init__(self, task):
        self.task = task

    def box(self, x: float, y: float, to_x: float, to_y: float, name: str | None = None) -> Box:
        return self.task.box_of_screen(x, y, to_x, to_y, name=name)
