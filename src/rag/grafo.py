"""Pipeline RAG orquestrado com LangGraph (StateGraph).

Fluxo:

    analisar_pergunta ──(fora do escopo)──► recusar_fora_escopo ──► FIM
          │
          ▼
    recuperar ──► avaliar_evidencias ──(insuficiente)──► responder_sem_evidencia ──► FIM
                          │
                          ▼
                       gerar ──► verificar_citacoes ──► FIM

Cada nó grava no estado o que fez (consultas, filtros, escores, tempos) para que
a interface mostre o "raciocínio" do pipeline e cada insight seja rastreável.
"""
from __future__ import annotations

import re
import time
from typing import Any, Literal, Optional, TypedDict

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import HumanMessage, SystemMessage
from langgraph.graph import END, START, StateGraph

from src import config
from src.rag import prompts
from src.rag.estatisticas import format_stats, segment_stats
from src.rag.recuperacao import HybridRetriever, RetrievedReview, get_retriever
from src.rag.schemas import GroundedAnswer, QueryAnalysis, RelevanceJudgement, SearchFilters
from src.transform.texto import normalize

Status = Literal["answered", "out_of_scope", "insufficient_evidence"]


class RAGState(TypedDict, total=False):
    question: str
    user_filters: Optional[SearchFilters]  # filtros escolhidos manualmente na interface
    analysis: QueryAnalysis
    filters: SearchFilters
    queries: list[str]
    retrieved: list[RetrievedReview]
    evidence: dict[str, RetrievedReview]  # short_id -> avaliação relevante
    stats: dict
    answer: GroundedAnswer
    status: Status
    message: str
    citation_check: dict
    trace: dict[str, Any]


def get_llm() -> BaseChatModel:
    from langchain_openai import ChatOpenAI

    return ChatOpenAI(model=config.LLM_MODEL, temperature=config.LLM_TEMPERATURE, timeout=60)


# Termos (texto normalizado, sem acento) que autorizam um filtro de nota.
# Limites de palavra evitam falsos positivos como "alta" dentro de "faltando".
_SATISFACTION_TERMS = re.compile(
    r"\b(?:in)?satisf\w*|\bnotas?\b|\bestrelas?\b|\bnegativ\w*|\bpositiv\w*|\bneutr\w*"
    r"|\bruins?\b|\bbaixas?\b|\baltas?\b|\bdetrat\w*|\bpromot\w*|\bfelizes\b|\binfeliz\w*"
    r"|\b(?:bem|mal) avaliad\w*"
)


def sanitize_filters(f: SearchFilters, question: str) -> SearchFilters:
    """Trava determinística contra filtros inventados pelo LLM.

    * Filtro de nota só vale se a pergunta falar de satisfação/nota (ex.: "insatisfeitos").
      "Reclamações" ou "elogios" aparecem em todas as notas.
    * Limites que não restringem nada (nota 1-5, anos 2016-2018) são removidos.
    """
    data = f.model_dump()
    if not _SATISFACTION_TERMS.search(normalize(question)):
        data["score_min"] = data["score_max"] = None
    if data["score_min"] == 1:
        data["score_min"] = None
    if data["score_max"] == 5:
        data["score_max"] = None
    if data["year_from"] is not None and data["year_from"] <= 2016:
        data["year_from"] = None
    if data["year_to"] is not None and data["year_to"] >= 2018:
        data["year_to"] = None
    return SearchFilters(**data)


def _merge_filters(llm_filters: SearchFilters, user_filters: SearchFilters | None) -> SearchFilters:
    """Filtros escolhidos pelo usuário têm prioridade sobre os inferidos pelo LLM."""
    if user_filters is None:
        return llm_filters
    merged = llm_filters.model_dump()
    for key, value in user_filters.model_dump().items():
        if value not in (None, [], ""):
            merged[key] = value
    return SearchFilters(**merged)


def _timed(state: RAGState, step: str, start: float) -> dict:
    trace = dict(state.get("trace") or {})
    trace.setdefault("timings_s", {})[step] = round(time.perf_counter() - start, 3)
    return trace


def build_graph(llm: BaseChatModel | None = None, retriever: HybridRetriever | None = None):
    llm = llm or get_llm()
    retriever = retriever or get_retriever()
    valid_categories = set(retriever.categories)

    analyzer = llm.with_structured_output(QueryAnalysis)
    grader = llm.with_structured_output(RelevanceJudgement)
    generator = llm.with_structured_output(GroundedAnswer)

    # ------------------------------------------------------------------ nós
    def analyze(state: RAGState) -> dict:
        t0 = time.perf_counter()
        question = state["question"].strip()
        system = prompts.ANALYZE_SYSTEM.format(categories=", ".join(retriever.categories))
        analysis: QueryAnalysis = analyzer.invoke(
            [SystemMessage(system), HumanMessage(f"Pergunta: {question}")]
        )
        # Descarta categorias inventadas pelo LLM (só mantém as que existem na base)
        analysis.filters.product_categories = [
            c for c in analysis.filters.product_categories if c in valid_categories
        ]
        analysis.filters = sanitize_filters(analysis.filters, question)
        filters = _merge_filters(analysis.filters, state.get("user_filters"))
        # A pergunta original também é usada como consulta (garante que nada se perca)
        queries = [question, *analysis.search_queries][:5]
        return {
            "analysis": analysis,
            "filters": filters,
            "queries": queries,
            "trace": _timed(state, "analisar_pergunta", t0),
        }

    def refuse_out_of_scope(state: RAGState) -> dict:
        reason = state["analysis"].scope_reason
        return {
            "status": "out_of_scope",
            "message": (
                "Não posso responder a essa pergunta com esta base de conhecimento. Ela contém "
                "apenas avaliações de clientes do e-commerce (notas e comentários de 2016 a 2018). "
                f"Motivo: {reason}"
            ),
        }

    def retrieve(state: RAGState) -> dict:
        t0 = time.perf_counter()
        busca: dict = {}
        results = retriever.search(state["queries"], state["filters"], top_k=config.TOP_K, stats=busca)
        stats = segment_stats(state["filters"])
        trace = _timed(state, "recuperar", t0)
        # Maior similaridade de toda a busca densa (não só do top-k após a fusão)
        trace["max_dense_similarity"] = busca.get("max_dense_similarity", 0.0)
        trace["n_retrieved"] = len(results)
        return {"retrieved": results, "stats": stats, "trace": trace}

    def grade(state: RAGState) -> dict:
        t0 = time.perf_counter()
        results = state["retrieved"]
        trace = dict(state["trace"])
        # Guarda 1: nada recuperado, ou similaridade semântica muito baixa -> sem evidência
        if not results or trace["max_dense_similarity"] < config.MIN_SIMILARIDADE:
            trace["grading"] = "pulado: similaridade abaixo do limiar ou nenhum resultado"
            return {"evidence": {}, "trace": _timed({"trace": trace}, "avaliar_evidencias", t0)}

        short = {f"R{i}": r for i, r in enumerate(results, start=1)}
        listing = "\n".join(f"[{sid}] (nota {r.metadata['review_score']}) {r.text}" for sid, r in short.items())
        judgement: RelevanceJudgement = grader.invoke(
            [
                SystemMessage(prompts.GRADE_SYSTEM),
                HumanMessage(f"Pergunta: {state['question']}\n\nAvaliações:\n{listing}"),
            ]
        )
        relevant = [sid for sid in judgement.relevant_ids if sid in short]
        # Renumera as evidências relevantes em ordem (R1..Rn) para o prompt de geração
        evidence = {f"R{i}": short[sid] for i, sid in enumerate(relevant, start=1)}
        trace["grading"] = judgement.reasoning
        trace["n_relevant"] = len(evidence)
        return {"evidence": evidence, "trace": _timed({"trace": trace}, "avaliar_evidencias", t0)}

    def answer_insufficient(state: RAGState) -> dict:
        n = len(state.get("evidence") or {})
        return {
            "status": "insufficient_evidence",
            "message": (
                "Não encontrei evidências suficientes nas avaliações para responder com segurança "
                f"({n} avaliação(ões) relevante(s); mínimo exigido: {config.MIN_EVIDENCIAS}). "
                f"Filtros aplicados: {state['filters'].describe()}. Tente reformular a pergunta "
                "ou ampliar os filtros."
            ),
        }

    def generate(state: RAGState) -> dict:
        t0 = time.perf_counter()
        reviews = "\n".join(
            prompts.format_review_for_prompt(sid, r) for sid, r in state["evidence"].items()
        )
        user = prompts.GENERATE_USER.format(
            question=state["question"], stats=format_stats(state["stats"]), reviews=reviews
        )
        answer: GroundedAnswer = generator.invoke(
            [SystemMessage(prompts.GENERATE_SYSTEM), HumanMessage(user)]
        )
        return {"answer": answer, "trace": _timed(state, "gerar", t0)}

    def verify(state: RAGState) -> dict:
        """Confere se as citações apontam para avaliações realmente fornecidas ao modelo."""
        answer = state["answer"]
        valid = set(state["evidence"])
        cited_in_text = set(re.findall(r"R\d+", answer.answer))
        invalid: set[str] = {c for c in cited_in_text if c not in valid}
        for ins in answer.insights:
            invalid |= {e for e in ins.evidence_ids if e not in valid}
            ins.evidence_ids = [e for e in ins.evidence_ids if e in valid]
        answer.insights = [i for i in answer.insights if i.evidence_ids]
        cited = (cited_in_text & valid) | {e for i in answer.insights for e in i.evidence_ids}
        check = {
            "valid_citations": sorted(cited, key=lambda s: int(s[1:])),
            "invalid_citations": sorted(invalid),
            "evidence_used_pct": round(100 * len(cited) / len(valid), 1) if valid else 0.0,
        }
        if not answer.insights or invalid:
            answer.confidence = "baixa"
        return {"answer": answer, "status": "answered", "citation_check": check}

    # ------------------------------------------------------------------ roteamento
    def route_scope(state: RAGState) -> str:
        return "recuperar" if state["analysis"].in_scope else "recusar_fora_escopo"

    def route_evidence(state: RAGState) -> str:
        enough = len(state.get("evidence") or {}) >= config.MIN_EVIDENCIAS
        return "gerar" if enough else "responder_sem_evidencia"

    g = StateGraph(RAGState)
    g.add_node("analisar_pergunta", analyze)
    g.add_node("recusar_fora_escopo", refuse_out_of_scope)
    g.add_node("recuperar", retrieve)
    g.add_node("avaliar_evidencias", grade)
    g.add_node("responder_sem_evidencia", answer_insufficient)
    g.add_node("gerar", generate)
    g.add_node("verificar_citacoes", verify)

    g.add_edge(START, "analisar_pergunta")
    g.add_conditional_edges("analisar_pergunta", route_scope, ["recuperar", "recusar_fora_escopo"])
    g.add_edge("recuperar", "avaliar_evidencias")
    g.add_conditional_edges(
        "avaliar_evidencias", route_evidence, ["gerar", "responder_sem_evidencia"]
    )
    g.add_edge("gerar", "verificar_citacoes")
    g.add_edge("verificar_citacoes", END)
    g.add_edge("recusar_fora_escopo", END)
    g.add_edge("responder_sem_evidencia", END)
    return g.compile()


class VoCAssistant:
    """Fachada simples usada pela CLI, pela interface e pela avaliação."""

    def __init__(self, llm: BaseChatModel | None = None, retriever: HybridRetriever | None = None):
        self.graph = build_graph(llm, retriever)

    def ask(self, question: str, user_filters: SearchFilters | None = None) -> RAGState:
        t0 = time.perf_counter()
        state = self.graph.invoke({"question": question, "user_filters": user_filters, "trace": {}})
        state.setdefault("trace", {})["total_s"] = round(time.perf_counter() - t0, 2)
        return state
