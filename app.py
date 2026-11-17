"""Interface web (Streamlit) do Voice of Customer Intelligence.

Executar:  streamlit run app.py
"""
from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd
import streamlit as st

sys.path.insert(0, str(Path(__file__).parent))

from src import config  # noqa: E402
from src.rag.schemas import SearchFilters  # noqa: E402
from src.transform.categorias import rotulo_categoria  # noqa: E402
from src.transform.texto import strip_accents  # noqa: E402

st.set_page_config(page_title="VoC Intelligence · Olist", page_icon="💬", layout="wide")

EXAMPLES = [
    "Quais são os principais problemas relatados pelos clientes?",
    "Quais reclamações relacionadas à entrega aparecem com maior frequência?",
    "O que os clientes mais elogiam em suas experiências de compra?",
    "Quais padrões aparecem nas avaliações de clientes insatisfeitos?",
    "Existem padrões recorrentes nas avaliações sobre a qualidade dos produtos?",
    "Quais avaliações sustentam que existem problemas recorrentes com o prazo de entrega?",
    "O que os clientes mais elogiam nos produtos de cama, mesa e banho?",
    "Quais temas aparecem nas avaliações de pedidos entregues com atraso?",
    "Quais reclamações os clientes fazem sobre pagamentos com criptomoedas?",
    "Qual é a previsão do tempo para amanhã em São Paulo?",
]


STATUS_ENTREGA = {"atrasado": "Atrasado", "no_prazo": "No prazo", "nao_entregue": "Não entregue"}


@st.cache_resource(show_spinner="Carregando índice vetorial e modelos...")
def load_assistant():
    from src.rag.grafo import VoCAssistant

    return VoCAssistant()


@st.cache_data
def categorias_em_portugues() -> dict[str, str]:
    """Categoria em inglês (valor usado no filtro) -> nome em português, em ordem alfabética."""
    codigos = pd.read_parquet(config.GOLD_DOCUMENTOS, columns=["product_category"])["product_category"].unique()
    rotulos = {c: rotulo_categoria(c) for c in codigos}
    return dict(sorted(rotulos.items(), key=lambda kv: strip_accents(kv[1]).lower()))


def sidebar_filters() -> SearchFilters | None:
    st.sidebar.header("Filtros de metadados")
    st.sidebar.caption(
        "Opcional. Quando vazios, o próprio pipeline infere os filtros a partir da pergunta."
    )
    use = st.sidebar.toggle("Aplicar filtros manuais", value=False)
    score = st.sidebar.slider("Nota da avaliação", 1, 5, (1, 5), disabled=not use)
    categorias = categorias_em_portugues()
    cats = st.sidebar.multiselect(
        "Categorias de produto",
        options=list(categorias),
        format_func=categorias.get,
        placeholder="Escolha uma ou mais categorias",
        disabled=not use,
    )
    years = st.sidebar.slider("Ano da compra", 2016, 2018, (2016, 2018), disabled=not use)
    delivery = st.sidebar.selectbox(
        "Status da entrega",
        options=[None, *STATUS_ENTREGA],
        format_func=lambda s: "Todos" if s is None else STATUS_ENTREGA[s],
        disabled=not use,
    )
    uf = st.sidebar.text_input("UF do cliente (ex.: SP)", max_chars=2, disabled=not use)
    if not use:
        return None
    return SearchFilters(
        score_min=score[0] if score[0] > 1 else None,
        score_max=score[1] if score[1] < 5 else None,
        product_categories=cats,
        year_from=years[0] if years[0] > 2016 else None,
        year_to=years[1] if years[1] < 2018 else None,
        delivery_status=delivery,
        customer_state=uf.upper() or None,
    )


def evidence_card(sid: str, r) -> None:
    m = r.metadata
    stars = "★" * int(m["review_score"]) + "☆" * (5 - int(m["review_score"]))
    dup = f" · {r.duplicates} comentários idênticos" if r.duplicates > 1 else ""
    categoria = rotulo_categoria(m["product_category"])
    entrega = STATUS_ENTREGA.get(m["delivery_status"], m["delivery_status"]).lower()
    data = pd.to_datetime(m["review_creation_date"]).strftime("%d/%m/%Y")
    st.markdown(
        f"**[{sid}]** {stars} · `{categoria}` · entrega **{entrega}** · "
        f"{data} · UF {m['customer_state']}{dup}  \n> {r.text}"
    )
    st.caption(
        f"review_id {r.review_id} · similaridade cosseno {r.dense_similarity or 0:.3f} · "
        f"rank BM25 {r.best_bm25_rank or '-'} · RRF {r.rrf_score:.4f}"
    )


def show_result(state: dict) -> None:
    status = state.get("status")
    if status == "out_of_scope":
        st.warning(state["message"], icon="🚫")
    elif status == "insufficient_evidence":
        st.info(state["message"], icon="🔎")
    else:
        ans = state["answer"]
        color = {"alta": "green", "media": "orange", "baixa": "red"}[ans.confidence]
        st.markdown(f"### Resposta  :{color}-badge[confiança {ans.confidence}]")
        st.markdown(ans.answer)
        st.markdown("#### Insights e evidências")
        for ins in ans.insights:
            with st.expander(f"**{ins.title}** · {len(ins.evidence_ids)} evidência(s)", expanded=True):
                st.write(ins.description)
                for sid in ins.evidence_ids:
                    evidence_card(sid, state["evidence"][sid])
        st.caption(f"Limitações: {ans.limitations}")

    if "stats" in state and state["stats"]["n_reviews"]:
        s = state["stats"]
        with st.expander("📊 Estatísticas do recorte (base completa, não apenas a amostra recuperada)"):
            c1, c2, c3 = st.columns(3)
            c1.metric("Avaliações no recorte", f"{s['n_reviews']:,}".replace(",", "."))
            c2.metric("Nota média", s["avg_score"])
            c3.metric("Pedidos atrasados", f"{s['pct_late']}%")
            themes = (
                pd.DataFrame(s["themes"].values())
                .set_index("label")[["pct_neg", "pct_pos"]]
                .rename(columns={"pct_neg": "Notas 1-2", "pct_pos": "Notas 4-5"})
                .fillna(0)
            )
            st.bar_chart(
                themes, horizontal=True, stack=False, y_label="", x_label="% dos comentários",
                color=["#c0504d", "#4a9a5b"],  # mesmas cores dos gráficos da EDA
            )
            st.caption("Temas marcados por regras de palavras-chave: estimativa aproximada.")

    if "analysis" in state:
        with st.expander("🧭 Rastro do pipeline (transparência da recuperação)"):
            a = state["analysis"]
            tr = state.get("trace", {})
            st.markdown(
                f"- **Escopo:** {'dentro' if a.in_scope else 'fora'} ({a.scope_reason})\n"
                f"- **Intenção:** {a.intent}\n"
                f"- **Filtros aplicados:** {state['filters'].describe()}\n"
                f"- **Maior similaridade densa:** {tr.get('max_dense_similarity', 0):.3f} "
                f"(limiar {config.MIN_SIMILARIDADE})\n"
                f"- **Critério do avaliador de relevância:** {tr.get('grading', '-')}\n"
                f"- **Checagem de citações:** {state.get('citation_check', '-')}\n"
                f"- **Tempos (s):** {tr.get('timings_s')} · total {tr.get('total_s')}"
            )
            st.markdown("**Consultas usadas na busca (pergunta original + reescritas):**")
            for q in state.get("queries", []):
                st.markdown(f"- {q}")
            if state.get("retrieved"):
                relevant_ids = {r.review_id for r in (state.get("evidence") or {}).values()}
                rows = [
                    {
                        "relevante": r.review_id in relevant_ids,
                        "nota": r.metadata["review_score"],
                        "texto": r.text[:120],
                        "cosseno": round(r.dense_similarity, 3) if r.dense_similarity else None,
                        "rank_denso": r.best_dense_rank,
                        "rank_bm25": r.best_bm25_rank,
                        "rrf": round(r.rrf_score, 4),
                    }
                    for r in state["retrieved"]
                ]
                st.markdown("**Candidatos recuperados (busca híbrida + RRF):**")
                st.dataframe(pd.DataFrame(rows), width="stretch", hide_index=True)


def main() -> None:
    st.title("💬 Voice of Customer Intelligence")
    st.caption(
        "RAG sobre ~37 mil avaliações de clientes da Olist (2016-2018). As respostas citam as "
        "avaliações usadas como evidência e o sistema recusa perguntas sem suporte nos dados."
    )
    if not config.GOLD_DOCUMENTOS.exists() or not config.CHROMA_DIR.exists():
        st.error("Base de conhecimento não encontrada. Rode `python main.py` primeiro.")
        st.stop()

    filters = sidebar_filters()
    st.sidebar.divider()
    st.sidebar.subheader("Perguntas de exemplo")
    for ex in EXAMPLES:
        if st.sidebar.button(ex, width="stretch"):
            st.session_state["question"] = ex

    question = st.text_input(
        "Faça uma pergunta sobre a experiência dos clientes",
        key="question",
        placeholder="Ex.: Quais problemas são relatados por clientes insatisfeitos?",
    )
    if st.button("Perguntar", type="primary") or (question and st.session_state.get("_last") != question):
        if not question.strip():
            st.stop()
        st.session_state["_last"] = question
        with st.spinner("Analisando pergunta, recuperando avaliações e gerando resposta..."):
            try:
                st.session_state["state"] = load_assistant().ask(question, filters)
            except Exception as exc:  # erro de API (chave, crédito, rede) não deve derrubar a página
                st.session_state.pop("state", None)
                st.error(f"Falha ao consultar o LLM: {exc}")
                st.stop()
    if "state" in st.session_state:
        show_result(st.session_state["state"])


main()
