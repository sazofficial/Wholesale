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

LABEL_BOLD = re.compile(r"^(FABRICS?|SIZES?|SINGLES?|NOTE|BOOK|READY|NO GST|SELL|DEAL|NEW DESIGNS|LOOK LIKE|PRESENTING|ALHUMDULILAH|FULL STOCK|RESTOCK|SUPERHIT DESIGNS|LENTH|DUPATTA|SHOP|ASSALAM|HIT DESIGN|SINGLES & MULTIPLE)", re.I)
MARKETING = re.compile(r"LAUNCHING|BEAUTIFUL DESIGN|PRESENTING|ALHUMDULILAH|NEW LAUNCH|NO GST|BOOK FAST|READY TO SHIP|NOTE\s*:|WITH ALL ACCESSORIES|WITH MIYANI|SIZES? AVL|MENTION ON PIC", re.I)
VARIANT_RE = re.compile(r"^([A-Z0-9][A-Z0-9.\-& ]{0,19}?)\s*:+\s*-?\s*(\d{3,5})\s*/?-?\s*$", re.I)
PRODUCT_LIKE = re.compile(r"fabric|sizes?\b|singles?|rate|mention on pic|book fast|ready to ship|d\s*no", re.I)
EMOJI_RE = re.compile("[\U0001F000-\U0001FFFF\u2600-\u27BF\u2B00-\u2BFF\uFE0F\u200d\u2122\u00A9\u00AE]")
RATE_RE = re.compile(r"\brate\s*[:\-=]*\s*(?:₹|rs\.?)?\s*-?\s*(\d[\d,]{2,6})", re.I)
SIZE_ALIAS = {"XXXL": "3XL", "XXXXL": "4XL"}


def clean_line(l):
    return EMOJI_RE.sub("", l).replace("*", "").replace("_", "").strip(" -–:|")


def make_product(text, sender):
    """Return (list_of_products, error). Har variant (jaise 246 :- 1750/-) ek alag product banta hai."""
    raw_lines = [x.strip() for x in text.splitlines()]
    variants = []
    for l in raw_lines:
        mv = VARIANT_RE.match(l.replace("*", "").strip())
        if mv and not LABEL_BOLD.match(mv.group(1).strip()):
            variants.append((mv.group(1).strip(), int(mv.group(2))))
    single = PRICE_NOW.search(text) or PRICE_SGL.search(text) or RATE_RE.search(text)
    if not variants and single:
        variants = [(None, int(single.group(1).replace(",", "")))]
    variants = [(c, p) for c, p in variants if 10 <= p <= 500000]
    if not variants:
        return None, "price nahi"
    name = None
    dno = re.search(r"d\.?\s*no\s*[:\-]*\s*\*?\s*([A-Za-z0-9\-\. ]{2,20})", text, re.I)
    for x in raw_lines:
        cx = clean_line(x)
        if x.strip().startswith("*") and cx and not cx.startswith("("):
            c = cx
            if c and not LABEL_BOLD.match(c) and not re.search(r"DELIVERY|RATE|READY TO|BOOK|LIMITED|STOCK|LIST", c, re.I) and ":" not in c and 4 <= len(c) <= 80 and not PRICE_SGL.search(c):
                if re.search(r"\d", c):
                    name = c
                    break
                name = name or c
    if not name:
        for l in lines_of(text):
            c = l.strip()
            if HDR_RE.match(c) or SIZE_LINE.match(c) or ":" in c or len(c) > 80 or len(c) < 4 or MARKETING.search(c):
                continue
            name = c
            break
    if not name:
        return None, "naam nahi"
    desc = []
    for l in lines_of(text):
        if l == name or HDR_RE.match(l) or MARKETING.search(l):
            continue
        if re.fullmatch(r"(3XL|4XL|5XL|XXL|XL|XS|S|M|L)\s*\(\s*\d{2,3}\s*\)", l.strip(), re.I):
            continue
        if PRICE_NOW.search(l) or PRICE_SGL.search(l) or VARIANT_RE.match(l.replace("*", "").strip()):
            continue
        desc.append(l)
    desc_txt = "\n".join(desc)[:1000]
    sizes = [SIZE_ALIAS.get(z, z) for z in parse_size(text)]
    if dno and name and "D" not in name.upper().split():
        name = f"{name} D.NO {dno.group(1).strip()}"[:140]
    out = []
    for code, price in variants:
        title = name if code is None else f"{name} - {code}"
        out.append({"name": title[:140], "price": price, "sizes": sizes,
                    "description": desc_txt, "code": code or ""})
    return out, None
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


TIME_WINDOW = 120


def build(msgs, index, now):
    """Media ko product text se TIME se jodta hai: +-2 minute ke andar, sabse nazdeek wala product."""
    cutoff = now - dt.timedelta(days=DAYS)
    items, media, skipped = [], [], []
    for m in msgs:
        files, text = clean_text(m["text"])
        files = [f for f in files if not f.upper().startswith("GIF")]
        if not text.strip():
            if files:
                media.append((m["time"], files))
            continue
        if not PRODUCT_LIKE.search(text):
            continue
        if m["time"] < cutoff:
            continue
        prods, err = make_product(text, m["sender"])
        if not prods:
            skipped.append(f"{err} ({m['time']:%d-%m %H:%M}): " + text.strip().splitlines()[0][:50])
            continue
        for prod in prods:
            items.append({"time": m["time"], "prod": prod, "names": list(files)})
    for t, fl in media:
        near = [it for it in items if abs((it["time"] - t).total_seconds()) <= TIME_WINDOW]
        if not near:
            continue
        best = min(near, key=lambda it: (abs((it["time"] - t).total_seconds()),
                                         (it["time"] - t).total_seconds() < 0))
        best["names"] += fl
    best_map = {}
    for it in items:
        prod = it["prod"]
        images, videos = save_media(it["names"], index)
        if not images:
            skipped.append(f"photo nahi ({it['time']:%d-%m %H:%M}): " + prod["name"][:60])
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
            "images": images, "videos": videos, "sizes": prod["sizes"], "_key": norm_key(prod)})
    return [v[1] for v in best_map.values()], skipped


def norm_key(prod):
    """Cross-group dedupe ke liye: naam + price + description, sirf letters/digits."""
    f = lambda x: re.sub(r"[^a-z0-9]", "", x.lower())
    return f(prod["name"]) + "|" + str(prod["price"]) + "|" + f(prod["description"])


def merge_groups(list_of_product_lists):
    """Pehle group ke products jeetenge. Baad wale group me exact match hua to upload nahi."""
    seen, out = set(), []
    for plist in list_of_product_lists:
        for p in plist:
            k = p.pop("_key", None) or p["title"]
            if k in seen:
                continue
            seen.add(k)
            out.append(p)
    return out


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


def group_key(name):
    n = name.lower()
    for o in SETTINGS.get("group_order", []):
        if o.lower() in n:
            return o.lower()
    return re.sub(r"\(\d+\)|\.zip$", "", n).strip()


async def fetch_latest_per_group(chat_id):
    """Channel ki har group ki sabse nayi zip. Purani zip (same group ka) skip."""
    chat = int(chat_id)
    got = {}
    async with TelegramClient(StringSession(os.environ["TG_SESSION"]),
                              int(os.environ["TG_API_ID"]), os.environ["TG_API_HASH"]) as client:
        async for msg in client.iter_messages(chat, limit=60):
            f = msg.file
            if not (f and f.name and f.name.lower().endswith(".zip")):
                continue
            k = group_key(f.name)
            if k in got:
                continue
            path = WORK / f"zip{len(got)}.zip"
            total, read, last = f.size, 0, 0
            print("download shuru:", f.name, total // 1048576, "MB", flush=True)
            with open(path, "wb") as out:
                async for chunk in client.iter_download(msg.media, request_size=1024 * 1024):
                    out.write(chunk)
                    read += len(chunk)
                    if read - last >= 50 * 1024 * 1024:
                        print(f"download: {read // 1048576} MB / {total // 1048576} MB", flush=True)
                        last = read
            print("download poora hua:", f.name, flush=True)
            got[k] = (f.name, path)
    return list(got.values())


def process_zip(zip_path, now, tag):
    ex = WORK / tag
    with zipfile.ZipFile(zip_path) as z:
        z.extractall(ex)
    txts = sorted(ex.rglob("*.txt"), key=lambda p: p.stat().st_size, reverse=True)
    if not txts:
        print("txt nahi mili:", tag)
        return [], []
    index = {p.name: p for p in ex.rglob("*") if p.is_file()}
    text = txts[0].read_text(encoding="utf-8", errors="ignore")
    msgs = parse_chat(text)
    print(f"{tag} chat range:", msgs[0]["time"], "to", msgs[-1]["time"], "| messages:", len(msgs), flush=True)
    return build(msgs, index, now)


def main():
    now = dt.datetime.now(IST)
    for d in (WORK, IMG_DIR, VID_DIR, OUT / "p"):
        d.mkdir(parents=True, exist_ok=True)
    chans = [c for c in (os.environ.get("TG_ZIP_CHANNEL"), os.environ.get("TG_ZIP_CHANNEL_2")) if c]
    if not chans:
        print("koi channel set nahi hai")
        sys.exit(1)
    lists, all_skipped = [], []
    for i, c in enumerate(chans):
        tag = f"group{i + 1}"
        zp = asyncio.run(fetch_zip(c, f"{tag}.zip"))
        if zp is None:
            print("zip nahi mili:", tag)
            continue
        ps, sk = process_zip(zp, now, tag)
        print(f"{tag} products:", len(ps), "skipped:", len(sk), flush=True)
        lists.append(ps)
        all_skipped += sk
    products = merge_groups(lists)
    write_site(products, now)
    print(f"TOTAL products: {len(products)}, skipped: {len(all_skipped)}")
    for x in all_skipped[:80]:
        print(" -", x)
    shutil.rmtree(WORK, ignore_errors=True)

if __name__ == "__main__":
    main()
