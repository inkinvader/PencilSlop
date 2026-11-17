"""PENCILSLOP catalog ingest: grows the catalog with no manual review, at no cost.

Each run:
  1. Fetches candidates: human paintings from the Art Institute of Chicago
     (public domain, CC0 images) and AI images from Wikimedia Commons.
  2. Applies fixed rules: free license, explicit AI evidence on the file page
     (AI only), image type, near-square shape, no "not artwork" title words.
  3. Normalizes every image the same way (RGB, centre-cropped 640 px square,
     JPEG quality 82, no metadata) so shape and file properties can't reveal
     the answer.
  4. Applies image checks (image_problem): no near-duplicate of any stored
     image (perceptual hash) and no readable text (Tesseract OCR). Passing
     images are saved to public/img/<id>.jpg.
  5. With --remote, publishes in a safe order: commit and push the new images,
     deploy the Worker, and only then add their rows to D1. A round can then
     never point at an image the live site doesn't have.
  6. Records every refused source in D1 `rejected` so it is never fetched again.
  7. Sets each image's difficulty tier (1 easy, 2 medium, 3 hard) by fixed rules:
     human by the museum's own popularity flags, AI by Commons upload year
     (see human_tier and ai_tier). The Worker picks tiers by the player's streak.

D1 is the source of truth for the catalog. Rules can't catch everything (e.g.
photorealism or a textless watermark), so players' reports hide images that
slip through (see the Worker). All database access goes through Wrangler, so the
same script runs locally (logged-in Wrangler) and in GitHub Actions
(CLOUDFLARE_API_TOKEN and CLOUDFLARE_ACCOUNT_ID). OCR needs the Tesseract
program installed (the workflow installs it).

Usage:
  python scripts/ingest.py --remote --human 10 --ai 5     add up to N new of each and publish
  python scripts/ingest.py --local --human 3 --ai 3       same against local dev storage (no publish)
  python scripts/ingest.py --remote --reject ID "REASON"  hide an image and block its source
  python scripts/ingest.py --remote --recheck-ai          re-verify AI evidence of active AI images
  python scripts/ingest.py --remote --recheck-images      apply the image checks to the active
                                                          catalog (also fills missing hashes)
  python scripts/ingest.py --remote --retier              set tiers on existing images by the rules
  python scripts/ingest.py --remote --add-own DIR --tool NAME
                                                          add the developer's own AI images from DIR
                                                          (title = file name; same normalization and
                                                          checks; tier hard). Keep DIR in scripts/raw/
                                                          so the originals stay out of git.
  python scripts/ingest.py --remote --set-own-tool NAME   change the generator recorded for all of the
                                                          developer's own images
  python scripts/ingest.py --remote --void-rounds FILE    remove the votes of the rounds listed in FILE
                                                          (one round ID per line; e.g. test traffic)
  python scripts/ingest.py --remote --check-files         hide active images the site can't serve
                                                          (also runs before every ingest)
"""

import argparse
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
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytesseract
from PIL import Image, ImageOps

ROOT = Path(__file__).resolve().parent.parent
IMG_DIR = ROOT / "public" / "img"
DB_NAME = "pencilslop"

# Identifies the client to the museum and Commons APIs; the repo URL is the contact point.
USER_AGENT = "PENCILSLOP-class-project/0.2 (+https://github.com/inkinvader/PencilSlop)"
SITE_URL = "https://pencilslop.pencilslop.workers.dev"   # where --remote checks that images are served
SQUARE_SIDE = 640          # every image is stored at exactly this size
MAX_ASPECT = 1.6           # skip images wider or taller than this before cropping
JPEG_QUALITY = 82
DOWNLOAD_PAUSE_S = 1.0     # be polite to the museum and Commons servers

# Near-duplicate check: Hamming distance between 64-bit dHashes. Calibrated
# 2026-11-09: a known near-duplicate scored 1; the closest genuinely different
# catalog pair scored 10.
DUPLICATE_DISTANCE = 6
# Difficulty tiers for AI images: upload year stands in for generator quality
# (older generators look more obviously AI). Through this year = easy, the next = medium,
# later = hard. Approved 2026-11-16; vote data is meant to replace this in Phase 4.
AI_EASY_THROUGH_YEAR = 2022
AI_MEDIUM_YEAR = 2023
# Text check (Tesseract): see image_text_words. Set by calibration on known text
# images vs the catalog (plan.md, testing and evidence).
OCR_PSM = 11               # "sparse text": find words anywhere in a picture
OCR_SCALE = 2              # upscale before OCR; small text is missed otherwise
OCR_MIN_CONF = 85          # word confidence (0-100)
OCR_MIN_WORDS = 3          # this many confident words means the image contains text

# Windows installs Tesseract outside PATH; CI (apt) puts it on PATH.
_WINDOWS_TESSERACT = Path(r"C:\Program Files\Tesseract-OCR\tesseract.exe")
if not shutil.which("tesseract") and _WINDOWS_TESSERACT.exists():
    pytesseract.pytesseract.tesseract_cmd = str(_WINDOWS_TESSERACT)

FREE_LICENSE = re.compile(r"^(public domain|pd|cc0|cc by(-sa)? [0-9.]+)", re.I)
# Titles that name something other than an artwork.
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
# Storage: D1 via Wrangler; images are files in public/img published by git + deploy

def run(cmd):
    out = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True, encoding="utf-8")
    if out.returncode != 0:
        raise RuntimeError(f"{' '.join(cmd[:4])} failed:\n{out.stdout}\n{out.stderr}")
    return out.stdout


class Store:
    def __init__(self, target):
        self.target = target                            # "local" or "remote"
        self.npx = shutil.which("npx") or "npx"

    def wrangler(self, *args):
        return run([self.npx, "wrangler", *args])

    def query(self, sql_text):
        out = self.wrangler("d1", "execute", DB_NAME, f"--{self.target}", "--json", "--command", sql_text)
        return json.loads(out)[0]["results"]

    def execute(self, statements):
        """Run statements from a temp file (avoids command-line length limits)."""
        if not statements:
            return
        with tempfile.NamedTemporaryFile("w", suffix=".sql", delete=False, encoding="utf-8") as f:
            f.write("\n".join(statements))
        try:
            self.wrangler("d1", "execute", DB_NAME, f"--{self.target}", "--file", f.name, "-y")
        finally:
            Path(f.name).unlink()

    def publish_images(self, count):
        """Commit and push new images, then deploy, so they are live before any row points at them."""
        run(["git", "add", "public/img"])
        run(["git", "commit", "-m", f"Ingest: add {count} artwork images"])
        run(["git", "push"])
        self.wrangler("deploy")

    def stored_hashes(self):
        """Hashes of every stored image, active or hidden, so a hidden image can't return as a copy."""
        rows = self.query("SELECT id, phash FROM images WHERE phash IS NOT NULL")
        return {r["id"]: int(r["phash"], 16) for r in rows}

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
    cols = ["id", "is_ai", "title", "artist", "source_url", "license", "ai_evidence", "added_at", "screening", "phash", "tier"]
    return f"INSERT OR IGNORE INTO images ({', '.join(cols)}) VALUES ({', '.join(sql(entry.get(c)) for c in cols)});"


def insert_rejected(source_url, title, reason):
    return ("INSERT OR IGNORE INTO rejected (source_url, title, reason, rejected_at) VALUES "
            f"({sql(source_url)}, {sql(title)}, {sql(reason)}, {int(time.time() * 1000)});")


# --------------------------------------------------------------------------
# Candidate sources

# Human tiers come from the museum's own flags: "boosted" highlights are its famous
# works (easy); "not viewed much" works are obscure (hard); everything else is medium.
AIC_FIELDS = ["id", "title", "artist_title", "image_id", "is_boosted", "has_not_been_viewed_much"]
BOOSTED = {"term": {"is_boosted": True}}
RARELY_VIEWED = {"term": {"has_not_been_viewed_much": True}}
HUMAN_TIER_FILTERS = {           # tier: (must, must_not) added to the base query
    1: ([BOOSTED], []),
    2: ([], [BOOSTED, RARELY_VIEWED]),
    3: ([RARELY_VIEWED], [BOOSTED]),
}


def human_tier(artwork):
    if artwork.get("is_boosted"):
        return 1
    return 3 if artwork.get("has_not_been_viewed_much") else 2


def human_candidates():
    """Public-domain paintings from the Art Institute of Chicago, alternating
    between the three tiers so each tier grows at a similar rate."""
    sources = [human_candidates_in_tier(t) for t in HUMAN_TIER_FILTERS]
    while sources:
        for src in list(sources):
            try:
                yield next(src)
            except StopIteration:
                sources.remove(src)


def human_candidates_in_tier(tier):
    must, must_not = HUMAN_TIER_FILTERS[tier]
    for page in range(1, 100):
        query = {
            "query": {"bool": {
                "must": [{"term": {"is_public_domain": True}}, {"term": {"artwork_type_id": 1}}, *must],
                "must_not": must_not,
            }},
            "fields": AIC_FIELDS,
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
                    "tier": human_tier(a),
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


def ai_tier(timestamp):
    """Tier from a Commons upload timestamp such as '2023-04-01T12:00:00Z'."""
    year = int(timestamp[:4])
    if year <= AI_EASY_THROUGH_YEAR:
        return 1
    return 2 if year == AI_MEDIUM_YEAR else 3


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
            prop="imageinfo|revisions", iiprop="url|mime|extmetadata|timestamp", iiurlwidth="1000",
            rvprop="content", rvslots="main", **cont,
        )
        for page in data.get("query", {}).get("pages", {}).values():
            if "imageinfo" in page and "revisions" in page:
                yield ai_entry(page)
        if "continue" not in data:
            return
        cont = data["continue"]


def ai_entry(page):
    """Candidate entry for a Commons file. A rule failure is carried in 'rule_failure'
    so it gets recorded as a rejection."""
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
        "tier": ai_tier(info["timestamp"]),
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
# Normalization and image checks

def normalize(raw_bytes):
    """Identical shape and size for every image. Saved later with save_jpeg."""
    img = Image.open(io.BytesIO(raw_bytes)).convert("RGB")
    w, h = img.size
    if max(w, h) / min(w, h) > MAX_ASPECT:
        raise ValueError(f"aspect {w}x{h} too far from square")
    side = min(w, h)
    left, top = (w - side) // 2, (h - side) // 2
    return img.crop((left, top, left + side, top + side)).resize((SQUARE_SIDE, SQUARE_SIDE), Image.Resampling.LANCZOS)


def save_jpeg(img, out_path):
    """Same format and quality for every image; saving fresh drops all metadata."""
    img.save(out_path, "JPEG", quality=JPEG_QUALITY, optimize=True)


def dhash(img, size=8):
    """64-bit difference hash: compares neighbouring pixels of a tiny grayscale copy.
    Near-identical pictures get hashes a few bits apart."""
    small = img.convert("L").resize((size + 1, size), Image.Resampling.LANCZOS)
    px = small.load()
    bits = 0
    for y in range(size):
        for x in range(size):
            bits = (bits << 1) | (px[x, y] > px[x + 1, y])
    return bits


def image_text_words(img):
    """Words Tesseract reads with high confidence (3+ letters, alphabetic only, so
    brush texture rarely counts)."""
    gray = ImageOps.grayscale(img)
    gray = gray.resize((gray.width * OCR_SCALE, gray.height * OCR_SCALE), Image.Resampling.LANCZOS)
    data = pytesseract.image_to_data(gray, config=f"--psm {OCR_PSM}", output_type=pytesseract.Output.DICT)
    return [w for w, c in zip(data["text"], data["conf"])
            if float(c) >= OCR_MIN_CONF and len(w) >= 3 and w.isalpha()]


def image_problem(img, img_hash, hashes):
    """Return why a normalized image fails the image checks, or None. `hashes` maps
    stored image IDs to their dHash."""
    for other_id, other in hashes.items():
        if bin(img_hash ^ other).count("1") <= DUPLICATE_DISTANCE:
            return f"Near-duplicate of {other_id}"
    words = image_text_words(img)
    if len(words) >= OCR_MIN_WORDS:
        return "Contains text: " + " ".join(words[:6])
    return None


# --------------------------------------------------------------------------
# Commands

def ingest(store, candidates, wanted, known, hashes):
    """Save up to `wanted` new images to public/img and return their D1 rows (not
    yet inserted). `hashes` gains each accepted image, so one run can't add two
    copies. Rejections are written in batches, since each Wrangler call takes
    seconds; the finally block flushes the rest."""
    rows, refused = [], []

    def refuse(c, reason):
        refused.append(insert_rejected(c["source_url"], c["title"], reason))
        if len(refused) >= 50:
            store.execute(refused)
            refused.clear()

    try:
        for c in candidates:
            if len(rows) >= wanted:
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
                img = normalize(http_get(c.pop("download_url")))
            except ValueError as err:              # shape rule: permanent, record it
                refuse(c, str(err))
                continue
            except Exception as err:               # network trouble: try again another night
                print(f"  skip (will retry later) {c['source_url']}: {err}")
                continue

            img_hash = dhash(img)
            problem = image_problem(img, img_hash, hashes)
            if problem:
                refuse(c, problem)
                print(f"  - {'AI   ' if c['is_ai'] else 'human'} {c['title'][:50]}  [{problem}]")
                continue

            image_id = secrets.token_hex(8)
            save_jpeg(img, IMG_DIR / f"{image_id}.jpg")
            hashes[image_id] = img_hash
            rows.append(insert_image({**c, "id": image_id, "added_at": int(time.time() * 1000),
                                      "screening": "fixed rules + image checks (automatic)",
                                      "phash": f"{img_hash:016x}"}))
            print(f"  + {'AI   ' if c['is_ai'] else 'human'} {image_id}  {c['title'][:50]}")
    finally:
        store.execute(refused)
    return rows


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


def recheck_images(store):
    """Apply the image checks to the active catalog, oldest first, so of two
    near-duplicates the older one stays. Stores each image's hash."""
    rows = store.query("SELECT id, title, source_url FROM images WHERE active = 1 ORDER BY added_at, rowid")
    kept, updates, hidden = {}, [], []
    for row in rows:
        path = IMG_DIR / f"{row['id']}.jpg"
        if not path.exists():
            print(f"  ! missing file for {row['id']}")
            continue
        img = Image.open(path).convert("RGB")
        img_hash = dhash(img)
        updates.append(f"UPDATE images SET phash = '{img_hash:016x}' WHERE id = {sql(row['id'])};")
        problem = image_problem(img, img_hash, kept)
        if problem:
            hidden.append((row, problem))
        else:
            kept[row["id"]] = img_hash
    store.execute(updates)
    for row, problem in hidden:
        reject(store, row["id"], problem)
    print(f"  checked {len(rows)} images, hid {len(hidden)}")


# The developer's own AI images (see add_own).
OWN_AI_TIER = 3                    # recent generators, chosen to be convincing: hard
OWN_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp"}


def add_own(store, folder, tool, hashes):
    """Prepare the developer's own AI images from `folder` for publishing and return
    their D1 rows (not yet inserted). Each goes through the same normalization and
    image checks as fetched images; the file name (without extension) is the title.
    The reveal's Source link opens the image itself, since there is no outside page."""
    rows = []
    for path in sorted(Path(folder).iterdir()):
        if path.suffix.lower() not in OWN_EXTENSIONS:
            continue
        try:
            img = normalize(path.read_bytes())
        except ValueError as err:
            print(f"  - skip {path.name}: {err}")
            continue
        img_hash = dhash(img)
        problem = image_problem(img, img_hash, hashes)
        if problem:
            print(f"  - skip {path.name}: {problem}")
            continue
        image_id = secrets.token_hex(8)
        save_jpeg(img, IMG_DIR / f"{image_id}.jpg")
        hashes[image_id] = img_hash
        rows.append(insert_image({
            "id": image_id, "is_ai": True, "title": path.stem,
            "artist": "PENCILSLOP developer",
            "source_url": f"{SITE_URL}/img/{image_id}.jpg",
            "license": "Generated for PENCILSLOP by its developer",
            "ai_evidence": f"generated by the developer with {tool}",
            "added_at": int(time.time() * 1000),
            "screening": "developer-provided + image checks",
            "phash": f"{img_hash:016x}", "tier": OWN_AI_TIER,
        }))
        print(f"  + AI    {image_id}  {path.stem}")
    return rows


def set_own_tool(store, tool):
    """Record `tool` as the generator of every developer-provided image."""
    store.execute([f"UPDATE images SET ai_evidence = {sql(f'generated by the developer with {tool}')} "
                   "WHERE screening LIKE 'developer-provided%';"])
    n = store.query("SELECT COUNT(*) AS n FROM images WHERE screening LIKE 'developer-provided%'")[0]["n"]
    print(f"  {n} developer images now credit {tool} in their AI evidence")


def void_rounds(store, id_file):
    """Remove the votes cast by the rounds listed in `id_file` (one ID per line),
    e.g. automated test traffic. Each round's two votes are subtracted from its
    images and the round is marked 'void' (kept, never deleted), so running this
    twice can't subtract twice."""
    ids = [line.strip() for line in Path(id_file).read_text().splitlines() if line.strip()]
    rounds = []
    for i in range(0, len(ids), 50):
        batch = ", ".join(sql(x) for x in ids[i:i + 50])
        rounds += store.query(f"SELECT id, left_id, right_id, choice, left_counted, right_counted FROM rounds WHERE id IN ({batch}) "
                              "AND choice IN ('left', 'right', 'both', 'neither')")
    # Tally per image, as the Worker counted them: judged AI for left/both (left
    # image) and right/both (right image); judged human otherwise.
    minus = {}
    for r in rounds:
        # Repeat votes from the same device were never counted, so skip them.
        sides = []
        if r["left_counted"]:
            sides.append((r["left_id"], r["choice"] in ("left", "both")))
        if r["right_counted"]:
            sides.append((r["right_id"], r["choice"] in ("right", "both")))
        for image_id, judged_ai in sides:
            m = minus.setdefault(image_id, {"ai": 0, "real": 0})
            m["ai" if judged_ai else "real"] += 1
    statements = [
        f"UPDATE images SET votes_ai = MAX(0, votes_ai - {m['ai']}), votes_real = MAX(0, votes_real - {m['real']}) "
        f"WHERE id = {sql(image_id)};" for image_id, m in minus.items()
    ] + [f"UPDATE rounds SET choice = 'void' WHERE id = {sql(r['id'])};" for r in rounds]
    store.execute(statements)
    votes = sum(m["ai"] + m["real"] for m in minus.values())
    print(f"  voided {len(rounds)} of {len(ids)} listed rounds; removed {votes} votes from {len(minus)} images")


def hide_unservable(store):
    """Hide active images that can't be shown. Remote: the live site must serve the
    file (checked over HTTP, so a local checkout that is missing new images can't
    hide them by mistake). Local: the file must exist in public/img. An image whose
    check fails for another reason (network trouble) is kept."""
    rows = store.query("SELECT id, title FROM images WHERE active = 1")

    def servable(row):                     # True, False (definitely gone), or None (unknown)
        if store.target == "local":
            return (IMG_DIR / f"{row['id']}.jpg").exists()
        req = urllib.request.Request(f"{SITE_URL}/img/{row['id']}.jpg", method="HEAD",
                                     headers={"User-Agent": USER_AGENT})
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                return r.headers.get("content-type", "").startswith("image/")
        except urllib.error.HTTPError as err:
            return False if err.code in (404, 410) else None
        except Exception:
            return None

    with ThreadPoolExecutor(8) as ex:
        results = list(ex.map(servable, rows))
    gone = [r for r, ok in zip(rows, results) if ok is False]
    store.execute([f"UPDATE images SET active = 0 WHERE id = {sql(r['id'])};" for r in gone])
    for r in gone:
        print(f"  - hid {r['id']}  {r['title'][:50]}  (image not served)")
    unknown = sum(1 for ok in results if ok is None)
    print(f"  checked {len(rows)} images: {len(gone)} hidden" + (f", {unknown} unreachable (kept)" if unknown else ""))


def retier(store):
    """Set every image's tier by the current rules (human_tier, ai_tier), in
    batches so each source API sees only a few requests."""
    rows = store.query("SELECT id, is_ai, source_url FROM images")
    tiers = {}                                   # image id -> tier
    human = [r for r in rows if not r["is_ai"]]
    ai = [r for r in rows if r["is_ai"]]

    for i in range(0, len(human), 100):
        batch = {r["source_url"].rsplit("/", 1)[1]: r["id"] for r in human[i:i + 100]}
        url = ("https://api.artic.edu/api/v1/artworks?fields=id,is_boosted,has_not_been_viewed_much"
               "&limit=100&ids=" + ",".join(batch))
        for a in json.loads(http_get(url))["data"]:
            if str(a["id"]) in batch:
                tiers[batch[str(a["id"])]] = human_tier(a)

    for i in range(0, len(ai), 50):
        batch = {"File:" + urllib.parse.unquote(r["source_url"].split("/File:")[1]).replace("_", " "): r["id"]
                 for r in ai[i:i + 50]}
        data = commons_query(titles="|".join(batch), prop="imageinfo", iiprop="timestamp")
        # The API normalizes some titles; map them back before matching.
        renamed = {n["to"]: n["from"] for n in data["query"].get("normalized", [])}
        for page in data["query"]["pages"].values():
            image_id = batch.get(renamed.get(page["title"], page["title"]))
            if image_id and "imageinfo" in page:
                tiers[image_id] = ai_tier(page["imageinfo"][0]["timestamp"])

    store.execute([f"UPDATE images SET tier = {t} WHERE id = {sql(i)};" for i, t in tiers.items()])
    print(f"  set tiers on {len(tiers)} images; {len(rows) - len(tiers)} not found at their source (unchanged)")


def publish(store, target, rows):
    """Remote: commit, push and deploy the new image files, then insert their rows,
    so a round can never point at an image the live site doesn't have."""
    if not rows:
        return
    if target == "remote":
        print(f"Publishing {len(rows)} images (git push, deploy)...", flush=True)
        store.publish_images(len(rows))
    store.execute(rows)                        # rows last: images are already live


def main():
    ap = argparse.ArgumentParser()
    where = ap.add_mutually_exclusive_group(required=True)
    where.add_argument("--local", action="store_const", const="local", dest="target")
    where.add_argument("--remote", action="store_const", const="remote", dest="target")
    ap.add_argument("--human", type=int, default=0, help="add up to N new human artworks")
    ap.add_argument("--ai", type=int, default=0, help="add up to N new AI images")
    ap.add_argument("--reject", nargs=2, metavar=("ID", "REASON"))
    ap.add_argument("--recheck-ai", action="store_true")
    ap.add_argument("--recheck-images", action="store_true")
    ap.add_argument("--retier", action="store_true", help="set tiers on existing images by the rules")
    ap.add_argument("--check-files", action="store_true", help="hide active images the site can't serve")
    ap.add_argument("--add-own", metavar="DIR", help="add the developer's own AI images from DIR")
    ap.add_argument("--set-own-tool", metavar="NAME", help="generator recorded for the developer's own images")
    ap.add_argument("--void-rounds", metavar="FILE", help="remove the votes of the round IDs listed in FILE")
    ap.add_argument("--tool", default="an unnamed AI generator", help="generator used for --add-own")
    args = ap.parse_args()

    sys.stdout.reconfigure(encoding="utf-8")  # titles include non-Latin text
    IMG_DIR.mkdir(parents=True, exist_ok=True)
    store = Store(args.target)

    if args.reject:
        reject(store, *args.reject)
    elif args.recheck_ai:
        recheck_ai(store)
    elif args.recheck_images:
        recheck_images(store)
    elif args.retier:
        retier(store)
    elif args.check_files:
        hide_unservable(store)
    elif args.void_rounds:
        void_rounds(store, args.void_rounds)
    elif args.set_own_tool:
        set_own_tool(store, args.set_own_tool)
    elif args.add_own:
        print("Developer's own AI images:")
        publish(store, args.target, add_own(store, args.add_own, args.tool, store.stored_hashes()))
    else:
        print("Checking that every active image is served:")
        hide_unservable(store)
        known, hashes = store.known_sources(), store.stored_hashes()
        print("Human art (Art Institute of Chicago):")
        rows = ingest(store, human_candidates(), args.human, known, hashes)
        print("AI art (Wikimedia Commons):")
        rows += ingest(store, ai_candidates(), args.ai, known, hashes)
        publish(store, args.target, rows)

    human, ai = store.counts()
    print(f"Active catalog: {human} human, {ai} AI")


if __name__ == "__main__":
    main()
