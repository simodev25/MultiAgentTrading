import logging
import sys


def configure_logging() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s %(levelname)s %(name)s %(message)s',
        handlers=[logging.StreamHandler(sys.stdout)],
    )
    # Silence noisy MetaAPI SDK loggers (WebSocket reconnection loops, PING/PONG)
    logging.getLogger('engineio').setLevel(logging.ERROR)
    logging.getLogger('engineio.client').setLevel(logging.ERROR)
    logging.getLogger('socketio').setLevel(logging.ERROR)
    logging.getLogger('socketio.client').setLevel(logging.ERROR)
