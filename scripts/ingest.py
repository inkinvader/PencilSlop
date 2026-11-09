"""PENCILSLOP catalog ingest: grows the catalog with no manual review.

Each run:
  1. Fetches candidates: human paintings from the Art Institute of Chicago
     (public domain, CC0 images) and AI images from Wikimedia Commons.
  2. Applies fixed rules: free license, explicit AI evidence on the file page
     (AI only), image type, near-square shape, no "not artwork" title words.
  3. Normalizes every image the same way (RGB, centre-cropped 640 px square,
     JPEG quality 82, no metadata) so shape and file properties can't reveal
     the answer.
  4. Screens the normalized image with Claude (SCREEN_MODEL) against the
     fairness checklist in SCREEN_PROMPT.
  5. Stores passing images in R2 and the D1 `images` table; records every
     refused source in D1 `rejected` so it is never fetched again.

D1 is the source of truth for the catalog. Players' reports (see the Worker)
hide images that slip through. All storage goes through Wrangler, so the same
script runs locally (logged-in Wrangler) and in GitHub Actions
(CLOUDFLARE_API_TOKEN and CLOUDFLARE_ACCOUNT_ID). Screening needs
ANTHROPIC_API_KEY.

Usage:
  python scripts/ingest.py --remote --human 10 --ai 5     add up to N new of each
  python scripts/ingest.py --local ...                    same, against local dev storage
  python scripts/ingest.py --remote --reject ID "REASON"  hide an image and block its source
  python scripts/ingest.py --remote --recheck-ai          re-verify AI evidence of active AI images
  python scripts/ingest.py --remote --import-legacy       one-off: move the Phase 2 catalog
                                                          (db/catalog.json) into D1/R2
"""

import argparse
import base64
import html
import io
import json
import re
import secrets
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import List

import anthropic
from PIL import Image
from pydantic import BaseModel

ROOT = Path(__file__).resolve().parent.parent
DB_NAME = "pencilslop"
BUCKET = "pencilslop-images"

USER_AGENT = "PENCILSLOP-class-project/0.2 (njk796@eid.utexas.edu)"
SQUARE_SIDE = 640          # every image is stored at exactly this size
MAX_ASPECT = 1.6           # skip images wider or taller than this before cropping
JPEG_QUALITY = 82
DOWNLOAD_PAUSE_S = 1.0     # be polite to the museum and Commons servers
SCREEN_MODEL = "claude-haiku-5-5"

FREE_LICENSE = re.compile(r"^(public domain|pd|cc0|cc by(-sa)? [0-9.]+)", re.I)
# Titles that name something other than an artwork. Cheap pre-filter before screening.
NOT_ARTWORK_TITLE = re.compile(
    r"\b(screenshot|poster|diagram|infographic|logo|cover|chart|map|flag|icon|meme|"
    r"interface|banner|thumbnail|comparison|collage|grid)\b", re.I
)


# --------------------------------------------------------------------------
# HTTP

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


# --------------------------------------------------------------------------
# Storage (D1 + R2 via Wrangler)

class Store:
    def __init__(self, target):
        self.flag = f"--{target}"                       # --local or --remote
        self.npx = shutil.which("npx") or "npx"

    def _wrangler(self, *args):
        cmd = [self.npx, "wrangler", *args]
        out = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True, encoding="utf-8")
        if out.returncode != 0:
            raise RuntimeError(f"wrangler {' '.join(args[:3])} failed:\n{out.stdout}\n{out.stderr}")
        return out.stdout

    def query(self, sql):
        out = self._wrangler("d1", "execute", DB_NAME, self.flag, "--json", "--command", sql)
        return json.loads(out)[0]["results"]

    def execute(self, statements):
        """Run statements from a temp file (avoids command-line length limits)."""
        if not statements:
            return
        with tempfile.NamedTemporaryFile("w", suffix=".sql", delete=False, encoding="utf-8") as f:
            f.write("\n".join(statements))
        try:
            self._wrangler("d1", "execute", DB_NAME, self.flag, "--file", f.name, "-y")
        finally:
            Path(f.name).unlink()

    def put_image(self, image_id, jpeg_bytes):
        with tempfile.NamedTemporaryFile(suffix=".jpg", delete=False) as f:
            f.write(jpeg_bytes)
        try:
            self._wrangler("r2", "object", "put", f"{BUCKET}/{image_id}.jpg",
                           "--file", f.name, "--content-type", "image/jpeg", self.flag)
        finally:
            Path(f.name).unlink()

    def known_sources(self):
        rows = self.query("SELECT source_url FROM images UNION SELECT source_url FROM rejected")
        return {r["source_url"] for r in rows}

    def counts(self):
        rows = self.query("SELECT is_ai, COUNT(*) AS n FROM images WHERE active = 1 GROUP BY is_ai")
        c = {r["is_ai"]: r["n"] for r in rows}
        return c.get(0, 0), c.get(1, 0)


def sql(v):
    if v is None:
        return "NULL"
    if isinstance(v, bool):
        return str(int(v))
    if isinstance(v, int):
        return str(v)
    return "'" + str(v).replace("'", "''") + "'"


def insert_image(entry):
    cols = ["id", "is_ai", "title", "artist", "source_url", "license", "ai_evidence", "added_at", "screening"]
    return f"INSERT OR IGNORE INTO images ({', '.join(cols)}) VALUES ({', '.join(sql(entry.get(c)) for c in cols)});"


def insert_rejected(source_url, title, reason):
    return ("INSERT OR IGNORE INTO rejected (source_url, title, reason, rejected_at) VALUES "
            f"({sql(source_url)}, {sql(title)}, {sql(reason)}, {int(time.time() * 1000)});")


# --------------------------------------------------------------------------
# Candidate sources

def human_candidates():
    """Public-domain paintings from the Art Institute of Chicago, skipping its
    'boosted' highlights so the works are less famous. Pages until exhausted."""
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
        for a in data["data"]:
            if a.get("image_id"):
                yield {
                    "is_ai": False,
                    "title": a["title"],
                    "artist": a.get("artist_title") or "Unknown artist",
                    "source_url": f"https://www.artic.edu/artworks/{a['id']}",
                    "license": "CC0 (public domain), Art Institute of Chicago",
                    "ai_evidence": None,
                    "download_url": f"https://www.artic.edu/iiif/2/{a['image_id']}/full/843,/0/default.jpg",
                }


# Commons categories searched for AI images. Category membership is only where we
# look: anyone can edit categories, so it is not accepted as proof (see ai_evidence).
# Deep Dream and Craiyon are left out: their output is too distorted to be a fair test.
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


def ai_candidates():
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
                yield ai_entry(page)
        if "continue" not in data:
            return
        cont = data["continue"]


def ai_entry(page):
    """Candidate entry for a Commons file. Rule failures are carried in 'rule_failure'
    so they get recorded as rejections."""
    info = page["imageinfo"][0]
    meta = info.get("extmetadata", {})
    license_name = meta.get("LicenseShortName", {}).get("value", "")
    title = strip_html(meta.get("ObjectName", {}).get("value")) or page["title"].removeprefix("File:").rsplit(".", 1)[0]
    entry = {
        "is_ai": True,
        "title": title,
        "artist": strip_html(meta.get("Artist", {}).get("value")) or "Unknown prompter",
        "source_url": info["descriptionurl"],
        "license": f"{license_name}, via Wikimedia Commons",
        "ai_evidence": ai_evidence(wikitext_of(page)),
        "download_url": info.get("thumburl") or info["url"],
    }
    if entry["ai_evidence"] is None:
        entry["rule_failure"] = "No explicit AI statement on the file page"
    elif not FREE_LICENSE.match(license_name):
        entry["rule_failure"] = f"License not free: {license_name or 'unknown'}"
    elif info["mime"] not in ("image/jpeg", "image/png", "image/webp"):
        entry["rule_failure"] = f"Unsupported file type {info['mime']}"
    return entry


# --------------------------------------------------------------------------
# Normalization and screening

def normalize(raw_bytes):
    """Identical shape, size, format, and quality for every image; saving fresh drops all metadata."""
    img = Image.open(io.BytesIO(raw_bytes)).convert("RGB")
    w, h = img.size
    if max(w, h) / min(w, h) > MAX_ASPECT:
        raise ValueError(f"aspect {w}x{h} too far from square")
    side = min(w, h)
    left, top = (w - side) // 2, (h - side) // 2
    img = img.crop((left, top, left + side, top + side)).resize((SQUARE_SIDE, SQUARE_SIDE), Image.Resampling.LANCZOS)
    out = io.BytesIO()
    img.save(out, "JPEG", quality=JPEG_QUALITY, optimize=True)
    return out.getvalue()


SCREEN_PROMPT = """You are screening images for a game where players see two artworks and guess
which ones are AI-generated. Some images are human-made paintings, some are AI-generated.
Do NOT judge whether this image is AI. Judge only whether it is a fair, appropriate game image.

The image FAILS if any of these apply:
- photo: it is a photograph or looks photorealistic (like a camera photo) rather than a painting, drawing, or illustration
- text: it contains visible text, letters, numbers, captions, labels, or user-interface elements
- watermark: it has a watermark, logo, signature badge, or generator mark (e.g. a row of colored squares in a corner)
- frame: it shows a picture frame, mat, border, or is several panels or a grid of images
- not_artwork: it is a diagram, chart, map, infographic, screenshot, product shot, or document
- real_person: it shows an identifiable real public figure (politician, celebrity, religious leader) in an invented scene
- sexual: it is sexually suggestive or explicit (non-sexual nudity in classical art is acceptable)
- graphic: it shows gore or graphic violence

If none apply, it PASSES. List every rule that fails by its short name."""


class Screening(BaseModel):
    passes: bool
    failed_rules: List[str]
    reason: str


def screen(client, jpeg_bytes):
    """Return (passes, note). A refusal or unparseable answer counts as a failure."""
    response = client.messages.parse(
        model=SCREEN_MODEL,
        max_tokens=2048,
        messages=[{
            "role": "user",
            "content": [
                {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg",
                                             "data": base64.standard_b64encode(jpeg_bytes).decode()}},
                {"type": "text", "text": SCREEN_PROMPT},
            ],
        }],
        output_format=Screening,
    )
    if response.stop_reason == "refusal" or response.parsed_output is None:
        return False, "Screening model refused or gave no verdict"
    v = response.parsed_output
    passed = v.passes and not v.failed_rules
    note = f"{SCREEN_MODEL}: " + ("passed" if passed else "failed " + ", ".join(v.failed_rules)) + f" ({v.reason})"
    return passed, note


# --------------------------------------------------------------------------
# Commands

def ingest(store, candidates, wanted, screen_client, known):
    """Add up to `wanted` new images from `candidates`. Rejections are written in
    batches (each Wrangler call takes seconds); the finally block flushes the rest."""
    added, refused = 0, []

    def refuse(c, reason):
        refused.append(insert_rejected(c["source_url"], c["title"], reason))
        if len(refused) >= 50:
            store.execute(refused)
            refused.clear()

    try:
        for c in candidates:
            if added >= wanted:
                break
            if c["source_url"] in known:
                continue
            known.add(c["source_url"])

            failure = c.pop("rule_failure", None)
            if failure is None and NOT_ARTWORK_TITLE.search(c["title"]):
                failure = "Title names something other than an artwork"
            if failure:
                refuse(c, failure)
                continue

            try:
                time.sleep(DOWNLOAD_PAUSE_S)
                jpeg = normalize(http_get(c.pop("download_url")))
            except ValueError as err:              # shape rule: permanent, record it
                refuse(c, str(err))
                continue
            except Exception as err:               # network trouble: try again another night
                print(f"  skip (will retry later) {c['source_url']}: {err}")
                continue

            try:
                passed, note = screen(screen_client, jpeg)
            except anthropic.APIError as err:      # API trouble: try again another night
                print(f"  skip (screening unavailable) {c['source_url']}: {err}")
                continue
            if not passed:
                refuse(c, note)
                print(f"  - {'AI   ' if c['is_ai'] else 'human'} {c['title'][:50]}  [{note}]")
                continue

            image_id = secrets.token_hex(8)
            store.put_image(image_id, jpeg)        # image first: a row never points at a missing file
            store.execute([insert_image({**c, "id": image_id, "added_at": int(time.time() * 1000), "screening": note})])
            added += 1
            print(f"  + {'AI   ' if c['is_ai'] else 'human'} {image_id}  {c['title'][:50]}")
    finally:
        store.execute(refused)
    return added


def reject(store, image_id, reason):
    rows = store.query(f"SELECT source_url, title FROM images WHERE id = {sql(image_id)}")
    if not rows:
        sys.exit(f"No image with id {image_id}")
    store.execute([
        f"UPDATE images SET active = 0 WHERE id = {sql(image_id)};",
        insert_rejected(rows[0]["source_url"], rows[0]["title"], reason),
    ])
    print(f"  - hid {image_id}  {rows[0]['title'][:60]}  ({reason})")


def recheck_ai(store):
    """Apply the current evidence rule to active AI images."""
    for row in store.query("SELECT id, title, source_url FROM images WHERE active = 1 AND is_ai = 1"):
        title = "File:" + urllib.parse.unquote(row["source_url"].split("/File:")[1])
        data = commons_query(titles=title, prop="revisions", rvprop="content", rvslots="main")
        evidence = ai_evidence(wikitext_of(next(iter(data["query"]["pages"].values()))))
        if evidence:
            store.execute([f"UPDATE images SET ai_evidence = {sql(evidence)} WHERE id = {sql(row['id'])};"])
        else:
            reject(store, row["id"], "No explicit AI statement on the file page")


def import_legacy(store):
    """One-off migration of the Phase 2 catalog (db/catalog.json, with images moved
    to scripts/raw/legacy-img/) into D1 and R2. INSERT OR IGNORE keeps rows (and
    votes) that already exist."""
    catalog = json.loads((ROOT / "db/catalog.json").read_text(encoding="utf-8"))
    rejected = json.loads((ROOT / "db/rejected.json").read_text(encoding="utf-8"))
    for i, e in enumerate(catalog, 1):
        store.put_image(e["id"], (ROOT / "scripts/raw/legacy-img" / f"{e['id']}.jpg").read_bytes())
        if i % 25 == 0:
            print(f"  uploaded {i}/{len(catalog)}", flush=True)
    store.execute([insert_image({**e, "screening": "manual review (Phase 2)"}) for e in catalog]
                  + [insert_rejected(r["source_url"], r["title"], r["reason"]) for r in rejected])
    print(f"  imported {len(catalog)} images and {len(rejected)} rejections")


def main():
    ap = argparse.ArgumentParser()
    where = ap.add_mutually_exclusive_group(required=True)
    where.add_argument("--local", action="store_const", const="local", dest="target")
    where.add_argument("--remote", action="store_const", const="remote", dest="target")
    ap.add_argument("--human", type=int, default=0, help="add up to N new human artworks")
    ap.add_argument("--ai", type=int, default=0, help="add up to N new AI images")
    ap.add_argument("--reject", nargs=2, metavar=("ID", "REASON"))
    ap.add_argument("--recheck-ai", action="store_true")
    ap.add_argument("--import-legacy", action="store_true")
    args = ap.parse_args()

    sys.stdout.reconfigure(encoding="utf-8")  # titles include non-Latin text
    store = Store(args.target)

    if args.import_legacy:
        import_legacy(store)
    elif args.reject:
        reject(store, *args.reject)
    elif args.recheck_ai:
        recheck_ai(store)
    else:
        client = anthropic.Anthropic()        # reads ANTHROPIC_API_KEY
        known = store.known_sources()
        print("Human art (Art Institute of Chicago):")
        ingest(store, human_candidates(), args.human, client, known)
        print("AI art (Wikimedia Commons):")
        ingest(store, ai_candidates(), args.ai, client, known)

    human, ai = store.counts()
    print(f"Active catalog: {human} human, {ai} AI")


if __name__ == "__main__":
    main()
