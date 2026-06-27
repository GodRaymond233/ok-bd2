def set_text_if_present(widget, text: str) -> None:
    if widget is not None and hasattr(widget, "setText"):
        widget.setText(text)
