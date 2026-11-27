"""The search the knowledge agent uses: the configuration measured in ``serving.py`` (the
hybrid over the structural chunks), with the agreement filter.

The difference with ``evaluate_filter.py``: there the filter comes from the text of the
question (``keep_for``), because each gold question names its agreement. In Origo the
question about the rules is a part of the user's message, rewritten by the orchestrator, and
may not name it: in "are the kitchens originating under CETA, and how long do we keep the
records?" the part "how long do we keep the records?" names no agreement, and the Korean or
British period would come up. So the filter comes from the plan's agreements, which the
orchestrator already identified for the whole message.

The retriever is built once, the first time a question needs it (the corpus, the chunks, the
embedding model): a question that only evaluates a product does not pay for it.
"""

from __future__ import annotations

from functools import lru_cache

from origo.rag.agreements import agreement_of
from origo.rag.serving import HYBRID_LAMBDA, HYBRID_POOL, INDEX_DIR, SERVING_STRATEGY
from origo.state import Passage

# Origo's agreements (origo/state.py) -> the agreements of the corpus (origo/rag/agreements.py).
CORPUS_AGREEMENT = {"TCA": "tca", "UE-JP": "japan", "UE-KR": "korea", "CETA": "ceta"}
TOP_K = 5  # the passages the model gets for each question, as measured (R@5)


def keep_agreements(agreements: list[str]):
    """Passages of these agreements or of no agreement (general documents: the REX and
    authorised exporter guides, the CN notes); ``None`` (no filter) if there is no agreement."""
    names = {CORPUS_AGREEMENT[a] for a in agreements}
    if not names:
        return None

    def keep(source: str) -> bool:
        agreement = agreement_of(source)
        return agreement is None or agreement in names

    return keep


@lru_cache(maxsize=1)
def served_retriever():
    """The hybrid retriever of ``serving.py``, built once. The dense vectors are read from
    ``origo/rag/index/`` (the ones ``evaluate_filter.py`` saved) if the chunks are the same."""
    from origo.rag.embed import DEFAULT_QUERY_INSTRUCTION, CachedEmbedder
    from origo.rag.ingest import load_corpus
    from origo.rag.retriever import BM25Retriever, DenseRetriever, HybridRetriever
    from origo.rag.strategies import build_chunks

    documents = load_corpus()
    if not documents:
        raise RuntimeError("the corpus is empty: run `python -m origo.rag.download` first")
    embedder = CachedEmbedder()
    chunks = build_chunks(SERVING_STRATEGY, documents, embedder)
    dense = DenseRetriever(
        chunks,
        embedder,
        persist_dir=INDEX_DIR / SERVING_STRATEGY,
        instruction=DEFAULT_QUERY_INSTRUCTION,
    )
    return HybridRetriever(
        BM25Retriever(chunks), dense, weight_bm25=HYBRID_LAMBDA, pool=HYBRID_POOL
    )


def search(question: str, agreements: list[str], retriever=None) -> list[Passage]:
    """The ``TOP_K`` best passages for ``question``, from ``agreements`` and general documents.
    ``retriever`` is for the tests; by default, the served one."""
    retriever = retriever or served_retriever()
    hits = retriever.search(question, top_k=TOP_K, keep=keep_agreements(agreements))
    return [
        Passage(source=h.chunk.source, section=h.chunk.section, text=h.chunk.text) for h in hits
    ]