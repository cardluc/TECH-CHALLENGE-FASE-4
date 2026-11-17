"""Mini-base sintética no mesmo formato dos CSVs da Olist (camada bronze)."""
from __future__ import annotations

import pandas as pd
import pytest

from src import config


@pytest.fixture
def bronze_exemplo(tmp_path, monkeypatch):
    """Cria os 6 CSVs com casos de borda e aponta as camadas para tmp_path."""
    bronze, silver, gold = tmp_path / "bronze", tmp_path / "silver", tmp_path / "gold"
    for d in (bronze, silver, gold):
        d.mkdir()
    monkeypatch.setattr(config, "BRONZE_DIR", bronze)
    monkeypatch.setattr(config, "SILVER_DIR", silver)
    monkeypatch.setattr(config, "GOLD_DIR", gold)
    monkeypatch.setattr(config, "SILVER_AVALIACOES", silver / "avaliacoes.parquet")
    monkeypatch.setattr(config, "GOLD_DOCUMENTOS", gold / "documentos.parquet")
    monkeypatch.setattr(config, "MANIFESTO_BRONZE", bronze / "_ingestao.json")
    monkeypatch.setattr(config, "REPORTS_DIR", tmp_path / "reports")
    monkeypatch.setattr(config, "IMAGES_DIR", tmp_path / "images")

    pd.DataFrame({
        "review_id": ["r1", "r2", "r3", "r4", "r4", "r5"],
        "order_id": ["o1", "o2", "o3", "o4", "o5", "o6"],
        "review_score": [1, 5, 4, 2, 2, 5],
        "review_comment_title": [None, "Recomendo", None, None, None, None],
        "review_comment_message": [
            "Produto não chegou até hoje, contato fulano@mail.com",  # e-mail a mascarar
            "Chegou antes do prazo\nRecomendo",                     # quebra de linha no comentário
            None,                                                  # sem texto
            "veio quebrado",                                       # 2 palavras, mas com tema
            "veio quebrado",                                       # review_id duplicado
            "muito bom",                                           # curto e sem tema
        ],
        "review_creation_date": ["2017-05-10"] * 6,
        "review_answer_timestamp": ["2017-05-11"] * 6,
    }).to_csv(bronze / "olist_order_reviews_dataset.csv", index=False)
    pd.DataFrame({
        "order_id": ["o1", "o2", "o3", "o4", "o5", "o6"],
        "customer_id": ["c1", "c2", "c3", "c4", "c5", "c6"],
        "order_status": ["shipped", "delivered", "delivered", "delivered", "delivered", "delivered"],
        "order_purchase_timestamp": ["2017-04-01"] * 6,
        "order_approved_at": ["2017-04-01"] * 6,
        "order_delivered_carrier_date": ["2017-04-02"] * 6,
        "order_delivered_customer_date": [None, "2017-04-05", "2017-04-08", "2017-04-30", "2017-04-30", "2017-04-06"],
        "order_estimated_delivery_date": ["2017-04-20"] * 6,
    }).to_csv(bronze / "olist_orders_dataset.csv", index=False)
    pd.DataFrame({
        "customer_id": ["c1", "c2", "c3", "c4", "c5", "c6"],
        "customer_unique_id": ["u1", "u2", "u3", "u4", "u5", "u6"],
        "customer_zip_code_prefix": [1000] * 6,
        "customer_city": ["sao paulo"] * 6,
        "customer_state": ["SP", "RJ", "MG", "SP", "SP", "BA"],
    }).to_csv(bronze / "olist_customers_dataset.csv", index=False)
    pd.DataFrame({
        "order_id": ["o1", "o2", "o2", "o3", "o4", "o5", "o6"],
        "order_item_id": [1, 1, 2, 1, 1, 1, 1],
        "product_id": ["p1", "p2", "p1", "p2", "p1", "p1", "p2"],
        "seller_id": ["s1"] * 7,
        "shipping_limit_date": ["2017-04-03"] * 7,
        "price": [50.0, 30.0, 80.0, 30.0, 50.0, 50.0, 30.0],
        "freight_value": [10.0] * 7,
    }).to_csv(bronze / "olist_order_items_dataset.csv", index=False)
    pd.DataFrame({
        "product_id": ["p1", "p2"],
        "product_category_name": ["cama_mesa_banho", None],  # categoria ausente
    }).to_csv(bronze / "olist_products_dataset.csv", index=False)
    pd.DataFrame({
        "product_category_name": ["cama_mesa_banho"],
        "product_category_name_english": ["bed_bath_table"],
    }).to_csv(bronze / "product_category_name_translation.csv", index=False)
    return bronze
