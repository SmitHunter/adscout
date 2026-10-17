"""Pillow-drawn ad creatives for demo mode.

Every image is drawn from an :class:`Art` with simple shapes (no photos, no third-party
media): an abstract product silhouette, a brand palette and an invented wordmark. Drawing
is deterministic: the same ``Art`` always gives the same PNG bytes.
"""

import io
import math
import random
from dataclasses import dataclass
from functools import lru_cache
from typing import Literal

import imagehash
from PIL import Image, ImageDraw, ImageFilter, ImageFont

# "box" is a generic package: off-topic, unrelated and catalogue ads.
Product = Literal["posture", "eye", "led", "phone", "box"]
Layout = Literal["studio", "lifestyle", "split", "review", "offer", "ugc"]
RGB = tuple[int, int, int]

SIZE = 512
WHITE: RGB = (250, 248, 244)
INK: RGB = (24, 24, 28)
SKIN: tuple[RGB, ...] = (
    (241, 205, 178),
    (224, 172, 138),
    (190, 134, 98),
    (141, 94, 64),
    (98, 64, 44),
)


@dataclass(frozen=True)
class Palette:
    top: RGB  # background gradient, top
    bottom: RGB  # background gradient, bottom
    accent: RGB  # badges, glyph, highlights
    product: RGB  # the product's main colour


@dataclass(frozen=True)
class Art:
    product: Product
    layout: Layout
    brand: str
    palette: Palette
    headline: str
    badge: str | None = None
    quote: str = ""
    reviewer: str = ""
    variant: int = 0  # composition jitter; bump it to get a different-looking image


# --- Primitives -----------------------------------------------------------------------------


@lru_cache(maxsize=32)
def _font(size: int) -> ImageFont.FreeTypeFont:
    font = ImageFont.load_default(size=size)
    assert isinstance(font, ImageFont.FreeTypeFont)
    return font


def _mix(a: RGB, b: RGB, t: float) -> RGB:
    return (
        round(a[0] + (b[0] - a[0]) * t),
        round(a[1] + (b[1] - a[1]) * t),
        round(a[2] + (b[2] - a[2]) * t),
    )


def _luma(c: RGB) -> float:
    return 0.2126 * c[0] + 0.7152 * c[1] + 0.0722 * c[2]


def _ink_on(c: RGB) -> RGB:
    return INK if _luma(c) > 150 else WHITE


@lru_cache(maxsize=16)
def _linear_mask(size: tuple[int, int]) -> Image.Image:
    return Image.linear_gradient("L").resize(size)


@lru_cache(maxsize=64)
def _radial_mask(size: int, strength: float) -> Image.Image:
    mask = Image.radial_gradient("L").resize((size, size))
    return mask.point(lambda v: round(max(0.0, 255 - v * 1.4) * strength))


def _gradient(top: RGB, bottom: RGB, size: tuple[int, int] = (SIZE, SIZE)) -> Image.Image:
    mask = _linear_mask(size)
    return Image.composite(Image.new("RGB", size, bottom), Image.new("RGB", size, top), mask)


def _glow(img: Image.Image, cx: float, cy: float, r: float, color: RGB, strength: float) -> None:
    """A soft radial light centred at (cx, cy)."""
    size = max(2, round(2 * r))
    mask = _radial_mask(size, strength)
    img.paste(Image.new("RGB", (size, size), color), (round(cx - r), round(cy - r)), mask)


def _shadow(img: Image.Image, box: tuple[float, float, float, float], darkness: int) -> None:
    """A soft elliptical contact shadow."""
    small = Image.new("L", (SIZE // 4, SIZE // 4), 0)
    d = ImageDraw.Draw(small)
    d.ellipse([v / 4 for v in box], fill=darkness)
    mask = small.filter(ImageFilter.GaussianBlur(4)).resize((SIZE, SIZE))
    img.paste(Image.new("RGB", (SIZE, SIZE), (0, 0, 0)), (0, 0), mask)


def _blobs(rng: random.Random, colors: list[RGB], base: RGB) -> Image.Image:
    """An out-of-focus 'photo' background: big soft shapes, blurred small and scaled up."""
    small = Image.new("RGB", (64, 64), base)
    d = ImageDraw.Draw(small)
    for _ in range(7):
        x, y = rng.uniform(-10, 64), rng.uniform(-10, 64)
        r = rng.uniform(10, 26)
        d.ellipse([x - r, y - r, x + r, y + r], fill=rng.choice(colors))
    return small.filter(ImageFilter.GaussianBlur(6)).resize((SIZE, SIZE), Image.Resampling.BICUBIC)


def _wrap(text: str, font: ImageFont.FreeTypeFont, width: float) -> list[str]:
    lines: list[str] = []
    line = ""
    for word in text.split():
        trial = f"{line} {word}".strip()
        if line and font.getlength(trial) > width:
            lines.append(line)
            line = word
        else:
            line = trial
    if line:
        lines.append(line)
    return lines


def _text_block(
    d: ImageDraw.ImageDraw,
    text: str,
    xy: tuple[float, float],
    size: int,
    fill: RGB,
    width: float,
    *,
    bold: bool = True,
    anchor: Literal["la", "ma"] = "la",
    spacing: float = 1.15,
    max_lines: int = 3,
) -> float:
    """Wrapped text; returns the y below the last line."""
    font = _font(size)
    x, y = xy
    for line in _wrap(text, font, width)[:max_lines]:
        d.text(
            (x, y),
            line,
            font=font,
            fill=fill,
            anchor=anchor,
            stroke_width=1 if bold else 0,
            stroke_fill=fill,
        )
        y += size * spacing
    return y


def _spaced(
    d: ImageDraw.ImageDraw, text: str, xy: tuple[float, float], size: int, fill: RGB
) -> float:
    """Letter-spaced uppercase text (a wordmark); returns its right edge."""
    font = _font(size)
    x, y = xy
    for ch in text.upper():
        d.text((x, y), ch, font=font, fill=fill, stroke_width=1, stroke_fill=fill)
        x += font.getlength(ch) + size * 0.18
    return x


def _wordmark(img: Image.Image, art: Art, xy: tuple[float, float], fill: RGB) -> None:
    d = ImageDraw.Draw(img)
    x, y = xy
    glyph = art.palette.accent
    shape = sum(map(ord, art.brand)) % 3
    if shape == 0:
        d.ellipse([x, y + 1, x + 16, y + 17], fill=glyph)
        d.ellipse([x + 5, y + 6, x + 11, y + 12], fill=fill)
    elif shape == 1:
        d.polygon([(x, y + 17), (x + 8, y + 1), (x + 16, y + 17)], fill=glyph)
    else:
        d.rounded_rectangle([x, y + 1, x + 16, y + 17], radius=4, fill=glyph)
        d.line([x + 4, y + 9, x + 12, y + 9], fill=fill, width=2)
    _spaced(d, art.brand, (x + 24, y), 17, fill)


def _badge(img: Image.Image, text: str, cx: float, cy: float, r: float, color: RGB) -> None:
    d = ImageDraw.Draw(img)
    d.ellipse([cx - r, cy - r, cx + r, cy + r], fill=color)
    d.ellipse([cx - r + 5, cy - r + 5, cx + r - 5, cy + r - 5], outline=_ink_on(color), width=1)
    words = text.split()
    ink = _ink_on(color)
    if len(words) >= 2:
        head, rest = words[0], " ".join(words[1:])
        d.text(
            (cx, cy - 2),
            head,
            font=_font(round(r * 0.62)),
            fill=ink,
            anchor="ms",
            stroke_width=1,
            stroke_fill=ink,
        )
        d.text((cx, cy + r * 0.42), rest, font=_font(round(r * 0.3)), fill=ink, anchor="ms")
    else:
        d.text(
            (cx, cy),
            text,
            font=_font(round(r * 0.5)),
            fill=ink,
            anchor="mm",
            stroke_width=1,
            stroke_fill=ink,
        )


def _stars(d: ImageDraw.ImageDraw, x: float, y: float, size: float, color: RGB) -> None:
    for i in range(5):
        cx, cy = x + i * size * 1.15 + size / 2, y + size / 2
        pts = []
        for k in range(10):
            ang = -math.pi / 2 + k * math.pi / 5
            rad = size / 2 if k % 2 == 0 else size / 4.6
            pts.append((cx + rad * math.cos(ang), cy + rad * math.sin(ang)))
        d.polygon(pts, fill=color)


# --- Product silhouettes (drawn on a transparent layer, then composited) ---------------------


def _layer() -> tuple[Image.Image, ImageDraw.ImageDraw]:
    layer = Image.new("RGBA", (SIZE, SIZE), (0, 0, 0, 0))
    return layer, ImageDraw.Draw(layer)


def _posture(cx: float, cy: float, s: float, p: Palette) -> Image.Image:
    """A figure-eight back brace: shoulder loops, a back panel and side straps."""
    layer, d = _layer()
    body = p.product
    edge = _mix(body, INK, 0.35)
    light = _mix(body, WHITE, 0.35)
    loop_w = max(6, round(s * 0.085))
    for side in (-1, 1):
        lx = cx + side * s * 0.22
        d.ellipse(
            [lx - s * 0.2, cy - s * 0.55, lx + s * 0.2, cy - s * 0.05],
            outline=body,
            width=loop_w,
        )
    # Crossed straps behind the panel.
    d.line([cx - s * 0.36, cy - s * 0.3, cx + s * 0.3, cy + s * 0.38], fill=edge, width=loop_w)
    d.line([cx + s * 0.36, cy - s * 0.3, cx - s * 0.3, cy + s * 0.38], fill=edge, width=loop_w)
    panel = [cx - s * 0.2, cy - s * 0.32, cx + s * 0.2, cy + s * 0.4]
    d.rounded_rectangle(panel, radius=s * 0.08, fill=body, outline=edge, width=2)
    for i in (-1, 0, 1):
        x = cx + i * s * 0.08
        d.line([x, cy - s * 0.25, x, cy + s * 0.33], fill=light, width=max(2, round(s * 0.012)))
    # Side straps with velcro tabs.
    for side in (-1, 1):
        x0 = cx + side * s * 0.18
        x1 = cx + side * s * 0.58
        d.rounded_rectangle(
            [min(x0, x1), cy + s * 0.12, max(x0, x1), cy + s * 0.3], radius=s * 0.06, fill=body
        )
        tab = [x1 - side * s * 0.12, cy + s * 0.13, x1, cy + s * 0.29]
        d.rounded_rectangle(
            [min(tab[0], tab[2]), tab[1], max(tab[0], tab[2]), tab[3]],
            radius=s * 0.05,
            fill=p.accent,
        )
    return layer


def _eye_mask(cx: float, cy: float, s: float, p: Palette, heat: bool = True) -> Image.Image:
    """A contoured eye mask with a strap, quilting and (optionally) heat waves."""
    layer, d = _layer()
    body = p.product
    seam = _mix(body, WHITE, 0.3)
    d.rounded_rectangle(
        [cx - s * 0.75, cy - s * 0.05, cx + s * 0.75, cy + s * 0.05],
        radius=s * 0.04,
        fill=_mix(body, INK, 0.3),
    )
    for side in (-1, 1):
        ex = cx + side * s * 0.24
        d.ellipse([ex - s * 0.32, cy - s * 0.22, ex + s * 0.32, cy + s * 0.24], fill=body)
    d.rectangle([cx - s * 0.24, cy - s * 0.22, cx + s * 0.24, cy + s * 0.1], fill=body)
    # Nose notch.
    d.ellipse([cx - s * 0.1, cy + s * 0.08, cx + s * 0.1, cy + s * 0.32], fill=(0, 0, 0, 0))
    for side in (-1, 1):
        ex = cx + side * s * 0.24
        d.arc(
            [ex - s * 0.22, cy - s * 0.14, ex + s * 0.22, cy + s * 0.16],
            200,
            340,
            fill=seam,
            width=max(2, round(s * 0.012)),
        )
    if heat:
        for i in (-1, 0, 1):
            x0 = cx + i * s * 0.2
            pts = [
                (x0 + math.sin(t / 3) * s * 0.035, cy - s * 0.32 - t * s * 0.018)
                for t in range(0, 14)
            ]
            d.line(pts, fill=p.accent, width=max(3, round(s * 0.02)), joint="curve")
    return layer


def _led_mask(cx: float, cy: float, s: float, p: Palette, glow: RGB) -> Image.Image:
    """A face-shaped LED shield with eye holes and a grid of lit diodes."""
    layer, d = _layer()
    shell = _mix(WHITE, p.product, 0.25)
    d.ellipse([cx - s * 0.36, cy - s * 0.5, cx + s * 0.36, cy + s * 0.5], fill=(*shell, 255))
    for side in (-1, 1):
        ex = cx + side * s * 0.14
        d.ellipse([ex - s * 0.09, cy - s * 0.12, ex + s * 0.09, cy - s * 0.02], fill=(0, 0, 0, 0))
    d.rounded_rectangle(
        [cx - s * 0.1, cy + s * 0.22, cx + s * 0.1, cy + s * 0.26],
        radius=s * 0.02,
        fill=(0, 0, 0, 0),
    )
    step = s * 0.075
    r = max(2.0, s * 0.014)
    y = cy - s * 0.42
    while y < cy + s * 0.45:
        x = cx - s * 0.33
        while x < cx + s * 0.33:
            nx, ny = (x - cx) / (s * 0.34), (y - cy) / (s * 0.48)
            inside = nx * nx + ny * ny < 0.82
            eye = any(
                ((x - (cx + side * s * 0.14)) / (s * 0.13)) ** 2
                + ((y - (cy - s * 0.07)) / (s * 0.09)) ** 2
                < 1
                for side in (-1, 1)
            )
            mouth = abs(x - cx) < s * 0.14 and abs(y - (cy + s * 0.24)) < s * 0.05
            if inside and not eye and not mouth:
                d.ellipse([x - r, y - r, x + r, y + r], fill=(*glow, 255))
            x += step
        y += step
    halo = layer.filter(ImageFilter.GaussianBlur(max(2, round(s * 0.02))))
    out = Image.new("RGBA", (SIZE, SIZE), (0, 0, 0, 0))
    out.alpha_composite(halo)
    out.alpha_composite(layer)
    return out


def _phone_mount(cx: float, cy: float, s: float, p: Palette, tilt: float) -> Image.Image:
    """A phone on a round magnetic puck with a vent clip."""
    layer, d = _layer()
    metal = _mix(p.product, INK, 0.2)
    # Vent clip and arm.
    d.rounded_rectangle(
        [cx - s * 0.05, cy + s * 0.05, cx + s * 0.05, cy + s * 0.48],
        radius=s * 0.03,
        fill=_mix(metal, INK, 0.3),
    )
    d.rounded_rectangle(
        [cx - s * 0.16, cy + s * 0.42, cx + s * 0.16, cy + s * 0.5],
        radius=s * 0.03,
        fill=_mix(metal, INK, 0.45),
    )
    phone, pd = _layer()
    w, h = s * 0.48, s * 0.9
    box = [cx - w / 2, cy - h / 2 - s * 0.05, cx + w / 2, cy + h / 2 - s * 0.05]
    pd.rounded_rectangle(box, radius=s * 0.07, fill=(30, 31, 36, 255))
    screen = [box[0] + s * 0.025, box[1] + s * 0.025, box[2] - s * 0.025, box[3] - s * 0.025]
    sw, sh = round(screen[2] - screen[0]), round(screen[3] - screen[1])
    grad = _gradient(_mix(p.top, WHITE, 0.55), _mix(p.bottom, WHITE, 0.35), (sw, sh))
    gd = ImageDraw.Draw(grad)
    street = _mix(_mix(p.top, WHITE, 0.55), WHITE, 0.6)
    for i in range(1, 6):
        gd.line([0, sh * i / 6, sw, sh * i / 6 + sh * 0.08], fill=street, width=3)
        gd.line([sw * i / 5, 0, sw * i / 5 - sw * 0.1, sh], fill=street, width=2)
    route = [
        (sw * 0.2, sh * 0.85),
        (sw * 0.35, sh * 0.6),
        (sw * 0.7, sh * 0.5),
        (sw * 0.6, sh * 0.2),
    ]
    gd.line(route, fill=p.accent, width=max(3, round(s * 0.02)), joint="curve")
    gd.ellipse([sw * 0.6 - 6, sh * 0.2 - 6, sw * 0.6 + 6, sh * 0.2 + 6], fill=p.accent)
    smask = Image.new("L", (sw, sh), 0)
    ImageDraw.Draw(smask).rounded_rectangle([0, 0, sw, sh], radius=s * 0.05, fill=255)
    phone.paste(grad, (round(screen[0]), round(screen[1])), smask)
    phone = phone.rotate(tilt, resample=Image.Resampling.BICUBIC, center=(cx, cy))
    # The magnet ring peeking out behind the phone.
    d.ellipse([cx - s * 0.2, cy - s * 0.2, cx + s * 0.2, cy + s * 0.2], fill=metal)
    d.ellipse(
        [cx - s * 0.14, cy - s * 0.14, cx + s * 0.14, cy + s * 0.14], outline=p.accent, width=3
    )
    layer.alpha_composite(phone)
    return layer


def _box(cx: float, cy: float, s: float, p: Palette, label: str) -> Image.Image:
    """A product package: front, top and side faces with a label band."""
    layer, d = _layer()
    w, h, dx, dy = s * 0.46, s * 0.62, s * 0.16, s * 0.1
    x0, y0 = cx - w / 2 - dx / 2, cy - h / 2 + dy / 2
    front = p.product
    d.polygon(
        [(x0, y0), (x0 + dx, y0 - dy), (x0 + w + dx, y0 - dy), (x0 + w, y0)],
        fill=_mix(front, WHITE, 0.3),
    )
    d.polygon(
        [(x0 + w, y0), (x0 + w + dx, y0 - dy), (x0 + w + dx, y0 + h - dy), (x0 + w, y0 + h)],
        fill=_mix(front, INK, 0.3),
    )
    d.rectangle([x0, y0, x0 + w, y0 + h], fill=front)
    band = [x0, y0 + h * 0.56, x0 + w, y0 + h * 0.76]
    d.rectangle(band, fill=p.accent)
    size = max(10, round(s * 0.05))
    d.text(
        ((band[0] + band[2]) / 2, (band[1] + band[3]) / 2),
        label.upper()[:12],
        font=_font(size),
        fill=_ink_on(p.accent),
        anchor="mm",
        stroke_width=1,
        stroke_fill=_ink_on(p.accent),
    )
    d.ellipse(
        [x0 + w / 2 - s * 0.08, y0 + h * 0.16, x0 + w / 2 + s * 0.08, y0 + h * 0.16 + s * 0.16],
        outline=_mix(front, WHITE, 0.5),
        width=max(2, round(s * 0.012)),
    )
    return layer


def _product(art: Art, cx: float, cy: float, s: float, rng: random.Random) -> Image.Image:
    p = art.palette
    if art.product == "box":
        return _box(cx, cy, s, p, art.brand)
    if art.product == "posture":
        return _posture(cx, cy, s, p)
    if art.product == "eye":
        return _eye_mask(cx, cy, s, p)
    if art.product == "led":
        return _led_mask(cx, cy, s, p, _led_colour(art))
    return _phone_mount(cx, cy, s, p, rng.uniform(-9, 9))


def _led_colour(art: Art) -> RGB:
    return ((255, 64, 72), (255, 92, 60), (90, 140, 255), (255, 120, 170))[art.variant % 4]


# --- Figures --------------------------------------------------------------------------------


def _figure(
    d: ImageDraw.ImageDraw, cx: float, top: float, s: float, skin: RGB, shirt: RGB, hair: RGB
) -> None:
    """A simple front-facing figure: shoulders, neck, head and hair."""
    d.rounded_rectangle(
        [cx - s * 0.42, top + s * 0.62, cx + s * 0.42, top + s * 1.5], radius=s * 0.22, fill=shirt
    )
    d.rectangle(
        [cx - s * 0.08, top + s * 0.45, cx + s * 0.08, top + s * 0.68], fill=_mix(skin, INK, 0.12)
    )
    d.ellipse([cx - s * 0.2, top + s * 0.02, cx + s * 0.2, top + s * 0.54], fill=skin)
    d.chord([cx - s * 0.22, top - s * 0.02, cx + s * 0.22, top + s * 0.42], 180, 360, fill=hair)


# --- Layouts --------------------------------------------------------------------------------


def _studio(art: Art, rng: random.Random) -> Image.Image:
    p = art.palette
    img = _gradient(p.top, p.bottom)
    ink = _ink_on(_mix(p.top, p.bottom, 0.5))
    cx = SIZE / 2 + rng.uniform(-30, 30)
    cy = SIZE * 0.6 + rng.uniform(-10, 10)
    s = rng.uniform(250, 290) if art.product != "phone" else rng.uniform(230, 260)
    _glow(img, cx, cy - 10, s * 0.75, _mix(p.top, WHITE, 0.45), 0.55)
    # A pedestal disc under the product.
    d = ImageDraw.Draw(img)
    d.ellipse(
        [cx - s * 0.62, cy + s * 0.38, cx + s * 0.62, cy + s * 0.56], fill=_mix(p.bottom, INK, 0.12)
    )
    _shadow(img, (cx - s * 0.48, cy + s * 0.36, cx + s * 0.48, cy + s * 0.5), 70)
    prod = _product(art, cx, cy, s, rng)
    img.paste(prod, (0, 0), prod)
    _wordmark(img, art, (28, 26), ink)
    d = ImageDraw.Draw(img)
    _text_block(d, art.headline, (28, 64), 34, ink, SIZE * 0.62, max_lines=2)
    if art.badge:
        _badge(img, art.badge, SIZE - 78, 92, 52, p.accent)
    return img


def _lifestyle(art: Art, rng: random.Random) -> Image.Image:
    p = art.palette
    skin = SKIN[rng.randrange(len(SKIN))]
    hair = _mix(skin, INK, rng.uniform(0.6, 0.85))
    wall = _mix(p.top, WHITE, 0.35)
    img = _gradient(wall, _mix(p.bottom, WHITE, 0.2))
    d = ImageDraw.Draw(img)
    shirt = _mix(p.bottom, INK, 0.25)
    if art.product == "posture":
        # A window, a plant, a desk and a laptop; the figure wears the brace over a tee.
        d.rectangle([300, 60, 470, 250], fill=_mix(wall, WHITE, 0.6))
        d.line([385, 60, 385, 250], fill=wall, width=6)
        d.line([300, 155, 470, 155], fill=wall, width=6)
        d.ellipse([46, 230, 120, 300], fill=(92, 140, 96))
        d.ellipse([70, 205, 140, 275], fill=(76, 124, 84))
        d.rectangle([66, 290, 126, 350], fill=_mix(p.accent, INK, 0.2))
        cx = 256 + rng.uniform(-20, 20)
        _figure(d, cx, 120, 250, skin, shirt, hair)
        brace = p.product
        d.line([cx - 90, 290, cx + 70, 430], fill=brace, width=18)
        d.line([cx + 90, 290, cx - 70, 430], fill=brace, width=18)
        for side in (-1, 1):
            d.arc(
                [cx + side * 62 - 44, 262, cx + side * 62 + 44, 330], 180, 360, fill=brace, width=16
            )
        d.rectangle([0, 410, SIZE, SIZE], fill=_mix(p.bottom, INK, 0.35))
        d.rounded_rectangle([cx - 120, 360, cx + 120, 414], radius=6, fill=(48, 50, 58))
        d.rectangle([cx - 140, 410, cx + 140, 420], fill=(70, 72, 80))
    elif art.product == "eye":
        night = _mix(p.top, INK, 0.55)
        img = _gradient(night, _mix(p.bottom, INK, 0.4))
        d = ImageDraw.Draw(img)
        d.rectangle([330, 50, 470, 200], fill=_mix(night, (40, 60, 110), 0.4))
        d.ellipse([400, 80, 440, 120], fill=(244, 236, 210))
        d.ellipse([412, 74, 450, 114], fill=_mix(night, (40, 60, 110), 0.4))
        d.rounded_rectangle([40, 250, 300, 340], radius=40, fill=(236, 232, 226))
        d.ellipse([110, 222, 230, 340], fill=skin)
        d.chord([96, 210, 244, 330], 150, 330, fill=hair)
        mask = _eye_mask(170, 268, 150, p, heat=False)
        img.paste(mask, (0, 0), mask)
        d = ImageDraw.Draw(img)
        d.rounded_rectangle([170, 300, 540, 470], radius=60, fill=_mix(p.bottom, WHITE, 0.15))
        for i in range(3):
            d.arc(
                [220 + i * 90, 330, 330 + i * 90, 420],
                200,
                330,
                fill=_mix(p.bottom, INK, 0.2),
                width=4,
            )
        _glow(img, 170, 250, 120, p.accent, 0.35)
    elif art.product == "led":
        d.rounded_rectangle([20, 300, 492, 470], radius=50, fill=_mix(p.bottom, INK, 0.3))
        cx = 256 + rng.uniform(-16, 16)
        _figure(d, cx, 120, 300, skin, _mix(WHITE, p.top, 0.25), hair)
        d.chord([cx - 70, 120, cx + 70, 240], 180, 360, fill=hair)
        mask = _led_mask(cx, 205, 190, p, _led_colour(art))
        img = img.convert("RGBA")
        _glow_rgba(img, cx, 205, 170, _led_colour(art), 0.45)
        img.alpha_composite(mask)
        img = img.convert("RGB")
    else:
        sky = _gradient(_mix(p.top, (255, 196, 140), 0.45), _mix(p.bottom, (255, 140, 90), 0.3))
        img = sky
        d = ImageDraw.Draw(img)
        d.polygon([(0, 300), (226, 214), (286, 214), (SIZE, 300)], fill=(70, 74, 82))
        d.line([(256, 216), (236, 300)], fill=(240, 220, 120), width=4)
        d.polygon(
            [(-20, 330), (140, 270), (372, 270), (532, 330), (532, SIZE), (-20, SIZE)],
            fill=(36, 38, 44),
        )
        d.arc([-120, 300, 260, 680], 230, 330, fill=(22, 22, 26), width=34)
        for i in range(4):
            d.rounded_rectangle([300 + i * 30, 380, 322 + i * 30, 440], radius=6, fill=(56, 58, 66))
        mount = _phone_mount(370, 300, 210, p, rng.uniform(-6, 6))
        img.paste(mount, (0, 0), mount)
    ink = WHITE if art.product in ("eye", "phone") else _ink_on(wall)
    # A legibility bar behind the headline.
    bar = Image.new("RGB", (SIZE, 118), _mix(p.bottom, INK, 0.65))
    fade = _linear_mask((SIZE, 118)).point(lambda v: min(225, v + 30))
    img.paste(bar, (0, SIZE - 118), fade)
    d = ImageDraw.Draw(img)
    _text_block(d, art.headline, (28, SIZE - 86), 30, WHITE, SIZE - 56, max_lines=2)
    _wordmark(img, art, (28, 24), ink)
    return img


def _glow_rgba(
    img: Image.Image, cx: float, cy: float, r: float, color: RGB, strength: float
) -> None:
    size = max(2, round(2 * r))
    mask = _radial_mask(size, strength)
    glow = Image.new("RGBA", (size, size), (*color, 0))
    glow.putalpha(mask)
    img.alpha_composite(glow, (round(cx - r), round(cy - r)))


def _split(art: Art, rng: random.Random) -> Image.Image:
    p = art.palette
    img = Image.new("RGB", (SIZE, SIZE))
    dull = _gradient((168, 168, 172), (128, 128, 134), (SIZE // 2, SIZE))
    bright = _gradient(p.top, p.bottom, (SIZE // 2, SIZE))
    img.paste(dull, (0, 0))
    img.paste(bright, (SIZE // 2, 0))
    d = ImageDraw.Draw(img)
    if art.product == "posture":
        # Side views: a slouched spine, then an upright one wearing the brace.
        for half, upright in ((0, False), (1, True)):
            ox = half * SIZE / 2 + SIZE / 4 + rng.uniform(-8, 8)
            skin = SKIN[(art.variant + half) % len(SKIN)]
            colour = _mix(skin, (150, 150, 150), 0.6) if not upright else skin
            if upright:
                spine = [(ox, 400), (ox + 4, 320), (ox, 240), (ox + 2, 190)]
                head = (ox + 2, 160)
            else:
                spine = [(ox - 10, 400), (ox + 12, 320), (ox + 34, 250), (ox + 58, 214)]
                head = (ox + 74, 196)
            d.line(spine, fill=colour, width=58, joint="curve")
            d.ellipse([head[0] - 36, head[1] - 36, head[0] + 36, head[1] + 36], fill=colour)
            if upright:
                d.line([(ox - 26, 236), (ox + 26, 330)], fill=p.product, width=12)
                d.line([(ox + 26, 236), (ox - 26, 330)], fill=p.product, width=12)
    else:
        for half, fresh in ((0, False), (1, True)):
            ox = half * SIZE / 2 + SIZE / 4
            skin = SKIN[(art.variant + 1) % len(SKIN)]
            face = skin if fresh else _mix(skin, (150, 140, 130), 0.45)
            d.ellipse([ox - 84, 150, ox + 84, 370], fill=face)
            if fresh:
                _glow(img, ox, 250, 150, _mix(_led_colour(art), WHITE, 0.4), 0.35)
                d = ImageDraw.Draw(img)
                d.ellipse([ox - 84, 150, ox + 84, 370], fill=face)
                d.arc([ox - 40, 190, ox + 40, 250], 200, 250, fill=WHITE, width=5)
            else:
                spots = random.Random(art.variant * 7 + 3)
                for _ in range(16):
                    x, y = ox + spots.uniform(-58, 58), 180 + spots.uniform(10, 170)
                    d.ellipse([x - 4, y - 4, x + 4, y + 4], fill=_mix(face, (150, 60, 60), 0.5))
            d.arc([ox - 46, 220, ox - 10, 240], 200, 340, fill=INK, width=4)
            d.arc([ox + 10, 220, ox + 46, 240], 200, 340, fill=INK, width=4)
            d.arc([ox - 26, 300, ox + 26, 330], 20, 160, fill=_mix(face, INK, 0.5), width=4)
    d.rectangle([SIZE / 2 - 2, 0, SIZE / 2 + 2, SIZE], fill=WHITE)
    for half, label in ((0, "BEFORE"), (1, "AFTER")):
        x = half * SIZE / 2 + 20
        d.rounded_rectangle([x, 22, x + 92, 50], radius=4, fill=INK if half == 0 else p.accent)
        _spaced(d, label, (x + 10, 27), 15, WHITE if half == 0 else _ink_on(p.accent))
    bar = _mix(p.bottom, INK, 0.7)
    d.rectangle([0, SIZE - 104, SIZE, SIZE], fill=bar)
    _text_block(
        d, art.headline, (SIZE / 2, SIZE - 86), 28, WHITE, SIZE - 60, anchor="ma", max_lines=2
    )
    _spaced(d, art.brand, (SIZE - 24 - len(art.brand) * 13.5, 470), 12, _mix(WHITE, bar, 0.35))
    return img


def _review(art: Art, rng: random.Random) -> Image.Image:
    p = art.palette
    img = _gradient(p.top, p.bottom)
    ink = _ink_on(_mix(p.top, p.bottom, 0.5))
    d = ImageDraw.Draw(img)
    card = [36, 96, SIZE - 36, 360]
    _shadow(img, (card[0] + 10, card[3] - 16, card[2] - 10, card[3] + 20), 60)
    d = ImageDraw.Draw(img)
    d.rounded_rectangle(card, radius=18, fill=WHITE)
    _stars(d, card[0] + 28, card[1] + 26, 24, (245, 176, 34))
    quote = art.quote or art.headline
    y = _text_block(
        d, f"“{quote}”", (card[0] + 28, card[1] + 70), 26, INK, card[2] - card[0] - 56, max_lines=4
    )
    d.text(
        (card[0] + 28, min(y + 8, card[3] - 40)),
        f"- {art.reviewer or 'Verified buyer'}",
        font=_font(17),
        fill=_mix(INK, WHITE, 0.45),
    )
    s = 190 if art.product != "phone" else 170
    cx = SIZE - 128 + rng.uniform(-10, 10)
    cy = SIZE - 96
    prod = _product(art, cx, cy, s, rng)
    img.paste(prod, (0, 0), prod)
    _wordmark(img, art, (36, 40), ink)
    d = ImageDraw.Draw(img)
    _text_block(d, art.headline, (36, 392), 24, ink, SIZE * 0.5, max_lines=3)
    return img


def _offer(art: Art, rng: random.Random) -> Image.Image:
    p = art.palette
    base = _mix(p.accent, INK, 0.15)
    img = _gradient(base, _mix(base, INK, 0.35))
    ink = _ink_on(base)
    d = ImageDraw.Draw(img)
    for i in range(9):
        x = -200 + i * 110 + rng.uniform(-10, 10)
        d.polygon(
            [(x, SIZE), (x + 60, SIZE), (x + 360, 0), (x + 300, 0)], fill=_mix(base, WHITE, 0.06)
        )
    big = art.badge or "SALE"
    _text_block(d, big, (SIZE / 2, 40), 82, ink, SIZE - 40, anchor="ma", max_lines=1)
    _text_block(d, art.headline, (SIZE / 2, 140), 26, ink, SIZE - 80, anchor="ma", max_lines=2)
    cx, cy = SIZE / 2 + rng.uniform(-20, 20), 350
    _glow(img, cx, cy, 190, _mix(base, WHITE, 0.5), 0.5)
    _shadow(img, (cx - 150, cy + 90, cx + 150, cy + 130), 80)
    s = 250 if art.product != "phone" else 230
    prod = _product(art, cx, cy, s, rng)
    img.paste(prod, (0, 0), prod)
    d = ImageDraw.Draw(img)
    d.polygon(
        [
            (SIZE - 170, SIZE - 64),
            (SIZE, SIZE - 64),
            (SIZE, SIZE - 24),
            (SIZE - 170, SIZE - 24),
            (SIZE - 150, SIZE - 44),
        ],
        fill=WHITE,
    )
    _spaced(d, "TODAY ONLY", (SIZE - 140, SIZE - 54), 14, INK)
    _wordmark(img, art, (24, SIZE - 52), ink)
    return img


def _ugc(art: Art, rng: random.Random) -> Image.Image:
    p = art.palette
    tones = [
        _mix(p.top, WHITE, 0.3),
        _mix(p.bottom, INK, 0.2),
        _mix(p.accent, WHITE, 0.4),
        (200, 190, 178),
    ]
    img = _blobs(rng, tones, _mix(p.top, p.bottom, 0.5))
    cx, cy = SIZE / 2 + rng.uniform(-40, 40), SIZE * 0.55
    s = rng.uniform(250, 300)
    _shadow(img, (cx - s * 0.45, cy + s * 0.3, cx + s * 0.45, cy + s * 0.48), 60)
    prod = _product(art, cx, cy, s, rng)
    img.paste(prod, (0, 0), prod)
    d = ImageDraw.Draw(img)
    # Story-style caption pills.
    font = _font(25)
    lines = _wrap(art.headline, font, SIZE - 120)[:3]
    y = 70 + rng.uniform(0, 30)
    for line in lines:
        w = font.getlength(line)
        d.rounded_rectangle(
            [SIZE / 2 - w / 2 - 14, y - 6, SIZE / 2 + w / 2 + 14, y + 32], radius=8, fill=WHITE
        )
        d.text(
            (SIZE / 2, y + 13),
            line,
            font=font,
            fill=INK,
            anchor="mm",
            stroke_width=1,
            stroke_fill=INK,
        )
        y += 42
    handle = "@" + art.brand.lower().replace(" ", "").replace("&", "and")
    d.text(
        (24, SIZE - 40), handle, font=_font(18), fill=WHITE, stroke_width=1, stroke_fill=(0, 0, 0)
    )
    # A paused-video progress bar.
    d.rectangle([0, SIZE - 6, SIZE, SIZE], fill=(255, 255, 255))
    d.rectangle([0, SIZE - 6, SIZE * rng.uniform(0.15, 0.6), SIZE], fill=p.accent)
    return img


_LAYOUTS = {
    "studio": _studio,
    "lifestyle": _lifestyle,
    "split": _split,
    "review": _review,
    "offer": _offer,
    "ugc": _ugc,
}


def render(art: Art) -> Image.Image:
    rng = random.Random(f"{art.brand}|{art.layout}|{art.headline}|{art.variant}")
    return _LAYOUTS[art.layout](art, rng)


def jpeg_bytes(img: Image.Image) -> bytes:
    """Baseline JPEG, like the creatives Meta serves."""
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=86)
    return buf.getvalue()


def phash(img: Image.Image) -> str:
    return str(imagehash.phash(img))
