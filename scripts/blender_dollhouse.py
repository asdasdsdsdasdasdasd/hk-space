"""Blender headless：读场景 JSON，搭房间 + 摆家具，渲染娃娃屋视图。

用法：
    blender --background --factory-startup --python blender_dollhouse.py -- scene.json out.png

坐标：场景 JSON 全部是整数毫米、Z 向上。GLB 模板是 Z-up 导出的。
"""
import json
import math
import sys

import bpy
import mathutils
from mathutils import Matrix, Vector

MM = 0.001


def log(*a):
    print("[dollhouse]", *a, flush=True)


def reset_scene():
    bpy.ops.wm.read_factory_settings(use_empty=True)
    for blk in (bpy.data.objects, bpy.data.meshes, bpy.data.materials, bpy.data.lights):
        for it in list(blk):
            try:
                blk.remove(it)
            except Exception:
                pass


def mesh_obj(name, verts, faces):
    me = bpy.data.meshes.new(name)
    me.from_pydata(verts, [], faces)
    me.validate()
    me.update()
    ob = bpy.data.objects.new(name, me)
    bpy.context.scene.collection.objects.link(ob)
    return ob


def material(name, rgba, rough=0.6, metal=0.0):
    mat = bpy.data.materials.new(name)
    mat.use_nodes = True
    b = mat.node_tree.nodes.get("Principled BSDF")
    if b:
        b.inputs["Base Color"].default_value = (rgba[0], rgba[1], rgba[2], 1.0)
        b.inputs["Roughness"].default_value = rough
        b.inputs["Metallic"].default_value = metal
    if len(rgba) > 3 and rgba[3] < 1.0:
        mat.blend_method = "BLEND"
        if b:
            b.inputs["Alpha"].default_value = rgba[3]
    return mat


def paint(ob, mat):
    ob.data.materials.clear()
    ob.data.materials.append(mat)


def world_bbox(objs):
    pts = []
    for o in objs:
        if o.type != "MESH":
            continue
        for v in o.data.vertices:
            pts.append(o.matrix_world @ v.co)
    if not pts:
        return None
    mn = Vector((min(p.x for p in pts), min(p.y for p in pts), min(p.z for p in pts)))
    mx = Vector((max(p.x for p in pts), max(p.y for p in pts), max(p.z for p in pts)))
    return mn, mx


# ------------------------------------------------------------------ 房间
def build_room(scene):
    room = scene["room"]
    H = room["height_mm"] * MM
    poly = [(x * MM, y * MM) for x, y in room["polygon"]]
    cx = sum(x for x, _ in poly) / len(poly)
    cy = sum(y for _, y in poly) / len(poly)

    floor = mesh_obj("floor", [(x, y, 0.0) for x, y in poly], [list(range(len(poly)))])
    paint(floor, material("mat_floor", (0.84, 0.80, 0.72), 0.75))
    floor.data.materials[0].node_tree.nodes["Principled BSDF"].inputs[
        "Base Color"].default_value = (0.84, 0.80, 0.72, 1.0)
    # 楼板加厚：0.14 太薄，从斜上方看像纸片。0.30 才读得出是一块楼板。
    sol = floor.modifiers.new("solid", "SOLIDIFY")
    sol.thickness = 0.30
    sol.offset = -1

    walls = []
    for i, w in enumerate(room["walls"]):
        ax, ay = w["a"][0] * MM, w["a"][1] * MM
        bx, by = w["b"][0] * MM, w["b"][1] * MM
        mx, my = (ax + bx) / 2, (ay + by) / 2
        nx, ny = mx - cx, my - cy
        L = math.hypot(nx, ny) or 1.0
        nx, ny = nx / L, ny / L
        t = 0.11
        ox, oy = nx * t, ny * t
        verts = [(ax, ay, 0), (bx, by, 0), (bx, by, H), (ax, ay, H),
                 (ax + ox, ay + oy, 0), (bx + ox, by + oy, 0),
                 (bx + ox, by + oy, H), (ax + ox, ay + oy, H)]
        faces = [(0, 1, 2, 3), (4, 5, 6, 7), (0, 1, 5, 4),
                 (2, 3, 7, 6), (0, 3, 7, 4), (1, 2, 6, 5)]
        ob = mesh_obj("wall_%d" % i, verts, faces)
        load = w.get("type") == "load_bearing"
        paint(ob, material("mat_wall_%d" % i,
                           (0.78, 0.76, 0.73) if load else (0.90, 0.89, 0.87), 0.85))
        walls.append((ob, nx, ny))

    for hp in room.get("height_field", []):
        z0 = hp["z_max"] * MM
        reg = [(x * MM, y * MM) for x, y in hp["region"]]
        vs = [(x, y, z0) for x, y in reg] + [(x, y, H) for x, y in reg]
        n = len(reg)
        ob = mesh_obj("beam", vs, [list(range(n)), list(range(n, 2 * n))])
        paint(ob, material("mat_beam", (0.88, 0.66, 0.28), 0.6))

    for op in room.get("openings", []):
        wall = next((x for x in room["walls"] if x.get("id") == op["wall"]), None)
        if wall is None:
            continue
        ax, ay = wall["a"][0] * MM, wall["a"][1] * MM
        bx, by = wall["b"][0] * MM, wall["b"][1] * MM
        L = math.hypot(bx - ax, by - ay) or 1.0
        ux, uy = (bx - ax) / L, (by - ay) / L
        d = op["center_mm"] * MM
        px, py = ax + ux * d, ay + uy * d
        half = op["width_mm"] * MM / 2
        if op["kind"] == "window":
            z0 = max(op.get("sill_h_mm", 0) * MM, 0.85)
            z1 = min(H - 0.10, z0 + 1.35)
            col = (0.56, 0.78, 0.95)
        else:
            z0, z1 = 0.0, 2.02
            col = (0.70, 0.32, 0.28)
        nx, ny = -uy, ux
        if ((px - cx) * nx + (py - cy) * ny) < 0:
            nx, ny = -nx, -ny
        push = 0.035
        p = [(px - ux * half + nx * push, py - uy * half + ny * push, z0),
             (px + ux * half + nx * push, py + uy * half + ny * push, z0),
             (px + ux * half + nx * push, py + uy * half + ny * push, z1),
             (px - ux * half + nx * push, py - uy * half + ny * push, z1)]
        ob = mesh_obj("open_" + op["id"], p, [(0, 1, 2, 3)])
        paint(ob, material("mat_open_" + op["id"], col, 0.3))
    return walls


# ------------------------------------------------------------------ 家具
def bake_transform(objs):
    """把世界矩阵烘进网格数据。

    ⚠️ 必须用数据 API（mesh.transform），**不能用 bpy.ops.object.transform_apply**：
    在 --background 模式下 bpy.ops 会因为 context 不完整而静默失败（返回 CANCELLED
    但不抛异常），结果就是"看起来执行了、其实什么都没做"。
    """
    for o in objs:
        mw = o.matrix_world.copy()
        o.parent = None
        o.matrix_world = Matrix.Identity(4)
        o.data.transform(mw)
    bpy.context.view_layer.update()


def import_and_flatten(path):
    before = set(bpy.data.objects)
    bpy.ops.import_scene.gltf(filepath=path)
    new = [o for o in bpy.data.objects if o not in before]
    meshes = [o for o in new if o.type == "MESH"]
    if not meshes:
        return []
    bake_transform(meshes)
    return meshes


# GLB 导出约定是全局的，所以朝向纠正只探测一次，然后对所有模板统一套用。
# 用 wardrobe_tall（1.2 x 0.6 x 2.4，三个尺寸区分度最大）当参考。
_ORIENT_CACHE = {}


def detect_orientation(tdir):
    """返回 True 表示导入后需要绕 X 轴转 -90 度。

    背景：GLB 是 Z-up 内容，而 glTF 标准是 Y-up。Blender 导入器会做一次
    Y-up -> Z-up 转换（绕 X 轴 +90°），于是模型「高」的方向落到了 **-Y**。
    要转回 Z 向上，正确的逆变换是绕 X 轴 **-90°**（把 -Y 映到 +Z）。

    （曾经写成 +90°，结果把家具整个倒过来了 —— 衣柜这种对称盒子看不出来，
    椅子/高架床一眼就露馅。）
    """
    if "v" in _ORIENT_CACHE:
        return _ORIENT_CACHE["v"]
    ref = {"w": 1.20, "d": 0.60, "h": 2.40}   # wardrobe_tall 的模板默认尺寸
    probe = import_and_flatten("%s/wardrobe_tall.glb" % tdir)
    ok_swap = None
    if probe:
        bb = world_bbox(probe)
        if bb:
            size = bb[1] - bb[0]
            err_as_is = abs(size.y - ref["d"]) + abs(size.z - ref["h"])
            err_swap = abs(size.y - ref["h"]) + abs(size.z - ref["d"])
            ok_swap = err_swap < err_as_is
            log("orientation probe: raw=(%.2f,%.2f,%.2f) as_is_err=%.3f swap_err=%.3f -> %s"
                % (size.x, size.y, size.z, err_as_is, err_swap,
                   "rotate X -90 (restore Z-up)" if ok_swap else "as exported"))
        for o in probe:
            bpy.data.objects.remove(o, do_unlink=True)
    _ORIENT_CACHE["v"] = bool(ok_swap) if ok_swap is not None else True
    return _ORIENT_CACHE["v"]


def place(scene, placements=None):
    tdir = scene["templates_dir"]
    need_swap = detect_orientation(tdir)
    for pl in (placements if placements is not None else scene["placements"]):
        w, d, h = pl["w"] * MM, pl["d"] * MM, pl["h"] * MM
        rot = pl["rot"]
        fw = d if rot in (90, 270) else w
        fd = w if rot in (90, 270) else d
        cx = pl["x"] * MM + fw / 2.0
        cy = pl["y"] * MM + fd / 2.0

        objs = import_and_flatten("%s/%s.glb" % (tdir, pl["template"]))
        if not objs:
            log("MISSING", pl["template"])
            continue
        bb = world_bbox(objs)
        if bb is None:
            continue
        mn, mx = bb
        size = mx - mn

        # ---- 朝向纠正（统一套用探测结果，用数据 API）------------------
        if need_swap:
            R = Matrix.Rotation(math.radians(-90.0), 4, "X")
            for o in objs:
                o.data.transform(R)
            bpy.context.view_layer.update()
            bb = world_bbox(objs)
            mn, mx = bb
            size = mx - mn

        sx = w / size.x if size.x > 1e-6 else 1.0
        sy = d / size.y if size.y > 1e-6 else 1.0
        sz = h / size.z if size.z > 1e-6 else 1.0

        holder = bpy.data.objects.new("H_" + pl["inst"].strip("#"), None)
        bpy.context.scene.collection.objects.link(holder)
        for o in objs:
            o.parent = holder
            o.matrix_parent_inverse = mathutils.Matrix.Identity(4)

        rz = math.radians(rot)
        cs, sn = math.cos(rz), math.sin(rz)
        corners = []
        for ix in (mn.x, mx.x):
            for iy in (mn.y, mx.y):
                for iz in (mn.z, mx.z):
                    qx, qy, qz = ix * sx, iy * sy, iz * sz
                    corners.append(Vector((cs * qx - sn * qy, sn * qx + cs * qy, qz)))
        qmn = Vector((min(c.x for c in corners), min(c.y for c in corners),
                      min(c.z for c in corners)))
        qmx = Vector((max(c.x for c in corners), max(c.y for c in corners),
                      max(c.z for c in corners)))
        holder.location = (cx - (qmn.x + qmx.x) / 2.0, cy - (qmn.y + qmx.y) / 2.0, -qmn.z)
        holder.rotation_euler.z = rz
        holder.scale = (sx, sy, sz)

        col = pl["color"]
        for o in objs:
            for slot in o.material_slots:
                if slot.material and slot.material.use_nodes:
                    b = slot.material.node_tree.nodes.get("Principled BSDF")
                    if b:
                        b.inputs["Base Color"].default_value = (col[0], col[1], col[2], 1.0)
                        b.inputs["Roughness"].default_value = 0.45 if col[3] < 1 else 0.6
        bpy.context.view_layer.update()
        fin = world_bbox(objs)
        fmin, fmax = fin
        log("placed %-14s %-16s mesh=(%.2f,%.2f,%.2f) -> world=(%.2f,%.2f,%.2f) @ (%.2f,%.2f)"
            % (pl["inst"], pl["template"], size.x, size.y, size.z,
               fmax.x - fmin.x, fmax.y - fmin.y, fmax.z - fmin.z,
               holder.location.x, holder.location.y))


# ------------------------------------------------------------------ 相机 / 灯光
def setup(scene, walls, out_png):
    room = scene["room"]
    poly = [(x * MM, y * MM) for x, y in room["polygon"]]
    cx = sum(x for x, _ in poly) / len(poly)
    cy = sum(y for _, y in poly) / len(poly)
    xs = [x for x, _ in poly]
    ys = [y for _, y in poly]
    span = max(max(xs) - min(xs), max(ys) - min(ys))
    H = room["height_mm"] * MM

    az = math.radians(-125.0)
    el = math.radians(41.0)
    dist = span * 2.25
    cam_loc = Vector((cx + dist * math.cos(az) * math.cos(el),
                      cy + dist * math.sin(az) * math.cos(el),
                      H + dist * math.sin(el)))
    target = Vector((cx, cy, H * 0.30))
    view = Vector((cam_loc.x - cx, cam_loc.y - cy, 0)).normalized()
    for ob, nx, ny in walls:
        if nx * view.x + ny * view.y > 0.10:
            ob.hide_render = True

    cd = bpy.data.cameras.new("cam")
    cd.lens = 36
    cam = bpy.data.objects.new("cam", cd)
    bpy.context.scene.collection.objects.link(cam)
    cam.location = cam_loc
    cam.rotation_euler = (target - cam_loc).to_track_quat("-Z", "Y").to_euler()
    bpy.context.scene.camera = cam

    gp = mesh_obj("ground", [(-40, -40, -0.145), (40, -40, -0.145),
                             (40, 40, -0.145), (-40, 40, -0.145)], [(0, 1, 2, 3)])
    paint(gp, material("mat_ground", (0.80, 0.82, 0.85), 0.95))

    world = bpy.data.worlds.new("W")
    world.use_nodes = True
    bg = world.node_tree.nodes.get("Background")
    if bg:
        bg.inputs[0].default_value = (0.42, 0.50, 0.62, 1.0)
        bg.inputs[1].default_value = 0.40
    bpy.context.scene.world = world

    sd = bpy.data.lights.new("sun", type="SUN")
    sd.energy = 3.1
    sd.angle = math.radians(5)
    sd.color = (1.0, 0.96, 0.88)
    sun = bpy.data.objects.new("sun", sd)
    bpy.context.scene.collection.objects.link(sun)
    sun.rotation_euler = (math.radians(48), math.radians(8), math.radians(-118))

    ad = bpy.data.lights.new("fill", type="AREA")
    ad.energy = 14.0
    ad.size = span * 1.6
    ad.color = (0.80, 0.87, 1.0)
    fill = bpy.data.objects.new("fill", ad)
    bpy.context.scene.collection.objects.link(fill)
    fill.location = (cx - span * 0.6, cy + span * 0.8, H * 1.6)
    fill.rotation_euler = (math.radians(-26), 0, math.radians(-160))

    sc = bpy.context.scene
    sc.render.engine = "BLENDER_EEVEE_NEXT"
    try:
        sc.eevee.taa_render_samples = 96
        sc.eevee.use_shadows = True
    except Exception as e:
        log("eevee:", e)
    sc.render.resolution_x, sc.render.resolution_y = 1700, 1200
    sc.render.film_transparent = False
    sc.render.image_settings.file_format = "PNG"
    try:
        sc.view_settings.view_transform = "Standard"
        sc.view_settings.look = "None"
        sc.view_settings.exposure = 0.0
    except Exception as e:
        log("view transform:", e)
    sc.render.filepath = out_png


WALL_CUT = 0.62        # 娃娃屋墙体保留高度比例
# 40% 太矮（0.98m）：白墙叠在白地板上，房间分界完全读不出来。
# 62%（1.5m）既能看进房间内部，又能明确读出墙。


def _wall_box(name, ax, ay, ux, uy, nx, ny, t, s0, s1, z0, z1, mat):
    """墙的一段：沿 u 从 s0 到 s1，沿外法线 n 厚 t，高度 z0..z1。"""
    if s1 - s0 < 1.0 or z1 - z0 < 1.0:
        return None
    def P(s, o, z):
        return (ax + ux * s + nx * o, ay + uy * s + ny * o, z)
    verts = [P(s0, 0, z0), P(s1, 0, z0), P(s1, t, z0), P(s0, t, z0),
             P(s0, 0, z1), P(s1, 0, z1), P(s1, t, z1), P(s0, t, z1)]
    faces = [(0, 1, 2, 3), (4, 5, 6, 7), (0, 1, 5, 4),
             (2, 3, 7, 6), (0, 3, 7, 4), (1, 2, 6, 5)]
    ob = mesh_obj(name, verts, faces)
    paint(ob, mat)
    return ob


def build_wall(name, wall, ops, H, cx, cy, mat, mat_glass):
    """★ 把一面墙按开口**真正切开** —— 门洞和窗洞就是空的。

    之前是把门/窗画成贴在墙上的彩色板子，墙体本身还是完整的，
    加上娃娃屋剖切到 40% 高，图里根本看不出哪里有门。
    现在门洞是真的洞，一眼就能看到"从这里可以过去"。
    """
    ax, ay = wall["a"][0] * MM, wall["a"][1] * MM
    bx, by = wall["b"][0] * MM, wall["b"][1] * MM
    L = math.hypot(bx - ax, by - ay)
    if L < 0.02:
        return [], (ax, ay, 1.0, 0.0, 0.0, 1.0, 0.1, L)
    ux, uy = (bx - ax) / L, (by - ay) / L
    mx, my = (ax + bx) / 2, (ay + by) / 2
    nx, ny = mx - cx, my - cy
    ln = math.hypot(nx, ny) or 1.0
    nx, ny = nx / ln, ny / ln
    t = max(0.08, wall.get("thickness_mm", 100) * MM)
    cuts = []
    for op in ops:
        c, w = op["center_mm"] * MM, op["width_mm"] * MM
        s0, s1 = max(0.0, c - w / 2), min(L, c + w / 2)
        if s1 - s0 < 10 * MM:
            continue
        if op["kind"] == "window":
            zlo = max(op.get("sill_h_mm", 0) * MM, 0.85)
            zhi = min(H, zlo + 1.35)
        else:
            zlo, zhi = 0.0, min(H - 0.10, 2.03)
        cuts.append((s0, s1, zlo, zhi))
    cuts.sort()
    out = []
    solid = 0.0
    for i, (s0, s1, zlo, zhi) in enumerate(cuts):
        if s0 - solid > 10 * MM:
            ob = _wall_box("%s_%d" % (name, len(out)), ax, ay, ux, uy, nx, ny, t,
                           solid, s0, 0.0, H, mat)
            if ob:
                out.append(ob)
        if zhi < H - 10 * MM:                       # 门楣 / 窗楣
            ob = _wall_box("%s_h%d" % (name, i), ax, ay, ux, uy, nx, ny, t,
                           s0, s1, zhi, H, mat)
            if ob:
                out.append(ob)
        if zlo > 10 * MM:                           # 窗台
            ob = _wall_box("%s_s%d" % (name, i), ax, ay, ux, uy, nx, ny, t,
                           s0, s1, 0.0, zlo, mat)
            if ob:
                out.append(ob)
        solid = max(solid, s1)
    if L - solid > 10 * MM:
        ob = _wall_box("%s_end" % name, ax, ay, ux, uy, nx, ny, t,
                       solid, L, 0.0, H, mat)
        if ob:
            out.append(ob)
    return out, (ax, ay, ux, uy, nx, ny, t, L)


def build_room_multi(scene, rdef):
    """多房间场景：每个房间单独建地板和墙，统一放到全局坐标。

    ★ 墙只保留 40% 高度。这是娃娃屋的标准做法，也是**必须的**：
      真实层高 2.45m 的墙配 46 度阳光，整个室内都会落在墙的阴影里，
      渲染出来是一片深灰，家具全看不见。
    """
    Hfull = rdef["height_mm"] * MM
    H = Hfull * WALL_CUT
    poly = [(x * MM, y * MM) for x, y in rdef["polygon"]]
    cx = sum(x for x, _ in poly) / len(poly)
    cy = sum(y for _, y in poly) / len(poly)
    floor = mesh_obj("floor_" + rdef["id"],
                     [(x, y, 0.0) for x, y in poly], [list(range(len(poly)))])
    # ★ 每个房间一个**明显不同**的地板色。之前色差太小，渲染出来全是白，
    # 根本分不出哪间是哪间。
    TINT = {
        "living":  (0.90, 0.78, 0.58),   # 暖木
        "bedroom": (0.82, 0.76, 0.84),   # 淡紫灰
        "kitchen": (0.72, 0.85, 0.80),   # 淡青
        "bath":    (0.70, 0.82, 0.92),   # 淡蓝
        "storage": (0.86, 0.83, 0.70),   # 米灰
    }
    paint(floor, material("mat_floor_" + rdef["id"],
                          TINT.get(rdef.get("func"), TINT.get(rdef["id"], (0.88, 0.85, 0.79))), 0.75))
    # 楼板加厚：0.14 太薄，从斜上方看像纸片。0.30 才读得出是一块楼板。
    sol = floor.modifiers.new("solid", "SOLIDIFY")
    sol.thickness = 0.30
    sol.offset = -1
    walls = []
    ops_by_wall = {}
    for op in rdef.get("openings", []):
        ops_by_wall.setdefault(op.get("wall", ""), []).append(op)
    for i, w in enumerate(rdef.get("walls", [])):
        load = w.get("type") == "load_bearing"
        # 承重墙深一点、间隔墙浅一点，都要和地板拉开对比
        mat = material("mat_w_%s_%d" % (rdef["id"], i),
                       (0.58, 0.56, 0.54) if load else (0.78, 0.77, 0.75), 0.85)
        objs, geom = build_wall("wall_%s_%d" % (rdef["id"], i), w,
                                ops_by_wall.get(w.get("id", ""), []), H, cx, cy, mat, None)
        _, _, ux, uy, nx, ny, t, L = geom
        for ob in objs:
            walls.append((ob, nx, ny))
    for op in rdef.get("openings", []):
        wall = next((x for x in rdef.get("walls", []) if x.get("id") == op["wall"]), None)
        if wall is None:
            continue
        ax, ay = wall["a"][0] * MM, wall["a"][1] * MM
        bx, by = wall["b"][0] * MM, wall["b"][1] * MM
        L = math.hypot(bx - ax, by - ay) or 1.0
        ux, uy = (bx - ax) / L, (by - ay) / L
        d = op["center_mm"] * MM
        px, py = ax + ux * d, ay + uy * d
        half = op["width_mm"] * MM / 2
        nx, ny = -uy, ux
        if ((px - cx) * nx + (py - cy) * ny) < 0:
            nx, ny = -nx, -ny
        if op["kind"] == "window":
            z0 = max(op.get("sill_h_mm", 0) * MM, 0.85)
            z1 = min(H - 0.06, z0 + 1.35)
            push = 0.035
            p = [(px - ux * half + nx * push, py - uy * half + ny * push, z0),
                 (px + ux * half + nx * push, py + uy * half + ny * push, z0),
                 (px + ux * half + nx * push, py + uy * half + ny * push, z1),
                 (px - ux * half + nx * push, py - uy * half + ny * push, z1)]
            ob = mesh_obj("win_%s_%s" % (rdef["id"], op["id"]), p, [(0, 1, 2, 3)])
            paint(ob, material("mat_win_%s_%s" % (rdef["id"], op["id"]),
                               (0.52, 0.76, 0.95), 0.15))
            continue
        # 洞口已经是真的空的了（见 build_wall）。这里只补一圈细门框 + 开启的
        # 门扇 —— 让"这是门"一眼可辨，而不是靠一块贴在墙上的色板。
        dh = min(2.03, Hfull - 0.12)
        t = 0.10
        wood = material("mat_door_%s_%s" % (rdef["id"], op["id"]),
                        (0.66, 0.47, 0.30), 0.55)
        for sgn in (-1, 1):
            jx = px + ux * half * sgn + nx * t / 2
            jy = py + uy * half * sgn + ny * t / 2
            c = [(jx - ux * 0.035, jy - uy * 0.035), (jx + ux * 0.035, jy + uy * 0.035),
                 (jx + ux * 0.035 + nx * t, jy + uy * 0.035 + ny * t),
                 (jx - ux * 0.035 + nx * t, jy - uy * 0.035 + ny * t)]
            v = [(a, b, 0.0) for a, b in c] + [(a, b, dh + 0.10) for a, b in c]
            ob = mesh_obj("jamb_%s_%s_%d" % (rdef["id"], op["id"], sgn), v,
                          [(0, 1, 2, 3), (4, 5, 6, 7), (0, 1, 5, 4),
                           (2, 3, 7, 6), (0, 3, 7, 4), (1, 2, 6, 5)])
            paint(ob, wood)
        lv = [(px - ux * half + nx * t, py - uy * half + ny * t, dh),
              (px + ux * half + nx * t, py + uy * half + ny * t, dh),
              (px + ux * half, py + uy * half, dh),
              (px - ux * half, py - uy * half, dh)]
        lv = lv + [(x, y, dh + 0.12) for x, y, _z in lv]
        ob = mesh_obj("lintel_%s_%s" % (rdef["id"], op["id"]), lv,
                      [(0, 1, 2, 3), (4, 5, 6, 7), (0, 1, 5, 4),
                       (2, 3, 7, 6), (0, 3, 7, 4), (1, 2, 6, 5)])
        paint(ob, wood)
        # 门扇：以一侧门框为铰链，向房内开 90 度
        hx, hy = px - ux * half, py - uy * half
        leaf_w = op["width_mm"] * MM
        lt = 0.045
        v = []
        for along in (0.0, leaf_w):
            for side in (0.0, lt):
                v.append((hx + nx * along - ux * side, hy + ny * along - uy * side, 0.0))
        v = v + [(x, y, dh) for x, y, _z in v]
        ob = mesh_obj("leaf_%s_%s" % (rdef["id"], op["id"]), v,
                      [(0, 1, 3, 2), (4, 5, 7, 6), (0, 1, 5, 4),
                       (2, 3, 7, 6), (0, 2, 6, 4), (1, 3, 7, 5)])
        paint(ob, material("mat_leaf_%s_%s" % (rdef["id"], op["id"]),
                           (0.76, 0.58, 0.38), 0.45))
        # ★ 门槛：门洞里铺一条深色地板 —— 墙矮且白的时候，
        # 光靠"挖了个洞"是读不出来的，一条对比色门槛立刻就能看出这里有门。
        th = max(0.24, t * 2.4)
        v = [(px - ux * half, py - uy * half, 0.004),
             (px + ux * half, py + uy * half, 0.004),
             (px + ux * half + nx * th, py + uy * half + ny * th, 0.004),
             (px - ux * half + nx * th, py - uy * half + ny * th, 0.004)]
        v = v + [(x, y, 0.020) for x, y, _z in v]
        ob = mesh_obj("sill_%s_%s" % (rdef["id"], op["id"]), v,
                      [(0, 1, 2, 3), (4, 5, 6, 7), (0, 1, 5, 4),
                       (2, 3, 7, 6), (0, 3, 7, 4), (1, 2, 6, 5)])
        paint(ob, material("mat_sill_%s_%s" % (rdef["id"], op["id"]),
                           (0.42, 0.34, 0.26), 0.6))
    return walls


def main():
    argv = sys.argv
    args = argv[argv.index("--") + 1:] if "--" in argv else []
    with open(args[0], encoding="utf-8") as f:
        scene = json.load(f)
    reset_scene()
    log("scene:", scene["name"])
    if "rooms" in scene:                       # 整屋多房间
        allwalls = []
        for rdef in scene["rooms"]:
            allwalls += build_room_multi(scene, rdef)
        for rdef in scene["rooms"]:
            place(scene, rdef.get("placements", []))
        add_room_labels(scene)
        setup_house(scene, allwalls, args[1])
    else:
        walls = build_room(scene)
        place(scene)
        setup(scene, walls, args[1])
    bpy.ops.render.render(write_still=True)
    log("rendered ->", args[1])
    if len(args) > 2:
        bpy.ops.export_scene.gltf(filepath=args[2], export_format="GLB")
        log("model ->", args[2])


def add_room_labels(scene, az_deg: float = -118.0, z_m: float = 2.85):
    """房间标签：**立起来、面向相机、抬到墙以上**。

    踩过的坑：默认的文字对象平躺在 XY 平面上，只从正上方才读得到 ——
    45-60 度俯视角下看到的是一堆躺倒的字，而且会被家具压住。
    """
    # 抬到 2.85m —— 必须高过全屋最高的家具（到顶衣柜 2.36m），
    # 否则「睡房」这两个字会被衣柜整个挡住。
    rot = (math.radians(90.0), 0.0, math.radians(az_deg + 90.0))
    # 朝相机方向前移一点 —— 否则标签会被房间里最高的家具（到顶衣柜 2.36m）
    # 挡在投影上。相机方位角 az，前移方向就是 (cos az, sin az)。
    ox, oy = math.cos(math.radians(az_deg)), math.sin(math.radians(az_deg))
    for rdef in scene["rooms"]:
        xs = [x * MM for x, _y in rdef["polygon"]]
        ys = [y * MM for _x, y in rdef["polygon"]]
        cx, cy = (min(xs) + max(xs)) / 2, (min(ys) + max(ys)) / 2
        span_r = min(max(xs) - min(xs), max(ys) - min(ys))
        shift = min(1.3, span_r * 0.32)
        cx, cy = cx + ox * shift, cy + oy * shift
        try:
            cu = bpy.data.curves.new(name="lbl_" + rdef["id"], type="FONT")
            cu.body = rdef.get("label") or rdef["id"]
            cu.size = 0.40
            cu.align_x = "CENTER"
            cu.align_y = "CENTER"
            ob = bpy.data.objects.new("label_" + rdef["id"], cu)
            bpy.context.scene.collection.objects.link(ob)
            ob.location = (cx, cy, z_m)
            ob.rotation_euler = rot
            cu.materials.append(material("mat_label_" + rdef["id"],
                                         (0.08, 0.08, 0.12), 0.95))
        except Exception as e:
            log("label fail:", rdef["id"], e)


def setup_house(scene, walls, out_png):
    """整屋取景：所有房间的全局包围盒。"""
    xs, ys = [], []
    for rd in scene["rooms"]:
        for x, y in rd["polygon"]:
            xs.append(x * MM)
            ys.append(y * MM)
    cx, cy = (min(xs) + max(xs)) / 2, (min(ys) + max(ys)) / 2
    span = max(max(xs) - min(xs), max(ys) - min(ys))
    H = max(rd["height_mm"] for rd in scene["rooms"]) * MM
    # 整屋取景：俯角要大（60 度左右）才看得到房间**内部** ——
    # 43 度时近侧的墙会把房间挡住，只剩一片灰。
    az = math.radians(-118.0)
    el = math.radians(52.0)
    dist = span * 1.80
    cam_loc = Vector((cx + dist * math.cos(az) * math.cos(el),
                      cy + dist * math.sin(az) * math.cos(el),
                      H + dist * math.sin(el)))
    target = Vector((cx, cy, H * 0.05))
    view = Vector((cam_loc.x - cx, cam_loc.y - cy, 0)).normalized()
    for ob, nx, ny in walls:
        if nx * view.x + ny * view.y > 0.10:
            ob.hide_render = True
    cd = bpy.data.cameras.new("cam")
    cd.lens = 40
    cam = bpy.data.objects.new("cam", cd)
    bpy.context.scene.collection.objects.link(cam)
    cam.location = cam_loc
    cam.rotation_euler = (target - cam_loc).to_track_quat("-Z", "Y").to_euler()
    bpy.context.scene.camera = cam
    # ★ 地面只留薄薄一圈。原来给 ±1.5m，加上 46 度阳光投出又长又硬的
    # 大片阴影三角，把画面切得乱七八糟 —— 这是整张图最难看的元凶。
    # 解法：地面收紧 + 太阳抬高到接近顶光，阴影短且贴在家具脚下。
    gx0, gy0 = min(xs) - 0.25, min(ys) - 0.25
    gx1, gy1 = max(xs) + 0.25, max(ys) + 0.25
    gp = mesh_obj("ground", [(gx0, gy0, -0.145), (gx1, gy0, -0.145),
                             (gx1, gy1, -0.145), (gx0, gy1, -0.145)], [(0, 1, 2, 3)])
    paint(gp, material("mat_ground", (0.94, 0.95, 0.97), 0.95))
    world = bpy.data.worlds.new("W")
    world.use_nodes = True
    bg = world.node_tree.nodes.get("Background")
    if bg:
        bg.inputs[0].default_value = (0.38, 0.46, 0.58, 1.0)
        bg.inputs[1].default_value = 0.50
    bpy.context.scene.world = world
    sd = bpy.data.lights.new("sun", type="SUN")
    # 太阳抬高之后单位面积照度大增，能量必须相应调小，
    # 否则地板直接过曝成白纸（色差全丢）。
    sd.energy = 2.6
    sd.angle = math.radians(4)
    sd.color = (1.0, 0.95, 0.86)
    sun = bpy.data.objects.new("sun", sd)
    bpy.context.scene.collection.objects.link(sun)
    # 太阳抬高到 ~68 度：阴影短、贴在家具脚下，不再横贯画面
    sun.rotation_euler = (math.radians(22), math.radians(8), math.radians(-115))
    ad = bpy.data.lights.new("fill", type="AREA")
    ad.energy = 30.0
    ad.size = span * 1.5
    ad.color = (0.78, 0.86, 1.0)
    fill = bpy.data.objects.new("fill", ad)
    bpy.context.scene.collection.objects.link(fill)
    fill.location = (cx - span * 0.35, cy + span * 0.5, H * 3.0)
    fill.rotation_euler = (math.radians(-18), 0, math.radians(-150))
    # 低位补光：让家具侧面不糊成一片
    bd = bpy.data.lights.new("bounce", type="AREA")
    bd.energy = 18.0
    bd.size = span * 0.9
    bd.color = (1.0, 0.97, 0.92)
    bounce = bpy.data.objects.new("bounce", bd)
    bpy.context.scene.collection.objects.link(bounce)
    bounce.location = (cx + span * 0.6, cy - span * 0.55, H * 1.4)
    bounce.rotation_euler = (math.radians(24), 0, math.radians(38))
    sc = bpy.context.scene
    sc.render.engine = "BLENDER_EEVEE_NEXT"
    try:
        sc.eevee.taa_render_samples = 128
        sc.eevee.use_shadows = True
        # ★ 接触阴影 / 环境光遮蔽。
        # EEVEE Next (4.2) 已经没有 use_gtao 了，AO 靠 ray tracing 做。
        # 不打开的话家具像贴在纸面上，完全没有落地感。
        sc.eevee.use_raytracing = True
        rt = sc.eevee.ray_tracing_options
        rt.use_denoise = True
        try:
            rt.trace_max_roughness = 0.6
        except Exception:
            pass
    except Exception as e:
        log("eevee cfg:", e)
    sc.render.resolution_x, sc.render.resolution_y = 2100, 1400
    sc.render.image_settings.file_format = "PNG"
    try:
        sc.view_settings.view_transform = "Standard"
    except Exception:
        pass
    sc.render.filepath = out_png


try:
    main()
except Exception:
    import traceback
    traceback.print_exc()
    sys.exit(1)
