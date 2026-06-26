"""Push embeddings from local ChromaDB into Supabase pgvector table."""
import sys
import time
from pathlib import Path

# --- setup path ---
sys.path.insert(0, str(Path(__file__).resolve().parent))

from config import DATA_DIR
from engine.supabase_client import get_client

CHROMA_DIR = DATA_DIR / "chromadb"
COLLECTION_NAME = "dispensary_engine_products"
BATCH = 200


def main():
    print("[1/3] Loading ChromaDB collection...")
    import chromadb
    client = chromadb.PersistentClient(path=str(CHROMA_DIR))
    try:
        col = client.get_collection(COLLECTION_NAME)
    except Exception as e:
        print(f"  ERROR: {e}")
        print("  Run 'python run_embeddings.py build' first.")
        return

    total = col.count()
    print(f"  {total} embeddings in local collection")

    print("[2/3] Fetching all vectors from ChromaDB...")
    # ChromaDB get() supports limit/offset for large collections
    all_ids, all_embeddings, all_docs, all_metas = [], [], [], []
    limit = 2000
    offset = 0
    while True:
        result = col.get(
            limit=limit,
            offset=offset,
            include=["embeddings", "documents", "metadatas"],
        )
        batch_ids = result["ids"]
        if not batch_ids:
            break
        all_ids.extend(batch_ids)
        all_embeddings.extend(result["embeddings"])
        all_docs.extend(result["documents"])
        all_metas.extend(result["metadatas"])
        print(f"  fetched {len(all_ids)}/{total}...")
        offset += limit
        if len(batch_ids) < limit:
            break

    print(f"  fetched {len(all_ids)} total")

    print("[3/3] Pushing to Supabase mcp_embeddings...")
    sb = get_client()
    inserted = errors = 0
    for i in range(0, len(all_ids), BATCH):
        chunk_ids   = all_ids[i:i + BATCH]
        chunk_embs  = all_embeddings[i:i + BATCH]
        chunk_docs  = all_docs[i:i + BATCH]
        chunk_metas = all_metas[i:i + BATCH]

        rows = []
        for j, mcp_id in enumerate(chunk_ids):
            meta = chunk_metas[j] or {}
            emb = chunk_embs[j]
            # ChromaDB returns numpy arrays; convert to plain Python list
            if hasattr(emb, "tolist"):
                emb = emb.tolist()
            rows.append({
                "mcp_id": mcp_id,
                "embedding": emb,
                "doc_text": chunk_docs[j] or "",
                "model": "all-MiniLM-L6-v2",
                "canonical_title":     meta.get("title", ""),
                "normalized_brand":    meta.get("brand", ""),
                "normalized_category": meta.get("category", ""),
                "normalized_form":     meta.get("form", ""),
                "normalized_size":     meta.get("size", ""),
            })

        try:
            sb.table("mcp_embeddings").upsert(rows, on_conflict="mcp_id").execute()
            inserted += len(rows)
        except Exception as exc:
            errors += 1
            print(f"  batch {i//BATCH}: ERROR {exc}")

        if i % (BATCH * 5) == 0 and i > 0:
            print(f"  {inserted}/{len(all_ids)} pushed...")
        time.sleep(0.05)

    print(f"\nDone: {inserted} embeddings in Supabase  ({errors} batch errors)")


if __name__ == "__main__":
    main()
