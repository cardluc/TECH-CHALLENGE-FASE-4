"""Recuperação híbrida: busca vetorial (Chroma/HNSW) + lexical (BM25) fundidas por RRF.

Por que híbrida?
* A busca **vetorial** captura paráfrases: "não chegou" ~ "nunca recebi o pedido".
* A busca **lexical** (BM25) é forte em termos literais como "lannister", "nota
  fiscal", "correios" ou "manual", que embeddings pequenos às vezes diluem.
* O **Reciprocal Rank Fusion** combina os rankings sem precisar normalizar
  escores de naturezas diferentes: ``RRF(d) = Σ 1 / (k + rank_i(d))``, com k = 60.

A busca roda para cada consulta reescrita (multi-query), e todos os rankings
entram na mesma fusão.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from functools import lru_cache

import numpy as np
import pandas as pd
from rank_bm25 import BM25Okapi

from src import config
from src.rag.indexacao import get_vectorstore
from src.rag.schemas import SearchFilters
from src.transform.texto import normalize, tokens_for_bm25
from src.transform.temas import THEMES


@dataclass
class RetrievedReview:
    review_id: str
    text: str
    metadata: dict
    rrf_score: float = 0.0
    dense_similarity: float | None = None  # melhor similaridade de cosseno entre as consultas
    bm25_score: float | None = None
    best_dense_rank: int | None = None
    best_bm25_rank: int | None = None
    duplicates: int = 1  # comentários idênticos agrupados
    matched_queries: set[str] = field(default_factory=set)

    @property
    def themes(self) -> list[str]:
        return [t for t in THEMES if self.metadata.get(f"tema_{t}")]


def reciprocal_rank_fusion(rankings: list[list[str]], k: int = 60) -> dict[str, float]:
    scores: dict[str, float] = {}
    for ranking in rankings:
        for rank, doc_id in enumerate(ranking, start=1):
            scores[doc_id] = scores.get(doc_id, 0.0) + 1.0 / (k + rank)
    return scores


def filters_to_chroma(f: SearchFilters | None) -> dict | None:
    if f is None or f.is_empty():
        return None
    clauses: list[dict] = []
    if f.score_min:
        clauses.append({"review_score": {"$gte": f.score_min}})
    if f.score_max:
        clauses.append({"review_score": {"$lte": f.score_max}})
    if f.product_categories:
        clauses.append({"product_category": {"$in": list(f.product_categories)}})
    if f.year_from:
        clauses.append({"purchase_year": {"$gte": f.year_from}})
    if f.year_to:
        clauses.append({"purchase_year": {"$lte": f.year_to}})
    if f.delivery_status:
        clauses.append({"delivery_status": {"$eq": f.delivery_status}})
    if f.customer_state:
        clauses.append({"customer_state": {"$eq": f.customer_state.upper()}})
    return clauses[0] if len(clauses) == 1 else {"$and": clauses}


def filters_to_mask(df: pd.DataFrame, f: SearchFilters | None) -> np.ndarray:
    mask = np.ones(len(df), dtype=bool)
    if f is None:
        return mask
    if f.score_min:
        mask &= df["review_score"].to_numpy() >= f.score_min
    if f.score_max:
        mask &= df["review_score"].to_numpy() <= f.score_max
    if f.product_categories:
        mask &= df["product_category"].isin(f.product_categories).to_numpy()
    if f.year_from:
        mask &= df["purchase_year"].to_numpy() >= f.year_from
    if f.year_to:
        mask &= df["purchase_year"].to_numpy() <= f.year_to
    if f.delivery_status:
        mask &= (df["delivery_status"] == f.delivery_status).to_numpy()
    if f.customer_state:
        mask &= (df["customer_state"] == f.customer_state.upper()).to_numpy()
    return mask


class HybridRetriever:
    def __init__(self, docs: pd.DataFrame | None = None):
        self.docs = docs if docs is not None else pd.read_parquet(config.GOLD_DOCUMENTOS)
        self.docs = self.docs.reset_index(drop=True)
        self.id_to_pos = {rid: i for i, rid in enumerate(self.docs["review_id"])}
        self.bm25 = BM25Okapi([list(t) for t in self.docs["bm25_tokens"]])
        self.store = get_vectorstore()
        self.categories = sorted(self.docs["product_category"].unique().tolist())

    # ------------------------------------------------------------------ buscas simples
    def dense_search(self, query: str, filters: SearchFilters | None, n: int) -> list[tuple[str, float]]:
        results = self.store.similarity_search_with_score(
            query, k=n, filter=filters_to_chroma(filters)
        )
        # Chroma devolve distância de cosseno; similaridade = 1 - distância
        return [(doc.metadata["review_id"], 1.0 - float(dist)) for doc, dist in results]

    def lexical_search(self, query: str, filters: SearchFilters | None, n: int) -> list[tuple[str, float]]:
        tokens = tokens_for_bm25(query, is_query=True)
        if not tokens:
            return []
        scores = self.bm25.get_scores(tokens)
        scores = np.where(filters_to_mask(self.docs, filters), scores, -np.inf)
        top = np.argsort(-scores)[:n]
        return [
            (self.docs.at[i, "review_id"], float(scores[i])) for i in top if scores[i] > 0
        ]

    # ------------------------------------------------------------------ busca híbrida
    def search(
        self,
        queries: list[str],
        filters: SearchFilters | None = None,
        top_k: int | None = None,
        candidates: int | None = None,
        mode: str = "hybrid",  # "hybrid" | "dense" | "bm25" (usado na ablação)
        stats: dict | None = None,
    ) -> list[RetrievedReview]:
        """Busca híbrida. Se ``stats`` for um dicionário, recebe ``max_dense_similarity``:
        a maior similaridade de cosseno de TODA a busca densa, e não só dos itens que
        sobreviveram à fusão (o top-k final pode vir todo do BM25)."""
        top_k = top_k or config.TOP_K
        n = candidates or config.CANDIDATOS
        queries = list(dict.fromkeys(q.strip() for q in queries if q and q.strip()))

        rankings: list[list[str]] = []
        dense_best: dict[str, tuple[float, int]] = {}
        bm25_best: dict[str, tuple[float, int]] = {}
        matched: dict[str, set[str]] = {}

        for q in queries:
            dense = self.dense_search(q, filters, n) if mode != "bm25" else []
            lexical = self.lexical_search(q, filters, n) if mode != "dense" else []
            rankings += [r for r in ([d for d, _ in dense], [d for d, _ in lexical]) if r]
            for rank, (rid, sim) in enumerate(dense, start=1):
                if rid not in dense_best or sim > dense_best[rid][0]:
                    dense_best[rid] = (sim, rank)
                matched.setdefault(rid, set()).add(q)
            for rank, (rid, sc) in enumerate(lexical, start=1):
                if rid not in bm25_best or sc > bm25_best[rid][0]:
                    bm25_best[rid] = (sc, rank)
                matched.setdefault(rid, set()).add(q)

        if stats is not None:
            stats["max_dense_similarity"] = max((s for s, _ in dense_best.values()), default=0.0)

        fused = reciprocal_rank_fusion(rankings, k=config.RRF_K)
        ordered = sorted(fused.items(), key=lambda kv: kv[1], reverse=True)

        # Agrupa comentários idênticos para não desperdiçar o contexto com repetições
        results: list[RetrievedReview] = []
        seen_text: dict[str, RetrievedReview] = {}
        for rid, score in ordered:
            row = self.docs.iloc[self.id_to_pos[rid]]
            key = normalize(row["text"]).strip(" .!")
            if key in seen_text:
                seen_text[key].duplicates += 1
                continue
            item = RetrievedReview(
                review_id=rid,
                text=row["text"],
                metadata=_row_metadata(row),
                rrf_score=score,
                dense_similarity=dense_best.get(rid, (None,))[0],
                bm25_score=bm25_best.get(rid, (None,))[0],
                best_dense_rank=dense_best.get(rid, (None, None))[1],
                best_bm25_rank=bm25_best.get(rid, (None, None))[1],
                matched_queries=matched.get(rid, set()),
            )
            seen_text[key] = item
            results.append(item)
            if len(results) >= top_k:
                break
        return results


def _row_metadata(row: pd.Series) -> dict:
    keys = [
        "order_id", "review_score", "sentiment", "purchase_yyyymm", "delivery_status",
        "delay_days", "product_category", "product_category_pt", "customer_state",
        "review_creation_date", *[f"tema_{t}" for t in THEMES],
    ]
    meta = {}
    for k in keys:
        v = row[k]
        if isinstance(v, pd.Timestamp):
            v = v.strftime("%Y-%m-%d")
        elif hasattr(v, "item"):
            v = v.item()
        if isinstance(v, float) and np.isnan(v):
            v = None
        meta[k] = v
    return meta


@lru_cache(maxsize=1)
def get_retriever() -> HybridRetriever:
    return HybridRetriever()
