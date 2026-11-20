import os, re, sys, json, zipfile, hashlib, asyncio, shutil
import datetime as dt
from pathlib import Path
from zoneinfo import ZoneInfo

from PIL import Image
from telethon import TelegramClient
from telethon.sessions import StringSession

IST = ZoneInfo("Asia/Kolkata")
SETTINGS = json.loads(Path("settings.json").read_text(encoding="utf-8"))
DAYS = int(SETTINGS.get("days", 15))
SLABS = sorted(SETTINGS["slabs"], key=lambda s: s["upto"])
EXTRA_BLOCK = [w.lower() for w in SETTINGS.get("block_words", []) if w]
GROUP_GAP = 120
WORK = Path("work")
OUT = Path("public")
IMG_DIR = OUT / "images"
VID_DIR = OUT / "videos"
MAX_VIDEO = 15 * 1024 * 1024

BLOCK_WORDS = ["stock in", "stock out", "stock over", "address", "whatsapp",
               "telegram", "t.me", "@", "http", "www."] + EXTRA_BLOCK
PHONE_RE = re.compile(r"\+?\d[\d\s-]{8,}\d")
LINE_RE = re.compile(
    r"^\[?(\d{1,2}/\d{1,2}/\d{2,4}),?\s+(\d{1,2}:\d{2}(?::\d{2})?(?:\s*[AaPp][Mm])?)\]?\s*-?\s*([^:]{1,60}?):\s?(.*)$"
)
ATT_RE = re.compile(r"([\w\-]+\.(?:jpe?g|png|webp|mp4|3gp|mov))\s*\(file attached\)|<attached:\s*([^>]+)>", re.I)
OMIT_RE = re.compile(r"<?media omitted>?|(?:image|video|sticker|audio|gif) omitted", re.I)
SKIP_PREFIX = ("STK-", "AUD-", "PTT-", "DOC-")
PRICE_CUT = re.compile(r"\s*[-–|:]?\s*(?:₹|\brs\b|\binr\b|\bprice\b|\brate\b|\bmrp\b)", re.I)
PRICE_PATTERNS = [
    re.compile(r"(?:₹|\brs\.?|\binr\b)\s*([\d,]{2,7})", re.I),
    re.compile(r"\b(?:price|rate|mrp|cost)\s*[:\-=]?\s*([\d,]{2,7})", re.I),
    re.compile(r"(?<![\d.,])(\d{2,6})(?![\d.,]*\s*(?:pcs|pc|pieces|piece|mtr|meters|meter|m|cm|inch|in|ml|gm|g|kg|pack|sets|set|ft|feet)\b)", re.I),
]


def parse_dt(d, t):
    s = re.sub(r"\s+", " ", f"{d} {t}").strip()
    for fmt in ("%d/%m/%Y %H:%M:%S", "%d/%m/%Y %H:%M", "%d/%m/%y %H:%M:%S", "%d/%m/%y %H:%M",
                "%d/%m/%Y %I:%M:%S %p", "%d/%m/%Y %I:%M %p", "%d/%m/%y %I:%M:%S %p", "%d/%m/%y %I:%M %p"):
        try:
            return dt.datetime.strptime(s, fmt).replace(tzinfo=IST)
        except ValueError:
            pass
    return None


def parse_chat(text):
    msgs = []
    for raw in text.splitlines():
        line = raw.replace("\u200e", "").replace("\u202f", " ").replace("\u00a0", " ")
        m = LINE_RE.match(line)
        when = parse_dt(m.group(1), m.group(2)) if m else None
        if m and when:
            msgs.append({"time": when, "sender": m.group(3).strip(), "text": m.group(4)})
        elif msgs:
            msgs[-1]["text"] += "\n" + line
    return msgs


def split_media(text):
    files = []
    for m in ATT_RE.finditer(text):
        name = (m.group(1) or m.group(2) or "").strip()
        if name and not name.upper().startswith(SKIP_PREFIX):
            files.append(name)
    clean = OMIT_RE.sub("", ATT_RE.sub("", text)).strip()
    return files, clean


def group_messages(msgs):
    groups, cur = [], None
    for m in msgs:
        files, text = split_media(m["text"])
        if not files and not text:
            continue
        same = (cur is not None and cur["sender"] == m["sender"]
                and (m["time"] - cur["last"]).total_seconds() <= GROUP_GAP)
        if same and (not text or not cur["text"]):
            cur["files"] += files
            cur["text"] = cur["text"] or text
            cur["last"] = m["time"]
        else:
            cur = {"sender": m["sender"], "text": text, "files": files,
                   "first": m["time"], "last": m["time"]}
            groups.append(cur)
    return groups


def clean_lines(text):
    out = []
    for l in text.splitlines():
        l = l.strip().strip("*•-–_~ ").strip()
        if l:
            out.append(l)
    return out


def make_product(g):
    lines = clean_lines(g["text"])
    if not lines:
        return None
    sender_re = re.compile(re.escape(g["sender"]), re.I) if g["sender"] else None

    def scrub(s):
        return sender_re.sub("", s).strip(" -–:|*") if sender_re else s.strip()

    name = scrub(PRICE_CUT.split(lines[0])[0])[:120]
    if not name and len(lines) > 1:
        name = scrub(lines[1])[:120]
    if not name:
        return None

    desc = []
    for l in lines[1:]:
        low = l.lower()
        if any(w in low for w in BLOCK_WORDS):
            continue
        if PHONE_RE.search(l):
            continue
        if sender_re and sender_re.search(l):
            continue
        desc.append(l)
    return {"name": name, "description": "\n".join(desc)[:1000]}


def parse_price(text):
    for p in PRICE_PATTERNS:
        for m in p.finditer(text):
            val = int(m.group(1).replace(",", ""))
            if 10 <= val <= 500000:
                return val
    return None


def margin_for(w):
    for s in SLABS:
        if w <= s["upto"]:
            return s["add"]
    return None
def file_hash(p):
    h = hashlib.sha1()
    with open(p, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()[:12]


def save_media(names, src_dir):
    images, video = [], None
    for n in names:
        p = src_dir / n
        if not p.exists():
            continue
        ext = p.suffix.lower()
        try:
            if ext in (".jpg", ".jpeg", ".png", ".webp"):
                out = IMG_DIR / f"{file_hash(p)}.jpg"
                if not out.exists():
                    with Image.open(p) as im:
                        im = im.convert("RGB")
                        im.thumbnail((900, 900))
                        im.save(out, "JPEG", quality=80, optimize=True)
                images.append(f"images/{out.name}")
            elif ext == ".mp4" and video is None and p.stat().st_size <= MAX_VIDEO:
                out = VID_DIR / f"{file_hash(p)}.mp4"
                if not out.exists():
                    shutil.copyfile(p, out)
                video = f"videos/{out.name}"
        except Exception as e:
            print("media skip:", n, e)
    return images, video


def build(groups, src_dir, now):
    cutoff = now - dt.timedelta(days=DAYS)
    best, skipped = {}, []
    for g in groups:
        if g["last"] < cutoff:
            continue
        info = make_product(g)
        if not info:
            skipped.append("naam nahi mila")
            continue
        wp = parse_price(g["text"])
        if wp is None:
            skipped.append("price nahi: " + info["name"][:60])
            continue
        mg = margin_for(wp)
        if mg is None:
            skipped.append(f"slab se bahar ({wp}): " + info["name"][:60])
            continue
        images, video = save_media(g["files"], src_dir)
        key = (info["name"].lower(), info["description"].lower(), images[0] if images else "")
        if key in best and best[key][0] >= g["last"]:
            continue
        pid = "p" + hashlib.sha1("|".join(key).encode("utf-8")).hexdigest()[:8]
        prod = {"id": pid, "title": info["name"], "raw": info["name"],
                "text": info["description"], "price": wp + mg,
                "date": g["last"].isoformat(), "category": "", "crop": "",
                "images": images or [""], "video": video}
        best[key] = (g["last"], prod)
    products = [v[1] for v in best.values()]
    return products, skipped


def write_site(products, now):
    products.sort(key=lambda p: p["date"], reverse=True)
    compact = [{"i": p["id"], "t": p["title"], "p": p["price"], "d": p["date"],
                "k": p["text"], "m": p["images"][0]} for p in products]
    (OUT / "first.json").write_text(json.dumps(compact[:40], ensure_ascii=False), encoding="utf-8")
    (OUT / "light.json").write_text(json.dumps(compact, ensure_ascii=False), encoding="utf-8")
    buckets = {c: [] for c in "0123456789abcdef"}
    for p in products:
        buckets[p["id"][1]].append(p)
    for c, arr in buckets.items():
        (OUT / "p" / f"{c}.json").write_text(json.dumps(arr, ensure_ascii=False), encoding="utf-8")
    cfg = {"shop_name": SETTINGS["shop_name"], "whatsapp": SETTINGS.get("whatsapp", ""),
           "order_url": SETTINGS.get("order_url", ""), "v": str(int(now.timestamp()))}
    (OUT / "config.json").write_text(json.dumps(cfg, ensure_ascii=False), encoding="utf-8")


async def fetch_zip():
    chat = int(os.environ["TG_ZIP_CHANNEL"])
    async with TelegramClient(StringSession(os.environ["TG_SESSION"]),
                              int(os.environ["TG_API_ID"]),
                              os.environ["TG_API_HASH"]) as client:
        async for msg in client.iter_messages(chat, limit=20):
            f = msg.file
            if f and f.name and f.name.lower().endswith(".zip"):
                path = WORK / "chat.zip"
                print("zip mili:", f.name, f.size, "bytes", flush=True)
                await client.download_media(msg, file=str(path))
                return path
    return None


def main():
    now = dt.datetime.now(IST)
    for d in (WORK, IMG_DIR, VID_DIR, OUT / "p"):
        d.mkdir(parents=True, exist_ok=True)

    zip_path = asyncio.run(fetch_zip())
    if zip_path is None:
        print("Channel mein koi zip nahi mili")
        sys.exit(1)

    ex = WORK / "chat"
    with zipfile.ZipFile(zip_path) as z:
        z.extractall(ex)
    txts = sorted(ex.rglob("*.txt"), key=lambda p: p.stat().st_size, reverse=True)
    if not txts:
        print("zip mein chat ki txt file nahi mili")
        sys.exit(1)

    text = txts[0].read_text(encoding="utf-8", errors="ignore")
    groups = group_messages(parse_chat(text))
    products, skipped = build(groups, txts[0].parent, now)
    write_site(products, now)
    print(f"products: {len(products)}, skipped: {len(skipped)}")
    for s in skipped[:80]:
        print(" -", s)
    shutil.rmtree(WORK, ignore_errors=True)


if __name__ == "__main__":
    main()
