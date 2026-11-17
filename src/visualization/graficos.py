"""Gráficos do projeto (salvos em images/)."""
from __future__ import annotations

from pathlib import Path

import matplotlib

matplotlib.use("Agg")  # sem janela: só gera arquivos
import matplotlib.pyplot as plt  # noqa: E402
import pandas as pd  # noqa: E402

from src.transform.categorias import rotulo_categoria  # noqa: E402

AZUL = "#2a6f97"
VERMELHO = "#c0504d"
VERDE = "#4a9a5b"
CINZA = "#8c8c8c"

plt.rcParams.update({
    "figure.dpi": 110,
    "axes.spines.top": False,
    "axes.spines.right": False,
    "axes.titleweight": "bold",
    "axes.titlesize": 12,
})


def _salvar(fig, caminho: Path) -> Path:
    caminho.parent.mkdir(parents=True, exist_ok=True)
    fig.tight_layout()
    fig.savefig(caminho, bbox_inches="tight")
    plt.close(fig)
    return caminho


def distribuicao_notas(silver: pd.DataFrame, caminho: Path) -> Path:
    todas = silver["review_score"].value_counts(normalize=True).sort_index() * 100
    com_texto = silver.loc[silver["has_text"], "review_score"].value_counts(normalize=True).sort_index() * 100
    fig, ax = plt.subplots(figsize=(7, 3.8))
    x = range(1, 6)
    ax.bar([i - 0.2 for i in x], todas.values, width=0.4, color=CINZA, label="Todas as avaliações")
    ax.bar([i + 0.2 for i in x], com_texto.values, width=0.4, color=AZUL, label="Com comentário")
    ax.set_xticks(list(x), [f"{i}★" for i in x])
    ax.set_ylabel("% das avaliações")
    ax.set_title("Distribuição das notas")
    ax.legend(frameon=False)
    return _salvar(fig, caminho)


def taxa_comentario_por_nota(silver: pd.DataFrame, caminho: Path) -> Path:
    taxa = silver.groupby("review_score")["has_text"].mean() * 100
    fig, ax = plt.subplots(figsize=(7, 3.5))
    barras = ax.bar([f"{i}★" for i in taxa.index], taxa.values, color=AZUL)
    ax.bar_label(barras, fmt="%.0f%%", padding=2)
    ax.set_ylabel("% com comentário")
    ax.set_title("Clientes insatisfeitos escrevem muito mais")
    ax.set_ylim(0, 100)
    return _salvar(fig, caminho)


def tamanho_comentarios(silver: pd.DataFrame, gold: pd.DataFrame, caminho: Path) -> Path:
    palavras = silver.loc[silver["has_text"], "n_words"]
    fig, ax = plt.subplots(figsize=(7, 3.5))
    ax.hist(palavras, bins=range(0, 52, 2), color=AZUL, alpha=0.85)
    ax.axvline(palavras.median(), color=VERMELHO, ls="--", label=f"mediana = {palavras.median():.0f} palavras")
    ax.set_xlabel("palavras por comentário")
    ax.set_ylabel("comentários")
    ax.set_title(f"Comentários curtos: 1 avaliação = 1 documento ({len(gold):,} indexados)".replace(",", "."))
    ax.legend(frameon=False)
    return _salvar(fig, caminho)


def temas_por_sentimento(temas: pd.DataFrame, caminho: Path) -> Path:
    """temas: índice = rótulo do tema; colunas 'Notas 1-2' e 'Notas 4-5' (em %)."""
    temas = temas.sort_values("Notas 1-2")
    fig, ax = plt.subplots(figsize=(8, 5))
    y = range(len(temas))
    ax.barh([i + 0.2 for i in y], temas["Notas 1-2"], height=0.4, color=VERMELHO, label="Notas 1-2")
    ax.barh([i - 0.2 for i in y], temas["Notas 4-5"], height=0.4, color=VERDE, label="Notas 4-5")
    ax.set_yticks(list(y), temas.index)
    ax.set_xlabel("% dos comentários que mencionam o tema")
    ax.set_title("Temas citados por clientes insatisfeitos x satisfeitos")
    ax.legend(frameon=False, loc="center right")
    return _salvar(fig, caminho)


def nota_por_entrega(silver: pd.DataFrame, caminho: Path) -> Path:
    rotulos = {"no_prazo": "No prazo", "atrasado": "Atrasado", "nao_entregue": "Não entregue"}
    media = silver.groupby("delivery_status")["review_score"].mean().reindex(list(rotulos))
    fig, ax = plt.subplots(figsize=(7, 3.5))
    barras = ax.bar([rotulos[k] for k in media.index], media.values, color=[VERDE, VERMELHO, CINZA])
    ax.bar_label(barras, fmt="%.2f", padding=2)
    ax.set_ylim(0, 5)
    ax.set_ylabel("nota média")
    ax.set_title("Atraso derruba a nota: o metadado de entrega importa")
    return _salvar(fig, caminho)


def volume_mensal(silver: pd.DataFrame, caminho: Path) -> Path:
    mensal = silver.groupby("purchase_yyyymm").agg(n=("review_id", "count"), nota=("review_score", "mean"))
    mensal = mensal[mensal["n"] >= 100]  # meses com volume mínimo
    rotulos = [f"{str(m)[4:]}/{str(m)[2:4]}" for m in mensal.index]
    fig, ax = plt.subplots(figsize=(9, 3.8))
    ax.bar(rotulos, mensal["n"], color=AZUL, alpha=0.8)
    ax.set_ylabel("avaliações")
    ax.tick_params(axis="x", rotation=60, labelsize=8)
    ax2 = ax.twinx()
    ax2.plot(rotulos, mensal["nota"], color=VERMELHO, marker="o", ms=3)
    ax2.set_ylabel("nota média", color=VERMELHO)
    ax2.set_ylim(1, 5)
    ax2.spines["top"].set_visible(False)
    ax.set_title("Volume de avaliações e nota média por mês da compra")
    return _salvar(fig, caminho)


def categorias(gold: pd.DataFrame, caminho: Path, top: int = 12) -> Path:
    agg = gold.groupby("product_category").agg(n=("review_id", "count"), nota=("review_score", "mean"))
    agg = agg.sort_values("n", ascending=False).head(top).iloc[::-1]
    agg.index = [rotulo_categoria(c) for c in agg.index]
    fig, ax = plt.subplots(figsize=(8, 4.8))
    barras = ax.barh(agg.index, agg["n"], color=AZUL)
    ax.bar_label(barras, labels=[f"nota {v:.2f}" for v in agg["nota"]], padding=3, fontsize=8)
    ax.set_xlabel("documentos no índice")
    ax.set_title(f"As {top} categorias com mais comentários indexados")
    return _salvar(fig, caminho)


def grafico_ablacao(medias: dict[str, float], caminho: Path) -> Path:
    fig, ax = plt.subplots(figsize=(8, 3.8))
    nomes, valores = list(medias), list(medias.values())
    cores = [CINZA] * (len(nomes) - 1) + [AZUL]
    barras = ax.barh(nomes[::-1], valores[::-1], color=cores[::-1])
    ax.bar_label(barras, fmt="%.2f", padding=3)
    ax.set_xlim(0, 1)
    ax.set_xlabel("precisão@10 média")
    ax.set_title("Ablação da recuperação: o que cada componente acrescenta")
    return _salvar(fig, caminho)
