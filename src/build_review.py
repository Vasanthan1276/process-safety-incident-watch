"""Rebuild the read-only review projection, without discovery or raw-data changes."""
import argparse
import json
from pathlib import Path

try:
    from . import main, reliability
except ImportError:
    import main
    import reliability


def build(output=None):
    database = main.load_json(main.INCIDENTS_PATH)
    result = reliability.build_review_index(
        database, main.load_json(main.DIAGNOSTICS_PATH), main.load_json(main.REVIEWS_PATH), main.iso_now())
    if output:
        main.save_json(Path(output), result)
    else:
        main.save_json(main.REVIEW_INDEX_PATH, result)
        main.save_json(main.PUBLIC_DATA_DIR / "review_index.json", result)
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", help="Write to this path instead of data/ and public/data/")
    args = parser.parse_args()
    result = build(args.output)
    from collections import Counter
    print(json.dumps({"records": result["raw_record_count"], "screening": Counter(i["screening"]["decision"] for i in result["incidents"]),
                      "duplicate_pairs": sum(len(i["possible_duplicates"]) for i in result["incidents"]) // 2}, indent=2))
