"""Esquemas Pydantic usados como contratos de saída estruturada do LLM."""
from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, Field

DeliveryStatus = Literal["atrasado", "no_prazo", "nao_entregue"]
Sentiment = Literal["negativo", "neutro", "positivo"]


class SearchFilters(BaseModel):
    """Filtros de metadados aplicados ANTES da busca (pré-filtragem)."""

    score_min: Optional[int] = Field(None, ge=1, le=5, description="Nota mínima (1-5)")
    score_max: Optional[int] = Field(None, ge=1, le=5, description="Nota máxima (1-5)")
    product_categories: list[str] = Field(
        default_factory=list,
        description="Categorias de produto em inglês, exatamente como na lista fornecida",
    )
    year_from: Optional[int] = Field(None, description="Ano inicial da compra (2016-2018)")
    year_to: Optional[int] = Field(None, description="Ano final da compra (2016-2018)")
    delivery_status: Optional[DeliveryStatus] = Field(
        None, description="Status de entrega do pedido, apenas se a pergunta pedir explicitamente"
    )
    customer_state: Optional[str] = Field(None, description="UF do cliente, ex.: SP, RJ")

    def is_empty(self) -> bool:
        return not any(
            [self.score_min, self.score_max, self.product_categories, self.year_from,
             self.year_to, self.delivery_status, self.customer_state]
        )

    def describe(self) -> str:
        parts = []
        if self.score_min or self.score_max:
            parts.append(f"nota entre {self.score_min or 1} e {self.score_max or 5}")
        if self.product_categories:
            parts.append("categorias: " + ", ".join(self.product_categories))
        if self.year_from or self.year_to:
            parts.append(f"compras de {self.year_from or 2016} a {self.year_to or 2018}")
        if self.delivery_status:
            parts.append(f"entrega: {self.delivery_status}")
        if self.customer_state:
            parts.append(f"UF: {self.customer_state}")
        return "; ".join(parts) or "sem filtros"


class QueryAnalysis(BaseModel):
    """Resultado da etapa de processamento/transformação da pergunta."""

    # A justificativa vem antes da decisão: o modelo "pensa" antes de decidir (chain-of-thought).
    scope_reason: str = Field(description="Justificativa curta da decisão de escopo")
    in_scope: bool = Field(
        description="True se a pergunta puder ser respondida com avaliações de clientes de e-commerce"
    )
    intent: Literal[
        "problemas", "elogios", "tema_especifico", "evidencias", "comparacao", "outro"
    ] = Field(description="Tipo de investigação da voz do cliente")
    search_queries: list[str] = Field(
        description=(
            "2 a 4 consultas de busca reescritas no estilo de comentários reais de clientes, "
            "cobrindo facetas diferentes da pergunta"
        )
    )
    filters: SearchFilters = Field(default_factory=SearchFilters)


class RelevanceJudgement(BaseModel):
    relevant_ids: list[str] = Field(
        description="IDs (ex.: R3) das avaliações que contêm informação útil para responder"
    )
    reasoning: str = Field(description="Uma frase explicando o critério usado")


class Insight(BaseModel):
    title: str = Field(description="Insight curto, ex.: 'Atrasos na entrega'")
    description: str = Field(description="Explicação do padrão observado nas evidências")
    evidence_ids: list[str] = Field(description="IDs das avaliações que sustentam o insight")


class GroundedAnswer(BaseModel):
    answer: str = Field(
        description="Resposta em português, objetiva, citando evidências no formato [R1]"
    )
    insights: list[Insight] = Field(description="Principais insights, cada um com evidências")
    confidence: Literal["alta", "media", "baixa"] = Field(
        description="Confiança com base na quantidade e consistência das evidências"
    )
    limitations: str = Field(description="Limitações da resposta (amostra, viés, cobertura)")
