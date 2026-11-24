class RepeatedAction:
    """Count dispatched attempts whose next confirmed state is unchanged."""

    def __init__(self):
        self.reset()

    def reset(self):
        self.pending = None
        self.unchanged = 0

    def dispatched(self, state):
        self.pending = state

    def observe(self, state):
        if self.pending is not None:
            self.unchanged = self.unchanged + 1 if self.pending == state else 0
            self.pending = None
        return self.unchanged >= 5
