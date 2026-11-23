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
PENDING_GAP = 900
WORK, OUT = Path("work"), Path("public")
IMG_DIR, VID_DIR = OUT / "images", OUT / "videos"
MAX_VIDEO = 50 * 1024 * 1024
import re, datetime as dt
from zoneinfo import ZoneInfo
IST = ZoneInfo("Asia/Kolkata")

LINE_RE = re.compile(r"^\[?(\d{1,2}/\d{1,2}/\d{2,4}),?\s+(\d{1,2}:\d{2}(?::\d{2})?(?:\s*[AaPp][Mm])?)\]?\s*-?\s*([^:]{1,60}?):\s?(.*)$")
ATT_RE = re.compile(r"([\w\-\. ]+?\.(?:jpe?g|png|webp|mp4|3gp|mov|opus|pdf))\s*\(file attached\)|<attached:\s*([^>\s]+)>", re.I)
MEDIA_OMIT = re.compile(r"<?media omitted>?|(?:image|video|sticker|audio|gif) omitted|This message was deleted|<This message was edited>", re.I)
STRIKE_RE = re.compile(r"~[^~\n]*~")
BLOCK_LINE = re.compile(r"(^WITH MIYANI$|^\W*WITH ALL ACCESSORIES|SINGLES\s*&|MULTIPLE|LOOK LIKE|WITH ALL ACCESSORIES|NOTE\s*:|\bLESS\b|^\W*\d+\s*LESS|\bBOOK FAST|READY TO SHIP|\bDEAL OF THE\b|\bSELL\b|\bFINAL RATE\b|NO ANY LESS|LAUNCH|PRESENTING|ALHUMDULILAH|NEW DESIGNS?|STOCK|READY|NEW COLOUR|COLOUR & DESIGN|PRESEND|PRESENT|BEAUTIFUL COLOURS|BEAUTIFUL DESIGN|SUPERHIT|RESTOCK|@|https?:|www\.|whatsapp|\+?\d[\d\s-]{8,}\d|\baddress\b|\bstock (in|out|over)\b|community members|changed\. Tap)", re.I)
HDR_RE = re.compile(r"^(SIZES?|FABRICS?|NOTE|QTY|DUPATTA|SINGLES?|NOW SINGLES?|ACTUAL RATE|NETT RATE)\s*[:\-]*\s*$", re.I)
PRICE_NOW = re.compile(r"now\s+singles?\s*[:@\-=]*\s*(?:₹|rs\.?)?\s*(\d[\d,]{2,6})", re.I)
PRICE_SGL = re.compile(r"(?<!now )singles?\s*[:@\-=]*\s*(?:₹|rs\.?)?\s*(\d[\d,]{2,6})", re.I)
SIZE_LINE = re.compile(r"^(3XL|4XL|5XL|XXL|XL|XS|S|M|L)\s*\(\s*\d{2,3}\s*\)$", re.I)
SIZE_ORDER = ["XS", "S", "M", "L", "XL", "XXL", "3XL", "4XL", "5XL"]
SIZE_TOK = re.compile(r"\b(3XL|4XL|5XL|XXL|XL|XS|S|M|L)\b(?:\s*\(\s*\d{2,3}\s*\))?", re.I)

def parse_dt(d, t):
    s = re.sub(r"\s+", " ", f"{d} {t}").strip()
    for fmt in ("%d/%m/%Y %H:%M:%S","%d/%m/%Y %H:%M","%d/%m/%y %H:%M:%S","%d/%m/%y %H:%M",
                "%m/%d/%y %I:%M %p","%m/%d/%y %I:%M:%S %p","%m/%d/%Y %I:%M %p","%m/%d/%Y %H:%M","%m/%d/%y %H:%M"):
        try: return dt.datetime.strptime(s, fmt).replace(tzinfo=IST)
        except ValueError: pass
    return None

def parse_chat(text):
    msgs = []
    for raw in text.splitlines():
        line = raw.replace("\u200e","").replace("\u202f"," ").replace("\u00a0"," ")
        m = LINE_RE.match(line)
        when = parse_dt(m.group(1), m.group(2)) if m else None
        if m and when:
            msgs.append({"time": when, "sender": m.group(3).strip(), "text": m.group(4)})
        elif msgs:
            msgs[-1]["text"] += "\n" + line
    return msgs

def clean_text(t):
    files = [(m.group(1) or m.group(2)).strip() for m in ATT_RE.finditer(t)]
    files = [m.group(1).strip() for m in ATT_RE.finditer(t)]
    t = ATT_RE.sub("", t)
    t = MEDIA_OMIT.sub("", t)
    t = STRIKE_RE.sub("", t)
    return files, t

def lines_of(t):
    out = []
    for l in t.splitlines():
        s = l.replace("*","").replace("_","").strip()
        if not s or BLOCK_LINE.search(s):
            continue
        out.append(s)
    return out

def parse_size(text):
    """Return list of sizes or ['FREE SIZE'] or [] if none stated in text."""
    if re.search(r"free\s*size", text, re.I):
        return ["FREE SIZE"]
    found = set()
    rng = re.search(r"\b(XS|S|M|L|XL)\s*(?:TO|-|–)\s*(3XL|XXL|XL|4XL|5XL)\b", text, re.I)
    if rng:
        a, b = rng.group(1).upper(), rng.group(2).upper()
        ia, ib = SIZE_ORDER.index(a), SIZE_ORDER.index(b)
        for k in SIZE_ORDER[ia:ib+1]: found.add(k)
    lines = text.splitlines()
    for i, l in enumerate(lines):
        if re.search(r"size", l, re.I):
            chunk = [l]
            for nxt in lines[i+1:i+4]:
                if not nxt.strip(): break
                chunk.append(nxt)
            for c in chunk:
                for m in SIZE_TOK.finditer(c.upper()):
                    found.add(m.group(1).upper())
    if not found:
        return []
    return sorted(found, key=lambda s: SIZE_ORDER.index(s))

def make_product(text, sender):
    t = text
    price = None
    m = PRICE_NOW.search(t) or PRICE_SGL.search(t)
    if m:
        price = int(m.group(1).replace(",", ""))
    if price is None or not (10 <= price <= 500000):
        return None, "price nahi"
    lines = lines_of(t)
    name = None
    bold = [x.strip().strip("*").strip() for x in t.splitlines() if x.strip().startswith("*") and x.strip().endswith("*")]
    for c in bold:
        if c and not HDR_RE.match(c) and ":" not in c and 4 <= len(c) <= 80 and not BLOCK_LINE.search(c) and not PRICE_SGL.search(c) and not SIZE_LINE.match(c) and not PRICE_NOW.search(c):
            name = c; break
    for l in ([] if name else lines):
        c = l.strip()
        if HDR_RE.match(c) or SIZE_LINE.match(c) or ":" in c or len(c) > 80 or len(c) < 4:
            continue
        name = c; break
    if not name:
        return None, "naam nahi"
    desc = [l for l in lines if l != name and not HDR_RE.match(l) and not re.fullmatch(r"(3XL|4XL|5XL|XXL|XL|XS|S|M|L)\s*\(\s*\d{2,3}\s*\)", l.strip(), re.I)
            and not PRICE_NOW.search(l) and not PRICE_SGL.search(l)]
    return {"name": name, "price": price, "sizes": parse_size(t),
            "description": "\n".join(desc)[:1000]}, None
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


def save_media(names, index):
    images, videos = [], []
    for n in names:
        p = index.get(n)
        if p is None:
            low = {k.lower().strip(): k for k in index}
            near = [k for k in index if k[-12:-4] in n][:3]
            print("NOT FOUND:", repr(n), "| near:", near, flush=True)
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
            elif ext == ".mp4" and p.stat().st_size <= MAX_VIDEO:
                out = VID_DIR / f"{file_hash(p)}.mp4"
                if not out.exists():
                    shutil.copyfile(p, out)
                if f"videos/{out.name}" not in videos:
                    videos.append(f"videos/{out.name}")
        except Exception as e:
            print("media skip:", n, e, flush=True)
    return images, videos


def build(msgs, index, now):
    """Admin ka pattern: pehle photo/video, phir description. Media ko agle product text se jodta hai,
    agar agla na mile to pichhle se. GIF (update wala) ignore hota hai."""
    cutoff = now - dt.timedelta(days=DAYS)
    items, media, skipped = [], [], []
    for m in msgs:
        files, text = clean_text(m["text"])
        files = [f for f in files if not f.upper().startswith("GIF")]
        if not text.strip():
            if files:
                media.append((m["time"], m["sender"], files))
            continue
        prod, err = make_product(text, m["sender"])
        if prod and m["time"] >= cutoff:
            items.append({"time": m["time"], "sender": m["sender"], "prod": prod,
                          "names": list(files)})
    for t, s, fl in media:
        gap = lambda it: (it["time"] - t).total_seconds()
        after = [it for it in items if it["sender"] == s and 0 <= gap(it) <= PENDING_GAP]
        before = [it for it in items if it["sender"] == s and -PENDING_GAP <= gap(it) < 0]
        if after:
            min(after, key=gap)["names"] += fl
        elif before:
            max(before, key=gap)["names"] += fl
    best_map = {}
    for it in items:
        prod = it["prod"]
        images, videos = save_media(it["names"], index)
        if not images:
            near = [(x["time"].strftime("%d %H:%M"), x["sender"][-4:], x["text"][:50].replace("\n", " "))
                    for x in msgs if abs((x["time"] - it["time"]).total_seconds()) <= 900][:8]
            print("NEAR", prod["name"][:30], near, flush=True)
            skipped.append(f"photo nahi ({it['time']:%d-%m}): " + prod["name"][:60])
            continue
        mg = margin_for(prod["price"])
        if mg is None:
            skipped.append(f"slab se bahar ({prod['price']}): " + prod["name"][:60])
            continue
        key = (prod["name"].lower(), prod["description"].lower(), images[0])
        if key in best_map and best_map[key][0] >= it["time"]:
            continue
        pid = "p" + hashlib.sha1("|".join(key).encode("utf-8")).hexdigest()[:8]
        best_map[key] = (it["time"], {
            "id": pid, "title": prod["name"], "raw": prod["name"], "text": prod["description"],
            "price": prod["price"] + mg, "date": it["time"].isoformat(), "category": "", "crop": "",
            "images": images, "videos": videos, "sizes": prod["sizes"]})
    return [v[1] for v in best_map.values()], skipped


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
                              int(os.environ["TG_API_ID"]), os.environ["TG_API_HASH"]) as client:
        async for msg in client.iter_messages(chat, limit=20):
            f = msg.file
            if f and f.name and f.name.lower().endswith(".zip"):
                path = WORK / "chat.zip"
                total, got, last = f.size, 0, 0
                print("download shuru:", total // 1048576, "MB", flush=True)
                with open(path, "wb") as out:
                    async for chunk in client.iter_download(msg.media, request_size=1024 * 1024):
                        out.write(chunk)
                        got += len(chunk)
                        if got - last >= 20 * 1024 * 1024:
                            print(f"download: {got // 1048576} MB / {total // 1048576} MB", flush=True)
                            last = got
                print("download poora hua", flush=True)
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
    index = {p.name: p for p in ex.rglob("*") if p.is_file()}
    text = txts[0].read_text(encoding="utf-8", errors="ignore")
    msgs = parse_chat(text)
    print("chat range:", msgs[0]["time"], "to", msgs[-1]["time"], "| messages:", len(msgs), flush=True)
    print("zip files:", len(index), flush=True)
    print("attached lines:", [l for l in text.splitlines() if "attached" in l.lower()][:5], flush=True)
    print("attached count:", sum("attached" in l.lower() for l in text.splitlines()), flush=True)
    print("zip media sample:", [n for n in index if n.lower().endswith((".jpg", ".jpeg", ".png", ".webp", ".mp4"))][:8], flush=True)
    products, skipped = build(parse_chat(text), index, now)
    write_site(products, now)
    print(f"products: {len(products)}, skipped: {len(skipped)}")
    for s in skipped[:80]:
        print(" -", s)
    shutil.rmtree(WORK, ignore_errors=True)


if __name__ == "__main__":
    main()
