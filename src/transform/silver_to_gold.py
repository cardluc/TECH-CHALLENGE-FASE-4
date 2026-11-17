"""Silver -> Gold: documentos prontos para o RAG.

* **Um documento = uma avaliação.** Os comentários são curtos, então não há chunking.
* **Seleção:** entram avaliações com texto e (>= 3 palavras OU menção a algum tema).
  Comentários como "ok", "10" e "muito bom" não explicam *por que* o cliente está
  satisfeito e ocupariam o contexto do LLM sem acrescentar informação.
* **Representação lexical:** tokens normalizados (sem acento, sem stopwords,
  stemming RSLP) para o BM25, persistidos junto com o documento.

O índice vetorial (embeddings no ChromaDB) é criado a partir desta camada pela
etapa ``indice`` (``src/rag/indexacao.py``).
"""
from __future__ import annotations

import pandas as pd

from src import config
from src.transform.texto import tokens_for_bm25
from src.utils import get_logger

logger = get_logger("gold")


def selecionar(silver: pd.DataFrame) -> pd.DataFrame:
    informativo = (silver["n_words"] >= config.MIN_PALAVRAS) | (silver["n_themes"] > 0)
    docs = silver[silver["has_text"] & informativo].copy()
    docs["bm25_tokens"] = docs["text"].map(tokens_for_bm25)
    return docs[docs["bm25_tokens"].str.len() > 0].reset_index(drop=True)


def executar(silver: pd.DataFrame | None = None) -> pd.DataFrame:
    config.ensure_dirs()
    silver = silver if silver is not None else pd.read_parquet(config.SILVER_AVALIACOES)
    gold = selecionar(silver)
    gold.to_parquet(config.GOLD_DOCUMENTOS, index=False)
    logger.info(
        "Gold: %d documentos indexáveis (de %d com texto) -> %s",
        len(gold), int(silver["has_text"].sum()), config.GOLD_DOCUMENTOS.name,
    )
    return gold
