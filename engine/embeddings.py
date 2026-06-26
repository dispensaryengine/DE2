"""Semantic product embeddings using ChromaDB + sentence-transformers.

Architecture
────────────
• Local ChromaDB persistent store at data/chromadb/
• Collection: "dispensary_engine_products"
• Each document = one MCP, embedded on: canonical_title + brand + category + form
• Embeddings model: all-MiniLM-L6-v2  (22MB, fast, good semantic quality)

Two roles
─────────
1. Semantic search   — better than LIKE queries for natural-language product lookup
2. Soft matching     — finds "same product" candidates for MCPs that didn't get
                       an exact PEK match (feeds the review queue with scored candidates)

Usage
─────
  from engine.embeddings import EmbeddingAgent
  agent = EmbeddingAgent()
  agent.build()                          # or agent.update() for incremental
  results = agent.search("relaxing indica gummy 10mg", n=10)
  candidates = agent.find_similar(mcp_id, threshold=0.85)
"""
import json
import uuid
from pathlib import Path
from typing import Optional

from config import DATA_DIR
from engine import db
from engine.logger import get_logger

CHROMA_DIR = DATA_DIR / "chromadb"
COLLECTION_NAME = "dispensary_engine_products"
MODEL_NAME = "all-MiniLM-L6-v2"


def _doc_text(mcp: dict) -> str:
    """Build the text string that gets embedded for a given MCP."""
    parts = [
        mcp.get("canonical_title") or "",
        mcp.get("normalized_brand") or "",
        mcp.get("normalized_category") or "",
        mcp.get("normalized_form") or "",
        mcp.get("subform") or "",
        mcp.get("canonical_product_name") or "",
        mcp.get("normalized_size") or "",
        mcp.get("extract_type") or "",
        mcp.get("dominance_or_type") or "",
    ]
    return " | ".join(p for p in parts if p).lower()


def _meta(mcp: dict) -> dict:
    """Lightweight metadata stored alongside each vector."""
    return {
        "brand": mcp.get("normalized_brand") or "",
        "category": mcp.get("normalized_category") or "",
        "form": mcp.get("normalized_form") or "",
        "size": mcp.get("normalized_size") or "",
        "title": mcp.get("canonical_title") or "",
        "pek": mcp.get("pek") or "",
    }


class EmbeddingAgent:
    """Manages the ChromaDB collection for product embeddings."""

    def __init__(self):
        self._client = None
        self._collection = None
        self._ef = None
        self._available = None

    def _check_available(self) -> bool:
        if self._available is None:
            try:
                import chromadb  # noqa: F401
                from sentence_transformers import SentenceTransformer  # noqa: F401
                self._available = True
            except ImportError:
                self._available = False
        return self._available

    def _init(self):
        if self._client is not None:
            return True
        if not self._check_available():
            return False
        try:
            import chromadb
            from chromadb.utils.embedding_functions import SentenceTransformerEmbeddingFunction
            CHROMA_DIR.mkdir(parents=True, exist_ok=True)
            self._client = chromadb.PersistentClient(path=str(CHROMA_DIR))
            self._ef = SentenceTransformerEmbeddingFunction(MODEL_NAME)
            self._collection = self._client.get_or_create_collection(
                name=COLLECTION_NAME,
                embedding_function=self._ef,
                metadata={"hnsw:space": "cosine"},
            )
            return True
        except Exception as exc:
            print(f"  [embeddings] init failed: {exc}")
            self._available = False
            return False

    def _load_all_mcps(self) -> list[dict]:
        with db.connect() as conn:
            rows = conn.execute("SELECT * FROM master_canonical_products").fetchall()
        return [dict(r) for r in rows]

    def _encode(self, texts: list[str]) -> list:
        """Generate raw float vectors using the local sentence-transformer model."""
        if not self._init():
            return []
        from sentence_transformers import SentenceTransformer
        if not hasattr(self, "_st_model"):
            self._st_model = SentenceTransformer(MODEL_NAME)
        return self._st_model.encode(texts, normalize_embeddings=True).tolist()

    def _push_to_supabase(self, rows: list[dict], log=print):
        """Upsert embedding rows into Supabase mcp_embeddings table."""
        try:
            from engine.supabase_client import get_client
            sb = get_client()
            for i in range(0, len(rows), 200):
                chunk = rows[i:i + 200]
                sb.table("mcp_embeddings").upsert(chunk, on_conflict="mcp_id").execute()
        except Exception as exc:
            log(f"  [embeddings] Supabase push error: {exc}")

    def build(self, log=print, batch_size: int = 500) -> dict:
        """(Re)build embeddings in ChromaDB (local) AND Supabase (remote)."""
        logger = get_logger()
        if not self._init():
            log("  [embeddings] ChromaDB not available — skipping")
            return {"status": "unavailable"}

        logger.info("embed_start", "Building product embeddings", source="embeddings.build")
        mcps = self._load_all_mcps()
        log(f"  [embeddings] embedding {len(mcps)} MCPs with {MODEL_NAME}")

        self._collection.delete(where={"_dummy": {"$ne": "_"}})  # clear all

        ids, docs, metas = [], [], []
        for mcp in mcps:
            ids.append(mcp["mcp_id"])
            docs.append(_doc_text(mcp))
            metas.append(_meta(mcp))

        added = 0
        for i in range(0, len(ids), batch_size):
            chunk_ids = ids[i:i + batch_size]
            chunk_docs = docs[i:i + batch_size]
            chunk_metas = metas[i:i + batch_size]

            # local ChromaDB (embeddings generated inside chroma)
            self._collection.add(ids=chunk_ids, documents=chunk_docs, metadatas=chunk_metas)

            # Supabase: generate vectors explicitly and push
            vectors = self._encode(chunk_docs)
            sb_rows = [
                {
                    "mcp_id": chunk_ids[j],
                    "embedding": vectors[j],
                    "doc_text": chunk_docs[j],
                    "model": MODEL_NAME,
                    "canonical_title":     chunk_metas[j].get("title", ""),
                    "normalized_brand":    chunk_metas[j].get("brand", ""),
                    "normalized_category": chunk_metas[j].get("category", ""),
                    "normalized_form":     chunk_metas[j].get("form", ""),
                    "normalized_size":     chunk_metas[j].get("size", ""),
                }
                for j in range(len(chunk_ids))
            ]
            self._push_to_supabase(sb_rows)

            added += len(chunk_ids)
            if i % (batch_size * 5) == 0 and i > 0:
                log(f"  [embeddings] {added}/{len(ids)} embedded...")

        result = {"status": "ok", "embedded": added, "model": MODEL_NAME}
        logger.info("embed_complete", f"Embedded {added} products",
                    payload=result, source="embeddings.build")
        log(f"  [embeddings] done: {added} products in ChromaDB + Supabase")
        return result

    def update(self, mcp_ids: list[str] | None = None, log=print) -> int:
        """Incremental update: embed new/changed MCPs into both stores."""
        if not self._init():
            return 0

        if mcp_ids:
            with db.connect() as conn:
                placeholders = ",".join("?" * len(mcp_ids))
                mcps = [dict(r) for r in conn.execute(
                    f"SELECT * FROM master_canonical_products WHERE mcp_id IN ({placeholders})",
                    mcp_ids
                ).fetchall()]
        else:
            existing = set(self._collection.get()["ids"])
            all_mcps = self._load_all_mcps()
            mcps = [m for m in all_mcps if m["mcp_id"] not in existing]

        if not mcps:
            return 0

        ids  = [m["mcp_id"] for m in mcps]
        docs = [_doc_text(m) for m in mcps]
        metas = [_meta(m) for m in mcps]

        self._collection.upsert(ids=ids, documents=docs, metadatas=metas)

        vectors = self._encode(docs)
        sb_rows = [
            {
                "mcp_id": ids[j],
                "embedding": vectors[j],
                "doc_text": docs[j],
                "model": MODEL_NAME,
                "canonical_title":     metas[j].get("title", ""),
                "normalized_brand":    metas[j].get("brand", ""),
                "normalized_category": metas[j].get("category", ""),
                "normalized_form":     metas[j].get("form", ""),
                "normalized_size":     metas[j].get("size", ""),
            }
            for j in range(len(ids))
        ]
        self._push_to_supabase(sb_rows)
        log(f"  [embeddings] upserted {len(ids)} products into ChromaDB + Supabase")
        return len(ids)

    def search(self, query: str, n: int = 20,
               category: str | None = None) -> list[dict]:
        """Semantic search over product titles. Returns ranked results."""
        if not self._init():
            return []

        where = {"category": category} if category else None
        try:
            results = self._collection.query(
                query_texts=[query.lower()],
                n_results=min(n, self._collection.count()),
                where=where,
                include=["metadatas", "distances"],
            )
        except Exception:
            return []

        out = []
        for i, mcp_id in enumerate(results["ids"][0]):
            meta = results["metadatas"][0][i]
            dist = results["distances"][0][i]
            score = max(0.0, 1.0 - dist)
            out.append({
                "mcp_id": mcp_id,
                "score": round(score, 4),
                "title": meta.get("title"),
                "brand": meta.get("brand"),
                "category": meta.get("category"),
                "form": meta.get("form"),
                "size": meta.get("size"),
            })
        return out

    def find_similar(self, mcp_id: str, threshold: float = 0.85,
                     n: int = 10) -> list[dict]:
        """Find MCPs semantically similar to the given one (for soft matching)."""
        if not self._init():
            return []
        try:
            existing = self._collection.get(ids=[mcp_id], include=["documents"])
            if not existing["documents"]:
                return []
            doc = existing["documents"][0]
            results = self._collection.query(
                query_texts=[doc],
                n_results=min(n + 1, self._collection.count()),
                include=["metadatas", "distances"],
            )
        except Exception:
            return []

        out = []
        for i, rid in enumerate(results["ids"][0]):
            if rid == mcp_id:
                continue
            dist = results["distances"][0][i]
            score = max(0.0, 1.0 - dist)
            if score < threshold:
                continue
            meta = results["metadatas"][0][i]
            out.append({
                "mcp_id": rid,
                "score": round(score, 4),
                "title": meta.get("title"),
                "brand": meta.get("brand"),
                "category": meta.get("category"),
            })
        return out[:n]

    def match_unmatched(self, threshold: float = 0.88, log=print) -> list[dict]:
        """Find products in review_queue and suggest semantic matches.

        Returns a list of {review_id, dpl_id, candidates: [...]} dicts.
        """
        if not self._init():
            return []

        with db.connect() as conn:
            queue = conn.execute(
                "SELECT review_id, dpl_id, detail FROM product_review_queue"
                " WHERE status='open'"
            ).fetchall()

        results = []
        for item in queue:
            detail = json.loads(item["detail"] or "{}")
            title = detail.get("title") or ""
            if not title:
                continue
            candidates = self.search(title, n=5)
            candidates = [c for c in candidates if c["score"] >= threshold]
            if candidates:
                results.append({
                    "review_id": item["review_id"],
                    "dpl_id": item["dpl_id"],
                    "query": title,
                    "candidates": candidates,
                })
        return results

    @property
    def count(self) -> int:
        if not self._init():
            return 0
        try:
            return self._collection.count()
        except Exception:
            return 0


# ── module-level singleton ────────────────────────────────────────────────────
_agent: EmbeddingAgent | None = None


def get_agent() -> EmbeddingAgent:
    global _agent
    if _agent is None:
        _agent = EmbeddingAgent()
    return _agent
