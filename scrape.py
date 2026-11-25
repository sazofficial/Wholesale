import os, re, sys, json, zipfile, hashlib, asyncio, shutil, time, traceback
import urllib.request
import html as _html
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
TIME_WINDOW = 120
MAX_VIDEO = 50 * 1024 * 1024
CHUNK = 1024 * 1024
BUDGET = 19 * 60
DL_BUDGET = 9 * 60
DL_DEADLINE = None
DEADLINE = None
WORK, OUT = Path("work"), Path("public")
STORE = Path("store")
ZIPS = STORE / "zips"
PRODS = STORE / "products"
STATE_F = STORE / "state.json"
CHANGED = STORE / "changed.flag"
IMG_DIR = STORE / "images"
VID_DIR = STORE / "videos"

# Libas public Store API
LIBAS_API = "https://thelibas.com/wp-json/wc/store/v1/products"
LIBAS_PAGES = 5
LIBAS_TIME_LIMIT = 300


class OutOfTime(Exception):
    pass


LINE_RE = re.compile(r"^\[?(\d{1,2}/\d{1,2}/\d{2,4}),?\s+(\d{1,2}:\d{2}(?::\d{2})?(?:\s*[AaPp][Mm])?)\]?\s*-?\s*([^:]{1,60}?):\s?(.*)$")
ATT_RE = re.compile(r"([\w\-\. ]+?\.(?:jpe?g|png|webp|mp4|3gp|mov|opus|pdf))\s*\(file attached\)", re.I)
MEDIA_OMIT = re.compile(r"<?media omitted>?|(?:image|video|sticker|audio|gif) omitted|This message was deleted|<This message was edited>", re.I)
STRIKE_RE = re.compile(r"~[^~\n]*~")
BLOCK_LINE = re.compile(r"(^WITH MIYANI$|^\W*WITH ALL ACCESSORIES|SINGLES\s*&|MULTIPLE|LOOK LIKE|WITH ALL ACCESSORIES|NOTE\s*:|\bLESS\b|^\W*\d+\s*LESS|\bBOOK FAST|READY TO SHIP|\bDEAL OF THE\b|\bSELL\b|\bFINAL RATE\b|NO ANY LESS|LAUNCH|PRESENTING|ALHUMDULILAH|NEW DESIGNS?|STOCK|READY|NEW COLOUR|COLOUR & DESIGN|PRESEND|PRESENT|BEAUTIFUL COLOURS|BEAUTIFUL DESIGN|SUPERHIT|RESTOCK|@|https?:|www\.|whatsapp|\+?\d[\d\s-]{8,}\d|\baddress\b|\bstock (in|out|over)\b|community members|changed\. Tap)", re.I)
HDR_RE = re.compile(r"^(SIZES?|FABRICS?|NOTE|QTY|DUPATTA|SINGLES?|NOW SINGLES?|ACTUAL RATE|NETT RATE)\s*[:\-]*\s*$", re.I)
PRICE_NOW = re.compile(r"now\s+singles?\s*[:@\-=]*\s*(?:₹|rs\.?)?\s*(\d[\d,]{2,6})", re.I)
PRICE_SGL = re.compile(r"(?<!now )singles?\s*[:@\-=]*\s*(?:₹|rs\.?)?\s*(\d[\d,]{2,6})", re.I)
SIZE_LINE = re.compile(r"^(3XL|4XL|5XL|XXL|XL|XS|S|M|L)\s*\(\s*\d{2,3}\s*\)$", re.I)
SIZE_ORDER = ["XS", "S", "M", "L", "XL", "XXL", "3XL", "4XL", "5XL"]
SIZE_TOK = re.compile(r"\b(3XL|4XL|5XL|XXL|XL|XS|S|M|L)\b(?:\s*\(\s*\d{2,3}\s*\))?", re.I)
LABEL_BOLD = re.compile(r"^(FABRICS?|SIZES?|SINGLES?|NOTE|BOOK|READY|NO GST|SELL|DEAL|NEW DESIGNS|LOOK LIKE|PRESENTING|ALHUMDULILAH|FULL STOCK|RESTOCK|SUPERHIT DESIGNS|LENTH|DUPATTA|SHOP|ASSALAM|HIT DESIGN|SINGLES & MULTIPLE)", re.I)
MARKETING = re.compile(r"LAUNCHING|BEAUTIFUL DESIGN|PRESENTING|ALHUMDULILAH|NEW LAUNCH|NO GST|BOOK FAST|READY TO SHIP|NOTE\s*:|WITH ALL ACCESSORIES|WITH MIYANI|SIZES? AVL|MENTION ON PIC", re.I)
VARIANT_RE = re.compile(r"^([A-Z0-9][A-Z0-9.\-& ]{0,19}?)\s*:+\s*-?\s*(\d{3,5})\s*/?-?\s*$", re.I)
PRODUCT_LIKE = re.compile(r"fabric|sizes?\b|singles?|rate|mention on pic|book fast|ready to ship|d\s*no", re.I)
EMOJI_RE = re.compile("[\U0001F000-\U0001FFFF\u2600-\u27BF\u2B00-\u2BFF\uFE0F\u200d\u2122\u00A9\u00AE]")
RATE_RE = re.compile(r"\brate\s*[:\-=]*\s*(?:₹|rs\.?)?\s*-?\s*(\d[\d,]{2,6})", re.I)
SIZE_ALIAS = {"XXXL": "3XL", "XXXXL": "4XL"}


def parse_dt(d, t):
    s = re.sub(r"\s+", " ", f"{d} {t}").strip()
    for fmt in ("%d/%m/%Y %H:%M:%S", "%d/%m/%Y %H:%M", "%d/%m/%y %H:%M:%S", "%d/%m/%y %H:%M",
                "%m/%d/%y %I:%M %p", "%m/%d/%y %I:%M:%S %p", "%m/%d/%Y %I:%M %p", "%m/%d/%Y %H:%M", "%m/%d/%y %H:%M"):
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


def clean_text(t):
    files = [(m.group(1) or m.group(2) or "").strip() for m in ATT_RE.finditer(t)]
    t = ATT_RE.sub("", t)
    t = MEDIA_OMIT.sub("", t)
    t = STRIKE_RE.sub("", t)
    return files, t


def lines_of(t):
    out = []
    for l in t.splitlines():
        s = l.replace("*", "").replace("_", "").strip()
        if not s or BLOCK_LINE.search(s):
            continue
        out.append(s)
    return out


def clean_line(l):
    return EMOJI_RE.sub("", l).replace("*", "").replace("_", "").strip(" -–:|")


def parse_size(text):
    if re.search(r"free\s*size", text, re.I):
        return ["FREE SIZE"]
    found = set()
    rng = re.search(r"\b(XS|S|M|L|XL)\s*(?:TO|-|–)\s*(3XL|XXL|XL|4XL|5XL)\b", text, re.I)
    if rng:
        a, b = rng.group(1).upper(), rng.group(2).upper()
        for k in SIZE_ORDER[SIZE_ORDER.index(a):SIZE_ORDER.index(b) + 1]:
            found.add(k)
    lines = text.splitlines()
    for i, l in enumerate(lines):
        if re.search(r"size", l, re.I):
            chunk = [l]
            for nxt in lines[i + 1:i + 4]:
                if not nxt.strip():
                    break
                chunk.append(nxt)
            for c in chunk:
                for m in SIZE_TOK.finditer(c.upper()):
                    found.add(m.group(1).upper())
    if not found:
        return []
    return sorted(found, key=lambda s: SIZE_ORDER.index(s))


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
            continue
        ext = p.suffix.lower()
        try:
            if ext in (".jpg", ".jpeg", ".png", ".webp"):
                out = IMG_DIR / f"{file_hash(p)}.jpg"
                if not out.exists():
                    with Image.open(p) as im:
                        im = im.convert("RGB")
                        im.thumbnail((800, 800), Image.LANCZOS)
                        im.save(out, "JPEG", quality=72, optimize=True, progressive=True)
                if f"images/{out.name}" not in images:
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
    """Media ko product text se TIME se jodta hai: +-2 minute ke andar, sabse nazdeek wala product.
    Variants alag products banenge, sab ki photos same."""
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
        if DEADLINE and time.time() > DEADLINE:
            raise OutOfTime()
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
        cat = detect_category(prod["name"], prod["description"], prod["sizes"])
        best_map[key] = (it["time"], {
            "id": pid, "title": prod["name"], "raw": prod["name"], "text": prod["description"],
            "price": prod["price"] + mg, "date": it["time"].isoformat(), "category": cat, "crop": "",
            "images": images, "videos": videos, "sizes": prod["sizes"], "_key": norm_key(prod)})
    return [v[1] for v in best_map.values()], skipped


def norm_key(prod):
    f = lambda x: re.sub(r"[^a-z0-9]", "", x.lower())
    return f(prod["name"]) + "|" + str(prod["price"]) + "|" + f(prod["description"])


def merge_groups(list_of_product_lists):
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
                "k": p["text"], "m": p["images"][0], "c": p.get("category", ""),
                "s": p.get("sizes", [])} for p in products]
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


def process_zip(zip_path, now, tag):
    ex = WORK / tag
    with zipfile.ZipFile(zip_path) as z:
        z.extractall(ex)
    txts = sorted(ex.rglob("*.txt"), key=lambda p: p.stat().st_size, reverse=True)
    if not txts:
        print("txt nahi mili:", tag, flush=True)
        return [], []
    index = {p.name: p for p in ex.rglob("*") if p.is_file()}
    text = txts[0].read_text(encoding="utf-8", errors="ignore")
    msgs = parse_chat(text)
    print(f"{tag} chat range:", msgs[0]["time"], "to", msgs[-1]["time"], "| messages:", len(msgs), flush=True)
    return build(msgs, index, now)


def detect_category(title, text, sizes):
    """Classify product. No size → Unstitched. Else keyword match → category."""
    t = (title + " " + (text or "")).lower()
    if re.search(r"sharara|gharara", t): return "Sharara & Gharara"
    if re.search(r"palazzo", t): return "Palazzo Sets"
    if re.search(r"kurti", t): return "Kurtis"
    if re.search(r"party|wedding|bridal", t): return "Party Wear"
    if re.search(r"salwar|kameez|suit", t): return "Salwar Kameez"
    if not sizes: return "Unstitched"
    return "Other"


def _libas_get(url, timeout=30):
    req = urllib.request.Request(url, headers={"User-Agent": "SAZ-Sync/1.0"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def fetch_libas_products():
    out = []
    for page in range(1, LIBAS_PAGES + 1):
        try:
            raw = _libas_get(f"{LIBAS_API}?per_page=100&page={page}")
            data = json.loads(raw.decode("utf-8"))
        except Exception as e:
            print(f"Libas fetch page {page} error: {e}", flush=True)
            break
        if not isinstance(data, list) or not data:
            break
        out.extend(data)
        if len(data) < 100:
            break
    return out


def download_libas_image(url):
    h = hashlib.sha1(url.encode()).hexdigest()[:12]
    out = IMG_DIR / f"lb{h}.jpg"
    if out.exists():
        return f"images/{out.name}"
    try:
        data = _libas_get(url)
        tmp = WORK / f"tmp_lb_{h}.jpg"
        tmp.write_bytes(data)
        with Image.open(tmp) as im:
            im = im.convert("RGB")
            im.thumbnail((800, 800), Image.LANCZOS)
            im.save(out, "JPEG", quality=72, optimize=True, progressive=True)
        tmp.unlink(missing_ok=True)
        return f"images/{out.name}"
    except Exception as e:
        print(f"Libas image skip: {e}", flush=True)
        return None


def _libas_price(prices):
    try:
        return int(prices.get("price") or 0) // 100
    except (ValueError, TypeError):
        return 0


def _libas_desc(raw):
    """Prefer short_description (real product details). HTML → newlines."""
    html_src = raw.get("short_description") or raw.get("description") or ""
    if not html_src:
        return ""
    s = re.sub(r"</(p|div|tr|li|h[1-6])>", "\n", html_src, flags=re.I)
    s = re.sub(r"<br\s*/?>", "\n", s, flags=re.I)
    s = re.sub(r"</t[dh]>", ": ", s, flags=re.I)
    s = re.sub(r"<[^>]+>", "", s)
    s = _html.unescape(s)
    s = re.sub(r"[ \t]+", " ", s)
    s = re.sub(r"\n\s*\n+", "\n", s)
    return s.strip()[:1200]


def convert_libas_product(raw, now, start_time):
    try:
        name = (raw.get("name") or "").strip()
        if not name:
            return None
        base_price = _libas_price(raw.get("prices") or {})
        if base_price < 10 or base_price > 500000:
            return None
        mg = margin_for(base_price)
        if mg is None:
            print(f"Libas skip (slab out): {name[:40]} @ {base_price}", flush=True)
            return None
        final_price = base_price + mg
        images = []
        for img in (raw.get("images") or []):
            if time.time() - start_time > LIBAS_TIME_LIMIT:
                break
            src = img.get("src")
            if src:
                local = download_libas_image(src)
                if local:
                    images.append(local)
        if not images:
            return None
        sizes = []
        for attr in (raw.get("attributes") or []):
            if (attr.get("name") or "").lower() == "size":
                sizes = [t.get("name", "").strip() for t in (attr.get("terms") or []) if t.get("name")]
        desc = _libas_desc(raw)
        category = detect_category(name, desc, sizes)
        date_str = raw.get("date_created") or raw.get("date_modified") or now.isoformat()
        libas_id = raw.get("id", "")
        pid = "p" + hashlib.sha1(f"libas-{libas_id}".encode()).hexdigest()[:8]
        return {
            "id": pid, "title": name[:140], "raw": name, "text": desc,
            "price": final_price, "date": date_str, "category": category, "crop": "",
            "images": images, "videos": [], "sizes": sizes,
        }
    except Exception as e:
        print(f"Libas convert error: {e}", flush=True)
        return None


def sync_libas(now):
    start = time.time()
    print("Libas sync: starting", flush=True)
    try:
        raws = fetch_libas_products()
    except Exception as e:
        print(f"Libas fetch failed: {e}", flush=True)
        return
    print(f"Libas sync: fetched {len(raws)} raw products", flush=True)
    converted = []
    for raw in raws:
        if time.time() - start > LIBAS_TIME_LIMIT:
            print("Libas sync: time limit hit", flush=True)
            break
        p = convert_libas_product(raw, now, start)
        if p:
            converted.append(p)
    print(f"Libas sync: {len(converted)} products ready", flush=True)
    PRODS.mkdir(parents=True, exist_ok=True)
    (PRODS / "libas.json").write_text(json.dumps(converted, ensure_ascii=False), encoding="utf-8")
    mark_changed()


def mark_changed():
    CHANGED.write_text("1")


def load_state():
    if STATE_F.exists():
        return json.loads(STATE_F.read_text(encoding="utf-8"))
    return {}


def save_state(st):
    STATE_F.write_text(json.dumps(st, ensure_ascii=False, indent=1), encoding="utf-8")


async def list_latest(client, chat):
    got = {}
    async for msg in client.iter_messages(chat, limit=80):
        f = msg.file
        if f is None or not f.name or not f.name.lower().endswith(".zip"):
            continue
        k = group_key(f.name)
        if k not in got:
            got[k] = {"msg": msg, "msg_id": msg.id, "name": f.name, "size": f.size}
    return got


async def download_resume(client, msg, cur, k, st):
    ZIPS.mkdir(parents=True, exist_ok=True)
    path = ZIPS / f"{k}.zip"
    off = cur["offset"]
    print(f"download {k}: {off // 1048576} MB se shuru", flush=True)
    mode = "r+b" if path.exists() else "wb"
    with open(path, mode) as f:
        f.seek(off)
        f.truncate()
        async for chunk in client.iter_download(msg.media, offset=off, request_size=CHUNK):
            f.write(chunk)
            off += len(chunk)
            cur["offset"] = off
            if (off // CHUNK) % 50 == 0:
                save_state(st)
                print(f"download {k}: {off // 1048576} / {cur['size'] // 1048576} MB", flush=True)
            if time.time() > DL_DEADLINE:
                break
    if off >= cur["size"]:
        cur["zip_done"] = True
        print("download poora:", k, flush=True)
    save_state(st)
    mark_changed()


async def sync_downloads(chat, st):
    async with TelegramClient(StringSession(os.environ["TG_SESSION"]),
                              int(os.environ["TG_API_ID"]), os.environ["TG_API_HASH"]) as client:
        latest = await list_latest(client, int(chat))
        for old in [k for k in st if k not in latest]:
            print("purana group hataya:", old, flush=True)
            (ZIPS / f"{old}.zip").unlink(missing_ok=True)
            (PRODS / f"{old}.json").unlink(missing_ok=True)
            st.pop(old, None)
            mark_changed()
        for k, info in latest.items():
            cur = st.get(k)
            if cur is None or cur["msg_id"] != info["msg_id"]:
                st[k] = {"msg_id": info["msg_id"], "name": info["name"], "size": info["size"],
                         "offset": 0, "zip_done": False, "json_done": False}
                (ZIPS / f"{k}.zip").unlink(missing_ok=True)
                (PRODS / f"{k}.json").unlink(missing_ok=True)
                mark_changed()
                print("naya zip mila:", k, info["name"], flush=True)
        save_state(st)
        for k, info in latest.items():
            cur = st[k]
            if cur["zip_done"] or time.time() > DL_DEADLINE:
                continue
            await download_resume(client, info["msg"], cur, k, st)


def process_pending(st, now):
    for k, cur in st.items():
        if not cur["zip_done"] or cur["json_done"]:
            continue
        if time.time() > DEADLINE:
            print("time khatam, processing agle run me:", k, flush=True)
            return
        try:
            prods, skipped = process_zip(ZIPS / f"{k}.zip", now, k)
        except OutOfTime:
            print("processing agle run me continue hoga:", k, flush=True)
            return
        except Exception as e:
            log_err(f"group {k} processing", e)
            continue
        PRODS.mkdir(parents=True, exist_ok=True)
        (PRODS / f"{k}.json").write_text(json.dumps(prods, ensure_ascii=False), encoding="utf-8")
        cur["json_done"] = True
        (ZIPS / f"{k}.zip").unlink(missing_ok=True)
        shutil.rmtree(WORK / k, ignore_errors=True)
        save_state(st)
        mark_changed()
        print(f"{k} ready | products: {len(prods)} | skipped: {len(skipped)}", flush=True)
        for x in skipped[:30]:
            print("  -", x, flush=True)


def publish(st, now):
    order = [o.lower() for o in SETTINGS.get("group_order", [])]
    keys = [p.stem for p in PRODS.glob("*.json")]
    if not keys:
        print("koi group ready nahi, site purani hi rahegi", flush=True)
        return
    keys.sort(key=lambda k: next((i for i, o in enumerate(order) if o in k), 99))
    cutoff = now - dt.timedelta(days=DAYS)
    lists = []
    for k in keys:
        arr = json.loads((PRODS / f"{k}.json").read_text(encoding="utf-8"))
        lists.append([p for p in arr if dt.datetime.fromisoformat(p["date"]) >= cutoff])
    products = merge_groups(lists)
    write_site(products, now)
    used = set()
    for p in products:
        used.update(p["images"])
        used.update(p["videos"])
    for rel in used:
        src, dst = STORE / rel, OUT / rel
        if src.exists() and not dst.exists():
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(src, dst)
    if all(c["json_done"] for c in st.values()):
        for folder, sub in ((IMG_DIR, "images"), (VID_DIR, "videos")):
            for f in folder.glob("*"):
                if f"{sub}/{f.name}" not in used:
                    f.unlink(missing_ok=True)
                    mark_changed()
    print(f"SITE: {len(products)} products (groups: {keys})", flush=True)


# ============================================================
# ERRORS / STATUS / MAIN
# ============================================================

ERRORS = []


def log_err(where, e):
    tb = traceback.extract_tb(e.__traceback__)
    loc = f"scrape.py line {tb[-1].lineno} (function {tb[-1].name})" if tb else "line unknown"
    msg = f"{where}: {type(e).__name__}: {e} | {loc}"
    print("ERROR", msg, flush=True)
    traceback.print_exc()
    ERRORS.append(msg)


def write_status(now):
    OUT.mkdir(parents=True, exist_ok=True)
    data = {"time": now.strftime("%d %b %Y %H:%M IST"), "ok": not ERRORS, "errors": ERRORS}
    (OUT / "status.json").write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")


def main():
    global DEADLINE, DL_DEADLINE
    start = time.time()
    DEADLINE = start + BUDGET
    DL_DEADLINE = start + DL_BUDGET
    now = dt.datetime.now(IST)
    for d in (WORK, STORE, ZIPS, PRODS, IMG_DIR, VID_DIR, OUT / "p", OUT / "images", OUT / "videos"):
        d.mkdir(parents=True, exist_ok=True)
    CHANGED.unlink(missing_ok=True)
    st = load_state()

    # NEW: Libas sync first (fast, ~3-5 min, always runs)
    try:
        sync_libas(now)
    except Exception as e:
        log_err("libas sync", e)

    chat = os.environ.get("TG_ZIP_CHANNEL")
    if not chat:
        ERRORS.append("TG_ZIP_CHANNEL secret set nahi hai")
        print("ERROR TG_ZIP_CHANNEL secret set nahi hai", flush=True)
    else:
        try:
            process_pending(st, now)
        except Exception as e:
            log_err("processing", e)
        try:
            asyncio.run(sync_downloads(chat, st))
        except Exception as e:
            log_err("telegram download", e)
        save_state(st)
    try:
        publish(st, now)
    except Exception as e:
        log_err("publish", e)
    if not (OUT / "first.json").exists():
        try:
            write_site([], now)
        except Exception as e:
            log_err("khali site files", e)
    write_status(now)
    done = [k for k, c in st.items() if c["json_done"]]
    pending = [k for k, c in st.items() if not c["json_done"]]
    print("READY:", done, "| PENDING:", pending, "| ERRORS:", len(ERRORS), flush=True)
    shutil.rmtree(WORK, ignore_errors=True)


if __name__ == "__main__":
    main()
