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
until the per-source target count is reached. Run with --seed-only to just
regenerate seed.sql.

To reject an image: --reject ID REASON. It is removed from the catalog, its file
is moved to scripts/raw/rejected/, and its source is recorded in db/rejected.json
so it is never fetched again.

AI images are accepted only with explicit evidence on their Commons file page
(see ai_evidence). --recheck-ai applies that rule to existing entries.

Usage: python scripts/build_catalog.py [--human N] [--ai N] [--seed-only]
       python scripts/build_catalog.py --reject ID REASON
       python scripts/build_catalog.py --recheck-ai
"""

import argparse
import html
import io
import json
import re
import secrets
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parent.parent
CATALOG = ROOT / "db" / "catalog.json"
SEED = ROOT / "db" / "seed.sql"
REJECTED = ROOT / "db" / "rejected.json"   # sources reviewed and refused, with reasons
IMG_DIR = ROOT / "public" / "img"
REJECTED_IMG_DIR = ROOT / "scripts" / "raw" / "rejected"

USER_AGENT = "PENCILSLOP-class-project/0.1 (njk796@eid.utexas.edu)"
SQUARE_SIDE = 640      # every image is served at exactly this size
MAX_ASPECT = 1.6       # skip images wider or taller than this before cropping
JPEG_QUALITY = 82
DOWNLOAD_PAUSE_S = 0.5 # be polite to the museum and Commons servers
FREE_LICENSE = re.compile(r"^(public domain|pd|cc0|cc by(-sa)? [0-9.]+)", re.I)


def http_get(url, data=None, retries=4):
    """GET (or POST JSON). On HTTP 429 (rate limited), wait and retry with backoff."""
    headers = {"User-Agent": USER_AGENT, "AIC-User-Agent": USER_AGENT}
    if data is not None:
        headers["Content-Type"] = "application/json"
        data = json.dumps(data).encode()
    for attempt in range(retries + 1):
        try:
            with urllib.request.urlopen(urllib.request.Request(url, data, headers), timeout=60) as r:
                return r.read()
        except urllib.error.HTTPError as err:
            if err.code != 429 or attempt == retries:
                raise
            wait = int(err.headers.get("Retry-After") or 0) or 10 * 2 ** attempt
            print(f"  rate limited; waiting {wait}s", flush=True)
            time.sleep(wait)


def strip_html(s):
    return html.unescape(re.sub(r"<[^>]+>", "", s or "")).strip()


def human_candidates(limit):
    """Public-domain paintings from the Art Institute of Chicago, skipping its
    'boosted' highlights so the works are less famous. Pages through results
    until the caller stops asking."""
    for page in range(1, 100):
        query = {
            "query": {"bool": {
                "must": [{"term": {"is_public_domain": True}}, {"term": {"artwork_type_id": 1}}],
                "must_not": [{"term": {"is_boosted": True}}],
            }},
            "fields": ["id", "title", "artist_title", "image_id"],
            "limit": 100,
            "page": page,
        }
        data = json.loads(http_get("https://api.artic.edu/api/v1/artworks/search", query))
        if not data["data"]:
            return
        yield from human_entries(data["data"])


def human_entries(artworks):
    for a in artworks:
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


# Commons categories searched for artwork-like AI images. Category membership is
# only where we look: anyone can edit categories, so it is not accepted as proof.
# Art-specific categories come first; generator categories add volume. Deep Dream
# and Craiyon are left out: their output is too distorted to be a fair test.
AI_CATEGORIES = [
    "Category:AI-generated paintings",
    "Category:AI-generated landscapes",
    "Category:AI-generated portraits",
    "Category:AI-generated images in the style of specific artists",
    "Category:Images generated by Midjourney",
    "Category:Images generated by Stable Diffusion",
    "Category:Images generated by ChatGPT",
    "Category:Images generated by Image Creator by Microsoft Bing",
    "Category:Images generated by DALL-E",
    "Category:Images generated by Gemini",
    "Category:Images generated by Flux (text-to-image model)",
    "Category:Images generated by Ideogram",
    "Category:AI-generated images including prompts",
]

# Proof of AI origin must be on the file page itself: the uploader names the
# generator, or applies Commons' PD-algorithm license template, which declares
# the work machine-generated.
AI_TOOLS = re.compile(
    r"midjourney|dall[-·\s]?e|stable diffusion|sdxl|firefly|chatgpt|gpt-4o|bing image creator|"
    r"image creator|leonardo\.ai|craiyon|imagen|flux\.1|image playground|copilot designer|ideogram",
    re.I,
)
PD_ALGORITHM = re.compile(r"\{\{\s*PD-algorithm", re.I)


def ai_evidence(wikitext):
    """Return a description of the explicit AI evidence on a file page, or None."""
    tools = sorted({m.group(0).strip() for m in AI_TOOLS.finditer(wikitext)}, key=str.lower)
    parts = []
    if tools:
        parts.append("generator named on file page: " + ", ".join(tools))
    if PD_ALGORITHM.search(wikitext):
        parts.append("uploader applied PD-algorithm (machine-generated) license")
    return "; ".join(parts) or None


def commons_query(**params):
    params = {"action": "query", "format": "json", **params}
    return json.loads(http_get("https://commons.wikimedia.org/w/api.php?" + urllib.parse.urlencode(params)))


def wikitext_of(page):
    return page.get("revisions", [{}])[0].get("slots", {}).get("main", {}).get("*", "")


def ai_candidates(limit):
    """Freely licensed files with explicit AI evidence, from the AI_CATEGORIES on Wikimedia Commons."""
    for category in AI_CATEGORIES:
        yield from ai_candidates_in(category)


def ai_candidates_in(category):
    """Page through one category. A page missing imageinfo or revisions in one
    batch is completed by the API in a continued batch, so it is skipped here."""
    cont = {}
    while True:
        data = commons_query(
            generator="categorymembers", gcmtitle=category, gcmtype="file", gcmlimit="20",
            prop="imageinfo|revisions", iiprop="url|mime|extmetadata", iiurlwidth="1000",
            rvprop="content", rvslots="main", **cont,
        )
        for page in data.get("query", {}).get("pages", {}).values():
            if "imageinfo" in page and "revisions" in page:
                entry = ai_entry(page)
                if entry:
                    yield entry
        if "continue" not in data:
            return
        cont = data["continue"]


def ai_entry(page):
    """Catalog entry for a Commons file page, or None if it lacks AI evidence or a free license."""
    evidence = ai_evidence(wikitext_of(page))
    if evidence is None:
        return None
    info = page["imageinfo"][0]
    meta = info.get("extmetadata", {})
    license_name = meta.get("LicenseShortName", {}).get("value", "")
    if info["mime"] not in ("image/jpeg", "image/png", "image/webp") or not FREE_LICENSE.match(license_name):
        return None
    title = strip_html(meta.get("ObjectName", {}).get("value")) or page["title"].removeprefix("File:").rsplit(".", 1)[0]
    return {
        "is_ai": True,
        "title": title,
        "artist": strip_html(meta.get("Artist", {}).get("value")) or "Unknown prompter",
        "source_url": info["descriptionurl"],
        "license": f"{license_name}, via Wikimedia Commons",
        "ai_evidence": evidence,
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


def load_json(path):
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else []


def save_json(path, data):
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def recheck_ai(catalog):
    """Apply the current evidence rule to AI entries already in the catalog:
    refresh the recorded evidence, and reject entries that no longer qualify."""
    for entry in [e for e in catalog if e["is_ai"]]:
        title = "File:" + urllib.parse.unquote(entry["source_url"].split("/File:")[1])
        data = commons_query(titles=title, prop="revisions", rvprop="content", rvslots="main")
        evidence = ai_evidence(wikitext_of(next(iter(data["query"]["pages"].values()))))
        if evidence:
            entry["ai_evidence"] = evidence
            print(f"  ok {entry['id']}  {entry['title'][:50]}  [{evidence}]")
        else:
            reject(catalog, entry["id"], "No explicit AI statement on the file page (category only)")


def reject(catalog, image_id, reason):
    """Remove an entry, keep its file aside, and remember its source so it isn't re-fetched."""
    entry = next((e for e in catalog if e["id"] == image_id), None)
    if entry is None:
        sys.exit(f"No catalog entry with id {image_id}")
    catalog.remove(entry)
    rejected = load_json(REJECTED)
    rejected.append({"source_url": entry["source_url"], "title": entry["title"], "reason": reason})
    save_json(REJECTED, rejected)
    REJECTED_IMG_DIR.mkdir(parents=True, exist_ok=True)
    src = IMG_DIR / f"{image_id}.jpg"
    if src.exists():
        src.replace(REJECTED_IMG_DIR / src.name)
    print(f"  - rejected {image_id}  {entry['title'][:60]}  ({reason})")


def add_entries(catalog, candidates, target, is_ai):
    have = sum(1 for e in catalog if e["is_ai"] == is_ai)
    known = {e["source_url"] for e in catalog} | {r["source_url"] for r in load_json(REJECTED)}
    for c in candidates:
        if have >= target:
            break
        if c["source_url"] in known:
            continue
        known.add(c["source_url"])  # a file can appear in several categories
        image_id = secrets.token_hex(8)
        try:
            time.sleep(DOWNLOAD_PAUSE_S)
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
    ap.add_argument("--reject", nargs=2, metavar=("ID", "REASON"))
    ap.add_argument("--recheck-ai", action="store_true", help="re-verify AI evidence of existing entries")
    args = ap.parse_args()

    sys.stdout.reconfigure(encoding="utf-8")  # titles include non-Latin text
    IMG_DIR.mkdir(parents=True, exist_ok=True)
    catalog = load_json(CATALOG)

    if args.recheck_ai:
        recheck_ai(catalog)
        save_json(CATALOG, catalog)
    elif args.reject:
        reject(catalog, *args.reject)
        save_json(CATALOG, catalog)
    elif not args.seed_only:
        try:
            print("Human art (Art Institute of Chicago):")
            add_entries(catalog, human_candidates(args.human), args.human, False)
            print("AI art (Wikimedia Commons):")
            add_entries(catalog, ai_candidates(args.ai), args.ai, True)
        finally:
            # Save even after an error so every saved image has a catalog entry.
            save_json(CATALOG, catalog)

    write_seed(catalog)
    print(f"Catalog: {sum(not e['is_ai'] for e in catalog)} human, {sum(e['is_ai'] for e in catalog)} AI -> {SEED.name}")


if __name__ == "__main__":
    main()
