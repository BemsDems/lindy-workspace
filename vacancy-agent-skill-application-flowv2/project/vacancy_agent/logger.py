import logging
import sys

from loguru import logger

from vacancy_agent.config import LOG_FILE, settings


class InterceptHandler(logging.Handler):
    """Пробрасывает стандартные logging.* логи в loguru.

    Это нужно, чтобы видеть логи внешнего cover-letter-generator,
    потому что он пишет через logging.info/warning/error, а не через loguru.
    """

    def emit(self, record: logging.LogRecord) -> None:
        try:
            level = logger.level(record.levelname).name
        except ValueError:
            level = record.levelno

        logger.opt(
            depth=6,
            exception=record.exc_info,
        ).log(level, record.getMessage())


def setup_std_logging() -> None:
    root_logger = logging.getLogger()
    root_logger.handlers = [InterceptHandler()]

    # Важно: именно DEBUG/INFO, иначе logging.info из генератора не будет видно.
    root_logger.setLevel(logging.DEBUG)

    # Эти библиотеки могут начать слишком много шуметь.
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    logging.getLogger("asyncio").setLevel(logging.WARNING)


def setup_logger():
    logger.remove()

    logger.add(
        sys.stdout,
        level=settings.log_level,
        colorize=True,
        format="<green>{time:HH:mm:ss}</green> | <level>{level:<8}</level> | <level>{message}</level>",
    )

    logger.add(
        LOG_FILE,
        level="DEBUG",
        rotation="10 MB",
        retention="14 days",
        compression="zip",
        encoding="utf-8",
        format="{time:YYYY-MM-DD HH:mm:ss} | {level:<8} | {name}:{function}:{line} | {message}",
    )

    setup_std_logging()

    return logger


log = setup_logger()
