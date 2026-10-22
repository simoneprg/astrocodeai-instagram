"""Render an AstroCodice carousel (1080x1350 JPEG slides) from a JSON spec.
spec: {"slides":[{"kicker":"","title":"","body":""},...], "handle":"@astrocodeai"}
usage: python3 carousel.py spec.json outdir
"""
import json, math, os, random, sys
from PIL import Image, ImageDraw, ImageFilter, ImageFont

W, H = 1080, 1350
import glob
def _pick(*names):
    for n in names:
        hits = glob.glob(f"/usr/share/fonts/**/{n}", recursive=True)
        if hits:
            return hits[0]
    return glob.glob("/usr/share/fonts/**/DejaVuSans*.ttf", recursive=True)[0]
BOLD = _pick("InterDisplay-Bold.otf", "Inter-Bold.otf", "Inter-Bold.ttf", "DejaVuSans-Bold.ttf")
MED = _pick("InterDisplay-Medium.otf", "Inter-Medium.otf", "Inter-Medium.ttf", "DejaVuSans.ttf")
REG = _pick("Inter-Regular.otf", "Inter-Regular.ttf", "DejaVuSans.ttf")
def font(path, size): return ImageFont.truetype(path, size)
RED = (230, 40, 40)
GLYPH = os.environ.get("GLYPH_FONT", "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf")
GLYPHS = "♈♉♊♋♌♍♎♏♐♑♒♓"

BADGE_URL = "https://play.google.com/intl/en_us/badges/static/images/badges/{}_badge_web_generic.png"

def badge(lang):
    """Official 'Get it on Google Play' badge (it/en), cached; None if unavailable."""
    import urllib.request
    path = f"/tmp/gp_badge_{lang}.png"
    if not os.path.exists(path):
        try:
            urllib.request.urlretrieve(BADGE_URL.format(lang), path)
        except Exception:
            return None
    try:
        return Image.open(path).convert("RGBA")
    except Exception:
        return None


def background(seed, ring=True):
    rnd = random.Random(seed)
    img = Image.new("RGB", (W, H), (6, 6, 10))
    d = ImageDraw.Draw(img)
    for _ in range(260):
        x, y, r = rnd.randrange(W), rnd.randrange(H), rnd.choice([1, 1, 1, 2])
        c = rnd.randint(90, 230)
        d.ellipse((x, y, x + r, y + r), fill=(c, c, c))
    if not ring:
        return img
    # eclipse ring glow, top-right
    glow = Image.new("RGB", (W, H), (0, 0, 0))
    g = ImageDraw.Draw(glow)
    cx, cy, R = 860, 230, 170
    for i in range(18):
        g.ellipse((cx - R - i * 3, cy - R - i * 3, cx + R + i * 3, cy + R + i * 3),
                  outline=(int(200 - i * 10), 20, 20), width=4)
    glow = glow.filter(ImageFilter.GaussianBlur(18))
    img = Image.blend(img, Image.composite(glow, img, glow.convert("L")), 0.9)
    d = ImageDraw.Draw(img)
    d.ellipse((cx - R, cy - R, cx + R, cy + R), fill=(4, 4, 6), outline=RED, width=6)
    return img

def wrap(d, text, f, maxw):
    lines = []
    for para in text.split("\n"):
        cur = ""
        for w in para.split():
            t = (cur + " " + w).strip()
            if d.textlength(t, font=f) <= maxw: cur = t
            else: lines.append(cur); cur = w
        lines.append(cur)
    return lines

def rows_slide(s, d, x, maxw):
    """kicker + rows: [{"glyph": 0-11 | null, "name": "", "text": ""}]"""
    y = 130
    if s.get("kicker"):
        d.text((x, y), s["kicker"].upper(), font=font(MED, 34), fill=RED); y += 90
    if s.get("title"):
        tf = font(BOLD, 56)
        for ln in wrap(d, s["title"], tf, maxw):
            d.text((x, y), ln, font=tf, fill=(245, 245, 245)); y += 66
        y += 20
    rows = s["rows"]
    gf, nf, bf = ImageFont.truetype(GLYPH, 88), font(BOLD, 54), font(REG, 40)
    avail = (H - 170) - y
    step = avail // len(rows)
    for r in rows:
        tx = x
        if r.get("glyph") is not None:
            d.text((x, y - 4), GLYPHS[r["glyph"]], font=gf, fill=RED); tx = x + 125
        d.text((tx, y), r["name"], font=nf, fill=(245, 245, 245))
        yy = y + 70
        for ln in wrap(d, r.get("text", ""), bf, maxw - (tx - x))[:4]:
            d.text((tx, yy), ln, font=bf, fill=(195, 195, 205)); yy += 52
        y += step


def slide(s, i, n, handle):
    img = background(i * 7 + 3, ring=not (s.get("rows") or s.get("badge")))
    d = ImageDraw.Draw(img)
    x, maxw = 90, W - 180
    if s.get("rows"):
        rows_slide(s, d, x, maxw)
        return footer(d, i, n, handle, img)
    y = 330 if s.get("badge") else (520 if i else 470)
    if s.get("kicker"):
        d.text((x, y), s["kicker"].upper(), font=font(MED, 34), fill=RED)
        y += 70
    tf = font(BOLD, 84 if i == 0 else 72)
    for ln in wrap(d, s["title"], tf, maxw):
        d.text((x, y), ln, font=tf, fill=(245, 245, 245)); y += int(tf.size * 1.15)
    if s.get("body"):
        y += 30
        bf = font(REG, 40)
        for ln in wrap(d, s["body"], bf, maxw):
            d.text((x, y), ln, font=bf, fill=(185, 185, 195)); y += 56
    if s.get("badge"):
        b = badge(s["badge"])
        if b is not None:
            bw = 620
            b = b.resize((bw, int(b.height * bw / b.width)), Image.LANCZOS)
            img.paste(b, ((W - bw) // 2, H - 200 - b.height), b)
    return footer(d, i, n, handle, img)


def footer(d, i, n, handle, img):
    x = 90
    ff = font(MED, 30)
    d.text((x, H - 110), handle, font=ff, fill=(150, 150, 160))
    pg = f"{i + 1}/{n}"
    d.text((W - 90 - d.textlength(pg, font=ff), H - 110), pg, font=ff, fill=(150, 150, 160))
    if i < n - 1:
        d.text((W - 90 - d.textlength("→", font=font(BOLD, 44)), H - 175), "→", font=font(BOLD, 44), fill=RED)
    return img

def main(spec_path, out):
    spec = json.load(open(spec_path))
    os.makedirs(out, exist_ok=True)
    n = len(spec["slides"])
    paths = []
    for i, s in enumerate(spec["slides"]):
        p = os.path.join(out, f"slide{i + 1:02d}.jpg")
        slide(s, i, n, spec.get("handle", "@astrocodeai")).save(p, "JPEG", quality=92)
        paths.append(p)
    print("\n".join(paths))

if __name__ == "__main__":
    main(*sys.argv[1:3])
