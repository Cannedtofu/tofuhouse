import sys
from typing import List, Dict
from brands import BRANDS
from crawlers import locally_crawl, on_crawl
from output import write_output


ADAPTER_MAP = {
    "locally": locally_crawl,
    "on_running": on_crawl,
}


def deduplicate(stores: List[dict]) -> List[dict]:
    """Remove duplicate stores by id, keeping first occurrence."""
    seen = set()
    unique = []
    for s in stores:
        sid = s.get("id")
        if sid is not None and sid not in seen:
            seen.add(sid)
            unique.append(s)
        elif sid is None:
            unique.append(s)  # keep stores without an id
    return unique


def run_all(brands=None) -> Dict[str, List[dict]]:
    target_brands = brands or BRANDS
    brand_data = {}

    for brand in target_brands:
        name = brand["name"]
        sheet = brand["sheet_name"]
        adapter_fn = ADAPTER_MAP.get(brand["adapter"])

        if not adapter_fn:
            print(f"[SKIP] Unknown adapter '{brand['adapter']}' for {name}")
            continue

        print(f"\n{'='*50}")
        print(f"Crawling: {name}")
        print(f"{'='*50}")

        raw = adapter_fn(brand)
        deduped = deduplicate(raw)
        print(f"  Raw: {len(raw)} | After dedup: {len(deduped)} stores")
        brand_data[sheet] = deduped

    return brand_data


if __name__ == "__main__":
    # Optional: pass brand names as args to run only specific brands
    # e.g. python main.py hoka salomon
    filter_names = sys.argv[1:] if len(sys.argv) > 1 else []
    if filter_names:
        selected = [b for b in BRANDS if b["name"] in filter_names]
        if not selected:
            print(f"No brands matched: {filter_names}")
            sys.exit(1)
    else:
        selected = BRANDS

    brand_data = run_all(selected)

    print(f"\n{'='*50}")
    print("Writing output...")
    print(f"{'='*50}")
    write_output(brand_data)
