"""Route uvicorn's log records to the app logger (from ok-script 2.0.6 ok/ui/web/server.py)."""

import logging

from ok.util.logger import Logger


class _OkServerLogHandler(logging.Handler):
    """Route ASGI server records through ok-script's logger."""

    _WEBSOCKET_FRAME_PREFIXES = (
        "> TEXT", "< TEXT", "> BINARY", "< BINARY",
        "> PING", "< PING", "> PONG", "< PONG",
        "> CLOSE", "< CLOSE",
    )

    def __init__(self):
        super().__init__(logging.DEBUG)
        self.logger = Logger.get_logger("web_server")

    def emit(self, record):
        message = self.format(record)
        # Uvicorn sends WebSocket wire traces through uvicorn.error in debug
        # mode, while other backends use websockets.server. Filter by the
        # actual frame marker so neither path can feed streamed log events
        # back into the socket.
        if (record.levelno <= logging.DEBUG
                and message.lstrip().startswith(self._WEBSOCKET_FRAME_PREFIXES)):
            return
        if record.levelno <= logging.INFO:
            self.logger.debug(message)
        elif record.levelno <= logging.WARNING:
            self.logger.warning(message)
        elif record.levelno <= logging.ERROR:
            self.logger.error(message)
        else:
            self.logger.critical(message)


def _configure_server_logging():
    handler = _OkServerLogHandler()
    for name in ("uvicorn", "uvicorn.error", "uvicorn.access", "websockets.server"):
        server_logger = logging.getLogger(name)
        server_logger.handlers = [handler]
        # websockets emits every inbound/outbound frame at DEBUG. Routing those
        # records through the app logger feeds log events back into the same
        # WebSocket and can create an endless stream of "> TEXT" messages.
        server_logger.setLevel(
            logging.INFO if name == "websockets.server" else logging.DEBUG
        )
        server_logger.propagate = False
