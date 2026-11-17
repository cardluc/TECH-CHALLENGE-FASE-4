"""Logging compartilhado."""
from __future__ import annotations

import logging
import sys


def get_logger(name: str) -> logging.Logger:
    logger = logging.getLogger(name)
    if not logger.handlers:
        h = logging.StreamHandler(sys.stdout)
        h.setFormatter(logging.Formatter("%(asctime)s | %(levelname)-7s | %(name)s | %(message)s",
                                         datefmt="%H:%M:%S"))
        logger.addHandler(h)
        logger.setLevel(logging.INFO)
        logger.propagate = False
    return logger


def silenciar_bibliotecas() -> None:
    """Reduz o ruído de log de bibliotecas de terceiros."""
    for nome in ("httpx", "httpx2", "openai", "chromadb", "sentence_transformers", "urllib3",
                 "huggingface_hub", "transformers"):
        logging.getLogger(nome).setLevel(logging.WARNING)
