from ok import BaseScene


class BD2Scene(BaseScene):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._logged_in = False
        self._in_combat = None

    def reset(self):
        self._in_combat = None

    def logged_in(self) -> bool:
        return self._logged_in

    def set_logged_in(self, value: bool = True) -> None:
        self._logged_in = value

    def in_combat(self):
        return self._in_combat

    def set_in_combat(self) -> bool:
        self._in_combat = True
        return True

    def set_not_in_combat(self) -> bool:
        self._in_combat = False
        return False
