"""Estatísticas descritivas do recorte filtrado (suporte quantitativo às respostas).

Calculadas sobre a camada Silver, ou seja, sobre TODAS as avaliações do recorte,
e não apenas sobre as poucas recuperadas: é isso que permite falar de frequência.
"""
from __future__ import annotations

from functools import lru_cache

import pandas as pd

from src import config
from src.rag.recuperacao import filters_to_mask
from src.rag.schemas import SearchFilters
from src.transform.temas import THEME_LABELS, THEMES


@lru_cache(maxsize=1)
def _all_reviews() -> pd.DataFrame:
    return pd.read_parquet(config.SILVER_AVALIACOES)


def segment_stats(filters: SearchFilters | None) -> dict:
    df = _all_reviews()
    seg = df[filters_to_mask(df, filters)]
    with_text = seg[seg["has_text"]]
    n_text = len(with_text)
    neg = with_text[with_text["review_score"] <= 2]
    pos = with_text[with_text["review_score"] >= 4]

    def pct(frame: pd.DataFrame, col: str) -> float | None:
        return round(100 * frame[col].mean(), 1) if len(frame) else None

    themes = {
        t: {
            "label": THEME_LABELS[t],
            "count": int(with_text[f"tema_{t}"].sum()),
            "pct": pct(with_text, f"tema_{t}") or 0.0,
            "pct_neg": pct(neg, f"tema_{t}"),
            "pct_pos": pct(pos, f"tema_{t}"),
        }
        for t in THEMES
    }
    return {
        "filters": (filters or SearchFilters()).describe(),
        "n_reviews": int(len(seg)),
        "n_with_text": int(n_text),
        "avg_score": round(float(seg["review_score"].mean()), 2) if len(seg) else None,
        "score_dist": {int(k): int(v) for k, v in seg["review_score"].value_counts().sort_index().items()},
        "pct_late": round(100 * (seg["delivery_status"] == "atrasado").mean(), 1) if len(seg) else None,
        "n_neg": int(len(neg)),
        "n_pos": int(len(pos)),
        "themes": dict(sorted(themes.items(), key=lambda kv: -kv[1]["count"])),
    }


def format_stats(stats: dict) -> str:
    if not stats["n_reviews"]:
        return "Nenhuma avaliação na base atende aos filtros."
    lines = [
        f"Recorte: {stats['filters']}",
        f"Avaliações no recorte: {stats['n_reviews']} (com comentário: {stats['n_with_text']}; "
        f"comentários com nota 1-2: {stats['n_neg']}; com nota 4-5: {stats['n_pos']})",
        f"Nota média: {stats['avg_score']} | distribuição de notas: {stats['score_dist']}",
        f"Pedidos do recorte entregues após a data estimada: {stats['pct_late']}%",
        "Percentual de COMENTÁRIOS que mencionam cada tema (regras de palavras-chave, aproximado):",
    ]
    fmt = lambda v: "-" if v is None else f"{v}%"  # noqa: E731
    for t in stats["themes"].values():
        lines.append(
            f"- {t['label']}: {fmt(t['pct'])} de todos | {fmt(t['pct_neg'])} dos de nota 1-2 | "
            f"{fmt(t['pct_pos'])} dos de nota 4-5"
        )
    return "\n".join(lines)
