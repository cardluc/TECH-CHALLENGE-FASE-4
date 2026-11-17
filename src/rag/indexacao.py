"""Criação dos embeddings da camada Gold e indexação no ChromaDB."""
from __future__ import annotations

import shutil
import time
from functools import lru_cache

import pandas as pd
from langchain_chroma import Chroma
from langchain_huggingface import HuggingFaceEmbeddings

from src import config
from src.transform.temas import THEMES
from src.utils import get_logger

logger = get_logger("indice")

COLUNAS_METADADOS = [
    "review_id", "order_id", "review_score", "sentiment", "purchase_year", "purchase_yyyymm",
    "delivery_status", "product_category", "customer_state", "order_status",
    *[f"tema_{t}" for t in THEMES],
]


@lru_cache(maxsize=1)
def get_embeddings() -> HuggingFaceEmbeddings:
    # normalize_embeddings=True: vetores unitários, então produto interno = similaridade de cosseno.
    return HuggingFaceEmbeddings(
        model_name=config.EMBEDDING_MODEL,
        model_kwargs={"device": config.EMBEDDING_DEVICE},
        encode_kwargs={"normalize_embeddings": True, "batch_size": 128},
    )


def get_vectorstore() -> Chroma:
    return Chroma(
        collection_name=config.COLECAO,
        embedding_function=get_embeddings(),
        persist_directory=str(config.CHROMA_DIR),
        # HNSW com distância de cosseno (busca aproximada de vizinhos, ANN)
        collection_metadata={"hnsw:space": "cosine"},
    )


def _metadados(linha: pd.Series) -> dict:
    meta = {}
    for col in COLUNAS_METADADOS:
        valor = linha[col]
        if hasattr(valor, "item"):  # numpy -> tipo nativo (o Chroma não aceita numpy)
            valor = valor.item()
        meta[col] = valor
    return meta


def executar(docs: pd.DataFrame | None = None, lote: int = 2000) -> None:
    docs = docs if docs is not None else pd.read_parquet(config.GOLD_DOCUMENTOS)
    if config.CHROMA_DIR.exists():
        try:
            shutil.rmtree(config.CHROMA_DIR)
        except PermissionError as exc:
            raise PermissionError(
                f"O índice em {config.CHROMA_DIR} está em uso. Feche a interface "
                "(Streamlit) ou outro processo que esteja usando o projeto e rode de novo."
            ) from exc
    logger.info("Gerando embeddings de %d documentos em %s", len(docs), config.CHROMA_DIR)
    t0 = time.perf_counter()
    store = get_vectorstore()
    for inicio in range(0, len(docs), lote):
        bloco = docs.iloc[inicio : inicio + lote]
        store.add_texts(
            texts=bloco["text"].tolist(),
            metadatas=[_metadados(r) for _, r in bloco.iterrows()],
            ids=bloco["review_id"].tolist(),
        )
        logger.info("Indexados %d/%d", min(inicio + lote, len(docs)), len(docs))
    logger.info("Índice criado em %.0fs", time.perf_counter() - t0)
