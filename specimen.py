#!/usr/bin/env python
# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (c) 2026 The Jungle Fruit
"""
type-specimen-finder
====================

Writes one word in every installed typeface that meets a set of requirements
(sans serif, heavy, ...) and prints a PDF with one specimen per family, plus
alphabetical and by-weight lists.

    python specimen.py "Hello" --out specimens

Typefaces are NOT chosen by their name: the drawing of the letters is measured.
  stem  = stem width of the 'n' / its x-height
          (Arial 0.16  Arial Bold 0.26  Arial Black 0.38  Gotham Ultra 0.41)
  serif = widest row / median row width of the 'I'
          (sans serif 1.00  Times 3.09  Rockwell 2.40)

The scan is cached in cache/fonts.json, so the second run starts instantly.
"""
import argparse
import collections
import datetime
import difflib
import html
import json
import logging
import os
import re
import shutil
import statistics
import subprocess
import sys
import urllib.parse
import warnings

warnings.filterwarnings("ignore")
logging.disable(logging.WARNING)

from fontTools.ttLib import TTFont, TTCollection          # noqa: E402
from PIL import Image, ImageDraw, ImageFont               # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
CACHE = os.path.join(HERE, "cache", "fonts.json")
CACHE_VERSION = 2       # 2: licence fields; an older cache is rescanned
EXTS = (".ttf", ".otf", ".ttc", ".otc")
MEASURE_PX = 240        # render size used for measuring
BROWSERS = [
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
]
BROWSER_COMMANDS = ("google-chrome", "google-chrome-stable", "chromium", "chromium-browser",
                    "chrome", "msedge", "microsoft-edge")

# ------------------------------------------------------------------ vocabulary
WEIGHTS = ["extra black", "extrablack", "ultra black", "ultrablack", "extra bold", "extrabold",
           "ultra bold", "ultrabold", "semi bold", "semibold", "demi bold", "demibold",
           "black", "heavy", "ultra", "bold", "demi", "medium", "book", "regular", "normal",
           "light", "thin", "hairline", "roman", "negra", "negrita", "fat", "poster", "super",
           "xbold", "bd", "blk"]
HEAVY = ("black", "heavy", "extrabold", "extra bold", "ultrabold", "ultra bold", "ultra",
         "bold", "negra", "negrita", "fat", "poster", "xbold", "blk", "super", "massive",
         "extrablack", "obese", "gordo")
SEMI = ("semibold", "semi bold", "demibold", "demi bold", "demi", "medium")
SERIF_WORDS = ("serif", "slab", "times", "georgia", "garamond", "bodoni", "didot", "egyptienne",
               "baskerville", "caslon", "clarendon", "rockwell", "courier", "bookman", "antiqua",
               "palatino", "schoolbook", "cheltenham", "minion", "sabon", "utopia", "scotch",
               "playfair", "merriweather", "glyphic", "rosewood")
SANS_WORDS = ("sans serif", "sans-serif", "sansserif", "gothic", "grotesk", "grotesque", "sans")
ODDITIES = ("symbol", "dingbat", "wingding", "webding", "emoji", "icon", "awesome", "barcode",
            "ornament", "flourish", "arrows", "signs", "music", "braille", "shape", "bats",
            "script", "handwrit", "brush", "calligra", "signature", "marker", "graffiti",
            "grunge", "distress", "rough", "scratch", "splatter", "destroy", "dirty",
            "outline", "inline", "shadow", "engraved", "hollow", "3d", "bevel", "emboss",
            "dots", "halftone", "stripe", "hatch", "zebra", "pixel", "bitmap", "lcd",
            "dot matrix", "stencil", "tattoo", "horror", "blood", "monster", "alien",
            "comic", "cartoon", "fraktur", "blackletter", "black letter", "old english",
            "oldenglish", "textur", "gotisch", "schwab", "lombardic", "cloister", "canterbury",
            "medieval", "celtic", "baroque text", "goudy text", "wedding text")


def log(msg):
    print(msg, file=sys.stderr)


# ===================================================================== 1. scan
def font_dirs():
    """System and user font folders on Windows, macOS and Linux."""
    home = os.path.expanduser("~")
    dirs = [os.path.join(os.environ.get("WINDIR", r"C:\Windows"), "Fonts")]
    local = os.environ.get("LOCALAPPDATA")
    if local:
        dirs.append(os.path.join(local, "Microsoft", "Windows", "Fonts"))
    dirs += ["/System/Library/Fonts", "/Library/Fonts", os.path.join(home, "Library", "Fonts"),
             "/usr/share/fonts", "/usr/local/share/fonts", os.path.join(home, ".fonts"),
             os.path.join(home, ".local", "share", "fonts")]
    return [d for d in dirs if os.path.isdir(d)]


def font_files(dirs):
    seen, out = set(), []
    for top in dirs:
        for root, _, names in os.walk(top):
            for fn in sorted(names):
                if not fn.lower().endswith(EXTS):
                    continue
                p = os.path.join(root, fn)
                try:
                    key = (fn.lower(), os.path.getsize(p))
                except OSError:
                    continue
                if key in seen:
                    continue
                seen.add(key)
                out.append(p)
    return out


def _draw(font, text, canvas=3):
    img = Image.new("L", (MEASURE_PX * canvas, MEASURE_PX * 3), 0)
    ImageDraw.Draw(img).text((MEASURE_PX, MEASURE_PX), text, font=font, fill=255)
    box = img.getbbox()
    return img.crop(box) if box else None


def measure(path, index):
    """Stem, ink and serif, measured on the real drawing of the letters."""
    try:
        f = ImageFont.truetype(path, size=MEASURE_PX, index=index)
    except Exception:
        return None
    n = _draw(f, "n")
    if n is None:
        return None
    w, h = n.size
    px = n.load()
    out = {"ink": round(sum(n.histogram()[128:]) / float(w * h), 4),   # pixels > 127
           "nw": w, "nh": h}
    stems = []
    for y in range(int(h * 0.45), int(h * 0.75) + 1):
        run, inside = 0, False
        for x in range(w):
            if px[x, y] > 127:
                run += 1
                inside = True
            elif inside:
                break
        if run:
            stems.append(run)
    out["stem"] = round(statistics.median(stems) / float(h), 4) if stems else 0.0
    i = _draw(f, "I")
    if i is None:
        out["serif"] = 1.0
    else:
        iw, ih = i.size
        ip = i.load()
        widths = []
        for y in range(ih):
            row = [x for x in range(iw) if ip[x, y] > 127]
            if row:
                widths.append(row[-1] - row[0] + 1)
        med = statistics.median(widths) if widths else 0
        out["serif"] = round(max(widths) / med, 3) if med else 1.0
    return out


def _names(tt):
    out = {}
    try:
        nt = tt["name"]
    except Exception:
        return out
    # 1 family, 2 subfamily, 4 full name, 16/17 typographic family/subfamily,
    # 8 manufacturer, 11 vendor URL, 12 designer URL, 13 licence description, 14 licence URL
    for nid in (1, 2, 4, 8, 11, 12, 13, 14, 16, 17):
        try:
            v = nt.getDebugName(nid)
        except Exception:
            v = None
        if v:
            out[nid] = v
    return out


def face_record(path, index, tt, word):
    nm = _names(tt)
    gdi = nm.get(1) or ""
    fam = nm.get(16) or gdi
    if not (gdi or fam):
        return None
    try:
        cmap = tt.getBestCmap()
    except Exception:
        return None
    if not all(ord(c) in cmap for c in set(word)):
        return None
    os2 = tt["OS/2"] if "OS/2" in tt else None
    pan = getattr(os2, "panose", None)
    m = measure(path, index)
    if not m:
        return None
    head = tt.get("head", None)
    r = {"path": path, "i": index, "fam": fam, "gdi": gdi,
         "sub": nm.get(2) or "", "full": nm.get(4) or "",
         "w": getattr(os2, "usWeightClass", 0) or 0,
         "wd": getattr(os2, "usWidthClass", 5) or 5,
         "pan": [getattr(pan, k, 0) for k in
                 ("bFamilyType", "bSerifStyle", "bWeight", "bProportion")] if pan else [0] * 4,
         "it": bool((getattr(head, "macStyle", 0) or 0) & 2),
         # licence data exactly as declared in the file (often missing or out of date: see licence_of)
         "lic_text": (nm.get(13) or "")[:600], "lic_url": nm.get(14) or "", "vendor": nm.get(8) or "",
         "vendor_url": nm.get(11) or "", "designer_url": nm.get(12) or "",
         "fstype": getattr(os2, "fsType", None)}
    r.update(m)
    return r


def scan(word, dirs=None, verbose=True):
    files = font_files(dirs or font_dirs())
    if verbose:
        log("Scanning %d font files..." % len(files))
    rows, errors = [], 0
    for n, p in enumerate(files):
        if verbose and n % 500 == 0 and n:
            log("  %d/%d" % (n, len(files)))
        try:
            indices = [0]
            if p.lower().endswith((".ttc", ".otc")):
                col = TTCollection(p, lazy=True)
                indices = list(range(len(col.fonts)))
                col.close()
            for idx in indices:
                try:
                    tt = TTFont(p, fontNumber=idx if len(indices) > 1 else -1, lazy=True)
                    rec = face_record(p, idx, tt, word)
                    tt.close()
                    if rec:
                        rows.append(rec)
                except Exception:
                    errors += 1
        except Exception:
            errors += 1
    if verbose:
        log("  %d faces measured (%d unreadable files)" % (len(rows), errors))
    return {"version": CACHE_VERSION, "files": len(files), "word": word, "rows": rows}


def load_scan(word, rescan, dirs=None, verbose=True):
    """Cached scan of the system fonts; a --fonts-dir scan is never cached."""
    if dirs:
        return scan(word, dirs, verbose)
    if not rescan and os.path.isfile(CACHE):
        try:
            with open(CACHE, encoding="utf-8") as fh:
                d = json.load(fh)
            # the cache is valid if the new word only uses letters already checked
            if d.get("version") == CACHE_VERSION and set(word) <= set(d.get("word", "")):
                if verbose:
                    log("Using the saved scan (%d faces)." % len(d["rows"]))
                return d
        except Exception:
            pass
    d = scan(word, None, verbose)
    os.makedirs(os.path.dirname(CACHE), exist_ok=True)
    with open(CACHE, "w", encoding="utf-8") as fh:
        json.dump(d, fh, ensure_ascii=False)
    return d


# ================================================================== 2. filters
def family_key(gdi, fam):
    s = (fam or gdi or "").strip()
    low = " " + s.lower() + " "
    for p in sorted(WEIGHTS, key=len, reverse=True):
        low = low.replace(" " + p + " ", " ")
    low = re.sub(r"\s+", " ", low).strip()
    low = re.sub(r"\s+(italic|oblique|it)$", "", low)
    return low or s.lower()


def has_serif(r, low):
    pfam, pser = r["pan"][0], r["pan"][1]
    by_name = any(w in low for w in SERIF_WORDS) and not any(w in low for w in SANS_WORDS)
    by_shape = r["serif"] > 1.42
    panose_serif = (pfam == 2 and 2 <= pser <= 10)
    panose_sans = (pfam == 2 and 11 <= pser <= 15)
    if by_shape:
        return True
    if by_name and not panose_sans:
        return True
    if panose_serif and r["serif"] > 1.2:
        return True
    return False


def is_heavy(r, low, minimum):
    declared = r["w"] >= 600 or r["pan"][2] >= 8 or any(w in low for w in HEAVY)
    semi = any(w in low for w in SEMI)
    if r["stem"] >= minimum:
        return True
    if declared and not semi and r["stem"] >= minimum * 0.56:
        return True
    if semi and r["stem"] >= minimum * 0.96:
        return True
    return False


def apply_filters(rows, op):
    rejected = collections.Counter()
    kept = []
    for r in rows:
        low = ((r["full"] or "") + " " + r["gdi"] + " " + r["sub"]).lower()
        if not op.italics and (r["it"] or "italic" in low or "oblique" in low):
            rejected["italic"] += 1
            continue
        if not op.oddities and (any(w in low for w in ODDITIES) or r["pan"][0] in (3, 5)):
            rejected["decorative or symbol"] += 1
            continue
        if r["nh"] < 40 or r["stem"] <= 0 or r["stem"] > 0.60:
            rejected["invalid drawing"] += 1
            continue
        if op.sans_only and has_serif(r, low):
            rejected["serif"] += 1
            continue
        if op.heavy_only and not is_heavy(r, low, op.min_stem):
            rejected["too light"] += 1
            continue
        r["key"] = family_key(r["gdi"], r["fam"])
        kept.append(r)
    return kept, rejected


def one_per_family(kept):
    best = {}
    for r in kept:
        c = best.get(r["key"])
        if c is None or (r["stem"], r["w"]) > (c["stem"], c["w"]):
            best[r["key"]] = r
    return list(best.values())


def drop_textures(chosen, word, verbose=True):
    """Drops faces that don't draw the word cleanly (hatching, distressed, broken)."""
    out, dropped = [], 0
    for r in chosen:
        try:
            f = ImageFont.truetype(r["path"], size=200, index=r["i"])
        except Exception:
            dropped += 1
            continue
        img = Image.new("L", (200 * (2 + len(word)), 600), 0)
        ImageDraw.Draw(img).text((200, 200), word, font=f, fill=255)
        box = img.getbbox()
        if not box:
            dropped += 1
            continue
        crop = img.crop(box)
        w, h = crop.size
        px = crop.load()
        runs = []
        for y in range(h):
            c, prev = 0, False
            for x in range(w):
                on = px[x, y] > 127
                if on and not prev:
                    c += 1
                prev = on
            if c:
                runs.append(c)
        mean = statistics.mean(runs) if runs else 0
        if mean > 1.9 * len(word) or (runs and max(runs) > 5 * len(word)):
            dropped += 1
            continue
        r["rel_width"] = round(w / float(h), 3)
        out.append(r)
    if verbose and dropped:
        log("  %d dropped for texture or broken drawing" % dropped)
    return out


# ============================================================ 2b. pick by name
def _norm(s):
    s = (s or "").lower()
    s = s.replace("&", " and ").replace("/", " ").replace("-", " ").replace("_", " ")
    s = re.sub(r"\b(demo|trial|std|pro|lt|mt|bt|itc|nf|ff|tt|ps|w\d{1,2}|regular)\b", " ", s)
    return re.sub(r"[^a-z0-9]+", " ", s).strip()


def _tokens(s):
    return [t for t in _norm(s).split() if t]


def find_face(request, rows, with_italics=False):
    """Finds the face that best matches a hand-typed name."""
    want = _norm(request)
    if not want:
        return None, []
    tw = set(_tokens(request))
    scored = []
    for r in rows:
        if r["it"] and not with_italics:
            continue
        best = 0
        for field in (r["full"], (r["gdi"] + " " + (r["sub"] or "")).strip(), r["gdi"]):
            n = _norm(field)
            if not n:
                continue
            tf = set(_tokens(field))
            if n == want:
                p = 100
            elif tw and tw <= tf:
                p = 90 - min(9, len(tf - tw))
            elif n.startswith(want) or want.startswith(n):
                p = 80 - min(9, abs(len(n) - len(want)) // 3)
            elif tw and len(tw) > 1 and len(tw & tf) >= len(tw) - 1:
                p = 70 - min(9, len(tf - tw))
            else:
                ratio = difflib.SequenceMatcher(None, n, want).ratio()
                p = 62 * ratio if ratio > 0.84 else 0
            best = max(best, p)
        if best >= 55:
            scored.append((best, r["stem"], r))
    if not scored:
        return None, []
    # if the request names no weight, widen the net and the heaviest face of the
    # family wins: it is the one the general specimen shows
    no_weight = not (tw & set(" ".join(HEAVY + SEMI).split()))
    top = max(m[0] for m in scored)
    finalists = [m for m in scored if m[0] >= top - (12 if no_weight else 0.5)]
    finalists.sort(key=lambda m: (-m[1],) if no_weight else (-m[0], -m[1]))
    others = [m[2] for m in finalists[1:4]]
    return finalists[0][2], others


def read_selection(path):
    """One request per line or comma-separated; skips blanks and # comments."""
    with open(path, encoding="utf-8") as fh:
        text = fh.read()
    parts = []
    for line in text.splitlines():
        line = line.split("#")[0].strip()
        if not line:
            continue
        parts += [t.strip() for t in line.split(",") if t.strip()]
    seen, out = set(), []
    for t in parts:
        k = _norm(t)
        if k and k not in seen:
            seen.add(k)
            out.append(t)
    return out


# ================================================================== 3. output
def file_url(p):
    p = os.path.abspath(p).replace("\\", "/")
    return "file://" + ("" if p.startswith("/") else "/") + urllib.parse.quote(p, safe="/:")


def display_name(r):
    gdi, sub = r["gdi"].strip(), (r["sub"] or "").strip()
    if sub.lower() in ("", "regular", "normal", "roman", "book") or sub.lower() in gdi.lower():
        return gdi
    return gdi + " " + sub


def weight_label(r):
    s = r["stem"]
    if s >= 0.40:
        return "ultra"
    if s >= 0.33:
        return "black"
    if s >= 0.26:
        return "bold+"
    if s >= 0.22:
        return "bold"
    return "regular" if s >= 0.15 else "light"


# ----------------------------------------------------------------- licences
# Everything comes from the font file itself (name table 8/11/12/13/14 and OS/2 fsType). Files are often
# incomplete or out of date, so the report says what the file DECLARES and asks to check before commercial use.
OPEN_LICENCES = [
    ("SIL OFL 1.1", r"open font licen[cs]e|scripts\.sil\.org/ofl|openfontlicense\.org|\bofl\b"),
    ("Apache 2.0", r"apache licen[cs]e|apache\.org/licenses"),
    ("Ubuntu Font Licence", r"ubuntu font licen[cs]e"),
    ("GPL + font exception", r"font exception"),
    ("Bitstream Vera licence", r"bitstream vera|dejavu fonts"),
    ("Public domain / CC0", r"public domain|\bcc0\b|creativecommons\.org/publicdomain"),
    ("Creative Commons", r"creative commons|creativecommons\.org"),
]
BUNDLED_LICENCES = [
    ("Bundled with Windows (Microsoft)", r"microsoft supplied font"),
    ("Bundled with macOS (Apple)", r"apple (computer|inc)\b.{0,80}(licen|bundled|supplied)"),
    ("Bundled with another product", r"as permitted by the licen[cs]e terms for the product"),
]
# checked before everything else: "free for personal use" fonts can't go in client work
PERSONAL_USE = r"personal use|non.?commercial use|for personal|not for commercial"
COMMERCIAL = (r"end.user licen[cs]e|\beula\b|licen[cs]e agreement|all rights reserved|proprietary|"
              r"is the property of|may not be (copied|distributed|modified|sold)|commercial (licen|use)|purchase")


def web_url(u):
    """A clickable https URL from what the font declares ('www.foundry.com', 'foundry.com/x'), or ''."""
    u = (u or "").strip()
    if not u:
        return ""
    u = u.split()[0].rstrip(".,;")
    if re.match(r"(?i)^https?://[^\s/$.?#].[^\s]*$", u):
        return u
    if re.match(r"(?i)^(www\.)?[a-z0-9-]+(\.[a-z0-9-]+)+(/\S*)?$", u):
        return "https://" + u
    return ""


def embedding(fstype):
    """OS/2 fsType in words. If several permission bits are set, the least restrictive applies (OpenType spec)."""
    if fstype is None:
        return "not declared"
    perm = fstype & 0x000F
    label = ("editable" if perm & 8 else "preview & print" if perm & 4 else "restricted" if perm & 2
             else "installable")
    extra = [t for bit, t in ((0x0100, "no subsetting"), (0x0200, "bitmap only")) if fstype & bit]
    return label + (" (" + ", ".join(extra) + ")" if extra else "")


def licence_of(r):
    """{licence, kind (open|personal|bundled|commercial|unknown), licence_url, home, foundry, embedding} for a face."""
    text = ((r.get("lic_text") or "") + " " + (r.get("lic_url") or "")).lower()
    label, kind = None, None
    if re.search(PERSONAL_USE, text):
        label, kind = "Personal use only", "personal"
    for name, rx in OPEN_LICENCES if not label else []:
        if re.search(rx, text):
            label, kind = name, "open"
            break
    if not label:
        for name, rx in BUNDLED_LICENCES:
            if re.search(rx, text):
                label, kind = name, "bundled"
                break
    if not label and re.search(COMMERCIAL, text):
        label, kind = "Commercial / proprietary", "commercial"
    if not label:
        label, kind = ("See the licence text in the font", "unknown") if text.strip() else ("Not declared in the file", "unknown")
    lic_url = web_url(r.get("lic_url")) or ("https://openfontlicense.org" if label == "SIL OFL 1.1" else "")
    return {"licence": label, "kind": kind, "licence_url": lic_url,
            "home": web_url(r.get("vendor_url")) or web_url(r.get("designer_url")),
            "foundry": (r.get("vendor") or "").strip(), "embedding": embedding(r.get("fstype"))}


def domain(url):
    m = re.match(r"(?i)^https?://(?:www\.)?([^/:?#]+)", url or "")
    return m.group(1) if m else ""


def origin(r):
    """'system', 'user' (installed by the user) or 'folder' (scanned with --fonts-dir)."""
    p = os.path.normcase(os.path.abspath(r["path"]))
    home = os.path.normcase(os.path.expanduser("~"))
    for d in font_dirs():
        if p.startswith(os.path.normcase(os.path.abspath(d)) + os.sep):
            return "user" if os.path.normcase(os.path.abspath(d)).startswith(home) else "system"
    return "folder"


def build_html(rows, word, nfiles, dest_html, title, per_page=12, list_per_page=171,
               with_lists=True):
    for i, r in enumerate(rows):
        r["id"] = "f%d" % i
        r["n"] = i + 1
        r["lic"] = licence_of(r)
    faces, css_of = [], {}
    for r in rows:
        bold = "bold" in (r["sub"] or "").lower()
        weight = 700 if bold else 400
        local = r["gdi"].replace("'", "").replace('"', "")
        if r["path"].lower().endswith((".ttc", ".otc")):
            src = "local('%s'), local('%s')" % ((r["full"] or local).replace("'", ""), local)
        else:
            fmt = "opentype" if r["path"].lower().endswith(".otf") else "truetype"
            src = "url('%s') format('%s'), local('%s')" % (file_url(r["path"]), fmt, local)
        faces.append("@font-face{font-family:'%s';src:%s;font-weight:%d;font-style:normal;"
                     "font-display:block}" % (r["id"], src, weight))
        css_of[r["id"]] = 'font-family:"%s","%s",sans-serif;font-weight:%d' % (r["id"], local, weight)
        r["css"] = css_of[r["id"]]

    today = datetime.date.today().isoformat()
    T = []
    A = T.append
    A("<!doctype html><html lang='en'><head><meta charset='utf-8'>")
    A("<title>%s</title><style>" % html.escape(title))
    A("@page{size:A4;margin:13mm}*{box-sizing:border-box}")
    A("html,body{margin:0;padding:0;background:#fff;color:#111;"
      "font-family:'Segoe UI',Arial,Helvetica,sans-serif;-webkit-print-color-adjust:exact}")
    A(".pg{page-break-after:always;height:271mm;width:184mm;display:flex;"
      "flex-direction:column;overflow:hidden}.pg:last-child{page-break-after:auto}")
    A(".hd{display:flex;justify-content:space-between;align-items:flex-end;flex:0 0 auto;"
      "border-bottom:1.6pt solid #111;padding-bottom:2mm;margin-bottom:4.5mm}")
    A(".hd .t{font-size:9.5pt;text-transform:uppercase;letter-spacing:.14em;font-weight:700}")
    A(".hd .p{font-size:7.5pt;color:#666}")
    A(".cover{justify-content:space-between}")
    A(".hero{font-size:112pt;line-height:.92;margin:0;letter-spacing:-.01em}")
    A("h1{font-size:27pt;line-height:1.06;margin:7mm 0 5mm;letter-spacing:-.015em;"
      "font-weight:700}")
    A(".meta{font-size:9pt;color:#444;line-height:1.6;max-width:135mm}.meta b{color:#000}")
    A(".note{font-size:7.8pt;color:#555;line-height:1.55;border-top:1.6pt solid #111;"
      "padding-top:3mm}.note b{color:#000}")
    A(".figs{display:flex;gap:9mm;margin:6mm 0 0}")
    A(".fig .v{font-size:23pt;font-weight:700;line-height:1}")
    A(".fig .k{font-size:7pt;text-transform:uppercase;letter-spacing:.1em;color:#777;"
      "margin-top:1mm}")
    A(".strip{margin:9mm 0 0;border-top:0.6pt solid #e2e2e2}")
    A(".strip .row{display:flex;align-items:baseline;justify-content:space-between;gap:6mm;"
      "border-bottom:0.6pt solid #e2e2e2;padding:3.2mm 0}")
    A(".strip .w{font-size:40pt;line-height:1;white-space:nowrap;overflow:hidden}")
    A(".strip .q{font-size:7pt;color:#999;text-transform:uppercase;letter-spacing:.1em;"
      "white-space:nowrap}")
    A(".grid{flex:1 1 auto;display:grid;grid-template-columns:1fr 1fr;"
      "grid-template-rows:repeat(%d,1fr);gap:3.5mm 5mm}" % (per_page // 2))
    A(".cell{border:0.6pt solid #dcdcdc;border-radius:1.2mm;padding:2.6mm 3mm 2mm;"
      "display:flex;flex-direction:column;overflow:hidden}")
    A(".word{flex:1 1 auto;display:flex;align-items:center;font-size:44pt;line-height:1.02;"
      "white-space:nowrap;overflow:hidden;color:#000}")
    A(".ft{flex:0 0 auto;display:flex;justify-content:space-between;align-items:flex-end;"
      "gap:2mm;border-top:0.6pt solid #e8e8e8;padding-top:1.4mm}")
    A(".nm{font-size:8pt;font-weight:700;line-height:1.2;overflow:hidden}")
    A(".num{font-size:6.4pt;color:#aaa;font-weight:400;margin-top:.4mm}")
    A(".dat{font-size:6.4pt;color:#888;white-space:nowrap;text-align:right;line-height:1.35}")
    A(".lic{font-size:6.2pt;color:#777;font-weight:400;margin-top:.3mm}.lic a,.ltab a{color:#1f5fbf;text-decoration:none}")
    A(".disc{font-size:7.4pt;color:#555;line-height:1.5;margin:0 0 3mm}")
    A(".ltab{width:100%;border-collapse:collapse;font-size:7.1pt;line-height:1.3}")
    A(".ltab th{text-align:left;font-size:6.4pt;text-transform:uppercase;letter-spacing:.06em;color:#888;"
      "border-bottom:0.8pt solid #111;padding:1mm 1.4mm}")
    A(".ltab td{border-bottom:0.5pt solid #e5e5e5;padding:1mm 1.4mm;vertical-align:top}")
    A(".ltab .i{color:#bbb;text-align:right}.k-open{color:#2b8a3e}.k-commercial{color:#c92a2a}"
      ".k-bundled{color:#a46200}.k-unknown{color:#888}.k-personal{color:#c92a2a;font-weight:700}")
    A(".cols{flex:1 1 auto;column-count:3;column-gap:7mm;font-size:7.6pt;line-height:1.62}")
    A(".cols div{break-inside:avoid;display:flex;gap:1.8mm;align-items:baseline}")
    A(".cols .i{color:#bbb;min-width:7.5mm;text-align:right;font-size:6.6pt}")
    A(".cols .n{flex:1 1 auto;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}")
    A(".cols .s{color:#999;font-size:6.4pt;text-transform:uppercase;letter-spacing:.05em}")
    A("".join(faces))
    A("</style></head><body>")

    # cover
    cover = max(rows, key=lambda r: (0.42 - abs(r["stem"] - 0.42), r["w"]))
    origins = collections.Counter(origin(r) for r in rows)
    A("<div class='pg cover'><div>")
    A("<div class='hero' style='%s'>%s</div>" % (cover["css"], html.escape(word)))
    A("<h1>%s</h1>" % html.escape(title).replace("\n", "<br>"))
    A("<div class='meta'>One specimen of <b>%s</b> for every family installed on this "
      "computer that meets the requirements. One family, one specimen: always its heaviest "
      "variant.</div>" % html.escape(word))
    A("<div class='figs'>")
    figures = [(len(rows), "families")] + \
        [(origins[k], label) for k, label in (("system", "system"), ("user", "user-installed"),
                                              ("folder", "from folder")) if origins[k]] + \
        [(nfiles, "files checked")]
    for v, k in figures:
        A("<div class='fig'><div class='v'>%s</div><div class='k'>%s</div></div>" % (v, k))
    A("</div>")
    # two heaviest, then the ones closest to a solid bold, without repeating a face
    strip = []
    for r in sorted(rows, key=lambda r: -r["stem"])[:2] + sorted(rows, key=lambda r: abs(r["stem"] - 0.30)):
        if r not in strip:
            strip.append(r)
    A("<div class='strip'>")
    for r in strip[:5]:
        A("<div class='row'><div class='w' style='%s'><span>%s</span></div>"
          "<div class='q'>%s</div></div>"
          % (r["css"], html.escape(word), html.escape(display_name(r))))
    A("</div></div>")
    A("<div class='note'><b>How it was decided.</b> Each typeface is measured on the real "
      "drawing of its letters, not by its name.<br>"
      "&middot; <b>sans serif</b> &mdash; row-by-row width of the &laquo;I&raquo;: a serif "
      "widens the ends. Cross-checked with PANOSE and the name.<br>"
      "&middot; <b>heavy</b> &mdash; stem width of the &laquo;n&raquo; divided by its "
      "x-height (Arial 0.16 &middot; Arial Bold 0.26 &middot; Arial Black 0.38). That number "
      "is the <b>stem</b> printed on each specimen.<br>"
      "&middot; <b>out</b> &mdash; italics, symbols, scripts, outlines, textures and solid "
      "blobs.<br>"
      "&middot; <b>licences</b> &mdash; as declared inside each font file, with links to the foundry and "
      "the licence (see the Licences pages). Confirm with the foundry before any commercial use."
      "<br>Generated on %s." % today)
    A("</div></div>")

    def listing(heading, data, sub):
        chunks = [data[i:i + list_per_page] for i in range(0, len(data), list_per_page)]
        for k, ch in enumerate(chunks):
            A("<div class='pg'><div class='hd'><div class='t'>%s</div>"
              "<div class='p'>%s &middot; %d/%d</div></div><div class='cols'>"
              % (heading, sub, k + 1, len(chunks)))
            for r in ch:
                A("<div><span class='i'>%d</span><span class='n'>%s</span>"
                  "<span class='s'>%s</span></div>"
                  % (r["n"], html.escape(display_name(r)), weight_label(r)))
            A("</div></div>")

    if with_lists:
        listing("Alphabetical list", rows, "%d families" % len(rows))
        listing("By weight", sorted(rows, key=lambda r: -r["stem"]), "heaviest first")

    def licences(data, per=34):
        chunks = [data[i:i + per] for i in range(0, len(data), per)]
        for k, ch in enumerate(chunks):
            A("<div class='pg'><div class='hd'><div class='t'>Licences</div>"
              "<div class='p'>as declared in each font file &middot; %d/%d</div></div><div>" % (k + 1, len(chunks)))
            if k == 0:
                A("<p class='disc'>Licence, embedding permission and links come from the font files themselves "
                  "(OpenType name table and OS/2 fsType). Files are often incomplete or out of date: always "
                  "confirm the terms with the foundry before commercial use, web embedding or redistribution.</p>")
            A("<table class='ltab'><tr><th>no.</th><th>Family</th><th>Licence</th><th>Embedding</th>"
              "<th>Foundry</th><th>Licence link</th></tr>")
            for r in ch:
                L = r["lic"]
                home = ("<a href=\"%s\">%s</a>" % (html.escape(L["home"]), html.escape(domain(L["home"])))
                        if L["home"] else "")
                foundry = html.escape(L["foundry"][:40]) + ("<br>" + home if home else "")
                link = ("<a href=\"%s\">%s</a>" % (html.escape(L["licence_url"]), html.escape(domain(L["licence_url"]) or "link"))
                        if L["licence_url"] else "&mdash;")
                A("<tr><td class='i'>%d</td><td>%s</td><td class='k-%s'>%s</td><td>%s</td><td>%s</td><td>%s</td></tr>"
                  % (r["n"], html.escape(display_name(r)), L["kind"], html.escape(L["licence"]),
                     html.escape(L["embedding"]), foundry or "&mdash;", link))
            A("</table></div></div>")

    licences(rows)

    pages = [rows[i:i + per_page] for i in range(0, len(rows), per_page)]
    for k, pg in enumerate(pages):
        A("<div class='pg'><div class='hd'><div class='t'>Specimens &middot; %s</div>"
          "<div class='p'>%d&ndash;%d of %d &middot; sheet %d/%d</div></div><div class='grid'>"
          % (html.escape(word), pg[0]["n"], pg[-1]["n"], len(rows), k + 1, len(pages)))
        for r in pg:
            extra = ""
            if r.get("requested") and _norm(r["requested"]) != _norm(display_name(r)):
                extra = " &middot; you asked for &laquo;%s&raquo;" % html.escape(r["requested"])
            L = r["lic"]
            where = (" &middot; <a href=\"%s\">%s</a>" % (html.escape(L["home"]), html.escape(domain(L["home"])))
                     if L["home"] else "")
            A("<div class='cell'><div class='word' style='%s'><span>%s</span></div>"
              "<div class='ft'><div class='nm'>%s<div class='num'>no.%d &middot; %s%s</div>"
              "<div class='lic'><span class='k-%s'>%s</span>%s</div>"
              "</div><div class='dat'>stem %.2f<br>%s</div></div></div>"
              % (r["css"], html.escape(word), html.escape(display_name(r)), r["n"],
                 weight_label(r), extra, L["kind"], html.escape(L["licence"]), where,
                 r["stem"], html.escape((r["sub"] or "Regular")[:18])))
        A("</div></div>")

    A("""<script>
// the word grows or shrinks until it fills its box: one jump, then fine steps
// (the inner <span> must be measured: scrollWidth never goes below the box width)
function fit(sel,cap){
  document.querySelectorAll(sel).forEach(function(el){
    var sp=el.firstElementChild; if(!sp){return;}
    var maxW=el.clientWidth-1, maxH=el.clientHeight*0.94;
    var s=40; el.style.fontSize=s+'pt';
    var r=sp.getBoundingClientRect();
    var k=Math.min(maxW/Math.max(1,r.width), maxH/Math.max(1,r.height));
    s=Math.max(7,Math.min(cap,s*k)); el.style.fontSize=s+'pt';
    for(var g=0; g<60; g++){
      r=sp.getBoundingClientRect();
      if((r.width<=maxW && r.height<=maxH) || s<=7){break;}
      s-=Math.max(.5,s*0.03); el.style.fontSize=s+'pt';
    }
  });
}
function layout(){ fit('.strip .w',44); fit('.word',CAP);
  document.documentElement.setAttribute('data-ready','1'); }
if(document.fonts&&document.fonts.ready){document.fonts.ready.then(layout);}
window.addEventListener('load',function(){setTimeout(layout,300);});
</script></body></html>""".replace("CAP", str(int(380 / per_page + 32))))

    with open(dest_html, "w", encoding="utf-8") as fh:
        fh.write("\n".join(T))
    return dest_html


def find_browser():
    found = next((b for b in BROWSERS if os.path.isfile(b)), None)
    return found or next((shutil.which(c) for c in BROWSER_COMMANDS if shutil.which(c)), None)


def to_pdf(html_path, pdf_path, verbose=True):
    browser = find_browser()
    if not browser:
        raise SystemExit("Chrome, Chromium or Edge is needed to print the PDF.")
    cmd = [browser, "--headless=new", "--disable-gpu", "--allow-file-access-from-files",
           "--no-pdf-header-footer", "--virtual-time-budget=240000",
           "--print-to-pdf=" + os.path.abspath(pdf_path), file_url(html_path)]
    if verbose:
        log("Printing with %s..." % os.path.basename(browser))
    subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=600)
    if not os.path.isfile(pdf_path):
        raise SystemExit("The browser did not write the PDF.")
    return pdf_path


def check_pdf(pdf_path, rows, per_page=12, verbose=True):
    """Warns if any specimen fell back to the default font."""
    try:
        import pymupdf
    except Exception:
        return []
    d = pymupdf.open(pdf_path)
    npg = -(-len(rows) // per_page)
    first = d.page_count - npg
    bad = []
    for pi in range(first, d.page_count):
        pg = d[pi]
        W, H = pg.rect.width, pg.rect.height
        top, height = H * 0.085, (H - H * 0.085 - H * 0.045) / (per_page // 2)
        for b in pg.get_text("dict")["blocks"]:
            for line in b.get("lines", []):
                for s in line["spans"]:
                    if s["size"] < 30 or "Segoe" not in s["font"]:
                        continue
                    x0, y0, x1, _ = s["bbox"]
                    col = 0 if (x0 + x1) / 2 < W / 2 else 1
                    row = min(int(max(0, y0 - top) // height), per_page // 2 - 1)
                    idx = (pi - first) * per_page + row * 2 + col
                    if idx < len(rows):
                        nm = display_name(rows[idx])
                        if "segoe" not in nm.lower():
                            bad.append(nm)
    if verbose and bad:
        log("Warning: %d specimens did not load their font: %s" % (len(bad), ", ".join(bad[:8])))
    return bad


def write_csv(rows, path):
    """Licence table for a spreadsheet: one row per family, links included."""
    import csv
    with open(path, "w", encoding="utf-8-sig", newline="") as fh:      # utf-8-sig: Excel reads accents right
        w = csv.writer(fh)
        w.writerow(["no", "family", "style", "licence", "kind", "embedding", "foundry", "foundry_url", "licence_url",
                    "licence_text_in_file"])
        for i, r in enumerate(rows, 1):
            L = licence_of(r)
            w.writerow([i, display_name(r), r["sub"], L["licence"], L["kind"], L["embedding"], L["foundry"],
                        L["home"], L["licence_url"], " ".join((r.get("lic_text") or "").split())])
    return path


def postscript_name(r):
    """PostScript name (name ID 6): what Illustrator and other apps use to pick the face."""
    try:
        tt = TTFont(r["path"], fontNumber=r["i"], lazy=True)
        return tt["name"].getDebugName(6) or ""
    except Exception:
        return ""


def write_illustrator_json(rows, word, path):
    """The chosen faces for illustrator/TypeSpecimen.jsx: one artboard per face, the word as live text."""
    faces = []
    for r in rows:
        L = licence_of(r)
        faces.append({"name": display_name(r), "family": r["fam"], "style": r["sub"],
                      "postscript": postscript_name(r), "stem": round(r["stem"], 3),
                      "licence": L["licence"], "foundry": L["foundry"]})
    with open(path, "w", encoding="utf-8") as fh:
        json.dump({"word": word, "faces": faces}, fh, ensure_ascii=True, indent=1)
    return path


# ===================================================================== 4. cli
def parse_args(argv=None):
    ap = argparse.ArgumentParser(
        description="Specimen PDF of the installed typefaces that meet a set of requirements.")
    ap.add_argument("word", nargs="?", default="Specimen", help="sample word")
    ap.add_argument("--out", default=os.path.join(HERE, "out"), help="output folder")
    ap.add_argument("--name", default=None, help="PDF file name (no folder)")
    ap.add_argument("--title", default=None, help="cover title")
    ap.add_argument("--fonts-dir", action="append", default=None,
                    help="scan this folder instead of the system fonts (repeatable; not cached)")
    ap.add_argument("--min-stem", type=float, default=0.25,
                    help="minimum stem ratio (0.25 = Arial Bold, 0.33 = Black)")
    ap.add_argument("--with-serif", dest="sans_only", action="store_false",
                    help="do not drop serif typefaces")
    ap.add_argument("--all-weights", dest="heavy_only", action="store_false",
                    help="do not drop light weights")
    ap.add_argument("--italics", action="store_true", help="include italics")
    ap.add_argument("--oddities", action="store_true",
                    help="include scripts, outlines, textures and decoratives")
    ap.add_argument("--rescan", action="store_true", help="ignore the saved scan")
    ap.add_argument("--list-only", action="store_true", help="write the list, no PDF")
    ap.add_argument("--selection", default=None,
                    help="file with typeface names (one per line or comma-separated): "
                         "a PDF with only those, no filters")
    ap.add_argument("--search", default=None,
                    help="list the installed faces matching this text and exit")
    ap.add_argument("--per-page", type=int, default=None,
                    help="specimens per sheet (12 by default; 6 or 8 for a selection)")
    return ap.parse_args(argv)


def main(argv=None):
    op = parse_args(argv)
    data = load_scan(op.word, op.rescan, op.fonts_dir)

    if op.search:
        q = _norm(op.search)
        hits = [r for r in data["rows"]
                if q in _norm(r["full"]) or q in _norm(r["gdi"] + " " + (r["sub"] or ""))]
        hits.sort(key=lambda r: (display_name(r).lower(), -r["stem"]))
        for r in hits:
            print("%-50s stem %.2f  serif %.2f  %s%s"
                  % (display_name(r), r["stem"], r["serif"],
                     "italic " if r["it"] else "",
                     origin(r)))
        log("%d faces" % len(hits))
        return []

    if op.selection:
        requests = read_selection(op.selection)
        chosen, missing, notes = [], [], []
        used = set()
        for p in requests:
            r, _ = find_face(p, data["rows"], op.italics)
            if not r:
                missing.append(p)
                continue
            key = (r["path"], r["i"])
            r = dict(r)
            r["requested"] = p
            if key in used:
                notes.append("%s -> already in: %s" % (p, display_name(r)))
                continue
            used.add(key)
            if _norm(p) != _norm(display_name(r)):
                notes.append("%-34s -> %s" % (p, display_name(r)))
            chosen.append(r)
        log("Requested %d, found %d." % (len(requests), len(chosen)))
        for d in notes:
            log("   ~ " + d)
        for s in missing:
            log("   X not installed: " + s)
        if not chosen:
            raise SystemExit("None of the requested typefaces is installed.")
    else:
        kept, rejected = apply_filters(data["rows"], op)
        log("Rejected: %s" % dict(rejected))
        chosen = one_per_family(kept)
        if not op.oddities:
            chosen = drop_textures(chosen, op.word)
        chosen.sort(key=lambda r: (display_name(r).lower(), r["stem"]))
        log("%d families meet the requirements." % len(chosen))

    os.makedirs(op.out, exist_ok=True)
    base = re.sub(r"[^A-Za-z0-9_-]+", "_", op.word) or "specimen"
    if op.title:
        title = op.title
    elif op.selection:
        title = "Shortlist"
    elif op.heavy_only and op.sans_only:
        title = "Every heavy sans serif\non this computer"
    else:
        traits = (["sans serif"] if op.sans_only else []) + (["heavy"] if op.heavy_only else [])
        title = "Type specimen" + (" — " + ", ".join(traits) if traits else "")
    txt = os.path.join(op.out, base + ("_shortlist.txt" if op.selection else "_list.txt"))
    with open(txt, "w", encoding="utf-8") as fh:
        for i, r in enumerate(chosen, 1):
            L = licence_of(r)
            fh.write("%4d  %-52s stem %.2f  %-7s %-7s %-34s %s\n"
                     % (i, display_name(r), r["stem"], weight_label(r), origin(r), L["licence"], L["home"]))
    csv_path = write_csv(chosen, os.path.join(op.out, base + ("_shortlist" if op.selection else "") + "_licences.csv"))
    ai_json = write_illustrator_json(chosen, op.word, os.path.join(
        op.out, base + ("_shortlist" if op.selection else "") + "_illustrator.json"))
    print(txt)
    print(csv_path)
    print(ai_json)
    if op.list_only:
        return chosen

    html_path = os.path.join(op.out, base + "_specimen.html")
    per_page = op.per_page or (6 if op.selection else 12)
    per_page = max(2, per_page - per_page % 2)
    build_html(chosen, op.word, data["files"], html_path, title,
               per_page=per_page, with_lists=not op.selection)
    pdf = os.path.join(op.out, op.name or (base + "_specimen.pdf"))
    to_pdf(html_path, pdf)
    check_pdf(pdf, chosen, per_page)
    try:
        os.remove(html_path)
    except OSError:
        pass
    print(pdf)
    return chosen


if __name__ == "__main__":
    main()
