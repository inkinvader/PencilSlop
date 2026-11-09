"""Build the PENCILSLOP image catalog.

Steps:
  1. Fetch candidates: human paintings from the Art Institute of Chicago (public
     domain, CC0 images) and AI paintings from Wikimedia Commons
     (artwork-like AI categories, freely licensed files only).
  2. Download and normalize every image the same way (RGB, centre-cropped to a
     640 px square, JPEG quality 82, no metadata) so shape, size, and file
     properties can't reveal the answer. Images too far from square to crop
     well are skipped.
  3. Save under a random ID in public/img/ and record it in db/catalog.json,
     the reviewable source of truth for the catalog.
  4. Generate db/seed.sql from catalog.json.

Re-running keeps existing catalog entries (and their IDs) and only adds new ones
until the per-source target count is reached. Delete an entry from catalog.json
(and its image) to reject it; run with --seed-only to just regenerate seed.sql.

Usage: python scripts/build_catalog.py [--human N] [--ai N] [--seed-only]
"""

import argparse
import html
import io
import json
import re
import secrets
import sys
import urllib.parse
import urllib.request
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parent.parent
CATALOG = ROOT / "db" / "catalog.json"
SEED = ROOT / "db" / "seed.sql"
IMG_DIR = ROOT / "public" / "img"

USER_AGENT = "PENCILSLOP-class-project/0.1 (njk796@eid.utexas.edu)"
SQUARE_SIDE = 640      # every image is served at exactly this size
MAX_ASPECT = 1.6       # skip images wider or taller than this before cropping
JPEG_QUALITY = 82
FREE_LICENSE = re.compile(r"^(public domain|pd|cc0|cc by(-sa)? [0-9.]+)", re.I)


def http_get(url, data=None):
    headers = {"User-Agent": USER_AGENT, "AIC-User-Agent": USER_AGENT}
    if data is not None:
        headers["Content-Type"] = "application/json"
        data = json.dumps(data).encode()
    with urllib.request.urlopen(urllib.request.Request(url, data, headers), timeout=60) as r:
        return r.read()


def strip_html(s):
    return html.unescape(re.sub(r"<[^>]+>", "", s or "")).strip()


def human_candidates(limit):
    """Public-domain paintings from the Art Institute of Chicago, skipping its
    'boosted' highlights so the works are less famous."""
    query = {
        "query": {"bool": {
            "must": [{"term": {"is_public_domain": True}}, {"term": {"artwork_type_id": 1}}],
            "must_not": [{"term": {"is_boosted": True}}],
        }},
        "fields": ["id", "title", "artist_title", "image_id"],
        "limit": limit * 3,
    }
    data = json.loads(http_get("https://api.artic.edu/api/v1/artworks/search", query))
    for a in data["data"]:
        if not a.get("image_id"):
            continue
        yield {
            "is_ai": False,
            "title": a["title"],
            "artist": a.get("artist_title") or "Unknown artist",
            "source_url": f"https://www.artic.edu/artworks/{a['id']}",
            "license": "CC0 (public domain), Art Institute of Chicago",
            "ai_evidence": None,
            "download_url": f"https://www.artic.edu/iiif/2/{a['image_id']}/full/843,/0/default.jpg",
        }


# Commons categories whose files are artwork-like. Membership in one of these is
# the AI confirmation recorded for each image.
AI_CATEGORIES = [
    "Category:AI-generated paintings",
    "Category:AI-generated landscapes",
    "Category:AI-generated portraits",
    "Category:AI-generated images in the style of specific artists",
]


def ai_candidates(limit):
    """Freely licensed files from the AI_CATEGORIES on Wikimedia Commons."""
    for category in AI_CATEGORIES:
        yield from ai_candidates_in(category)


def ai_candidates_in(category):
    params = {
        "action": "query", "format": "json",
        "generator": "categorymembers", "gcmtitle": category,
        "gcmtype": "file", "gcmlimit": "50",
        "prop": "imageinfo", "iiprop": "url|mime|extmetadata", "iiurlwidth": "1000",
    }
    data = json.loads(http_get("https://commons.wikimedia.org/w/api.php?" + urllib.parse.urlencode(params)))
    for page in data.get("query", {}).get("pages", {}).values():
        if "imageinfo" not in page:
            continue
        info = page["imageinfo"][0]
        meta = info.get("extmetadata", {})
        license_name = meta.get("LicenseShortName", {}).get("value", "")
        if info["mime"] not in ("image/jpeg", "image/png", "image/webp") or not FREE_LICENSE.match(license_name):
            continue
        title = strip_html(meta.get("ObjectName", {}).get("value")) or page["title"].removeprefix("File:").rsplit(".", 1)[0]
        yield {
            "is_ai": True,
            "title": title,
            "artist": strip_html(meta.get("Artist", {}).get("value")) or "Unknown prompter",
            "source_url": info["descriptionurl"],
            "license": f"{license_name}, via Wikimedia Commons",
            "ai_evidence": f"Listed in Wikimedia Commons {category}",
            "download_url": info.get("thumburl") or info["url"],
        }


def normalize(raw_bytes, out_path):
    """Identical shape, size, format, and quality for every image; saving fresh drops all metadata."""
    img = Image.open(io.BytesIO(raw_bytes)).convert("RGB")
    w, h = img.size
    if max(w, h) / min(w, h) > MAX_ASPECT:
        raise ValueError(f"aspect {w}x{h} too far from square")
    side = min(w, h)
    left, top = (w - side) // 2, (h - side) // 2
    img = img.crop((left, top, left + side, top + side)).resize((SQUARE_SIDE, SQUARE_SIDE), Image.Resampling.LANCZOS)
    img.save(out_path, "JPEG", quality=JPEG_QUALITY, optimize=True)


def add_entries(catalog, candidates, target, is_ai):
    have = sum(1 for e in catalog if e["is_ai"] == is_ai)
    known = {e["source_url"] for e in catalog}
    for c in candidates:
        if have >= target:
            break
        if c["source_url"] in known:
            continue
        image_id = secrets.token_hex(8)
        try:
            normalize(http_get(c.pop("download_url")), IMG_DIR / f"{image_id}.jpg")
        except Exception as err:  # skip unreadable or unreachable images
            print(f"  skip {c['source_url']}: {err}")
            continue
        catalog.append({"id": image_id, **c})
        have += 1
        print(f"  + {'AI   ' if is_ai else 'human'} {image_id}  {c['title'][:60]}")


def sql(v):
    if v is None:
        return "NULL"
    if isinstance(v, bool):
        return str(int(v))
    return "'" + str(v).replace("'", "''") + "'"


def write_seed(catalog):
    cols = ["id", "is_ai", "title", "artist", "source_url", "license", "ai_evidence"]
    lines = ["-- Generated by scripts/build_catalog.py from db/catalog.json. Do not edit by hand."]
    for e in catalog:
        lines.append(f"INSERT INTO images ({', '.join(cols)}) VALUES ({', '.join(sql(e[c]) for c in cols)});")
    SEED.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--human", type=int, default=10)
    ap.add_argument("--ai", type=int, default=10)
    ap.add_argument("--seed-only", action="store_true")
    args = ap.parse_args()

    sys.stdout.reconfigure(encoding="utf-8")  # titles include non-Latin text
    IMG_DIR.mkdir(parents=True, exist_ok=True)
    catalog = json.loads(CATALOG.read_text(encoding="utf-8")) if CATALOG.exists() else []

    if not args.seed_only:
        try:
            print("Human art (Art Institute of Chicago):")
            add_entries(catalog, human_candidates(args.human), args.human, False)
            print("AI art (Wikimedia Commons):")
            add_entries(catalog, ai_candidates(args.ai), args.ai, True)
        finally:
            # Save even after an error so every saved image has a catalog entry.
            CATALOG.write_text(json.dumps(catalog, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    write_seed(catalog)
    print(f"Catalog: {sum(not e['is_ai'] for e in catalog)} human, {sum(e['is_ai'] for e in catalog)} AI -> {SEED.name}")


if __name__ == "__main__":
    main()
