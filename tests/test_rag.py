"""Limpeza, temas, RRF, filtros e roteamento do grafo LangGraph (sem chamar a OpenAI)."""
from __future__ import annotations

import pandas as pd
import pytest

from src.rag.recuperacao import (
    RetrievedReview,
    filters_to_chroma,
    filters_to_mask,
    reciprocal_rank_fusion,
)
from src.rag.schemas import (
    GroundedAnswer,
    Insight,
    QueryAnalysis,
    RelevanceJudgement,
    SearchFilters,
)
from src.transform.texto import clean_for_embedding, mask_pii, tokens_for_bm25
from src.transform.temas import tag_themes


# ---------------------------------------------------------------- texto
def test_clean_for_embedding_keeps_accents_and_negation():
    assert clean_for_embedding("Não\r\nchegou   aindaaaaa!!!!") == "Não chegou aindaa!!"


def test_mask_pii():
    out = mask_pii("me liga 11 98765-4321 ou fulano@mail.com")
    assert "[telefone]" in out and "[email]" in out


def test_bm25_tokens_stem_and_keep_negation():
    toks = tokens_for_bm25("O produto não chegou e atrasou muito")
    assert "nao" in toks
    assert tokens_for_bm25("atrasou")[0] == tokens_for_bm25("atrasado")[0]


def test_themes():
    t = tag_themes("Produto veio quebrado e a entrega atrasou")
    assert t["tema_produto_defeituoso"] and t["tema_atraso_entrega"]
    assert tag_themes("Chegou antes do prazo")["tema_entrega_no_prazo"]
    assert not tag_themes("Muito bom")["tema_atraso_entrega"]


# ---------------------------------------------------------------- recuperação
def test_rrf_prefers_documents_in_both_rankings():
    scores = reciprocal_rank_fusion([["a", "b", "c"], ["c", "a", "d"]], k=60)
    assert max(scores, key=scores.get) == "a"
    assert scores["c"] > scores["b"]


def test_filters_translation():
    f = SearchFilters(score_max=2, product_categories=["bed_bath_table"], delivery_status="atrasado")
    where = filters_to_chroma(f)
    assert {"review_score": {"$lte": 2}} in where["$and"]
    df = pd.DataFrame(
        {
            "review_score": [1, 5, 2],
            "product_category": ["bed_bath_table"] * 3,
            "purchase_year": [2017] * 3,
            "delivery_status": ["atrasado", "atrasado", "no_prazo"],
            "customer_state": ["SP"] * 3,
        }
    )
    assert filters_to_mask(df, f).tolist() == [True, False, False]
    assert filters_to_chroma(SearchFilters()) is None


# ---------------------------------------------------------------- grafo (LLM e retriever falsos)
class FakeStructured:
    def __init__(self, response):
        self.response = response

    def invoke(self, _messages):
        return self.response


class FakeLLM:
    def __init__(self, in_scope=True, relevant=("R1", "R2", "R3"), cite=("R1", "R2")):
        self.responses = {
            QueryAnalysis: QueryAnalysis(
                in_scope=in_scope, scope_reason="teste", intent="problemas",
                search_queries=["não chegou"], filters=SearchFilters(score_max=2),
            ),
            RelevanceJudgement: RelevanceJudgement(relevant_ids=list(relevant), reasoning="ok"),
            GroundedAnswer: GroundedAnswer(
                answer="Clientes relatam atraso [R1] e produto não entregue [R2] [R99].",
                insights=[Insight(title="Atraso", description="x", evidence_ids=list(cite) + ["R99"])],
                confidence="alta", limitations="amostra",
            ),
        }

    def with_structured_output(self, schema):
        return FakeStructured(self.responses[schema])


class FakeRetriever:
    categories = ["bed_bath_table"]

    def __init__(self, n=5, sim=0.8, so_bm25=False):
        # so_bm25: o top-k final vem todo do BM25 (sem similaridade densa nos itens),
        # embora a busca densa tenha encontrado documentos parecidos.
        self.n, self.sim, self.so_bm25 = n, sim, so_bm25

    def search(self, queries, filters, top_k=None, stats=None):
        meta = {"review_score": 1, "product_category": "bed_bath_table", "delivery_status": "atrasado",
                "review_creation_date": "2017-01-01"}
        if stats is not None:
            stats["max_dense_similarity"] = self.sim
        return [
            RetrievedReview(review_id=f"id{i}", text=f"texto {i}", metadata=meta,
                            dense_similarity=None if self.so_bm25 else self.sim)
            for i in range(self.n)
        ]


@pytest.fixture(autouse=True)
def _stub_stats(monkeypatch):
    import src.rag.grafo as g

    monkeypatch.setattr(g, "segment_stats", lambda f: {"n_reviews": 0, "filters": "-"})
    monkeypatch.setattr(g, "format_stats", lambda s: "-")


def _assistant(**kw):
    from src.rag.grafo import VoCAssistant

    return VoCAssistant(llm=kw.get("llm", FakeLLM()), retriever=kw.get("retriever", FakeRetriever()))


def test_graph_answers_and_removes_invalid_citations():
    state = _assistant().ask("Quais os problemas?")
    assert state["status"] == "answered"
    assert state["citation_check"]["invalid_citations"] == ["R99"]
    assert state["answer"].insights[0].evidence_ids == ["R1", "R2"]
    assert state["answer"].confidence == "baixa"  # rebaixada por citação inválida


def test_graph_refuses_out_of_scope():
    state = _assistant(llm=FakeLLM(in_scope=False)).ask("Previsão do tempo?")
    assert state["status"] == "out_of_scope"
    assert "retrieved" not in state


def test_graph_insufficient_when_low_similarity():
    state = _assistant(retriever=FakeRetriever(sim=0.1)).ask("Algo sem evidência")
    assert state["status"] == "insufficient_evidence"


def test_graph_insufficient_when_few_relevant():
    state = _assistant(llm=FakeLLM(relevant=("R1",))).ask("Pergunta")
    assert state["status"] == "insufficient_evidence"


def test_sanitize_filters_drops_invented_score_and_noop_bounds():
    from src.rag.grafo import sanitize_filters

    f = SearchFilters(score_min=3, score_max=5, year_from=2016, year_to=2018)
    assert sanitize_filters(f, "Quais reclamações sobre qualidade?").is_empty()
    kept = sanitize_filters(SearchFilters(score_max=2), "O que dizem os clientes insatisfeitos?")
    assert kept.score_max == 2
    # "alta" dentro de "faltando" não pode liberar um filtro de nota
    assert sanitize_filters(SearchFilters(score_max=2), "Há itens faltando nos pedidos?").score_max is None
    assert sanitize_filters(SearchFilters(score_max=2), "Problemas nas avaliações com nota baixa").score_max == 2


def test_user_filters_override_llm_filters():
    state = _assistant().ask("Pergunta", SearchFilters(score_min=4, score_max=5))
    assert state["filters"].score_min == 4 and state["filters"].score_max == 5


def test_toda_categoria_da_base_tem_nome_em_portugues():
    from src import config
    from src.transform.categorias import CATEGORIAS_PT

    if not config.SILVER_AVALIACOES.exists():
        pytest.skip("camada silver ainda não gerada")
    codigos = set(pd.read_parquet(config.SILVER_AVALIACOES, columns=["product_category"])["product_category"])
    assert codigos <= set(CATEGORIAS_PT), codigos - set(CATEGORIAS_PT)


def test_topk_so_do_bm25_nao_vira_falta_de_evidencia():
    # Regressão: o limiar de similaridade olhava só o top-k final; quando ele vinha todo do
    # BM25 ("só tenho elogios"), a similaridade dava 0 e o pipeline recusava indevidamente.
    state = _assistant(retriever=FakeRetriever(sim=0.9, so_bm25=True)).ask("O que mais elogiam?")
    assert state["status"] == "answered"
    assert state["trace"]["max_dense_similarity"] == 0.9


def test_palavras_de_analista_nao_entram_na_consulta_bm25():
    assert tokens_for_bm25("os clientes elogiam a entrega", is_query=True) == tokens_for_bm25("entrega")
