import logging
import sys


def setup_logging(level: str) -> None:
    logging.basicConfig(
        level=level.upper(),
        stream=sys.stdout,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    # httpx logs every request at INFO, which is noise for a 30-minute poll.
    logging.getLogger("httpx").setLevel(logging.WARNING)
