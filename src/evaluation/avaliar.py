"""Avaliação do pipeline RAG.

Três blocos:

1. **Ablação da recuperação**: compara busca densa, BM25 e híbrida (RRF), com e sem
   as consultas reescritas (multi-query) e com e sem filtros de metadados. A
   relevância é "fraca": uma avaliação recuperada conta como relevante se tiver
   algum dos temas esperados para a pergunta (rótulos por regras) e atender à
   condição de metadados esperada.
2. **Pipeline completo** (com LLM): roteamento correto (responder x recusar),
   acerto na extração de filtros, precisão@10 da recuperação com multi-query,
   validade das citações e latência.
3. **LLM-as-a-judge**: *faithfulness* (fração das afirmações da resposta
   sustentadas pelas evidências citadas) e *answer relevancy* (1 a 5).
"""
from __future__ import annotations

import json
import statistics
from datetime import datetime
from pathlib import Path

from langchain_core.messages import HumanMessage, SystemMessage
from pydantic import BaseModel, Field

from src import config
from src.rag.estatisticas import format_stats
from src.rag.formatacao import render_state
from src.rag.prompts import format_review_for_prompt
from src.rag.recuperacao import HybridRetriever, get_retriever
from src.rag.schemas import SearchFilters
from src.utils import get_logger
from src.visualization.graficos import grafico_ablacao

logger = get_logger("avaliacao")
K = 10


# ---------------------------------------------------------------------- relevância fraca
def _is_relevant(meta: dict, item: dict) -> bool:
    themes = item.get("themes")
    if themes and not any(meta.get(f"tema_{t}") for t in themes):
        return False
    f = item.get("filters") or {}
    if f.get("score_min") and meta["review_score"] < f["score_min"]:
        return False
    if f.get("score_max") and meta["review_score"] > f["score_max"]:
        return False
    if f.get("product_categories") and meta["product_category"] not in f["product_categories"]:
        return False
    if f.get("delivery_status") and meta["delivery_status"] != f["delivery_status"]:
        return False
    if f.get("year_from") and int(str(meta["purchase_yyyymm"])[:4]) < f["year_from"]:
        return False
    if f.get("year_to") and int(str(meta["purchase_yyyymm"])[:4]) > f["year_to"]:
        return False
    return True


def _precision(results, item, k=K) -> float:
    top = results[:k]
    return sum(_is_relevant(r.metadata, item) for r in top) / k if top else 0.0


ABLATION_CONFIGS = {
    "densa_pergunta": "Densa · só a pergunta",
    "bm25_multiquery": "BM25 · multi-query",
    "densa_multiquery": "Densa · multi-query",
    "hibrida_multiquery": "Híbrida (RRF) · multi-query",
    "hibrida_multiquery_filtros": "Híbrida · multi-query + filtros (pipeline)",
}


def ablation_row(item: dict, queries: list[str], filters: SearchFilters, retriever: HybridRetriever) -> dict:
    """Precisão@10 de cada configuração de recuperação para uma pergunta.

    Usa as consultas reescritas pelo próprio pipeline (multi-query) para isolar o
    efeito de cada componente: tipo de busca, reescrita da pergunta e filtros.
    """
    q = item["question"]
    runs = {
        "densa_pergunta": retriever.search([q], None, top_k=K, mode="dense"),
        "bm25_multiquery": retriever.search(queries, None, top_k=K, mode="bm25"),
        "densa_multiquery": retriever.search(queries, None, top_k=K, mode="dense"),
        "hibrida_multiquery": retriever.search(queries, None, top_k=K, mode="hybrid"),
        "hibrida_multiquery_filtros": retriever.search(queries, filters, top_k=K, mode="hybrid"),
    }
    return {"id": item["id"], "question": q, **{k: _precision(v, item) for k, v in runs.items()}}


# ---------------------------------------------------------------------- LLM-as-a-judge
class ClaimCheck(BaseModel):
    claim: str
    supported: bool = Field(description="True se as avaliações fornecidas sustentam a afirmação")


class JudgeVerdict(BaseModel):
    claims: list[ClaimCheck] = Field(description="Afirmações factuais extraídas da resposta")
    relevancy: int = Field(ge=1, le=5, description="Quanto a resposta atende à pergunta (1-5)")
    comment: str


JUDGE_SYSTEM = """Você é um avaliador rigoroso de sistemas RAG. Receberá uma pergunta, as \
avaliações de clientes fornecidas como contexto e a resposta gerada. (1) Extraia as afirmações \
factuais da resposta sobre o que os clientes relatam ou sobre números. (2) Para cada uma, diga se \
ela é sustentada pelo contexto (avaliações ou estatísticas). Afirmações genéricas de ressalva não \
contam. (3) Dê uma nota de 1 a 5 para a relevância da resposta em relação à pergunta."""


def judge(state: dict, llm) -> dict:
    ctx = "\n".join(format_review_for_prompt(s, r) for s, r in state["evidence"].items())
    msg = (
        f"Pergunta: {state['question']}\n\n<estatisticas>\n{format_stats(state['stats'])}\n"
        f"</estatisticas>\n\n<contexto>\n{ctx}\n</contexto>\n\n<resposta>\n{state['answer'].answer}\n"
        + "\n".join(f"- {i.title}: {i.description} {i.evidence_ids}" for i in state["answer"].insights)
        + "\n</resposta>"
    )
    verdict: JudgeVerdict = llm.with_structured_output(JudgeVerdict).invoke(
        [SystemMessage(JUDGE_SYSTEM), HumanMessage(msg)]
    )
    n = len(verdict.claims)
    return {
        "faithfulness": sum(c.supported for c in verdict.claims) / n if n else 1.0,
        "n_claims": n,
        "unsupported": [c.claim for c in verdict.claims if not c.supported],
        "relevancy": verdict.relevancy,
        "comment": verdict.comment,
    }


def _filters_match(expected: dict | None, got: SearchFilters) -> bool:
    if not expected:
        return True
    g = got.model_dump()
    for k, v in expected.items():
        if isinstance(v, list):
            if not set(v) <= set(g.get(k) or []):
                return False
        elif g.get(k) != v:
            return False
    return True


# ---------------------------------------------------------------------- execução
def run_evaluation(use_judge: bool = True) -> Path:
    from langchain_openai import ChatOpenAI

    from src.rag.grafo import VoCAssistant

    golden = json.loads(config.GOLDEN_SET.read_text(encoding="utf-8"))
    retriever = get_retriever()
    config.REPORTS_DIR.mkdir(exist_ok=True)

    ablation: dict = {"rows": []}
    logger.info("Pipeline completo + ablação da recuperação...")
    assistant = VoCAssistant(retriever=retriever)
    judge_llm = ChatOpenAI(model=config.JUDGE_MODEL, temperature=0)
    results, examples = [], []
    for item in golden:
        logger.info("[%s] %s", item["id"], item["question"])
        state = assistant.ask(item["question"])
        got = "answered" if state["status"] == "answered" else "refused"
        res = {
            "id": item["id"],
            "source": item.get("source", "-"),
            "question": item["question"],
            "expected": item["expected"],
            "status": state["status"],
            "routing_ok": got == item["expected"],
            "latency_s": state["trace"].get("total_s"),
        }
        if "filters" in state:
            res["filters"] = state["filters"].describe()
            expected = item["expected_filters"] if "expected_filters" in item else item.get("filters")
            res["filters_ok"] = _filters_match(expected, state["filters"])
        if item["expected"] == "answered" and state.get("retrieved"):
            res["precision@10"] = _precision(state["retrieved"], item)
            ablation["rows"].append(ablation_row(item, state["queries"], state["filters"], retriever))
        if state["status"] == "answered":
            cc = state["citation_check"]
            res["invalid_citations"] = len(cc["invalid_citations"])
            res["confidence"] = state["answer"].confidence
            res["n_evidence"] = len(state["evidence"])
            if use_judge:
                res.update(judge(state, judge_llm))
        results.append(res)
        examples.append(f"### {item['id']} · {item.get('source', '-')}\n\n" + render_state(state))

    report = _write_report(ablation, results, use_judge)
    if ablation["rows"]:
        medias = {ABLATION_CONFIGS[k]: _mean(r[k] for r in ablation["rows"]) for k in ABLATION_CONFIGS}
        grafico_ablacao(medias, config.IMAGES_DIR / "08_ablacao_recuperacao.png")
    ex_path = config.REPORTS_DIR / "exemplos_respostas.md"
    ex_path.write_text(
        "# Exemplos de perguntas e respostas\n\nGerado automaticamente por "
        "`python main.py --etapa avaliacao` em " + datetime.now().strftime("%d/%m/%Y %H:%M") + ".\n\n"
        + "\n\n---\n\n".join(examples),
        encoding="utf-8",
    )
    (config.REPORTS_DIR / "avaliacao_resultados.json").write_text(
        json.dumps({"ablation": ablation, "pipeline": results}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    logger.info("Relatórios em %s", config.REPORTS_DIR)
    return report


def _mean(values) -> float | None:
    values = [v for v in values if v is not None]
    return round(statistics.mean(values), 3) if values else None


def _write_report(ablation: dict, results: list[dict], use_judge: bool) -> Path:
    rows = ablation["rows"]
    lines = [
        "# Avaliação do pipeline RAG",
        "",
        f"Gerado em {datetime.now():%d/%m/%Y %H:%M} · LLM: `{config.LLM_MODEL}` · "
        f"embeddings: `{config.EMBEDDING_MODEL}` · top_k={config.TOP_K}",
        "",
        "## 0. Cobertura das perguntas do enunciado",
        "",
        "Todas as perguntas citadas no enunciado do Tech Challenge, com a redação original.",
        "",
        "| ID | Origem | Pergunta | Resultado | Evidências | Confiança |",
        "|---|---|---|---|---|---|",
        *[
            f"| {r['id']} | {r['source'].replace('enunciado: ', '')} | {r['question']} | "
            f"{'respondida' if r['status'] == 'answered' else r['status']} | {r.get('n_evidence', '-')} | "
            f"{r.get('confidence', '-')} |"
            for r in results if str(r.get("source", "")).startswith("enunciado")
        ],
        "",
        "## 1. Ablação da recuperação (precisão@10, relevância fraca por tema/metadado)",
        "",
        "![Ablação](../images/08_ablacao_recuperacao.png)",
        "",
        "Multi-query = pergunta original + consultas reescritas pelo LLM no estilo do cliente.",
        "",
        "| Pergunta | " + " | ".join(ABLATION_CONFIGS.values()) + " |",
        "|---|" + "---|" * len(ABLATION_CONFIGS),
    ]
    for r in rows:
        lines.append(
            f"| {r['id']} {r['question'][:55]} | " + " | ".join(f"{r[k]:.2f}" for k in ABLATION_CONFIGS) + " |"
        )
    lines.append("| **Média** | " + " | ".join(f"**{_mean(r[k] for r in rows)}**" for k in ABLATION_CONFIGS) + " |")
    lines += ["", "## 2. Pipeline completo", ""]
    answered = [r for r in results if r["expected"] == "answered"]
    refused = [r for r in results if r["expected"] == "refused"]
    summary = {
        "Roteamento correto (geral)": _mean(r["routing_ok"] for r in results),
        "Respondeu quando devia (in-scope)": _mean(r["routing_ok"] for r in answered),
        "Recusou quando devia (fora do escopo / sem evidência)": _mean(r["routing_ok"] for r in refused),
        "Filtros extraídos corretamente": _mean(r.get("filters_ok") for r in answered),
        "Precisão@10 com multi-query + filtros do LLM": _mean(r.get("precision@10") for r in answered),
        "Citações inválidas (total)": sum(r.get("invalid_citations", 0) for r in results),
        "Latência média (s)": _mean(r["latency_s"] for r in results),
    }
    if use_judge:
        summary["Faithfulness média (LLM-as-a-judge)"] = _mean(r.get("faithfulness") for r in results)
        summary["Answer relevancy média (1-5)"] = _mean(r.get("relevancy") for r in results)
    lines += ["| Métrica | Valor |", "|---|---|"] + [f"| {k} | {v} |" for k, v in summary.items()]
    lines += [
        "",
        "## 3. Detalhe por pergunta",
        "",
        "| ID | Origem | Esperado | Status | Filtros | P@10 | Faithfulness | Relevância | Latência (s) |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    for r in results:
        p = f"{r['precision@10']:.2f}" if "precision@10" in r else "-"
        fa = f"{r['faithfulness']:.2f}" if "faithfulness" in r else "-"
        lines.append(
            f"| {r['id']} | {r['source']} | {r['expected']} | {r['status']} | {r.get('filters', '-')} | {p} | {fa} | "
            f"{r.get('relevancy', '-')} | {r['latency_s']} |"
        )
    unsupported = [(r["id"], c) for r in results for c in r.get("unsupported", [])]
    if unsupported:
        lines += ["", "### Afirmações não sustentadas apontadas pelo juiz", ""]
        lines += [f"- {i}: {c}" for i, c in unsupported]
    path = config.REPORTS_DIR / "avaliacao.md"
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path
