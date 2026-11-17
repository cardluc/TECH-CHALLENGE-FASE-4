"""Configurações do projeto: caminhos das camadas, modelos e parâmetros do RAG."""
from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

ROOT_DIR = Path(os.getenv("PROJETO_ROOT", Path(__file__).resolve().parents[1]))
load_dotenv(ROOT_DIR / ".env")

# --- Camadas do medalhão ----------------------------------------------------
DATA_DIR = ROOT_DIR / "data"
BRONZE_DIR = DATA_DIR / "bronze"  # CSVs originais da Olist, sem transformação
SILVER_DIR = DATA_DIR / "silver"  # avaliações limpas, deduplicadas e enriquecidas
GOLD_DIR = DATA_DIR / "gold"      # documentos prontos para o RAG + índice vetorial

SILVER_AVALIACOES = SILVER_DIR / "avaliacoes.parquet"
GOLD_DOCUMENTOS = GOLD_DIR / "documentos.parquet"
MANIFESTO_BRONZE = BRONZE_DIR / "_ingestao.json"

REPORTS_DIR = ROOT_DIR / "reports"
IMAGES_DIR = ROOT_DIR / "images"
GOLDEN_SET = ROOT_DIR / "eval" / "golden_set.json"

# Arquivos esperados na camada bronze (dataset do Kaggle)
ARQUIVOS_OLIST = [
    "olist_order_reviews_dataset.csv",
    "olist_orders_dataset.csv",
    "olist_customers_dataset.csv",
    "olist_order_items_dataset.csv",
    "olist_products_dataset.csv",
    "product_category_name_translation.csv",
]


def _chroma_dir() -> Path:
    """Pasta do índice vetorial (ChromaDB).

    O hnswlib, usado pelo ChromaDB, não grava seus arquivos binários em caminhos
    com caracteres não ASCII no Windows (ex.: "Área de Trabalho"). Nesse caso o
    índice vai para ~/.voc_rag/chroma (ou C:\\voc_rag_data se a pasta do usuário
    também tiver acentos). AppData é evitada de propósito: apps empacotados do
    Windows (MSIX) têm a AppData virtualizada, e o índice ficaria invisível para
    os demais programas. CHROMA_DIR tem precedência.
    """
    if os.getenv("CHROMA_DIR"):
        return Path(os.environ["CHROMA_DIR"])
    local = GOLD_DIR / "chroma"
    if str(local).isascii():
        return local
    fallback = Path.home() / ".voc_rag" / "chroma"
    if not str(fallback).isascii():
        fallback = Path(os.getenv("SystemDrive", "C:") + "\\") / "voc_rag_data" / "chroma"
    return fallback


CHROMA_DIR = _chroma_dir()
COLECAO = "olist_reviews"

# --- Modelos ----------------------------------------------------------------
LLM_MODEL = os.getenv("LLM_MODEL", "gpt-4o-mini")
LLM_TEMPERATURE = float(os.getenv("LLM_TEMPERATURE", "0"))
JUDGE_MODEL = os.getenv("JUDGE_MODEL", LLM_MODEL)
EMBEDDING_MODEL = os.getenv(
    "EMBEDDING_MODEL", "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"
)
EMBEDDING_DEVICE = os.getenv("EMBEDDING_DEVICE", "cpu")

# --- Recuperação ------------------------------------------------------------
CANDIDATOS = int(os.getenv("CANDIDATES", "40"))  # por buscador (denso e BM25), por consulta
TOP_K = int(os.getenv("TOP_K", "15"))            # candidatos enviados ao juiz de relevância
RRF_K = 60                                       # constante do Reciprocal Rank Fusion
# Similaridade de cosseno mínima do melhor resultado denso para haver evidência
MIN_SIMILARIDADE = float(os.getenv("MIN_DENSE_SIM", "0.35"))
# Nº mínimo de avaliações relevantes para gerar uma resposta
MIN_EVIDENCIAS = int(os.getenv("MIN_EVIDENCE", "3"))
# Seleção de documentos para o índice: >= N palavras OU menção a algum tema
MIN_PALAVRAS = 3


def ensure_dirs() -> None:
    for d in (BRONZE_DIR, SILVER_DIR, GOLD_DIR, REPORTS_DIR, IMAGES_DIR):
        d.mkdir(parents=True, exist_ok=True)


def _hf_offline_se_em_cache() -> None:
    """Com o modelo de embeddings já baixado, não consulta o huggingface.co.

    Precisa rodar antes de importar huggingface_hub, que lê HF_HUB_OFFLINE na importação.
    """
    hf_home = Path(os.getenv("HF_HOME") or Path.home() / ".cache" / "huggingface")
    repo = "models--" + EMBEDDING_MODEL.replace("/", "--")
    if (hf_home / "hub" / repo / "snapshots").exists():
        os.environ.setdefault("HF_HUB_OFFLINE", "1")


_hf_offline_se_em_cache()
