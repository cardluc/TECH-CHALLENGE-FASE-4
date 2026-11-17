"""Formatação textual (Markdown) de um resultado do pipeline: usada na CLI e nos relatórios."""
from __future__ import annotations

from src.transform.categorias import rotulo_categoria


def render_state(state: dict, show_trace: bool = True) -> str:
    out: list[str] = [f"**Pergunta:** {state['question']}", ""]
    status = state.get("status")

    if status != "answered":
        out += [f"**Status:** `{status}`", "", state.get("message", "")]
    else:
        ans = state["answer"]
        out += [ans.answer, ""]
        if ans.insights:
            out.append("**Insights e evidências:**")
            for ins in ans.insights:
                out.append(f"- **{ins.title}**: {ins.description} ({', '.join(ins.evidence_ids)})")
            out.append("")
        out += [f"**Confiança:** {ans.confidence}", f"**Limitações:** {ans.limitations}", ""]
        out.append("**Avaliações usadas como evidência:**")
        for sid, r in state["evidence"].items():
            m = r.metadata
            text = r.text if len(r.text) <= 220 else r.text[:217] + "..."
            dup = f" (x{r.duplicates} idênticas)" if r.duplicates > 1 else ""
            out.append(
                f"- [{sid}] nota {m['review_score']} · {rotulo_categoria(m['product_category'])} · "
                f"entrega {m['delivery_status']} · {m['review_creation_date']}{dup}: \"{text}\""
            )

    if show_trace and "analysis" in state:
        a = state["analysis"]
        tr = state.get("trace", {})
        out += [
            "",
            "<details><summary>Rastro do pipeline</summary>",
            "",
            f"- Escopo: {a.in_scope} ({a.scope_reason})",
            f"- Intenção: {a.intent}",
            f"- Consultas: {state.get('queries')}",
            f"- Filtros: {state['filters'].describe() if state.get('filters') else '-'}",
            f"- Recuperadas: {tr.get('n_retrieved', '-')} · relevantes: {tr.get('n_relevant', '-')}"
            f" · maior similaridade densa: {round(tr.get('max_dense_similarity', 0), 3)}",
            f"- Checagem de citações: {state.get('citation_check', '-')}",
            f"- Tempos (s): {tr.get('timings_s')} · total: {tr.get('total_s')}",
            "",
            "</details>",
        ]
    return "\n".join(out)
