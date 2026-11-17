"""Bronze -> Silver: avaliações limpas, deduplicadas, integradas e enriquecidas.

Etapas (justificadas no README, seção "Preparação da base"):

1. Junta avaliações com pedidos, clientes, itens e produtos para obter metadados.
2. Remove avaliações duplicadas (o mesmo ``review_id`` aparece em vários pedidos).
3. Trata valores ausentes: título e mensagem vazios viram string vazia e são
   concatenados. Avaliações sem texto continuam na Silver (contam nas
   estatísticas), mas não vão para a Gold.
4. Limpa o texto (leve, para embeddings), mascara dados pessoais e marca temas.
5. Calcula metadados de entrega (atraso real × estimado) e período da compra.
6. Valida a qualidade da camada; qualquer violação interrompe o pipeline.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from src import config
from src.transform.temas import THEMES, tag_themes
from src.transform.texto import clean_for_embedding
from src.utils import get_logger

logger = get_logger("silver")

SENTIMENTO = {1: "negativo", 2: "negativo", 3: "neutro", 4: "positivo", 5: "positivo"}
COLUNAS_TEMA = [f"tema_{t}" for t in THEMES]


def _ler(nome: str, **kw) -> pd.DataFrame:
    return pd.read_csv(config.BRONZE_DIR / nome, **kw)


def integrar() -> pd.DataFrame:
    reviews = _ler("olist_order_reviews_dataset.csv", parse_dates=["review_creation_date"])
    orders = _ler(
        "olist_orders_dataset.csv",
        parse_dates=[
            "order_purchase_timestamp",
            "order_delivered_customer_date",
            "order_estimated_delivery_date",
        ],
    )
    customers = _ler("olist_customers_dataset.csv")[["customer_id", "customer_state"]]
    items = _ler("olist_order_items_dataset.csv")
    products = _ler("olist_products_dataset.csv")[["product_id", "product_category_name"]]
    traducao = _ler("product_category_name_translation.csv")

    # Categoria do pedido: categoria do item de maior valor (a maioria dos pedidos tem 1 item).
    items = items.merge(products, on="product_id", how="left")
    items = items.sort_values(["order_id", "price"], ascending=[True, False])
    por_pedido = items.groupby("order_id").agg(
        product_category_name=("product_category_name", "first"),
        n_items=("order_item_id", "count"),
        order_value=("price", "sum"),
        freight_value=("freight_value", "sum"),
    )
    por_pedido = por_pedido.reset_index().merge(traducao, on="product_category_name", how="left")

    return (
        reviews.merge(orders, on="order_id", how="left")
        .merge(customers, on="customer_id", how="left")
        .merge(por_pedido, on="order_id", how="left")
    )


def transformar(df: pd.DataFrame) -> pd.DataFrame:
    n0 = len(df)
    df = df.drop_duplicates("review_id", keep="first").copy()
    logger.info("Duplicatas de review_id removidas: %d", n0 - len(df))

    # --- Valores ausentes no texto ------------------------------------------------
    titulo = df["review_comment_title"].fillna("").map(clean_for_embedding)
    mensagem = df["review_comment_message"].fillna("").map(clean_for_embedding)
    # O título costuma resumir o sentimento ("Recomendo", "Péssimo"): entra como prefixo.
    df["text"] = np.where(
        (titulo != "") & (mensagem != ""), titulo + ". " + mensagem, titulo + mensagem
    )
    df["text"] = df["text"].str.strip()
    df["has_text"] = df["text"].str.contains(r"[A-Za-zÀ-ÿ]", regex=True)
    df["n_words"] = df["text"].str.split().str.len().fillna(0).astype(int)

    # --- Valores ausentes nos metadados -------------------------------------------
    df["product_category"] = (
        df["product_category_name_english"].fillna(df["product_category_name"]).fillna("unknown")
    )
    df["product_category_pt"] = df["product_category_name"].fillna("desconhecida")
    df["customer_state"] = df["customer_state"].fillna("NA")
    df["order_status"] = df["order_status"].fillna("unknown")

    # --- Entrega e período --------------------------------------------------------
    entregue = df["order_delivered_customer_date"]
    df["delay_days"] = (entregue - df["order_estimated_delivery_date"]).dt.days
    df["delivery_days"] = (entregue - df["order_purchase_timestamp"]).dt.days
    df["delivery_status"] = np.select(
        [entregue.isna(), df["delay_days"] > 0], ["nao_entregue", "atrasado"], default="no_prazo"
    )
    compra = df["order_purchase_timestamp"].fillna(df["review_creation_date"])
    df["purchase_year"] = compra.dt.year.astype(int)
    df["purchase_yyyymm"] = (compra.dt.year * 100 + compra.dt.month).astype(int)
    df["sentiment"] = df["review_score"].map(SENTIMENTO)

    # --- Temas --------------------------------------------------------------------
    temas = pd.DataFrame([tag_themes(t) for t in df["text"]], index=df.index)
    df = pd.concat([df, temas], axis=1)
    df["n_themes"] = df[COLUNAS_TEMA].sum(axis=1)

    colunas = [
        "review_id", "order_id", "review_score", "sentiment", "text", "has_text", "n_words",
        "review_creation_date", "purchase_year", "purchase_yyyymm", "order_status",
        "delivery_status", "delay_days", "delivery_days", "product_category",
        "product_category_pt", "customer_state", "n_items", "order_value", "freight_value",
        "n_themes", *COLUNAS_TEMA,
    ]
    return df[colunas].reset_index(drop=True)


def validar(silver: pd.DataFrame) -> None:
    """Regras de qualidade da camada Silver (fail-fast)."""
    regras = {
        "review_id único": silver["review_id"].is_unique,
        "nota entre 1 e 5": silver["review_score"].between(1, 5).all(),
        "texto nunca nulo": silver["text"].notna().all(),
        "status de entrega válido": silver["delivery_status"].isin(
            ["atrasado", "no_prazo", "nao_entregue"]).all(),
        "ano da compra entre 2016 e 2018": silver["purchase_year"].between(2016, 2018).all(),
        "categoria preenchida": silver["product_category"].notna().all(),
        "nenhum e-mail sem máscara": not silver["text"].str.contains(r"[\w.+-]+@[\w-]+\.\w", regex=True).any(),
    }
    falhas = [nome for nome, ok in regras.items() if not ok]
    if falhas:
        raise ValueError(f"Qualidade da Silver violada: {falhas}")
    logger.info("Qualidade da Silver: %d regras OK", len(regras))


def executar() -> pd.DataFrame:
    config.ensure_dirs()
    silver = transformar(integrar())
    validar(silver)
    silver.to_parquet(config.SILVER_AVALIACOES, index=False)
    logger.info(
        "Silver: %d avaliações (%d com texto) -> %s",
        len(silver), int(silver["has_text"].sum()), config.SILVER_AVALIACOES.name,
    )
    return silver
