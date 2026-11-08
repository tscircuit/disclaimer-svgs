"""Build 1:1 marking SVGs and dimensionally aligned review overlays."""

import argparse
import json
import xml.etree.ElementTree as ET
from html import escape
from pathlib import Path

from fontTools.pens.boundsPen import BoundsPen
from fontTools.pens.svgPathPen import SVGPathPen
from fontTools.pens.transformPen import TransformPen
from fontTools.ttLib import TTFont
from shapely.affinity import rotate, translate as move
from shapely.geometry import Point, Polygon, box
from shapely.ops import unary_union

ROOT = Path(__file__).resolve().parent
NOTICES = ("DEVELOPMENT USE ONLY", "NOT FCC APPROVED", "NOT FOR RESALE")
EDGE_CLEARANCE = 0.45
FEATURE_CLEARANCE = 0.25

def mm(value):
    return f"{value:.5f}".rstrip("0").rstrip(".") if value else "0"

def path(d, label):
    return f'<path aria-label="{escape(label)}" d="{d}"/>'

def ring(points):
    return "M " + " L ".join(f"{mm(x)} {mm(-y)}" for x, y in points) + " Z"

def polygon_path(shape):
    return " ".join(ring(r.coords) for r in [shape.exterior, *shape.interiors])

class Lettering:
    def __init__(self, file):
        font = TTFont(file)
        self.glyphs = font.getGlyphSet()
        self.cmap = font.getBestCmap()
        h = BoundsPen(self.glyphs)
        self.glyphs[self.cmap[ord("H")]].draw(h)
        self.cap = h.bounds[3]

    def run(self, message, y, height, maximum, tracking=0.052):
        names = [self.cmap[ord(c)] for c in message]
        offset = 0
        bounds = BoundsPen(self.glyphs)
        positions = []
        for name in names:
            glyph = self.glyphs[name]
            glyph.draw(TransformPen(bounds, (1, 0, 0, 1, offset, 0)))
            positions.append(offset)
            offset += glyph.width + self.cap * tracking
        left, bottom, right, top = bounds.bounds
        scale = min(height / self.cap, maximum / (right - left))
        pen = SVGPathPen(self.glyphs, ntos=mm)
        for name, advance in zip(names, positions):
            self.glyphs[name].draw(TransformPen(pen, (
                scale, 0, 0, -scale, (advance - (left + right) / 2) * scale,
                -y + (bottom + top) / 2 * scale,
            )))
        w, h = (right - left) * scale, (top - bottom) * scale
        return path(pen.getCommands(), message), box(-w / 2, y - h / 2, w / 2, y + h / 2)

def translate(item, x):
    svg, bounds = item
    return f'<g transform="translate({mm(x)} 0)">{svg}</g>', move(bounds, xoff=x)

def logo(brand_asset, y, width):
    vb, paths = brand_asset
    w, h = vb[2:]
    scale = width / w
    height = h * scale
    transform = f"translate({mm(width / 2)} {mm(-y - height / 2)}) scale({-scale:.9f} {scale:.9f})"
    svg = f'<g aria-label="tscircuit logo" transform="{transform}" fill-rule="evenodd">{paths}</g>'
    return svg, box(-width / 2, y - height / 2, width / 2, y + height / 2)

def hazard(y, size):
    points = [(0, y + size / 2), (size * .56, y - size / 2), (-size * .56, y - size / 2)]
    border = Polygon(points)
    hollow = border.buffer(-size * .106, join_style="mitre")
    bar = box(-size * .048, y - size * .13, size * .048, y + size * .104)
    dot = Point(0, y - size * .252).buffer(size * .057, quad_segs=6)
    icon = border.difference(hollow).union(bar).union(dot)
    d = " ".join(polygon_path(part) for part in icon.geoms)
    return f'<path aria-label="Hazard" fill-rule="evenodd" d="{d}"/>', border

def pair(left, right, x, gap):
    left_width = left[1].bounds[2] - left[1].bounds[0]
    right_width = right[1].bounds[2] - right[1].bounds[0]
    return [
        translate(left, x - (gap + right_width) / 2),
        translate(right, x + (gap + left_width) / 2),
    ]

def composition(board, lettering, brand_asset):
    settings = board["layout"]
    x, width = settings["center"], settings["width"]
    marks = []
    for name, (px, y, h, *angles) in board["labels"].items():
        label = translate(lettering.run(name, y, h, 7.8, .10), px)
        if angles:
            svg, bounds = label
            angle = angles[0]
            label = (f'<g transform="rotate({angle} {mm(px)} {mm(-y)})">{svg}</g>',
                     rotate(bounds, -angle, origin=(px, y)))
        marks.append(label)
    brand = lettering.run("Made by AI with tscircuit", *settings["brand"],
                          width - 2.6 if settings.get("inline_brand") else width, .015)
    mark = logo(brand_asset, *settings["logo"])
    if settings.get("inline_brand"):
        marks.extend(pair(mark, brand, x, .55))
    else:
        marks.extend([translate(mark, x), translate(brand, x)])

    y, h = settings["hazard"]
    marks.extend(pair(hazard(y, h * 1.22), lettering.run("DANGEROUS", y, h, width - h * 2), x, h * .36))
    marks.append(translate(lettering.run("USE AT YOUR OWN RISK", *settings["risk"], width, .054), x))
    divider_y = settings["divider"]
    line = box(x - width * .40, divider_y - .10, x + width * .40, divider_y + .10)
    marks.append((path(polygon_path(line), "Divider"), line))
    marks.extend(translate(lettering.run(message, y, h, width), x)
                 for message, (y, h) in zip(NOTICES, settings["notices"]))
    return marks

def export(slug, board, lettering, brand_asset):
    w, h = board["size"]
    face = Polygon(board["outline"]) if "outline" in board else box(-w / 2, -h / 2, w / 2, h / 2)
    mounts = [Point(x, y).buffer(keepout, quad_segs=32) for x, y, _, keepout in board["mounts"]]
    openings = [box(*rect) for rect in board["keepouts"].values()]
    if board["basis"] == "Cover CAD":
        face = face.difference(openings[0].buffer(-.8).buffer(.8, quad_segs=12))
    # Keep four corner squares blank even on boards whose mounting axes sit farther in.
    corners = [box(x - w * .08, y - h * .08, x + w * .08, y + h * .08)
               for x in (-w / 2, w / 2) for y in (-h / 2, h / 2)]
    features = unary_union([*mounts, *openings, *corners])
    marks = composition(board, lettering, brand_asset)
    for i, (svg, bounds) in enumerate(marks):
        if not face.buffer(-EDGE_CLEARANCE).covers(bounds) or bounds.distance(features) < FEATURE_CLEARANCE:
            raise ValueError(f"{slug} clearance failure for mark {i}: {svg[:100]} ({bounds.distance(features):.3f} mm)")
        for _, other in marks[:i]:
            if bounds.intersection(other).area > 1e-6:
                raise ValueError(f"{slug} overlapping marks: {svg[:100]}")
    ink = "\n".join(svg for svg, _ in marks)
    dims = f'width="{mm(w)}mm" height="{mm(h)}mm" viewBox="{mm(-w/2)} {mm(-h/2)} {mm(w)} {mm(h)}"'
    title = escape(f'{board["title"]} · {board["revision"]} · {board["basis"]}')
    meta = f'<title>{title}</title><desc>Exterior face. DANGEROUS - USE AT YOUR OWN RISK. Black fills are markings.</desc>'
    root = f'<svg xmlns="http://www.w3.org/2000/svg" {dims}>'
    (ROOT / "artwork" / f"{slug}.svg").write_text(f'{root}\n{meta}\n<g fill="#000">\n{ink}\n</g>\n</svg>\n')
    guide = [
        f'<path fill="#f3f5f7" stroke="#8f9aa8" stroke-width=".11" d="{polygon_path(face)}"/>',
        *[f'<path fill="#e2e8ee" d="{polygon_path(corner.intersection(face))}"/>'
          for corner in corners if corner.intersection(face).geom_type == "Polygon" and not corner.intersection(face).is_empty],
        *[f'<path fill="#dbe8f4" stroke="#557a9e" stroke-width=".10" stroke-dasharray=".36 .23" d="{polygon_path(window)}"><title>{escape(name)}</title></path>'
          for name, window in zip(board["keepouts"], openings) if board["basis"] != "Cover CAD"],
    ]
    for x, y, drill, keepout in board["mounts"]:
        guide.extend([
            f'<circle cx="{mm(x)}" cy="{mm(-y)}" r="{mm(keepout)}" fill="#f1e4d7" stroke="#ac7854" stroke-width=".10" stroke-dasharray=".36 .23"/>',
            f'<circle cx="{mm(x)}" cy="{mm(-y)}" r="{mm(drill/2)}" fill="#fff" stroke="#8f9aa8" stroke-width=".11"/>',
        ])
    guides = "\n".join(guide)
    (ROOT / "proofs" / f"{slug}.svg").write_text(f'{root}\n{meta}\n{guides}\n<g fill="#111923">\n{ink}\n</g>\n</svg>\n')
    edge = min(face.boundary.distance(bounds) for _, bounds in marks)
    clearance = min(features.distance(bounds) for _, bounds in marks)
    print(f"{slug:11} edge ≥ {edge:.2f} mm    keepouts ≥ {clearance:.2f} mm")

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--font", required=True, help="DIN Alternate Bold TrueType font")
    args = parser.parse_args()
    font = Lettering(args.font)
    logo_xml = ET.parse(ROOT / "logo.svg").getroot()
    paths = "".join(f'<path d="{p.attrib["d"]}"/>' for p in logo_xml)
    brand_asset = ([float(v) for v in logo_xml.attrib["viewBox"].split()], paths)
    for slug, board in json.loads((ROOT / "layouts.json").read_text()).items():
        export(slug, board, font, brand_asset)

if __name__ == "__main__":
    main()
