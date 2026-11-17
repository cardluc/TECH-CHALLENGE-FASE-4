"""Pipeline reproduzível de ponta a ponta.

    python main.py                          # constrói a base de conhecimento (sem custo de API)
    python main.py --etapa silver           # apenas uma etapa
    python main.py --etapa avaliacao        # avaliação com LLM (usa a OpenAI)
    python main.py --perguntar "pergunta"   # pergunta pelo terminal (usa a OpenAI)
    streamlit run app.py                    # interface web

Etapas: bronze -> silver -> gold -> eda -> indice  (+ avaliacao, sob demanda)
"""
from __future__ import annotations

import argparse

from src import config
from src.utils import get_logger, silenciar_bibliotecas

logger = get_logger("main")
ETAPAS_BASE = ["bronze", "silver", "gold", "eda", "indice"]
ETAPAS = [*ETAPAS_BASE, "avaliacao"]


def main() -> None:
    parser = argparse.ArgumentParser(description="Voice of Customer Intelligence com RAG")
    parser.add_argument("--etapa", choices=ETAPAS, help="executa apenas uma etapa")
    parser.add_argument("--origem", help="(bronze) .zip do Kaggle ou pasta com os CSVs")
    parser.add_argument("--perguntar", metavar="PERGUNTA", help="faz uma pergunta ao assistente")
    parser.add_argument("--sem-juiz", action="store_true", help="(avaliacao) não usa LLM-as-a-judge")
    args = parser.parse_args()
    silenciar_bibliotecas()
    config.ensure_dirs()

    if args.perguntar:
        from src.rag.formatacao import render_state
        from src.rag.grafo import VoCAssistant

        print(render_state(VoCAssistant().ask(args.perguntar)))
        return

    etapas = [args.etapa] if args.etapa else ETAPAS_BASE
    silver = gold = None

    if "bronze" in etapas:
        logger.info("==== bronze: ingestão ====")
        from src.ingestion.ingestao_bronze import executar as bronze
        bronze(args.origem)

    if "silver" in etapas:
        logger.info("==== silver: limpeza e enriquecimento ====")
        from src.transform.bronze_to_silver import executar as para_silver
        silver = para_silver()

    if "gold" in etapas:
        logger.info("==== gold: documentos do RAG ====")
        from src.transform.silver_to_gold import executar as para_gold
        gold = para_gold(silver)

    if "eda" in etapas:
        logger.info("==== análise exploratória ====")
        from src.eda import executar as eda
        eda(silver, gold)

    if "indice" in etapas:
        logger.info("==== índice vetorial (embeddings + ChromaDB) ====")
        from src.rag.indexacao import executar as indexar
        indexar(gold)

    if "avaliacao" in etapas:
        logger.info("==== avaliação do pipeline RAG ====")
        from src.evaluation.avaliar import run_evaluation
        run_evaluation(use_judge=not args.sem_juiz)


if __name__ == "__main__":
    main()
