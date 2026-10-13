#!/usr/bin/env python3
"""瓶颈净宽（bottleneck clearance）—— 判断「最窄的一刀」到底是不是真的挡路。

min_corridor_width 是「逐刀取最长自由段再取最小」，它是**逐片**指标，不是**路径**指标：
一个房间可以某一刀只有 350mm，但人绕着走完全没问题。
真正该问的是：由大門口走到每一件家具面前，**路上最窄的一段有几阔**。

做法：自由空间栅格化 -> 到障碍物（墙 + 家具）的欧氏距离场 -> Dijkstra 最大化「路径上的最小净距」。
瓶颈净宽 = 2 x 该最小净距。
"""
from __future__ import annotations

import argparse
import math
import os
import sys
import time
import heapq

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, "packages", "geometry"))

from scipy import ndimage                                                # noqa: E402
from shapely.geometry import Point                                       # noqa: E402
from hkgeometry.house import load_flat, solve_house                      # noqa: E402
sys.path.insert(0, HERE)
from bench_hk import CASES, build_items                                  # noqa: E402

CELL = 50.0        # mm / 栅格


def grid_clearance(room, lay, cell=CELL):
    poly = room.geo()
    b = poly.bounds
    nx = int((b[2] - b[0]) / cell) + 3
    ny = int((b[3] - b[1]) / cell) + 3
    xs = b[0] - cell + np.arange(nx) * cell
    ys = b[1] - cell + np.arange(ny) * cell
    X, Y = np.meshgrid(xs, ys)
    from shapely.geometry import MultiPoint
    from shapely import contains_xy                                     # noqa: E402
    inside = contains_xy(poly, X, Y)
    obs = ~inside
    for p in lay.placements:
        fx, fy = p.footprint().exterior.xy
        from shapely.geometry import Polygon as SPoly
        fp = p.footprint()
        obs |= contains_xy(fp, X, Y)
    # 距障碍物的距离（mm）
    dist = ndimage.distance_transform_edt(~obs) * cell
    return dist, xs, ys, obs


def bottleneck(dist, xs, ys, start_xy, target_xy):
    """由 start 到 target 的「最大化最小净距」路径，返回最小净距 (mm)。"""
    def idx(xy):
        return (int(round((xy[0] - xs[0]) / CELL)), int(round((xy[1] - ys[0]) / CELL)))
    ny, nx = dist.shape
    sx, sy = idx(start_xy)
    tx, ty = idx(target_xy)
    def near(i, j):
        # 若落在家具/墙里，找最近的自由格
        if 0 <= i < nx and 0 <= j < ny and dist[j, i] > 0:
            return i, j
        best = None
        for r in range(1, 40):
            for di in range(-r, r + 1):
                for dj in range(-r, r + 1):
                    if max(abs(di), abs(dj)) != r:
                        continue
                    a, bq = i + di, j + dj
                    if 0 <= a < nx and 0 <= bq < ny and dist[bq, a] > 0:
                        return a, bq
            if best:
                break
        return i, j
    sx, sy = near(sx, sy)
    tx, ty = near(tx, ty)
    best = np.full((ny, nx), -1.0)
    best[sy, sx] = dist[sy, sx]
    pq = [(-dist[sy, sx], sx, sy)]
    while pq:
        d, i, j = heapq.heappop(pq)
        d = -d
        if (i, j) == (tx, ty):
            return d
        if d < best[j, i]:
            continue
        for di, dj in ((1, 0), (-1, 0), (0, 1), (0, -1)):
            a, bq = i + di, j + dj
            if not (0 <= a < nx and 0 <= bq < ny):
                continue
            if dist[bq, a] <= 0:
                continue
            nd = min(d, dist[bq, a])
            if nd > best[bq, a]:
                best[bq, a] = nd
                heapq.heappush(pq, (-nd, a, bq))
    return -1.0


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--case", default="onpak05")
    ap.add_argument("--time-limit", type=float, default=40.0)
    args = ap.parse_args()
    case = CASES[args.case]
    flat = load_flat(os.path.join(ROOT, case["plan"]))
    items = build_items(case)
    assign = {i: r for i, _c, _w, _d, _h, r in case["items"]}
    t0 = time.time()
    house = solve_house(flat, items, time_limit_s=args.time_limit, parallel=True,
                        assignment=assign)
    print("solve %.1fs" % (time.time() - t0))
    for rm in flat.rooms:
        lay = house.by_room.get(rm.id)
        if not lay or not lay.placements:
            continue
        dist, xs, ys, obs = grid_clearance(rm, lay)
        e = flat.entry_point
        start = (float(e[0]), float(e[1])) if e else rm.geo().representative_point().coords[0]
        print("=== %s (%.1f m2)" % (rm.id, rm.geo().area / 1e6))
        for p in lay.placements:
            tgt = p.center()
            w = bottleneck(dist, xs, ys, start, tgt)
            print("   %-18s 瓶颈净宽 %7.1f mm%s" % (p.inst, 2 * w, "" if 2 * w >= 900 else "   <-- 窄"))


if __name__ == "__main__":
    main()
