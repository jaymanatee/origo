"""The retrieval configuration ARCA measured and served in phase 3, in one place.

In ARCA these constants lived in ``rag/generate.py``, next to its generator (Qwen3-1.7B run
locally). Origo does not copy that generator: its agents use the 27B through ``origo.llm``.
What is kept is what the measurements chose (``reports/phase3_eval.json`` and
``reports/phase3_agreement_filter.json`` of ARCA):

* the structural chunking, with the ``[document › section]`` header;
* the hybrid retriever with λ = 0.3 (the weight of BM25), chosen by MRR on the gold set;
* a pool of 20 hits from each retriever before the hybrid merges them;
* the agreement filter on (``origo/rag/agreements.py``).
"""

from pathlib import Path

SERVING_STRATEGY = "structural"
HYBRID_LAMBDA = 0.3
HYBRID_POOL = 20
INDEX_DIR = Path(__file__).parent / "index"  # git-ignored, like the corpus