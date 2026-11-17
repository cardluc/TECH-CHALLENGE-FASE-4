"""Marcação de temas por regras de palavras-chave.

Um RAG recupera apenas os *top-k* comentários mais parecidos com a pergunta. Por
isso ele não consegue dizer sozinho "qual problema é **mais frequente**". Para
dar suporte quantitativo às respostas, cada avaliação recebe rótulos de tema
(multirrótulo) por regras transparentes sobre o texto normalizado. Esses
rótulos viram metadados que servem para:

1. calcular a frequência de cada tema no recorte filtrado (estatística enviada
   ao LLM junto com as evidências);
2. filtrar a recuperação por tema;
3. avaliar a recuperação (relevância "fraca" por tema).

As regras são aproximadas, o que é aceitável para indicar tendências. O
README discute as limitações e alternativas (LDA/BERTopic). Os temas são
calculados na camada Silver, sobre todas as avaliações com texto.
"""
from __future__ import annotations

import re

from src.transform.texto import normalize

# Padrões aplicados ao texto em minúsculas e sem acentos.
THEME_PATTERNS: dict[str, list[str]] = {
    "atraso_entrega": [
        r"atras", r"demor", r"fora do prazo", r"passou do prazo", r"apos o prazo",
        r"prazo (de entrega )?(ja )?(venceu|expirou|esgotou)", r"ainda nao (chegou|recebi)",
        r"esperando", r"aguardando",
    ],
    "nao_recebido": [
        r"nao (recebi|chegou|foi entregue|entregaram|veio)", r"nunca (chegou|recebi)",
        r"produto nao entregue", r"nao recebemos", r"extraviad",
    ],
    "entrega_no_prazo": [
        r"antes do prazo", r"no prazo", r"dentro do prazo", r"entrega rapida",
        r"chegou rapid", r"entregue rapid", r"super rapid", r"muito rapid", r"rapidez",
        r"antes da data",
    ],
    "produto_defeituoso": [
        r"defeit", r"quebrad", r"danificad", r"estragad", r"avariad", r"nao funciona",
        r"parou de funcionar", r"riscad", r"amassad", r"trincad", r"rasgad", r"com problema",
    ],
    "produto_diferente": [
        r"diferente", r"errad", r"outro produto", r"nao (e|era) o (que|mesmo)",
        r"nao corresponde", r"nao confere", r"trocad", r"modelo diferente", r"cor diferente",
        r"falsificad", r"nao original", r"nao e original",
    ],
    "item_faltando": [
        r"falt", r"incomplet", r"so recebi", r"somente (um|uma|1)", r"apenas (um|uma|1)",
        r"veio (so|apenas|somente)", r"recebi (so|apenas|somente)", r"nao veio (o|a|os|as) ",
    ],
    "qualidade_produto": [
        r"qualidade", r"material", r"acabamento", r"resistente", r"fragil", r"frageis",
        r"durabilidade", r"bem feito", r"mal feito",
    ],
    "atendimento": [
        r"atendiment", r"atendente", r"\bsac\b", r"suporte", r"nao (me )?respond",
        r"sem resposta", r"contato", r"retorno", r"descaso", r"falta de respeito",
    ],
    "embalagem": [r"embalag", r"embalad", r"embrulh", r"caixa"],
    "preco_custo_beneficio": [
        r"preco", r"barato", r"\bcaro\b", r"custo.beneficio", r"valor", r"frete",
    ],
    "troca_devolucao_reembolso": [
        r"troca", r"devolu", r"devolv", r"reembols", r"estorno", r"cancel", r"dinheiro de volta",
    ],
    "recomenda": [r"recomend"],
}

THEME_LABELS: dict[str, str] = {
    "atraso_entrega": "Atraso na entrega",
    "nao_recebido": "Produto não recebido",
    "entrega_no_prazo": "Entrega no prazo / rápida",
    "produto_defeituoso": "Produto com defeito ou danificado",
    "produto_diferente": "Produto diferente do anunciado / errado",
    "item_faltando": "Itens faltando no pedido",
    "qualidade_produto": "Qualidade do produto (menções)",
    "atendimento": "Atendimento / comunicação",
    "embalagem": "Embalagem",
    "preco_custo_beneficio": "Preço / custo-benefício / frete",
    "troca_devolucao_reembolso": "Troca, devolução ou reembolso",
    "recomenda": "Menciona recomendação",
}

THEMES = list(THEME_PATTERNS)
_COMPILED = {t: re.compile("|".join(p)) for t, p in THEME_PATTERNS.items()}


def tag_themes(text: str) -> dict[str, bool]:
    norm = normalize(text or "")
    return {f"tema_{t}": bool(rx.search(norm)) for t, rx in _COMPILED.items()}
