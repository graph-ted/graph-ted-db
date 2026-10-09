#!/usr/bin/env python3
"""Build the graph-ted-db logo SVG from a font and per-word settings.

The circles, connectors, and viewBox are fixed, so the logo's proportions
stay put. Only the three words change. Each word is an outlined path, not
live text.

EB Garamond Regular 1.002 is the shipped cut (SIL Open Font License 1.1).
The font binary is not in the repo. Download it and pass ``--font``:

    curl -fsSL -o /tmp/EBGaramond-Regular.ttf \\
      -L https://github.com/octaviopardo/EBGaramond12/raw/master/fonts/ttf/EBGaramond-Regular.ttf
    python scripts/build_logo_svg.py --font /tmp/EBGaramond-Regular.ttf

To try another font, edit ``scripts/logo_params.json`` (scale, letter_spacing,
dx, dy, stroke for graph / ted / db) and rerun. ``--compare`` renders the
SVG at the PNG master's 340×116 and prints bbox deltas.

    python scripts/build_logo_svg.py --font /path/to/Other.ttf \\
      --compare uploads/graph-ted-db_logo-color-v2.png

``scale`` multiplies the Inkscape font size (10.763 user units).
``letter_spacing`` is extra user units between glyphs, after the font's
own kerning. ``dx`` and ``dy`` shift the word from the Inkscape anchor
(dy positive moves it down). ``stroke`` is the outline width on that word.

Requires: fonttools, uharfbuzz, cairosvg, pillow.
Optional: svgo (float precision 5, circles left as circles).
"""

from __future__ import annotations

import argparse
import io
import json
import shutil
import subprocess
import tempfile
from pathlib import Path

import cairosvg
import uharfbuzz as hb
from fontTools.misc.transform import Transform
from fontTools.pens.svgPathPen import SVGPathPen
from fontTools.pens.transformPen import TransformPen
from fontTools.ttLib import TTFont
from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_PARAMS = Path(__file__).with_name("logo_params.json")
DEFAULT_OUT = ROOT / "docs" / "assets"

# Inkscape group translate. Baking it in drops the wrapper.
TX, TY = -11.230378, -121.71729
CIRCLE_STROKE = 0.75
MITER = 0.2
# name, cx, cy, r in the Inkscape layer (before the translate).
CIRCLES = [
    ("graph", 26.540628, 137.02754, 14.93525),
    ("ted", 60.257706, 136.78825, 10.812576),
    ("db", 89.891068, 136.78825, 10.812576),
]
# Inkscape text anchors: x, y, word. y is the baseline.
ANCHORS = {
    "graph": (26.977343, 139.08018, "graph"),
    "ted": (59.770798, 139.13786, "ted"),
    "db": (89.40416, 139.13786, "db"),
}
RECTS = [
    (41.756516, 136.48346, 7.3687968, 0.61406642, 0.3),
    (71.389877, 136.48346, 7.3687968, 0.61406642, 0.3),
]
PNG_SIZE = (340, 116)
README_WIDTH = 1020

SVGO_CONFIG = """
module.exports = {
  multipass: true,
  floatPrecision: 5,
  plugins: [
    {
      name: "preset-default",
      params: {
        overrides: {
          convertShapeToPath: false,
          removeViewBox: false,
          convertPathData: { floatPrecision: 5 },
          cleanupNumericValues: { floatPrecision: 5 },
        },
      },
    },
  ],
};
"""


def shifted(x, y):
    return x + TX, y + TY


def outer_bounds(names=None):
    xs, ys = [], []
    half = CIRCLE_STROKE / 2
    for name, cx, cy, r in CIRCLES:
        if names and name not in names:
            continue
        x, y = shifted(cx, cy)
        xs += [x - r - half, x + r + half]
        ys += [y - r - half, y + r + half]
    return min(xs), min(ys), max(xs), max(ys)


def font_version(font: TTFont) -> str:
    for rec in font["name"].names:
        if rec.nameID == 5:
            try:
                return rec.toUnicode()
            except Exception:
                continue
    return "unknown"


def shape_font(font_path: Path):
    font = TTFont(font_path)
    upem = font["head"].unitsPerEm
    glyphset = font.getGlyphSet()
    face = hb.Face(hb.Blob.from_file_path(str(font_path)))
    shaped = {}
    for name, (_x, _y, word) in ANCHORS.items():
        hbfont = hb.Font(face)
        hbfont.scale = (upem, upem)
        buf = hb.Buffer()
        buf.add_str(word)
        buf.guess_segment_properties()
        hb.shape(hbfont, buf)
        glyphs = []
        for info, pos in zip(buf.glyph_infos, buf.glyph_positions):
            glyphs.append(
                (
                    font.getGlyphName(info.codepoint),
                    pos.x_advance,
                    pos.x_offset,
                    pos.y_offset,
                )
            )
        shaped[name] = glyphs
    return font, upem, glyphset, shaped


def word_path(name, params, upem, glyphset, shaped, origin):
    word = params["words"][name]
    ax, ay, _text = ANCHORS[name]
    ax, ay = shifted(ax, ay)
    ax += word["dx"]
    ay += word["dy"]
    ox, oy = origin
    ax -= ox
    ay -= oy
    glyphs = shaped[name]
    gscale = (params["base_font_size"] * word["scale"]) / upem
    tracking = word["letter_spacing"]
    advance = sum(g[1] for g in glyphs) * gscale + tracking * (len(glyphs) - 1)
    pen_x = ax - advance / 2
    commands = []
    for i, (gname, adv, xoff, yoff) in enumerate(glyphs):
        t = Transform(gscale, 0, 0, -gscale, pen_x + xoff * gscale, ay - yoff * gscale)
        spen = SVGPathPen(glyphset)
        glyphset[gname].draw(TransformPen(spen, t))
        cmd = spen.getCommands()
        if cmd:
            commands.append(cmd)
        pen_x += adv * gscale
        if i != len(glyphs) - 1:
            pen_x += tracking
    return " ".join(commands)


def fmt(n):
    text = f"{n:.5f}".rstrip("0").rstrip(".")
    return "0" if text == "-0" else text


def build_svg(params, upem, glyphset, shaped, stroke, text, disc, only=None):
    names = [only] if only else [name for name, *_ in CIRCLES]
    minx, miny, maxx, maxy = outer_bounds(names)
    origin = (minx, miny)
    body = []
    for name, cx, cy, r in CIRCLES:
        if name not in names:
            continue
        x, y = shifted(cx, cy)
        body.append(
            f'<circle cx="{fmt(x - minx)}" cy="{fmt(y - miny)}" r="{fmt(r)}" '
            f'fill="{disc}" stroke="{stroke}" stroke-width="{fmt(CIRCLE_STROKE)}" '
            f'stroke-miterlimit="{fmt(MITER)}"/>'
        )
    if not only:
        for x, y, w, h, sw in RECTS:
            x, y = shifted(x, y)
            body.append(
                f'<rect x="{fmt(x - minx)}" y="{fmt(y - miny)}" '
                f'width="{fmt(w)}" height="{fmt(h)}" fill="{stroke}" stroke="{stroke}" '
                f'stroke-width="{fmt(sw)}" stroke-miterlimit="{fmt(MITER)}"/>'
            )
    for name in names:
        d = word_path(name, params, upem, glyphset, shaped, origin)
        sw = params["words"][name]["stroke"]
        body.append(
            f'<path d="{d}" fill="{text}" stroke="{text}" '
            f'stroke-width="{fmt(sw)}" stroke-miterlimit="{fmt(MITER)}"/>'
        )
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" '
        f'viewBox="0 0 {fmt(maxx - minx)} {fmt(maxy - miny)}">' + "".join(body) + "</svg>\n"
    )


def spell_brand(svg_text: str) -> str:
    """svgo shortens #009999 to #099. Keep the brand hex written out."""
    return svg_text.replace("#099", "#009999")


def optimize(svg_text: str, svgo: str | None) -> str:
    if not svgo:
        return spell_brand(svg_text)
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        config = tmp / "svgo.config.js"
        src = tmp / "in.svg"
        dest = tmp / "out.svg"
        config.write_text(SVGO_CONFIG)
        src.write_text(svg_text)
        subprocess.run(
            [svgo, "--config", str(config), "-i", str(src), "-o", str(dest)],
            check=True,
            capture_output=True,
        )
        text = spell_brand(dest.read_text())
        return text if text.endswith("\n") else text + "\n"


def render_svg(svg_text: str, width: int, height: int | None = None) -> Image.Image:
    concrete = (
        svg_text.replace("var(--gt-stroke, #009999)", "#009999")
        .replace("var(--gt-text, #009999)", "#009999")
        .replace("var(--gt-disc, #fff)", "#ffffff")
    )
    kwargs = {"bytestring": concrete.encode(), "output_width": width}
    if height:
        kwargs["output_height"] = height
    return Image.open(io.BytesIO(cairosvg.svg2png(**kwargs))).convert("RGBA")


def write_icons(favicon_svg: str, out: Path):
    def frame(size: int) -> Image.Image:
        inner = size - 2
        big = render_svg(favicon_svg, inner * 8, inner * 8)
        small = big.resize((inner, inner), Image.Resampling.LANCZOS)
        canvas = Image.new("RGBA", (size, size), (0, 0, 0, 0))
        canvas.paste(small, (1, 1), small)
        return canvas

    frames = {size: frame(size) for size in (16, 32, 48)}
    frames[32].save(out / "favicon-32.png")
    frames[48].save(
        out / "favicon.ico",
        format="ICO",
        sizes=[(16, 16), (32, 32), (48, 48)],
        append_images=[frames[16], frames[32]],
    )


def ink_masks(im: Image.Image):
    import numpy as np

    a = np.asarray(im)
    rgb = a[:, :, :3].astype(np.int16)
    alpha = a[:, :, 3]
    not_white = ~((rgb[:, :, 0] > 230) & (rgb[:, :, 1] > 230) & (rgb[:, :, 2] > 230))
    greenish = (rgb[:, :, 1] > rgb[:, :, 0] + 15) & (rgb[:, :, 2] > rgb[:, :, 0] + 15)
    ink = (alpha > 80) & not_white & greenish
    h, w = ink.shape
    sx = w / (outer_bounds()[2] - outer_bounds()[0])
    sy = h / (outer_bounds()[3] - outer_bounds()[1])
    minx, miny, _maxx, _maxy = outer_bounds()
    yy, xx = np.mgrid[0:h, 0:w]
    masks = {}
    for name, cx, cy, r in CIRCLES:
        x, y = shifted(cx, cy)
        pcx = (x - minx) * sx
        pcy = (y - miny) * sy
        inner = (r - CIRCLE_STROKE / 2) * sx - 1.5
        masks[name] = ink & (np.hypot(xx - pcx, yy - pcy) < inner)
    return masks


def word_metrics(mask):
    import numpy as np

    ys, xs = np.where(mask)
    if len(xs) == 0:
        return None
    x0, x1 = int(xs.min()), int(xs.max())
    y0, y1 = int(ys.min()), int(ys.max())
    bottoms, tops = [], []
    for x in range(x0, x1 + 1):
        col = np.where(mask[:, x])[0]
        if len(col):
            bottoms.append(int(col.max()))
            tops.append(int(col.min()))
    bottoms = np.array(bottoms)
    tops = np.array(tops)
    base = int(np.bincount(bottoms, minlength=mask.shape[0]).argmax())
    cap = int(tops.min())
    body = tops[tops > cap + 2]
    xh = int(np.bincount(body, minlength=mask.shape[0]).argmax()) if len(body) else cap
    return {
        "w": x1 - x0 + 1,
        "h": y1 - y0 + 1,
        "cx": (x0 + x1) / 2,
        "base": base,
        "cap_h": base - cap,
        "xh_h": base - xh,
    }


def write_overlay(png_path: Path, rendered: Image.Image, dest: Path):
    import numpy as np

    master = Image.open(png_path).convert("RGBA")
    if master.size != PNG_SIZE:
        raise SystemExit(f"{png_path} is {master.size}, expected {PNG_SIZE}")

    def flat(im):
        base = Image.new("RGB", im.size, (255, 255, 255))
        base.paste(im, mask=im.split()[-1])
        return base

    left, right = flat(master), flat(rendered)
    pa = np.asarray(left).astype(np.int16)
    sa = np.asarray(right).astype(np.int16)

    def ink(arr):
        return (
            (arr[:, :, 1] > arr[:, :, 0] + 15)
            & (arr[:, :, 2] > arr[:, :, 0] + 15)
            & ~((arr[:, :, 0] > 230) & (arr[:, :, 1] > 230) & (arr[:, :, 2] > 230))
        )

    pi, si = ink(pa), ink(sa)
    onion = np.full((*pa.shape[:2], 3), 255, np.uint8)
    onion[pi & ~si] = (200, 0, 60)
    onion[si & ~pi] = (0, 130, 150)
    onion[pi & si] = (20, 20, 20)
    diff = np.clip(np.abs(pa - sa).max(axis=2) * 3, 0, 255).astype(np.uint8)
    diff_rgb = np.stack([diff, diff, diff], axis=2)

    def x2(im):
        return im.resize((im.size[0] * 2, im.size[1] * 2), Image.Resampling.NEAREST)

    panels = [x2(left), x2(right), x2(Image.fromarray(onion)), x2(Image.fromarray(diff_rgb))]
    w, h = panels[0].size
    sheet = Image.new("RGB", (w * 2 + 16, h * 2 + 64), (255, 255, 255))
    draw = ImageDraw.Draw(sheet)
    draw.text(
        (8, 6),
        "Side by side, 340x116 rendered, shown 2x. Left: PNG master. Right: SVG.",
        fill=(0, 0, 0),
    )
    sheet.paste(panels[0], (0, 28))
    sheet.paste(panels[1], (w + 16, 28))
    draw.text(
        (8, 36 + h),
        "Onion skin (PNG only red, SVG only teal, overlap black) and difference.",
        fill=(0, 0, 0),
    )
    sheet.paste(panels[2], (0, 54 + h))
    sheet.paste(panels[3], (w + 16, 54 + h))
    dest.parent.mkdir(parents=True, exist_ok=True)
    sheet.save(dest)


def compare(svg_text: str, png_path: Path, overlay_path: Path | None):
    rendered = render_svg(svg_text, PNG_SIZE[0], PNG_SIZE[1])
    master = Image.open(png_path).convert("RGBA")
    png_m = ink_masks(master)
    svg_m = ink_masks(rendered)
    print(f"{'word':<8} {'dw':>6} {'dh':>6} {'dcx':>8} {'dbase':>7} {'dcap':>6} {'dxh':>6}")
    for name, _cx, _cy, _r in CIRCLES:
        sm = word_metrics(svg_m[name])
        pm = word_metrics(png_m[name])
        print(
            f"{name:<8} {sm['w'] - pm['w']:6d} {sm['h'] - pm['h']:6d} "
            f"{sm['cx'] - pm['cx']:8.1f} {sm['base'] - pm['base']:7d} "
            f"{sm['cap_h'] - pm['cap_h']:6d} {sm['xh_h'] - pm['xh_h']:6d}"
        )
    if overlay_path:
        write_overlay(png_path, rendered, overlay_path)
        print(f"overlay {overlay_path}")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--font", type=Path, required=True, help="TTF/OTF to outline")
    parser.add_argument("--params", type=Path, default=DEFAULT_PARAMS)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument(
        "--svgo", type=Path, default=None, help="svgo binary (default: lookup on PATH)"
    )
    parser.add_argument("--no-svgo", action="store_true")
    parser.add_argument("--compare", type=Path, default=None, help="PNG master, 340x116")
    parser.add_argument(
        "--overlay", type=Path, default=None, help="where to write the overlay image"
    )
    args = parser.parse_args()

    params = json.loads(args.params.read_text())
    font, upem, glyphset, shaped = shape_font(args.font)
    loaded = font_version(font)
    expected = params["font"]["version_string"]
    print(f"font {loaded}")
    if expected not in loaded and loaded not in expected:
        print(f"warning: params were fit to {expected}")

    svgo = None if args.no_svgo else (str(args.svgo) if args.svgo else shutil.which("svgo"))
    if not args.no_svgo and not svgo:
        print("warning: svgo not found; writing unoptimized SVG")

    logo = build_svg(
        params,
        upem,
        glyphset,
        shaped,
        "var(--gt-stroke, #009999)",
        "var(--gt-text, #009999)",
        "var(--gt-disc, #fff)",
    )
    light = build_svg(params, upem, glyphset, shaped, "#ffffff", "#009999", "#ffffff")
    fav = build_svg(params, upem, glyphset, shaped, "#009999", "#009999", "#ffffff", only="db")
    logo, light, fav = optimize(logo, svgo), optimize(light, svgo), optimize(fav, svgo)

    args.out.mkdir(parents=True, exist_ok=True)
    (args.out / "logo.svg").write_text(logo)
    (args.out / "logo-light-stroke.svg").write_text(light)
    (args.out / "favicon.svg").write_text(fav)
    write_icons(fav, args.out)
    readme = render_svg(logo, README_WIDTH)
    readme.save(args.out / "logo-readme.png")
    for name in (
        "logo.svg",
        "logo-light-stroke.svg",
        "favicon.svg",
        "favicon-32.png",
        "favicon.ico",
        "logo-readme.png",
    ):
        print(f"{name} {(args.out / name).stat().st_size}")

    if args.compare:
        compare(logo, args.compare, args.overlay)


if __name__ == "__main__":
    main()
