"""Limpeza e normalização de texto.

Há duas visões do mesmo comentário:

* ``clean_for_embedding``: limpeza **leve**. Modelos Transformer de embeddings
  foram treinados com texto natural: acentos, stopwords e pontuação carregam
  significado ("não chegou" ≠ "chegou"). Removemos apenas ruído (quebras de
  linha, espaços, repetições exageradas) e mascaramos dados pessoais.
* ``tokens_for_bm25``: normalização **pesada** para a busca lexical. Caixa baixa,
  sem acentos, sem stopwords (mantendo negações) e com stemming RSLP, para que
  "atrasou", "atraso" e "atrasada" casem com o mesmo radical.
"""
from __future__ import annotations

import re
import unicodedata
from functools import lru_cache
from pathlib import Path

import nltk

_EMAIL = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")
_PHONE = re.compile(r"(?:\(?\d{2}\)?\s?)?9?\d{4}[-\s]?\d{4}")
_CPF = re.compile(r"\d{3}\.?\d{3}\.?\d{3}-?\d{2}")
_URL = re.compile(r"https?://\S+|www\.\S+")
_REPEAT = re.compile(r"(.)\1{3,}")  # "muitoooooo" -> "muitoo"
_SPACES = re.compile(r"\s+")
_TOKEN = re.compile(r"[a-z0-9]+")

# Negações e intensificadores mudam o sentido da reclamação: não podem ser descartados.
_KEEP_WORDS = {"nao", "nem", "nunca", "sem", "mas", "muito", "pouco", "antes", "depois", "ainda"}


def strip_accents(text: str) -> str:
    return "".join(
        c for c in unicodedata.normalize("NFKD", text) if not unicodedata.combining(c)
    )


def mask_pii(text: str) -> str:
    """Mascara e-mails, URLs, CPFs e telefones (privacidade)."""
    text = _EMAIL.sub("[email]", text)
    text = _URL.sub("[url]", text)
    text = _CPF.sub("[cpf]", text)
    text = _PHONE.sub("[telefone]", text)
    return text


def clean_for_embedding(text: str | None) -> str:
    if not isinstance(text, str):
        return ""
    text = text.replace("\r", " ").replace("\n", " ")
    text = mask_pii(text)
    text = _REPEAT.sub(r"\1\1", text)
    return _SPACES.sub(" ", text).strip()


def normalize(text: str) -> str:
    """Caixa baixa + sem acentos: usado em regras de palavras-chave e no BM25."""
    return strip_accents(text.lower())


@lru_cache(maxsize=1)
def _nltk_pronto() -> None:
    """Garante stopwords e stemmer RSLP numa pasta própria (~/.voc_rag/nltk_data).

    A pasta padrão do NLTK fica na AppData, que é virtualizada para apps
    empacotados do Windows; uma pasta fora dela vale igual para qualquer programa.
    """
    pasta = Path.home() / ".voc_rag" / "nltk_data"
    pasta.mkdir(parents=True, exist_ok=True)
    nltk.data.path[:] = [str(pasta)]
    for recurso, caminho in (("stopwords", "corpora/stopwords"), ("rslp", "stemmers/rslp")):
        try:
            nltk.data.find(caminho)
        except LookupError:
            nltk.download(recurso, download_dir=str(pasta), quiet=True)


@lru_cache(maxsize=1)
def _stopwords() -> frozenset[str]:
    _nltk_pronto()
    words = nltk.corpus.stopwords.words("portuguese")
    return frozenset(strip_accents(w) for w in words) - _KEEP_WORDS


@lru_cache(maxsize=1)
def _stemmer():
    _nltk_pronto()
    return nltk.stem.RSLPStemmer()


@lru_cache(maxsize=50_000)
def _stem(word: str) -> str:
    return _stemmer().stem(word)


# Palavras típicas da pergunta do gestor que não aparecem no texto do cliente e só
# geram ruído no casamento lexical ("Quais problemas os clientes relatam nas avaliações?").
QUERY_META_WORDS = {
    "quais", "qual", "principais", "principal", "cliente", "clientes", "consumidor",
    "consumidores", "comprador", "compradores", "avaliacao", "avaliacoes", "comentario",
    "comentarios", "relatam", "relatado", "relatados", "relatadas", "relatos", "relato",
    "mencionam", "aparecem", "existem", "padrao", "padroes", "recorrente", "recorrentes",
    "frequencia", "frequentes", "relacionado", "relacionados", "relacionada", "relacionadas",
    "sustentam", "conclusao", "dizem", "falam", "experiencia", "experiencias", "maior", "mais",
    "aspectos", "temas", "tema", "existe", "costumam", "insatisfeitos", "satisfeitos",
    # "elogio" diz que houve elogio, mas não DO QUE: casaria com "só tenho elogios"
    "elogio", "elogios", "elogia", "elogiam", "elogiou", "elogiar", "elogiado", "elogiados",
    "elogiada", "elogiadas",
}


def tokens_for_bm25(text: str, is_query: bool = False) -> list[str]:
    stop = _stopwords() | QUERY_META_WORDS if is_query else _stopwords()
    return [_stem(t) for t in _TOKEN.findall(normalize(text)) if t not in stop and len(t) > 1]
