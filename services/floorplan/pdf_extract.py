"""从向量 PDF 抽出墙、填色、文字和尺寸线。

坐标留在 PDF point。毫米要等两条以上的尺寸线互相同意之后才换算。
fitz 的 rect 已经包含页面变换，抽出的多边形要再跟渲染图对一下。
"""
from __future__ import annotations

from pathlib import Path

import fitz
import numpy as np
from shapely.geometry import Point, Polygon, box
from shapely.ops import unary_union


def open_pdf(path: Path) -> fitz.Document:
    return fitz.open(path)


def find_page(doc: fitz.Document, page: int | None, unit_hint: str | None) -> int:
    """页码优先。没给页码时，用文字找出单位号落在填色房间里的那一页。"""
    if page is not None:
        return page - 1 if page >= 1 else page
    number = _digits(unit_hint)
    if not number:
        return 0
    best = None
    for index, pdf_page in enumerate(doc):
        words = pdf_page.get_text("words")
        hits = [w for w in words if w[4] == number]
        if not hits:
            continue
        for word in hits:
            anchor = Point((word[0] + word[2]) / 2, (word[1] + word[3]) / 2)
            color = _color_at(pdf_page, anchor)
            face = _component_at(_room_fills(pdf_page, color), anchor) if color else None
            if face is None or face.area < 500:
                continue
            score = face.area
            if best is None or score > best[0]:
                best = (score, index)
    return best[1] if best else 0


def render_page(pdf_page, out_path: Path, zoom: float = 2.0, clip=None) -> Path:
    pix = pdf_page.get_pixmap(matrix=fitz.Matrix(zoom, zoom), clip=clip, alpha=False)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    pix.save(out_path)
    return out_path


def extract_vector_plan(pdf_page, unit_hint: str | None) -> dict:
    """目标单位的客厅填色、贴着它的浴室和厨房、以及尺寸线。"""
    words = pdf_page.get_text("words")
    anchor, living = _living_face(pdf_page, words, unit_hint)
    living = _axis_polygon(living)
    if living is None:
        raise ValueError("unit fill was not found")
    window = _window(living)
    horizontals, verticals = _wall_lines(pdf_page, window)
    services = _service_rooms(pdf_page, words, living, horizontals, verticals, unit_hint)
    occupied = living
    for service in services:
        occupied = occupied.union(service["geom"])
        if living.intersection(service["geom"]).area > 0.6 * service["geom"].area:
            living = living.difference(service["geom"].buffer(0.4))
    living = _axis_polygon(living)
    if living is None:
        raise ValueError("unit fill was not found")
    living = _cut_white_pockets(pdf_page, living) or living
    ox0, oy0, ox1, oy1 = occupied.bounds
    dims = _dimension_observations(pdf_page, (ox0 - 15, oy0 - 15, ox1 + 15, oy1 + 15))
    verified = _fill_matches_render(pdf_page, living)
    return dict(
        anchor=(anchor.x, anchor.y),
        living=living,
        services=services,
        dimensions=dims,
        swings=_swing_arcs(pdf_page, occupied.bounds),
        entry_gap=_entry_gap(living, horizontals),
        vector_matches_render=verified,
        page_rect=[pdf_page.rect.x0, pdf_page.rect.y0, pdf_page.rect.x1, pdf_page.rect.y1],
    )


def _digits(unit_hint: str | None) -> str:
    return "".join(ch for ch in (unit_hint or "") if ch.isdigit())


def _living_face(pdf_page, words, unit_hint):
    number = _digits(unit_hint)
    hits = [w for w in words if number and w[4] == number]
    if not hits:
        fills = _room_fills(pdf_page, None)
        if not fills:
            raise ValueError("unit fill was not found")
        largest = max(fills, key=lambda face: face.area)
        return largest.representative_point(), largest
    best = None
    for word in hits:
        anchor = Point((word[0] + word[2]) / 2, (word[1] + word[3]) / 2)
        color = _color_at(pdf_page, anchor)
        if color is None:
            continue
        face = _component_at(_room_fills(pdf_page, color), anchor)
        if face is None:
            continue
        same = _labels_inside(words, face, {"LIV/DIN", "客/飯廳", "客厅"})
        score = face.area + (10000 if same else 0)
        if best is None or score > best[0]:
            best = (score, anchor, face)
    if best is None:
        raise ValueError("unit %s was not found on the page" % (unit_hint or ""))
    return best[1], best[2]


def _color_at(pdf_page, anchor: Point):
    for drawing in pdf_page.get_drawings():
        if drawing["type"] not in ("f", "fs") or not drawing.get("fill"):
            continue
        if not _is_room_color(drawing["fill"]):
            continue
        rect = drawing["rect"]
        if rect.width * rect.height < 4:
            continue
        if rect.contains(fitz.Point(anchor.x, anchor.y)):
            return drawing["fill"]
    return None


def _room_fills(pdf_page, color) -> list:
    parts = []
    for drawing in pdf_page.get_drawings():
        if drawing["type"] not in ("f", "fs") or not drawing.get("fill"):
            continue
        fill = drawing["fill"]
        if color is None:
            if not _is_room_color(fill):
                continue
        elif _color_distance(fill, color) > 0.08:
            continue
        rect = drawing["rect"]
        if rect.width * rect.height < 4:
            continue
        parts.append(box(rect.x0, rect.y0, rect.x1, rect.y1))
    if not parts:
        return []
    union = unary_union([part.buffer(0.55) for part in parts]).buffer(-0.4)
    if union.is_empty:
        return []
    geoms = [union] if union.geom_type == "Polygon" else list(union.geoms)
    return [geom for geom in geoms if geom.area > 30]


def _color_distance(a, b) -> float:
    return sum(abs(a[i] - b[i]) for i in range(3))


def _is_room_color(fill) -> bool:
    r, g, b = fill[:3]
    if r < 0.35 and g < 0.35 and b < 0.35:
        return False
    if r > 0.75 and g < 0.55 and b < 0.45:
        return False
    if r > 0.93 and g > 0.93 and b > 0.93:
        return False
    return max(r, g, b) > 0.45


def _component_at(parts, anchor: Point):
    hits = [part for part in parts if part.contains(anchor) or part.distance(anchor) < 6]
    if not hits:
        return None
    return max(hits, key=lambda part: part.area)


def _labels_inside(words, geom, names: set[str]) -> list[str]:
    found = []
    for word in words:
        if word[4] not in names:
            continue
        point = Point((word[0] + word[2]) / 2, (word[1] + word[3]) / 2)
        if geom.buffer(1.5).contains(point):
            found.append(word[4])
    return found


def _axis_polygon(geom):
    if geom is None or geom.is_empty:
        return None
    geom = geom.simplify(1.0, preserve_topology=True)
    if geom.geom_type != "Polygon":
        geom = max(geom.geoms, key=lambda item: item.area)
    coords = [(float(x), float(y)) for x, y in list(geom.exterior.coords)[:-1]]
    if len(coords) < 3:
        return None
    for index in range(len(coords)):
        x1, y1 = coords[index]
        x2, y2 = coords[(index + 1) % len(coords)]
        if abs(x2 - x1) <= abs(y2 - y1) and abs(x2 - x1) < 2.2:
            mid = (x1 + x2) / 2.0
            coords[index] = (mid, y1)
            coords[(index + 1) % len(coords)] = (mid, y2)
        elif abs(y2 - y1) < 2.2:
            mid = (y1 + y2) / 2.0
            coords[index] = (x1, mid)
            coords[(index + 1) % len(coords)] = (x2, mid)
    clean = []
    for point in coords:
        if not clean or abs(point[0] - clean[-1][0]) > 0.6 or abs(point[1] - clean[-1][1]) > 0.6:
            clean.append(point)
    if len(clean) >= 2 and abs(clean[0][0] - clean[-1][0]) < 0.6 and abs(clean[0][1] - clean[-1][1]) < 0.6:
        clean.pop()
    if len(clean) < 3:
        return None
    poly = Polygon(clean).buffer(0)
    if poly.is_empty:
        return None
    if poly.geom_type != "Polygon":
        poly = max(poly.geoms, key=lambda item: item.area)
    return poly if poly.area > 10 else None


def _window(living):
    minx, miny, maxx, maxy = living.bounds
    return (minx - 40, miny - 90, maxx + 40, maxy + 90)


def _wall_lines(pdf_page, window) -> tuple[list, list]:
    minx, miny, maxx, maxy = window
    raw_h, raw_v = [], []
    for drawing in pdf_page.get_drawings():
        color = drawing.get("color") or drawing.get("fill")
        if not color or max(color[:3]) > 0.35:
            continue
        for item in drawing["items"]:
            if item[0] != "l":
                continue
            a, b = item[1], item[2]
            if a.x < minx and b.x < minx:
                continue
            if a.y < miny and b.y < miny:
                continue
            if abs(a.y - b.y) <= 0.8 and abs(a.x - b.x) >= 6:
                y = (a.y + b.y) / 2.0
                if miny <= y <= maxy:
                    raw_h.append((y, min(a.x, b.x), max(a.x, b.x)))
            elif abs(a.x - b.x) <= 0.8 and abs(a.y - b.y) >= 6:
                x = (a.x + b.x) / 2.0
                if minx <= x <= maxx:
                    raw_v.append((x, min(a.y, b.y), max(a.y, b.y)))
    return _merge_lines(raw_h, axis="h"), _merge_lines(raw_v, axis="v")


def _merge_lines(segments, axis: str) -> list:
    if not segments:
        return []
    segments = sorted(segments)
    groups = []
    for pos, a, b in segments:
        if groups and abs(pos - groups[-1][0]) < 1.4:
            groups[-1][1].append((a, b))
        else:
            groups.append([pos, [(a, b)]])
    merged = []
    for pos, parts in groups:
        parts = sorted(parts)
        spans = []
        for a, b in parts:
            if spans and a <= spans[-1][1] + 1.6:
                spans[-1][1] = max(spans[-1][1], b)
            else:
                spans.append([a, b])
        for a, b in spans:
            if b - a >= 8:
                merged.append((pos, a, b))
    return merged


def _service_rooms(pdf_page, words, living, horizontals, verticals, unit_hint) -> list:
    insets = _inset_rects(living, horizontals, verticals)
    if insets:
        return _label_insets(pdf_page, living, insets, words)
    return _labeled_neighbors(words, living, horizontals, verticals, unit_hint)


def _inset_rects(living, horizontals, verticals) -> list:
    """客厅填色里面、四面靠墙切出来的小房间。入户凹位是单独一块，丢掉。"""
    h_lines = [item for item in horizontals if item[2] - item[1] >= 14]
    v_lines = [item for item in verticals if item[2] - item[1] >= 14]
    found = []
    for top in h_lines:
        for bottom in h_lines:
            height = bottom[0] - top[0]
            if not (18 < height < 48):
                continue
            for left in v_lines:
                for right in v_lines:
                    width = right[0] - left[0]
                    if not (14 < width < 42):
                        continue
                    sides = (
                        _covers(top, left[0], right[0], 0.6),
                        _covers(bottom, left[0], right[0], 0.6),
                        _covers(left, top[0], bottom[0], 0.6),
                        _covers(right, top[0], bottom[0], 0.6),
                    )
                    loose = (
                        _covers(top, left[0], right[0], 0.4),
                        _covers(bottom, left[0], right[0], 0.4),
                        _covers(left, top[0], bottom[0], 0.4),
                        _covers(right, top[0], bottom[0], 0.4),
                    )
                    if sum(sides) < 3 or sum(loose) < 4:
                        continue
                    rect = box(left[0], top[0], right[0], bottom[0])
                    if rect.area > living.area * 0.35:
                        continue
                    if living.intersection(rect).area < 0.55 * rect.area:
                        continue
                    found.append(rect)
    def _inside(rect):
        inter = living.intersection(rect).area
        return inter / rect.area if rect.area else 0.0

    unique = []
    for rect in sorted(found, key=lambda item: (-_inside(item), -item.area)):
        if any(rect.intersection(other).area > 0.75 * min(rect.area, other.area) for other in unique):
            continue
        unique.append(rect)
    claimed = Polygon()
    separated = []
    for rect in sorted(unique, key=lambda item: (item.centroid.y, item.centroid.x)):
        piece = rect.difference(claimed.buffer(0.6))
        if piece.is_empty:
            continue
        if piece.geom_type != "Polygon":
            piece = max(piece.geoms, key=lambda item: item.area)
        claimed = claimed.union(rect)
        if piece.area > 120:
            separated.append(piece)
    return _paired_service_rects(separated)


def _paired_service_rects(rects) -> list:
    """浴室和厨房贴在一起。入户凹位离它们较远，不算服务房间。"""
    if len(rects) <= 2:
        return rects
    paired = []
    for rect in rects:
        if any(rect is not other and rect.distance(other) < 1.5 for other in rects):
            paired.append(rect)
    return paired if len(paired) >= 2 else rects


def _covers(line, start, end, fraction) -> bool:
    span = end - start
    if span <= 0:
        return False
    overlap = max(0.0, min(line[2], end) - max(line[1], start))
    return overlap >= fraction * span


def _label_insets(pdf_page, living, rects, words=None) -> list:
    words = words or []
    by_label = {}
    for rect in rects:
        names = _labels_inside(words, rect.buffer(2), {"BATH", "浴室", "KIT", "廚房", "厨房"})
        kinds = []
        for name in names:
            kind = _service_kind(name)
            if kind and kind not in kinds:
                kinds.append(kind)
        if len(kinds) == 1:
            by_label[id(rect)] = kinds[0]
    kitchen = next((rect for rect in rects if by_label.get(id(rect)) == "kitchen"), None)
    if kitchen is None:
        callouts = _callout_points(pdf_page, living.bounds)
        for value, x, y in callouts:
            if not (1550 <= value <= 1900):
                continue
            point = Point(x, y)
            host = next((rect for rect in rects if rect.buffer(1).contains(point)), None)
            if host is not None:
                kitchen = host
                break
    if kitchen is None and len(rects) >= 2 and not by_label:
        kitchen = max(rects, key=lambda rect: rect.centroid.x)
    rooms = []
    used = set()
    for rect in rects:
        kind = by_label.get(id(rect))
        if kind is None:
            kind = "kitchen" if kitchen is not None and rect is kitchen else "bath"
            if kind in used:
                kind = "kitchen" if kind == "bath" else "bath"
        used.add(kind)
        label = "廚房" if kind == "kitchen" else "浴室"
        rooms.append(dict(kind=kind, label=label, geom=_axis_polygon(rect) or rect,
                          point=(rect.centroid.x, rect.centroid.y)))
    return rooms


def _callout_points(pdf_page, bounds) -> list:
    import cv2

    from vectorize import ocr_dimension_tokens

    minx, miny, maxx, maxy = bounds
    clip = fitz.Rect(minx - 10, miny - 10, maxx + 10, maxy + 15)
    zoom = 3.0
    pix = pdf_page.get_pixmap(matrix=fitz.Matrix(zoom, zoom), clip=clip, alpha=False)
    if pix.width < 8:
        return []
    image = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.width, 3)
    red, green, blue = image[:, :, 0], image[:, :, 1], image[:, :, 2]
    orange = ((red > 180) & (green < 150) & (blue < 120)).astype(np.uint8) * 255
    tokens, _engine = ocr_dimension_tokens(orange)
    points = []
    for token in tokens:
        value = int(token["value"])
        if not (400 <= value <= 9000):
            continue
        tx, ty = token["center"]
        points.append((value, tx / zoom + clip.x0, ty / zoom + clip.y0))
    return points


def _labeled_neighbors(words, living, horizontals, verticals, unit_hint) -> list:
    number = _digits(unit_hint)
    anchors = []
    for word in words:
        if word[4].isdigit() and len(word[4]) >= 2:
            anchors.append((word[4], Point((word[0] + word[2]) / 2, (word[1] + word[3]) / 2)))
    labels = []
    for word in words:
        kind = _service_kind(word[4])
        if not kind:
            continue
        point = Point((word[0] + word[2]) / 2, (word[1] + word[3]) / 2)
        if anchors and number:
            nearest = min(anchors, key=lambda item: item[1].distance(point))
            if nearest[0] != number:
                continue
        labels.append((living.distance(point), kind, word[4], point.x, point.y))
    labels.sort()
    rooms = []
    for _dist, kind, text, px, py in labels:
        point = Point(px, py)
        if any(room["kind"] == kind for room in rooms):
            continue
        cell = _wall_cell(horizontals, verticals, point)
        if cell is None or cell.area < 12 or cell.area > max(living.area * 0.45, 80):
            continue
        if cell.distance(living) > 12 and not any(cell.distance(room["geom"]) < 8 for room in rooms):
            continue
        rooms.append(dict(kind=kind, label=text, geom=_axis_polygon(cell) or cell, point=(point.x, point.y)))
    return rooms


def _service_kind(text: str) -> str:
    if text in ("BATH", "浴室"):
        return "bath"
    if text in ("KIT", "廚房", "厨房"):
        return "kitchen"
    return ""


def _wall_cell(horizontals, verticals, point: Point):
    x, y = point.x, point.y
    above = [line for line in horizontals if line[0] < y - 0.4 and line[1] - 3 <= x <= line[2] + 3]
    below = [line for line in horizontals if line[0] > y + 0.4 and line[1] - 3 <= x <= line[2] + 3]
    left = [line for line in verticals if line[0] < x - 0.4 and line[1] - 3 <= y <= line[2] + 3]
    right = [line for line in verticals if line[0] > x + 0.4 and line[1] - 3 <= y <= line[2] + 3]
    if not (above and below and left and right):
        return None
    top = _nearest_long(above, high=True)
    bottom = _nearest_long(below, high=False)
    west = _nearest_long(left, high=True)
    east = _nearest_long(right, high=False)
    if east[0] - west[0] < 4 or bottom[0] - top[0] < 4:
        return None
    return box(west[0], top[0], east[0], bottom[0])


def _nearest_long(lines, high: bool):
    """短线多半是洁具，不是墙。旁边有更长的线时用长线。"""
    long = [line for line in lines if (line[2] - line[1]) >= 14]
    pool = long or lines
    return max(pool, key=lambda line: line[0]) if high else min(pool, key=lambda line: line[0])


def _dimension_observations(pdf_page, window) -> list:
    """橙色尺寸线的长度配上旁边的数字。两条线的比例要接近才算数。"""
    import cv2

    from vectorize import ocr_dimension_tokens

    minx, miny, maxx, maxy = window
    clip = fitz.Rect(minx, miny, maxx, maxy)
    zoom = 3.0
    pix = pdf_page.get_pixmap(matrix=fitz.Matrix(zoom, zoom), clip=clip, alpha=False)
    if pix.width < 8 or pix.height < 8:
        return []
    image = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.width, 3)
    red, green, blue = image[:, :, 0], image[:, :, 1], image[:, :, 2]
    orange = ((red > 180) & (green < 150) & (blue < 120)).astype(np.uint8) * 255
    tokens, _engine = ocr_dimension_tokens(orange)
    horizontal = cv2.morphologyEx(orange, cv2.MORPH_OPEN, cv2.getStructuringElement(cv2.MORPH_RECT, (18, 1)))
    vertical = cv2.morphologyEx(orange, cv2.MORPH_OPEN, cv2.getStructuringElement(cv2.MORPH_RECT, (1, 18)))
    observations = []
    for token in tokens:
        value = int(token["value"])
        if not (400 <= value <= 9000):
            continue
        tx, ty = token["center"]
        match = _nearest_band(horizontal, tx, ty, "h") or _nearest_band(vertical, tx, ty, "v")
        if match is None:
            continue
        length_pt = match[0] / zoom
        if length_pt < 4:
            continue
        scale = value / length_pt
        if not (35 <= scale <= 100):
            continue
        observations.append(dict(
            value=value,
            mm_per_unit=round(float(scale), 3),
            length_pt=round(float(length_pt), 2),
            axis=match[1],
            status="observed",
            method="orange-dimension-line",
        ))
    return observations


def _nearest_band(mask, tx, ty, axis: str):
    import cv2
    count, _labels, stats, _ = cv2.connectedComponentsWithStats(mask, 8)
    best = None
    for index in range(1, count):
        x, y, w, h, _area = stats[index]
        if axis == "h":
            if w < 12 or abs(ty - (y + h / 2.0)) > 16:
                continue
            if not (x - 8 <= tx <= x + w + 8):
                continue
            gap = abs(tx - (x + w / 2.0))
            length = w
        else:
            if h < 12 or abs(tx - (x + w / 2.0)) > 16:
                continue
            if not (y - 8 <= ty <= y + h + 8):
                continue
            gap = abs(ty - (y + h / 2.0))
            length = h
        if best is None or gap < best[0]:
            best = (gap, length, axis)
    if best is None:
        return None
    return best[1], best[2]


def _cut_white_pockets(pdf_page, polygon):
    """多边形里如果包进了一块白纸，把那一块从房子里拿掉。"""
    import cv2

    minx, miny, maxx, maxy = polygon.bounds
    zoom = 4.0
    clip = fitz.Rect(minx - 1, miny - 1, maxx + 1, maxy + 1)
    pix = pdf_page.get_pixmap(matrix=fitz.Matrix(zoom, zoom), clip=clip, alpha=False)
    if pix.width < 8 or pix.height < 8:
        return None
    image = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.width, 3)
    white = ((image[:, :, 0] > 235) & (image[:, :, 1] > 235) & (image[:, :, 2] > 235)).astype(np.uint8) * 255
    inside = np.zeros(white.shape, np.uint8)
    ring = np.array([[int(round((x - clip.x0) * zoom)), int(round((y - clip.y0) * zoom))]
                     for x, y in list(polygon.exterior.coords)], np.int32)
    cv2.fillPoly(inside, [ring], 255)
    white = cv2.bitwise_and(white, inside)
    white = cv2.morphologyEx(white, cv2.MORPH_OPEN, np.ones((3, 3), np.uint8))
    count, labels, stats, _ = cv2.connectedComponentsWithStats(white, 8)
    trimmed = polygon
    for index in range(1, count):
        area_pt = stats[index, cv2.CC_STAT_AREA] / zoom / zoom
        if area_pt < 80:
            continue
        component = (labels == index).astype(np.uint8) * 255
        contours, _ = cv2.findContours(component, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        if not contours:
            continue
        contour = max(contours, key=cv2.contourArea)
        if len(contour) < 3:
            continue
        hole = [(clip.x0 + float(point[0]) / zoom, clip.y0 + float(point[1]) / zoom)
                for point in contour[:, 0, :]]
        pocket = Polygon(hole).buffer(0.8)
        if pocket.is_empty:
            continue
        trimmed = trimmed.difference(pocket)
    if trimmed.is_empty:
        return None
    if trimmed.geom_type != "Polygon":
        trimmed = max(trimmed.geoms, key=lambda item: item.area)
    return _axis_polygon(trimmed)


def _swing_arcs(pdf_page, bounds) -> list:
    """门的四分之一圆弧。两端是门扇的两个位置，铰链在圆心。"""
    minx, miny, maxx, maxy = bounds
    arcs = []
    for drawing in pdf_page.get_drawings():
        color = drawing.get("color") or drawing.get("fill")
        if not color or max(color[:3]) > 0.4:
            continue
        for item in drawing["items"]:
            if item[0] != "c":
                continue
            points = [point for point in item[1:] if hasattr(point, "x")]
            if len(points) < 2:
                continue
            start, end = points[0], points[-1]
            chord = ((end.x - start.x) ** 2 + (end.y - start.y) ** 2) ** 0.5
            if not (10 < chord < 24):
                continue
            center_x = (start.x + end.x) / 2
            center_y = (start.y + end.y) / 2
            if not (minx - 20 < center_x < maxx + 20 and miny - 30 < center_y < maxy + 30):
                continue
            arcs.append(dict(
                a=(float(start.x), float(start.y)),
                b=(float(end.x), float(end.y)),
            ))
    return arcs


def _entry_gap(living, horizontals) -> dict | None:
    """入户凹位内侧那条门槛上的门洞，不是凹位最外缘。"""
    coords = list(living.exterior.coords)
    if len(coords) < 4:
        return None
    top = min(point[1] for point in coords)
    top_span = [point[0] for point in coords if abs(point[1] - top) < 3]
    if len(top_span) < 2:
        return None
    notch_x0, notch_x1 = min(top_span), max(top_span)
    if notch_x1 - notch_x0 < 8:
        return None
    mouth_y = None
    for index in range(len(coords) - 1):
        start, end = coords[index], coords[index + 1]
        if abs(start[1] - end[1]) > 2 or abs(start[0] - end[0]) < 8:
            continue
        y = (start[1] + end[1]) / 2
        if y < top + 8:
            continue
        left, right = min(start[0], end[0]), max(start[0], end[0])
        if right < notch_x0 - 1 and left < notch_x0:
            mouth_y = y if mouth_y is None else min(mouth_y, y)
        if left > notch_x1 - 1 and right > notch_x1:
            mouth_y = y if mouth_y is None else min(mouth_y, y)
    if mouth_y is None:
        return None
    covered = []
    for y, start, end in horizontals:
        if abs(y - mouth_y) > 4:
            continue
        left, right = max(start, notch_x0), min(end, notch_x1)
        if right - left > 1:
            covered.append((left, right))
    covered.sort()
    merged = []
    for start, end in covered:
        if merged and start <= merged[-1][1] + 1.5:
            merged[-1][1] = max(merged[-1][1], end)
        else:
            merged.append([start, end])
    cursor = notch_x0
    gap = None
    for start, end in merged:
        if start - cursor > 8 and (gap is None or start - cursor > gap[1] - gap[0]):
            gap = (cursor, start)
        cursor = max(cursor, end)
    if notch_x1 - cursor > 8 and (gap is None or notch_x1 - cursor > gap[1] - gap[0]):
        gap = (cursor, notch_x1)
    if gap is None or not (8 < gap[1] - gap[0] < 30):
        return None
    return dict(y=mouth_y, x0=gap[0], x1=gap[1], notch=(notch_x0, notch_x1))


def _fill_matches_render(pdf_page, polygon) -> bool:
    """向量多边形的中心，在渲染图上不该是白纸或墙线。"""
    point = polygon.representative_point()
    clip = fitz.Rect(point.x - 2, point.y - 2, point.x + 2, point.y + 2)
    pix = pdf_page.get_pixmap(matrix=fitz.Matrix(2, 2), clip=clip, alpha=False)
    if pix.width < 1:
        return False
    image = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.width, 3)
    center = image[image.shape[0] // 2, image.shape[1] // 2]
    red, green, blue = int(center[0]), int(center[1]), int(center[2])
    if red > 245 and green > 245 and blue > 245:
        return False
    if red < 40 and green < 40 and blue < 40:
        return False
    return True
