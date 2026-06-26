"""CLI: build or update the ChromaDB embedding index."""
import sys
from engine.embeddings import get_agent

if __name__ == "__main__":
    mode = sys.argv[1] if len(sys.argv) > 1 else "build"
    agent = get_agent()
    if mode == "build":
        print("[Embeddings] Full build starting...")
        result = agent.build(log=print)
        print(f"Result: {result}")
    elif mode == "update":
        print("[Embeddings] Incremental update...")
        n = agent.update(log=print)
        print(f"Updated: {n} products")
    elif mode == "search":
        q = " ".join(sys.argv[2:]) or "relaxing indica gummy"
        print(f"[Embeddings] Semantic search: {q!r}")
        for r in agent.search(q, n=10):
            print(f"  [{r['score']:.3f}] {r['title']} — {r['brand']} | {r['category']}")
    else:
        print("Usage: python run_embeddings.py [build|update|search <query>]")
