from __future__ import annotations

import logging
import re
from pathlib import Path

import applypilot.cli as cli
import applypilot.config as config


def test_configure_logging_uses_timestamped_tailor_file_and_fixed_cover_file(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(config, "LOG_DIR", tmp_path)

    tailor_logger = logging.getLogger("applypilot.scoring.tailor")
    cover_logger = logging.getLogger("applypilot.scoring.cover_letter")
    original = {
        tailor_logger: (list(tailor_logger.handlers), tailor_logger.propagate),
        cover_logger: (list(cover_logger.handlers), cover_logger.propagate),
    }

    for logger in (tailor_logger, cover_logger):
        logger.handlers.clear()

    try:
        cli._configure_logging()

        tailor_handlers = [h for h in tailor_logger.handlers if isinstance(h, logging.FileHandler)]
        cover_handlers = [h for h in cover_logger.handlers if isinstance(h, logging.FileHandler)]

        assert len(tailor_handlers) == 1
        assert len(cover_handlers) == 1

        tailor_name = Path(tailor_handlers[0].baseFilename).name
        cover_name = Path(cover_handlers[0].baseFilename).name

        assert re.fullmatch(r"\d{4}-\d{2}-\d{2}_\d{2}-\d{2}-\d{2}_tailor\.log", tailor_name)
        assert cover_name == "cover_letter.log"

        cli._configure_logging()
        assert len([h for h in tailor_logger.handlers if isinstance(h, logging.FileHandler)]) == 1
        assert len([h for h in cover_logger.handlers if isinstance(h, logging.FileHandler)]) == 1
    finally:
        for logger in (tailor_logger, cover_logger):
            for handler in list(logger.handlers):
                logger.removeHandler(handler)
                handler.close()
            original_handlers, original_propagate = original[logger]
            for handler in original_handlers:
                logger.addHandler(handler)
            logger.propagate = original_propagate
