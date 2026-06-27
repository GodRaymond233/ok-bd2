from ok import TriggerTask


class BD2TriggerTask(TriggerTask):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.name = "BD2 触发任务示例"
        self.description = "触发任务示例。需要时在 config.py 中注册。"
        self.trigger_count = 0

    def run(self):
        self.trigger_count += 1
        self.log_debug(f"BD2TriggerTask run {self.trigger_count}")
        return False
