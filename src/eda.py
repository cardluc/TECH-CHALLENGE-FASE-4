"""Análise exploratória das avaliações: gera reports/eda.md e os gráficos em images/.

Os números e gráficos daqui embasam as decisões de preparação da base
(seleção de documentos, ausência de chunking, uso de metadados).
"""
from __future__ import annotations

import pandas as pd

from src import config
from src.transform.temas import THEME_LABELS, THEMES
from src.utils import get_logger
from src.visualization import graficos

logger = get_logger("eda")


def _fmt(n: float) -> str:
    return f"{n:,.0f}".replace(",", ".")


def executar(silver: pd.DataFrame | None = None, gold: pd.DataFrame | None = None) -> str:
    config.ensure_dirs()
    silver = silver if silver is not None else pd.read_parquet(config.SILVER_AVALIACOES)
    gold = gold if gold is not None else pd.read_parquet(config.GOLD_DOCUMENTOS)
    img = config.IMAGES_DIR
    texto = silver[silver["has_text"]]

    graficos.distribuicao_notas(silver, img / "01_distribuicao_notas.png")
    graficos.taxa_comentario_por_nota(silver, img / "02_comentario_por_nota.png")
    graficos.tamanho_comentarios(silver, gold, img / "03_tamanho_comentarios.png")
    neg, pos = texto[texto["review_score"] <= 2], texto[texto["review_score"] >= 4]
    temas = pd.DataFrame(
        {
            "Notas 1-2": [100 * neg[f"tema_{t}"].mean() for t in THEMES],
            "Notas 4-5": [100 * pos[f"tema_{t}"].mean() for t in THEMES],
        },
        index=[THEME_LABELS[t] for t in THEMES],
    ).round(1)
    graficos.temas_por_sentimento(temas, img / "04_temas_por_sentimento.png")
    graficos.nota_por_entrega(silver, img / "05_nota_por_entrega.png")
    graficos.volume_mensal(silver, img / "06_volume_mensal.png")
    graficos.categorias(gold, img / "07_categorias.png")

    taxa_texto = silver.groupby("review_score")["has_text"].mean() * 100
    nota_entrega = silver.groupby("delivery_status")["review_score"].mean()
    curtos = (texto["n_words"] < config.MIN_PALAVRAS).sum()
    repetidos = texto["text"].str.lower().str.strip(" .!").value_counts()
    sem_tema = (texto["n_themes"] == 0).mean() * 100
    top_neg = temas["Notas 1-2"].sort_values(ascending=False)
    top_pos = temas["Notas 4-5"].sort_values(ascending=False)

    mensal = silver.groupby("purchase_yyyymm").agg(
        n=("review_id", "count"),
        nota=("review_score", "mean"),
        atraso=("delivery_status", lambda s: 100 * (s == "atrasado").mean()),
    )
    mensal = mensal[mensal["n"] >= 100]
    piores = mensal.sort_values("atraso", ascending=False).head(3).sort_index()
    piores_txt = ", ".join(
        f"{str(m)[4:]}/{str(m)[:4]}: {r.atraso:.1f}% de atrasos e nota {r.nota:.2f}" for m, r in piores.iterrows()
    )
    atraso_resto = mensal.drop(piores.index)["atraso"].mean()

    md = f"""# Análise Exploratória — Avaliações de clientes da Olist

Base: **{_fmt(len(silver))} avaliações únicas** (camada Silver), de pedidos feitos entre 2016 e 2018.
**{_fmt(len(texto))}** têm algum comentário ({100 * len(texto) / len(silver):.1f}%) e **{_fmt(len(gold))}**
foram selecionadas como documentos do RAG (camada Gold).

## 1. As notas

{100 * (silver['review_score'] >= 4).mean():.1f}% das avaliações têm nota 4 ou 5, e
{100 * (silver['review_score'] <= 2).mean():.1f}% têm nota 1 ou 2. Entre as avaliações **com comentário**, a
proporção de notas baixas sobe para {100 * (texto['review_score'] <= 2).mean():.1f}%: quem escreve é, com mais
frequência, quem teve problema.

![Distribuição das notas](../images/01_distribuicao_notas.png)

## 2. Valores ausentes: quem escreve comentário

Título e mensagem são opcionais. {100 - 100 * len(texto) / len(silver):.1f}% das avaliações não têm nenhum texto.
A ausência não é aleatória: {taxa_texto.loc[1]:.0f}% dos clientes que deram nota 1 escreveram algo, contra
{taxa_texto.loc[5]:.0f}% dos que deram nota 5.

**Decisão:** avaliações sem texto ficam fora do índice (não há o que recuperar), mas continuam na Silver e
entram nas estatísticas de nota e de entrega.

![Comentário por nota](../images/02_comentario_por_nota.png)

## 3. Tamanho dos comentários

Mediana de **{texto['n_words'].median():.0f} palavras** e máximo de {texto['n_words'].max()}. Um comentário cabe
inteiro em qualquer janela de contexto e costuma tratar de um ou dois assuntos.

**Decisão:** sem chunking. Cada avaliação é um documento.

Há {_fmt(curtos)} comentários com menos de {config.MIN_PALAVRAS} palavras e muitos textos repetidos. Os mais
frequentes são "{repetidos.index[0]}" ({repetidos.iloc[0]}x), "{repetidos.index[1]}" ({repetidos.iloc[1]}x) e
"{repetidos.index[2]}" ({repetidos.iloc[2]}x).

**Decisão:** entram no índice apenas comentários com >= {config.MIN_PALAVRAS} palavras **ou** que mencionem
algum tema de negócio ("faltou produto" fica; "muito bom" sai). Comentários idênticos são agrupados na
recuperação.

![Tamanho dos comentários](../images/03_tamanho_comentarios.png)

## 4. Temas: o que os clientes contam

Percentual dos comentários que mencionam cada tema, por regras de palavras-chave. Um comentário pode ter
mais de um tema, e {sem_tema:.0f}% não mencionam nenhum.

| Tema | Notas 1-2 (%) | Notas 4-5 (%) |
|---|---:|---:|
""" + "\n".join(f"| {t} | {r['Notas 1-2']} | {r['Notas 4-5']} |" for t, r in temas.sort_values(
        "Notas 1-2", ascending=False).iterrows()) + f"""

Entre insatisfeitos, os temas mais citados são **{top_neg.index[0].lower()}** ({top_neg.iloc[0]}%),
**{top_neg.index[1].lower()}** ({top_neg.iloc[1]}%) e **{top_neg.index[2].lower()}** ({top_neg.iloc[2]}%).
Entre satisfeitos, **{top_pos.index[0].lower()}** ({top_pos.iloc[0]}%) lidera com folga: a entrega é o tema
central da experiência, para o bem e para o mal.

**Decisão:** essas frequências, calculadas sobre o recorte inteiro, são enviadas ao LLM junto com as
evidências. O top-k recuperado é uma amostra e não mede frequência.

![Temas por sentimento](../images/04_temas_por_sentimento.png)

## 5. Entrega e satisfação

| Status da entrega | Nota média |
|---|---:|
| No prazo | {nota_entrega['no_prazo']:.2f} |
| Atrasado | {nota_entrega['atrasado']:.2f} |
| Não entregue | {nota_entrega['nao_entregue']:.2f} |

Pedidos atrasados têm nota média {nota_entrega['no_prazo'] - nota_entrega['atrasado']:.1f} pontos menor
que os entregues no prazo.

**Decisão:** o status de entrega (data real × estimada) vira metadado filtrável. Assim, "o que dizem os
clientes com entrega atrasada?" funciona mesmo quando o cliente não escreveu "atraso".

![Nota por entrega](../images/05_nota_por_entrega.png)

## 6. Período e categorias

As quedas de nota acompanham os atrasos. Os três meses com maior proporção de pedidos atrasados
({piores_txt}) estão entre os de pior nota média, enquanto a média dos demais meses fica em
{atraso_resto:.1f}% de atrasos. Isso reforça o período da compra como metadado útil (o pipeline filtra
por ano, como em "quais eram as principais reclamações em 2017?").

![Volume mensal](../images/06_volume_mensal.png)

![Categorias](../images/07_categorias.png)

As {gold['product_category'].nunique()} categorias têm volumes muito diferentes, e perguntas sobre uma
categoria específica precisam de filtro: sem ele, a busca semântica traz comentários de qualquer produto
(ver a ablação em `reports/avaliacao.md`).
"""
    (config.REPORTS_DIR / "eda.md").write_text(md, encoding="utf-8")
    logger.info("EDA: reports/eda.md + 7 gráficos em images/")
    return md
