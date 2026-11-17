"""Bronze -> Silver -> Gold sobre a mini-base sintética."""
from __future__ import annotations

import json
import zipfile

import pandas as pd
import pytest

from src import config
from src.ingestion import ingestao_bronze
from src.transform import bronze_to_silver, silver_to_gold


def test_bronze_a_partir_do_zip(bronze_exemplo, tmp_path, monkeypatch):
    origem = tmp_path / "archive.zip"
    with zipfile.ZipFile(origem, "w") as z:
        for csv in bronze_exemplo.glob("*.csv"):
            z.write(csv, arcname=csv.name)
    destino = tmp_path / "bronze_novo"
    destino.mkdir()
    monkeypatch.setattr(config, "BRONZE_DIR", destino)
    monkeypatch.setattr(config, "MANIFESTO_BRONZE", destino / "_ingestao.json")

    manifesto = ingestao_bronze.executar(origem)
    assert set(manifesto["arquivos"]) == set(config.ARQUIVOS_OLIST)
    # 6 registros, embora o arquivo tenha 7 linhas físicas (comentário com quebra de linha)
    assert manifesto["arquivos"]["olist_order_reviews_dataset.csv"]["linhas"] == 6
    assert json.loads((destino / "_ingestao.json").read_text(encoding="utf-8"))["fonte"]


def test_bronze_sem_origem_explica_como_baixar(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "BRONZE_DIR", tmp_path)
    monkeypatch.delenv("OLIST_ORIGEM", raising=False)
    with pytest.raises(FileNotFoundError, match="kaggle"):
        ingestao_bronze.executar()


def test_silver_deduplica_trata_ausentes_e_mascara(bronze_exemplo):
    silver = bronze_to_silver.executar()
    assert silver["review_id"].is_unique and len(silver) == 5
    r = silver.set_index("review_id")
    assert "[email]" in r.at["r1", "text"]                    # PII mascarada
    assert r.at["r2", "text"] == "Recomendo. Chegou antes do prazo Recomendo"  # título + mensagem, sem \n
    assert not r.at["r3", "has_text"] and r.at["r3", "text"] == ""   # sem texto
    assert r.at["r1", "delivery_status"] == "nao_entregue"
    assert r.at["r4", "delivery_status"] == "atrasado"
    assert r.at["r2", "product_category"] == "bed_bath_table"  # item de maior valor
    assert r.at["r3", "product_category"] == "unknown"         # categoria ausente
    assert r.at["r4", "tema_produto_defeituoso"]


def test_regra_de_qualidade_interrompe_pipeline(bronze_exemplo):
    silver = bronze_to_silver.transformar(bronze_to_silver.integrar())
    quebrada = pd.concat([silver, silver.head(1)])  # review_id repetido
    with pytest.raises(ValueError, match="review_id único"):
        bronze_to_silver.validar(quebrada)


def test_gold_seleciona_documentos_informativos(bronze_exemplo):
    gold = silver_to_gold.executar(bronze_to_silver.executar())
    ids = set(gold["review_id"])
    assert ids == {"r1", "r2", "r4"}  # r3 sem texto; r5 "muito bom" curto e sem tema
    assert all(len(t) > 0 for t in gold["bm25_tokens"])
