"""CLI entrypoint: scrape -> normalize -> match -> build price index."""
from engine import pipeline

if __name__ == "__main__":
    stats = pipeline.run()
    print("\nDone:", stats)
