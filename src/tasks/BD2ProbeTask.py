from datetime import datetime

from qfluentwidgets import FluentIcon

from src.tasks.BaseBD2Task import BaseBD2Task


class BD2ProbeTask(BaseBD2Task):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.name = "BD2 截图 OCR 探针"
        self.description = "后台截图、执行 OCR，并保存探针结果。"
        self.icon = FluentIcon.SEARCH
        self.visible = True
        self.default_config.update(
            {
                "OCR 识别阈值": 0.2,
                "保存 OCR 调试截图": False,
            }
        )
        self.config_description.update(
            {
                "OCR 识别阈值": "文本框 OCR 识别使用的最低可信度。",
                "保存 OCR 调试截图": "同时保存 OCR 裁剪或调试图片。",
            }
        )

    def run(self):
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        frame = self.capture_frame(f"bd2_probe_{timestamp}")
        boxes = self.ocr_frame(
            frame=frame,
            threshold=float(self.config.get("OCR 识别阈值", self.config.get("OCR Threshold", 0.2))),
            screenshot=bool(
                self.config.get("保存 OCR 调试截图", self.config.get("Save OCR Screenshot", False))
            ),
        )

        lines = [
            f"timestamp={timestamp}",
            f"capture_method={self.capture_method_name}",
            f"resolution={frame.shape[1]}x{frame.shape[0]}",
            f"text_count={len(boxes)}",
            "",
        ]
        for box in boxes:
            lines.append(f"{box.name}\t{box.confidence:.3f}\t{box}")

        output_path = self.write_probe_text("bd2_probe_ocr_latest.txt", lines)
        self.log_info(f"BD2 截图 OCR 探针完成：{output_path}", notify=True)
        return True
