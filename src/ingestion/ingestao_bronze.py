"""Ingestão da camada Bronze: CSVs originais da Olist, sem nenhuma transformação.

A origem pode ser o .zip baixado do Kaggle ou a pasta com os CSVs extraídos:

    python main.py --etapa bronze --origem "caminho/para/archive.zip"

Sem ``--origem``, usa a variável de ambiente ``OLIST_ORIGEM``. Se a camada bronze
já estiver completa, a etapa apenas valida os arquivos.

Além dos CSVs, grava ``_ingestao.json`` com metadados técnicos (origem, data da
ingestão, tamanho e número de linhas de cada arquivo), garantindo rastreabilidade.
"""
from __future__ import annotations

import csv
import json
import os
import shutil
import zipfile
from datetime import datetime
from pathlib import Path

from src import config
from src.utils import get_logger

logger = get_logger("bronze")


def _contar_linhas(caminho: Path) -> int:
    """Número de registros (não de linhas físicas: há comentários com quebra de linha)."""
    with caminho.open(encoding="utf-8", newline="") as f:
        return sum(1 for _ in csv.reader(f)) - 1  # desconta o cabeçalho


def _faltantes() -> list[str]:
    return [a for a in config.ARQUIVOS_OLIST if not (config.BRONZE_DIR / a).exists()]


def _copiar_da_origem(origem: Path) -> None:
    if origem.is_file() and origem.suffix.lower() == ".zip":
        with zipfile.ZipFile(origem) as z:
            nomes = {Path(n).name: n for n in z.namelist() if n.lower().endswith(".csv")}
            for arquivo in config.ARQUIVOS_OLIST:
                if arquivo not in nomes:
                    raise FileNotFoundError(f"{arquivo} não está em {origem}")
                with z.open(nomes[arquivo]) as src, (config.BRONZE_DIR / arquivo).open("wb") as dst:
                    shutil.copyfileobj(src, dst)
    elif origem.is_dir():
        for arquivo in config.ARQUIVOS_OLIST:
            encontrados = list(origem.rglob(arquivo))
            if not encontrados:
                raise FileNotFoundError(f"{arquivo} não encontrado em {origem}")
            shutil.copy2(encontrados[0], config.BRONZE_DIR / arquivo)
    else:
        raise FileNotFoundError(f"Origem inválida: {origem} (use o .zip do Kaggle ou a pasta dos CSVs)")


def executar(origem: str | Path | None = None) -> dict:
    config.ensure_dirs()
    origem = origem or os.getenv("OLIST_ORIGEM")
    faltantes = _faltantes()

    if faltantes and not origem:
        raise FileNotFoundError(
            "Camada bronze incompleta. Baixe a base em "
            "https://www.kaggle.com/datasets/olistbr/brazilian-ecommerce e rode "
            '`python main.py --etapa bronze --origem "<arquivo .zip ou pasta>"`. '
            f"Faltando: {faltantes}"
        )
    if origem and (faltantes or not config.MANIFESTO_BRONZE.exists()):
        logger.info("Copiando CSVs de %s", origem)
        _copiar_da_origem(Path(origem))

    if config.MANIFESTO_BRONZE.exists() and not (origem and faltantes):
        manifesto = json.loads(config.MANIFESTO_BRONZE.read_text(encoding="utf-8"))
        logger.info("Camada bronze já completa (ingestão de %s)", manifesto["ingerido_em"])
        return manifesto

    manifesto = {
        "fonte": "Kaggle - olistbr/brazilian-ecommerce",
        "origem_local": str(origem) if origem else None,
        "ingerido_em": datetime.now().isoformat(timespec="seconds"),
        "arquivos": {
            a: {
                "bytes": (config.BRONZE_DIR / a).stat().st_size,
                "linhas": _contar_linhas(config.BRONZE_DIR / a),
            }
            for a in config.ARQUIVOS_OLIST
        },
    }
    config.MANIFESTO_BRONZE.write_text(json.dumps(manifesto, indent=2, ensure_ascii=False), encoding="utf-8")
    logger.info("Bronze: %d arquivos ingeridos", len(manifesto["arquivos"]))
    return manifesto
