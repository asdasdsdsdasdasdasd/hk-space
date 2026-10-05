"""
hk-space :: packages/templates/templates.py
=================================================================
参数化家具 3D 模板库 (Demo 版)

设计原则（重要）
-----------------------------------------------------------------
Demo 好看 != 模型精细。好看 = 比例对 + 材质对 + 光照对 + 配色对。
一个有门缝线、正确倒角、正确材质的立方体柜子，在娃娃屋视角下
和精细模型肉眼几乎看不出差别。

所以这里**不做一 SKU 一模型**：
    30 个品类模板 + (尺寸 / 颜色 / 材质 / 门数) 参数 = 全部 SKU
一个 SKU 只需要存 (template_id, w_mm, d_mm, h_mm, 颜色, 模板参数)。

数值规范（冻结，不可违反）
-----------------------------------------------------------------
* 所有长度对外一律 **int mm**，字段带 _mm 后缀。内部转成米喂给 trimesh。
* 角度只用整数度，且限制在 {0, 90, 180, 270}。
* 不使用 float 米作为对外接口。

坐标约定（全项目统一）
-----------------------------------------------------------------
* **Z 轴向上**，Z = 0 是地板。
* 原点在**家具占地矩形的中心**（不是角点）。
* **+Y 是家具的正面**（柜门、沙发坐面朝向 +Y）。
* 所以一个 800x400x800 的两门柜占 x∈[-400,400], y∈[-200,200], z∈[0,800]。

挂墙件（掛牆層架 / 洞洞板 / 鏡 / 冷氣機）特殊约定
-----------------------------------------------------------------
它们自己的 z=0 是**自身底面**，不是地板。布局引擎负责把它们抬到
离地高度（例如挂墙层架抬到 1200mm）。这样模板本身保持"可复用的一块"。

纹理贴图
-----------------------------------------------------------------
所有面板默认已经生成 **planar UV**（每面按主法线做轴对齐投影到 0..1），
所以下一步把商品主图贴到柜门上只需要：

    import templates as T
    from PIL import Image
    sc = T.wardrobe(1200, 600, 2400, doors=2)
    T.apply_texture(sc, "door_0", Image.open("sku.jpg"))
    sc.export("sku.glb")

节点命名规则见 README.md「如何接纹理贴图」。
"""

from __future__ import annotations

import math
from typing import Any, Iterable, Sequence

import numpy as np
import trimesh
from shapely.geometry import Polygon
from trimesh.creation import extrude_polygon
from trimesh.visual.material import PBRMaterial

__all__ = [
    "MATERIALS", "material", "slab", "cyl", "tapered_cyl", "soft_box", "annulus",
    "place", "build_scene", "apply_texture", "TEMPLATE_FUNCS",
    "cabinet", "wardrobe", "open_shelf_unit", "drawer_chest", "tv_console",
    "shoe_cabinet", "bed_single", "bed_double", "bed_loft", "bed_storage",
    "bunk_bed", "desk", "dining_table", "side_table", "chair", "sofa",
    "chaise_longue", "shelf_unit", "storage_box", "wall_shelf", "pegboard",
    "fridge", "washer", "microwave", "air_conditioner", "screen_divider",
    "mirror", "drying_rack", "platform_storage",
]

MM = 0.001  # mm -> m
DEG = math.pi / 180.0

# 板材标准厚度（mm）：香港/IKEA 常见 18mm 低压板 / 9mm 背板
T_PANEL = 18
T_BACK = 9
T_DOOR = 18
SEAM = 3          # 门缝（视觉上"立刻像真柜子"的关键）
REVEAL = 2        # 门上下的边缝
R_EDGE = 3        # 默认倒角半径（不要纯直角，纯直角像 Minecraft）


# =====================================================================
# 材质库
# =====================================================================
# color 用 0-255 整数；metallic / roughness 是 glTF PBR 参数。
MATERIALS: dict[str, dict[str, Any]] = {
    # --- 木 ---
    "oak":        dict(color=(222, 196, 158), metallic=0.0, roughness=0.62),  # 淺橡木
    "birch":      dict(color=(236, 219, 189), metallic=0.0, roughness=0.60),  # 樺木
    "walnut":     dict(color=(124, 86, 60),   metallic=0.0, roughness=0.55),  # 胡桃木
    "teak":       dict(color=(168, 118, 70),  metallic=0.0, roughness=0.58),  # 柚木
    "white_oak":  dict(color=(233, 220, 197), metallic=0.0, roughness=0.64),
    # --- 板材（柜体主色）---
    "white":      dict(color=(242, 242, 240), metallic=0.0, roughness=0.72),  # 白色啞光板
    "cream":      dict(color=(232, 224, 208), metallic=0.0, roughness=0.70),  # 米白
    "grey":       dict(color=(146, 148, 150), metallic=0.0, roughness=0.70),  # 灰色板
    "charcoal":   dict(color=(64, 66, 70),    metallic=0.0, roughness=0.68),
    "matte_black":dict(color=(38, 38, 40),    metallic=0.0, roughness=0.70),
    "sage":       dict(color=(150, 166, 148), metallic=0.0, roughness=0.72),
    "terracotta": dict(color=(190, 126, 96),  metallic=0.0, roughness=0.74),
    # --- 布 ---
    "fabric_linen": dict(color=(214, 208, 197), metallic=0.0, roughness=0.95),
    "fabric_grey":  dict(color=(126, 130, 134), metallic=0.0, roughness=0.95),
    "fabric_blue":  dict(color=(72, 100, 124),  metallic=0.0, roughness=0.95),
    "fabric_sand":  dict(color=(198, 180, 152), metallic=0.0, roughness=0.95),
    "fabric_green": dict(color=(96, 116, 100),  metallic=0.0, roughness=0.95),
    "mattress":     dict(color=(240, 238, 232), metallic=0.0, roughness=0.92),
    "bedding":      dict(color=(206, 214, 220), metallic=0.0, roughness=0.92),
    # --- 金属 ---
    "metal_steel":  dict(color=(176, 178, 182), metallic=0.90, roughness=0.28),
    "metal_black":  dict(color=(48, 50, 54),    metallic=0.85, roughness=0.36),
    "metal_brass":  dict(color=(186, 150, 86),  metallic=0.90, roughness=0.30),
    "metal_alu":    dict(color=(206, 208, 210), metallic=0.80, roughness=0.35),  # 铝（晾衫架）
    "metal_chrome": dict(color=(214, 218, 222), metallic=1.00, roughness=0.15),
    # --- 玻璃 / 镜 / 塑料 ---
    "glass":        dict(color=(196, 214, 220), metallic=0.10, roughness=0.05,
                         alpha=70, alpha_mode="BLEND"),
    "mirror":       dict(color=(226, 232, 236), metallic=1.00, roughness=0.03),
    "glass_dark":   dict(color=(46, 52, 60),    metallic=0.25, roughness=0.14),  # 黑玻璃（微波炉门）
    "plastic_white":dict(color=(238, 240, 242), metallic=0.05, roughness=0.42),
    "plastic_grey": dict(color=(118, 122, 126), metallic=0.05, roughness=0.45),
    "rattan":       dict(color=(198, 166, 112), metallic=0.0, roughness=0.82),
}

_MAT_CACHE: dict[tuple, PBRMaterial] = {}


def _norm_color(c: Any) -> tuple[int, int, int, int]:
    """(0.85,0.82,0.75) / (217,178,128) / (217,178,128,255) -> 0-255 RGBA."""
    if isinstance(c, str):
        if c not in MATERIALS:
            raise KeyError(f"unknown material key: {c!r}")
        c = MATERIALS[c]["color"]
    c = tuple(c)
    if len(c) == 3:
        c = c + (255,)
    if len(c) != 4:
        raise ValueError(f"color must be 3 or 4 components, got {c!r}")
    # 0-1 浮点自动升级到 0-255（用户示例是 (0.85,0.82,0.75)）
    if max(c) <= 1.0 and all(isinstance(x, (int, float)) for x in c):
        c = tuple(int(round(x * 255)) for x in c)
    return tuple(int(x) for x in c)  # type: ignore[return-value]


def material(key: str = "oak", color: Any = None, *, metallic: float | None = None,
             roughness: float | None = None, alpha: int | None = None,
             name: str | None = None) -> PBRMaterial:
    """从材质库取一个 PBRMaterial（带缓存，保证同参数只产生一个 glTF material）。

    key       材质库键（见 MATERIALS），决定 metallic/roughness
    color     None=用库里的颜色；可以是材质键名字符串，或 RGB(A) 元组
    """
    spec = dict(MATERIALS.get(key, MATERIALS["oak"]))
    rgba = _norm_color(color) if color is not None else _norm_color(spec["color"])
    if alpha is not None:
        rgba = rgba[:3] + (int(alpha),)
    elif len(rgba) == 3:
        rgba = rgba + (255,)
    met = spec.get("metallic", 0.0) if metallic is None else metallic
    rou = spec.get("roughness", 0.6) if roughness is None else roughness
    alpha_mode = spec.get("alpha_mode") if rgba[3] < 255 else None
    cache_key = (name or key, rgba, met, rou, alpha_mode)
    if cache_key not in _MAT_CACHE:
        _MAT_CACHE[cache_key] = PBRMaterial(
            name=name or key,
            baseColorFactor=list(rgba),
            metallicFactor=float(met),
            roughnessFactor=float(rou),
            alphaMode=alpha_mode,
            doubleSided=True,   # 薄板背面可见，demo 里避免"缺面"
        )
    return _MAT_CACHE[cache_key]


def _resolve(body_key: str, color: Any) -> PBRMaterial:
    """模板的 color= 参数：None -> 默认材质键；str -> 另一材质键；元组 -> 换色。"""
    if isinstance(color, str):
        return material(color)
    return material(body_key, color=color)


# =====================================================================
# 基础几何（全部进出 mm）
# =====================================================================
def _arc(cx: float, cy: float, r: float, a0: float, segs: int) -> list[tuple[float, float]]:
    out = []
    for i in range(segs + 1):
        a = (a0 + 90.0 * i / segs) * DEG
        out.append((cx + r * math.cos(a), cy + r * math.sin(a)))
    return out


def rrect_poly(w_mm: float, h_mm: float, r_mm: float = R_EDGE, segs: int = 3) -> Polygon:
    """圆角矩形（逆时针）。倒角的来源——不要用纯直角。"""
    w, h = float(w_mm), float(h_mm)
    r = max(0.0, min(float(r_mm), w / 2.0 - 1e-6, h / 2.0 - 1e-6))
    if r < 0.05:
        return Polygon([(-w / 2, -h / 2), (w / 2, -h / 2), (w / 2, h / 2), (-w / 2, h / 2)])
    cx, cy = w / 2.0 - r, h / 2.0 - r
    pts = (_arc(cx, cy, r, 0.0, segs) + _arc(-cx, cy, r, 90.0, segs)
           + _arc(-cx, -cy, r, 180.0, segs) + _arc(cx, -cy, r, 270.0, segs))
    p = Polygon(pts)
    if not p.exterior.is_ccw:
        p = Polygon(list(p.exterior.coords)[::-1])
    return p


def _center(m: trimesh.Trimesh) -> trimesh.Trimesh:
    m.apply_translation(-m.bounds.mean(axis=0))
    return m


def slab(x_mm: float, y_mm: float, z_mm: float, r_mm: float = R_EDGE,
         segs: int = 3) -> trimesh.Trimesh:
    """圆角长方体，中心在原点。沿**最短边**挤出圆角截面 —— 这样一块门板
    的 4 条门面棱都是圆的，一块侧板的立棱和前后棱都是圆的。
    这就是"不要纯直角"的统一来源。"""
    x, y, z = abs(float(x_mm)), abs(float(y_mm)), abs(float(z_mm))
    axis = int(np.argmin([x, y, z]))
    if axis == 2:
        m = extrude_polygon(rrect_poly(x, y, r_mm, segs), z)
    elif axis == 1:
        m = extrude_polygon(rrect_poly(x, z, r_mm, segs), y)
        m.apply_transform(trimesh.transformations.rotation_matrix(math.pi / 2, [1, 0, 0]))
    else:
        # 绕 Y 转 90 度后 profile-X 落到 -Z，所以 profile 的宽高必须写成 (z, y)
        m = extrude_polygon(rrect_poly(z, y, r_mm, segs), x)
        m.apply_transform(trimesh.transformations.rotation_matrix(math.pi / 2, [0, 1, 0]))
    m.apply_scale(MM)
    return _center(m)


def cyl(d_mm: float, h_mm: float, sections: int = 20) -> trimesh.Trimesh:
    """圆柱，轴向 Z，中心在原点。"""
    return trimesh.creation.cylinder(radius=float(d_mm) / 2.0 * MM,
                                     height=float(h_mm) * MM, sections=sections)


def tapered_cyl(d_bottom_mm: float, d_top_mm: float, h_mm: float,
                sections: int = 14) -> trimesh.Trimesh:
    """锥形圆腿（家具腿的正解：上粗下细），轴向 Z，中心在原点。"""
    m = cyl(d_bottom_mm, h_mm, sections=sections)
    k = float(d_top_mm) / float(d_bottom_mm)
    v = m.vertices.copy()
    top = v[:, 2] > 0
    v[top, 0] *= k
    v[top, 1] *= k
    m.vertices = v
    return m


def annulus(r_min_mm: float, r_max_mm: float, h_mm: float, sections: int = 32) -> trimesh.Trimesh:
    """圆环柱（洗衣机舱门圈、把手环），轴向 Z，中心在原点。"""
    return trimesh.creation.annulus(r_min=float(r_min_mm) * MM, r_max=float(r_max_mm) * MM,
                                    height=float(h_mm) * MM, sections=sections)


def soft_box(x_mm: float, y_mm: float, z_mm: float, bulge: float = 0.42,
             count: Sequence[int] = (28, 16)) -> trimesh.Trimesh:
    """软包块（坐垫/枕头/床垫）：超椭球，bulge 越小越方，越大越软。
    沙发/床的正解——硬盒子看着像石头。中心在原点。"""
    s = trimesh.creation.uv_sphere(radius=1.0, count=list(count))
    v = s.vertices
    u = np.arcsin(np.clip(v[:, 2], -1.0, 1.0))          # 纬度
    w = np.arctan2(v[:, 1], v[:, 0])                    # 经度
    n = float(bulge)

    def p(t):
        return np.sign(t) * np.abs(t) ** n

    cu, su = p(np.cos(u)), p(np.sin(u))
    cw, sw = p(np.cos(w)), p(np.sin(w))
    out = np.empty_like(v)
    out[:, 0] = cu * cw * (x_mm / 2.0)
    out[:, 1] = cu * sw * (y_mm / 2.0)
    out[:, 2] = su * (z_mm / 2.0)
    s.vertices = out * MM
    return s


def place(m: trimesh.Trimesh, x_mm: float = 0.0, y_mm: float = 0.0,
          z_mm: float | None = None, zb_mm: float | None = None) -> trimesh.Trimesh:
    """摆放：x_mm/y_mm 是**包围盒中心**相对模型原点的偏移，
    zb_mm = 底面离地高度（给了它就忽略 z_mm），z_mm = 中心高度。"""
    c = m.bounds.mean(axis=0)
    dz = (zb_mm * MM - m.bounds[0][2]) if zb_mm is not None else \
         ((z_mm * MM - c[2]) if z_mm is not None else 0.0)
    m.apply_translation([x_mm * MM - c[0], y_mm * MM - c[1], dz])
    return m


# =====================================================================
# UV（给"下一步贴商品主图"用）
# =====================================================================
def _uv_axes(axis: int, sign: int) -> tuple[int, int]:
    if axis == 2:
        return (0, 1) if sign > 0 else (0, 1)
    if axis == 0:
        return (1, 2) if sign > 0 else (2, 1)
    return (0, 2) if sign > 0 else (2, 0)


def planar_uv(mesh: trimesh.Trimesh) -> np.ndarray:
    """每面按主法线做轴对齐投影，UV 落在 0..1。
    代价是要炸开顶点（trimesh 的 uv 是 per-vertex 的），文件会大一点，demo 无所谓。"""
    bb0, bb1 = mesh.bounds
    size = np.maximum(bb1 - bb0, 1e-9)
    fn = mesh.face_normals
    dom = np.argmax(np.abs(fn), axis=1)
    sgn = np.sign(fn[np.arange(len(fn)), dom]).astype(int)
    sgn[sgn == 0] = 1
    mesh.unmerge_vertices()
    tri = mesh.faces
    verts = mesh.vertices
    uv = np.zeros((len(verts), 2), dtype=np.float64)
    for axis in (0, 1, 2):
        for s in (-1, 1):
            sel = np.where((dom == axis) & (sgn == s))[0]
            if len(sel) == 0:
                continue
            ua, va = _uv_axes(axis, s)
            vi = tri[sel].ravel()
            uv[vi, 0] = (verts[vi, ua] - bb0[ua]) / size[ua]
            uv[vi, 1] = 1.0 - (verts[vi, va] - bb0[va]) / size[va]
    return uv


# =====================================================================
# 装配器
# =====================================================================
class Model:
    """把一堆 (mesh, material) 装成一个 trimesh.Scene，节点名稳定可寻址。"""

    def __init__(self, name: str, *, uv: bool = True):
        self.name = name
        self.uv = uv
        self.scene = trimesh.Scene()
        self._n = 0
        self.parts: list[str] = []

    def add(self, mesh: trimesh.Trimesh, mat: PBRMaterial | str,
            node: str | None = None, *, uv: bool | None = None) -> str:
        if isinstance(mat, str):
            mat = material(mat)
        use_uv = self.uv if uv is None else uv
        if use_uv:
            mesh.visual = trimesh.visual.TextureVisuals(uv=planar_uv(mesh), material=mat)
        else:
            mesh.visual = trimesh.visual.TextureVisuals(material=mat)
        base = node or "part"
        nm = base if base not in self.parts else f"{base}_{self._n}"
        self._n += 1
        if nm in self.parts:
            nm = f"{base}_{self._n}"
        self.parts.append(nm)
        self.scene.add_geometry(mesh, node_name=nm, geom_name=nm)
        return nm

    def build(self, meta: dict[str, Any] | None = None) -> trimesh.Scene:
        md: dict[str, Any] = {"hk_space": dict(name=self.name, parts=len(self.parts))}
        if meta:
            md["hk_space"].update(meta)
        self.scene.metadata.update(md)
        return self.scene


def build_scene(name: str, parts: Iterable[tuple[trimesh.Trimesh, PBRMaterial]],
                *, uv: bool = True) -> trimesh.Scene:
    m = Model(name, uv=uv)
    for i, (mesh, mat) in enumerate(parts):
        m.add(mesh, mat, node=f"part_{i}")
    return m.build()


def apply_texture(scene: trimesh.Scene, node: str, image, *,
                  material_key: str = "white", uv_rect: tuple[float, float, float, float] | None = None,
                  keep_color: bool = True) -> None:
    """把一张商品主图贴到指定节点上（下一步：SKU 主图 -> 柜门）。

    node       Model.add 时的节点名，例如 "door_0" / "front"
    image      PIL.Image 或图片路径
    uv_rect    (u0,v0,u1,v1) 只贴一角；None = 贴满
    """
    if isinstance(image, str):
        from PIL import Image
        image = Image.open(image)
    if uv_rect is not None:
        gm = scene.geometry[node]
        uv = np.asarray(gm.visual.uv, dtype=np.float64)
        u0, v0, u1, v1 = uv_rect
        uv[:, 0] = u0 + uv[:, 0] * (u1 - u0)
        uv[:, 1] = v0 + uv[:, 1] * (v1 - v0)
        gm.visual.uv = uv
    base = material(material_key)
    col = base.baseColorFactor if keep_color else [255, 255, 255, 255]
    gm = scene.geometry[node]
    gm.visual.material = PBRMaterial(
        name=f"tex_{node}",
        baseColorFactor=list(col),
        metallicFactor=base.metallicFactor or 0.0,
        roughnessFactor=base.roughnessFactor or 0.7,
        baseColorTexture=image,
        doubleSided=True,
    )


# =====================================================================
# 零件级复用件
# =====================================================================
def _legs(w_mm: float, d_mm: float, inset_mm: float, h_mm: float, dia_mm: float,
          mat: PBRMaterial, *, taper: float = 0.62, bottom_mm: float = 0.0,
          sections: int = 12) -> list[tuple[trimesh.Trimesh, PBRMaterial]]:
    """四条锥形腿。**看得见地板**是"显大"的关键，所以桌/椅/沙发一律架起来。"""
    out = []
    dx = w_mm / 2.0 - inset_mm - dia_mm / 2.0
    dy = d_mm / 2.0 - inset_mm - dia_mm / 2.0
    for sx in (-1, 1):
        for sy in (-1, 1):
            leg = tapered_cyl(dia_mm * taper, dia_mm, h_mm, sections=sections)
            place(leg, sx * dx, sy * dy, zb_mm=bottom_mm)
            out.append((leg, mat))
    return out


def _door_bank(w_mm: float, h_mm: float, n: int, t_mm: float, y_mm: float,
               z0_mm: float, mat: PBRMaterial, *, seam: float = SEAM,
               r_mm: float = R_EDGE) -> list[tuple[trimesh.Trimesh, PBRMaterial, str, float, float]]:
    """一排柜门，门与门之间留 seam(mm) 缝。返回 (mesh, mat, name, 中心x, 宽度)。"""
    n = max(1, int(n))
    wd = (w_mm - seam * (n - 1)) / n
    out = []
    for i in range(n):
        cx = -w_mm / 2.0 + wd / 2.0 + i * (wd + seam)
        door = slab(wd, t_mm, h_mm, r_mm)
        place(door, cx, y_mm, zb_mm=z0_mm)
        out.append((door, mat, f"door_{i}", cx, wd))
    return out


def _shadow_of(mat: PBRMaterial, k: float = 0.42) -> PBRMaterial:
    """门缝里那条暗线用的材质（= 主色压暗），比纯黑自然。"""
    rgb = _norm_color(mat.baseColorFactor)[:3]
    return material("matte_black", color=tuple(int(c * k) for c in rgb), roughness=0.85)


def _seam_strips(w_mm: float, h_mm: float, n: int, t_mm: float, y_mm: float,
                 z0_mm: float, seam: float = SEAM, *, vertical: bool = True
                 ) -> list[trimesh.Trimesh]:
    """填在 3mm 门缝里的暗色条。

    为什么需要它：缝是真的留了 3mm，但在**平光渲染**里缝两侧是同一个颜色、
    同一个法线，3mm 的缝会完全看不见。真实照片里缝是暗的，是因为那条缝的
    ambient occlusion。这里用一个压暗的细条把这个 AO 假出来 —— 视觉上一秒
    变真柜子，比多加 500 个面有用得多。"""
    n = max(1, int(n))
    out: list[trimesh.Trimesh] = []
    if n < 2:
        return out
    if vertical:
        wd = (w_mm - seam * (n - 1)) / n
        for i in range(n - 1):
            cx = -w_mm / 2.0 + (i + 1) * wd + (i + 0.5) * seam
            s = slab(seam, t_mm, h_mm, 0.4)
            place(s, cx, y_mm, zb_mm=z0_mm)
            out.append(s)
    else:
        hd = (h_mm - seam * (n - 1)) / n
        for i in range(n - 1):
            cz = z0_mm + (i + 1) * hd + (i + 0.5) * seam
            s = slab(w_mm, t_mm, seam, 0.4)
            place(s, 0, y_mm, z_mm=cz)
            out.append(s)
    return out


def _bar_handle(length_mm: float, *, dia_mm: float = 12.0, standoff_mm: float = 30.0,
                vertical: bool = True, sections: int = 10) -> list[trimesh.Trimesh]:
    """金属长条把手（一根杆 + 两个脚）。便宜且立刻像真家具。"""
    if vertical:
        bar = cyl(dia_mm, length_mm, sections)
        a = cyl(dia_mm, standoff_mm, 8)
        a.apply_transform(trimesh.transformations.rotation_matrix(math.pi / 2, [1, 0, 0]))
        place(a, 0, 0, z_mm=length_mm / 2.0 - dia_mm * 1.2)
        b = a.copy()
        place(b, 0, 0, z_mm=-length_mm / 2.0 + dia_mm * 1.2)
    else:
        bar = cyl(dia_mm, length_mm, sections)
        bar.apply_transform(trimesh.transformations.rotation_matrix(math.pi / 2, [0, 1, 0]))
        a = cyl(dia_mm, standoff_mm, 8)
        a.apply_transform(trimesh.transformations.rotation_matrix(math.pi / 2, [1, 0, 0]))
        place(a, length_mm / 2.0 - dia_mm * 1.2, 0, z_mm=0)
        b = a.copy()
        place(b, -length_mm / 2.0 + dia_mm * 1.2, 0, z_mm=0)
    return [bar, a, b]


def _handle_at(mesh_list: list[trimesh.Trimesh], x_mm: float, y_mm: float, z_mm: float,
               rot_y_deg: int = 0) -> list[trimesh.Trimesh]:
    out = []
    for m in mesh_list:
        c = m.copy()
        if rot_y_deg:
            c.apply_transform(trimesh.transformations.rotation_matrix(rot_y_deg * DEG, [0, 0, 1]))
        place(c, x_mm, y_mm, z_mm=z_mm)
        out.append(c)
    return out


def _carcass(W: float, D: float, H: float, *, t: float = T_PANEL, back: float = T_BACK,
             shelves: int = 0, plinth: float = 0.0, mat_out: PBRMaterial,
             mat_in: PBRMaterial | None = None, r: float = 2.5) -> list[tuple[trimesh.Trimesh, PBRMaterial]]:
    """柜体：两侧板 + 顶板 + 底板 + 背板 + (可选)层板 + (可选)踢脚。
    正面敞开——门是独立零件，这样才有门缝阴影。"""
    parts: list[tuple[trimesh.Trimesh, PBRMaterial]] = []
    z0 = plinth
    inner_w = W - 2 * t
    inner_h = H - z0 - t
    for sx in (-1, 1):
        parts.append((place(slab(t, D, H - z0, r), sx * (W / 2.0 - t / 2.0), 0, zb_mm=z0), mat_out))
    parts.append((place(slab(inner_w, D, t, r), 0, 0, zb_mm=H - t), mat_out))
    parts.append((place(slab(inner_w, D, t, r), 0, 0, zb_mm=z0), mat_out))
    parts.append((place(slab(inner_w, back, inner_h, 1.0), 0, -D / 2.0 + back / 2.0, zb_mm=z0 + t),
                  mat_in or mat_out))
    for i in range(int(shelves)):
        zz = z0 + t + (inner_h) * (i + 1) / (shelves + 1)
        parts.append((place(slab(inner_w, D - back - 20, t, r), 0, 10, z_mm=zz), mat_in or mat_out))
    if plinth > 0:
        parts.append((place(slab(W - 40, D - 40, plinth, 1.5), 0, 0, zb_mm=0), mat_in or mat_out))
    return parts


# =====================================================================
# 櫃 (7)
# =====================================================================
def cabinet(w_mm: int = 800, d_mm: int = 400, h_mm: int = 800, doors: int = 2,
            color: Any = None, *, body: str = "white", shelves: int = 1,
            plinth_mm: int = 80, legs: bool = False, metal: str = "metal_steel",
            handles: bool = True, **kw) -> trimesh.Scene:
    """兩門櫃 / 三門櫃 —— 最通用的矮櫃（IKEA BESTÅ 那类）。"""
    W, D, H = int(w_mm), int(d_mm), int(h_mm)
    mo = _resolve(body, color)
    mi = material(body, color=tuple(max(0, c - 12) for c in _norm_color(mo.baseColorFactor)[:3]))
    m = Model("cabinet")
    plinth = 0 if legs else int(plinth_mm)
    for mesh, mat in _carcass(W, D, H, shelves=shelves, plinth=plinth, mat_out=mo, mat_in=mi):
        m.add(mesh, mat, node="body")
    z0 = plinth + REVEAL
    dh = H - plinth - 2 * REVEAL
    for s in _seam_strips(W, dh, doors, T_DOOR, D / 2.0 + T_DOOR / 2.0, z0):
        m.add(s, _shadow_of(mo), node="seam")
    for mesh, mat, nm, cx, wd in _door_bank(W, dh, doors, T_DOOR, D / 2.0 + T_DOOR / 2.0, z0, mo):
        m.add(mesh, mat, node=nm)
        if handles:
            hx = cx + (wd / 2.0 - 45) * (1 if doors <= 2 else (1 if cx < 0 else -1))
            for hm in _handle_at(_bar_handle(min(160, dh * 0.25)), hx, D / 2.0 + T_DOOR + 15, z0 + dh * 0.55):
                m.add(hm, material(metal), node=f"handle_{nm}")
    if legs:
        for lm in _legs(W, D, 40, 70, 40, material(metal), bottom_mm=0):
            m.add(lm[0], lm[1], node="leg")
    return m.build(dict(w_mm=W, d_mm=D, h_mm=H, doors=int(doors)))


def wardrobe(w_mm: int = 1200, d_mm: int = 600, h_mm: int = 2400, doors: int = 3,
             color: Any = None, *, body: str = "white_oak", rail: bool = True,
             top_box: bool = True, plinth_mm: int = 60, **kw) -> trimesh.Scene:
    """到頂衣櫃（香港核心品类：顶天立地，多出来的顶部空间做顶柜）。"""
    W, D, H = int(w_mm), int(d_mm), int(h_mm)
    mo = _resolve(body, color)
    mi = material(body)
    m = Model("wardrobe")
    split = int(H * 0.82) if top_box else H
    # 柜体做到顶（顶天立地），中间一块固定层板把下部挂衣区和顶部储物格分开
    parts = _carcass(W, D, H, shelves=0, plinth=int(plinth_mm), mat_out=mo, mat_in=mi)
    parts.append((place(slab(W - 2 * T_PANEL, D - 40, T_PANEL, 2), 0, 10, z_mm=split), mi))
    if rail:
        rod = cyl(25, W - 2 * T_PANEL - 20, 12)
        rod.apply_transform(trimesh.transformations.rotation_matrix(math.pi / 2, [0, 1, 0]))
        place(rod, 0, -60, z_mm=split - 180)
        parts.append((rod, material("metal_chrome")))
    for mesh, mat in parts:
        m.add(mesh, mat, node="body")
    # 下门
    dh_bottom = split - int(plinth_mm) - 2 * REVEAL - T_PANEL
    for s in _seam_strips(W, dh_bottom, doors, T_DOOR, D / 2.0 + T_DOOR / 2.0,
                          int(plinth_mm) + REVEAL):
        m.add(s, _shadow_of(mo), node="seam")
    for mesh, mat, nm, cx, wd in _door_bank(W, dh_bottom, doors, T_DOOR,
                                            D / 2.0 + T_DOOR / 2.0,
                                            int(plinth_mm) + REVEAL, mo):
        m.add(mesh, mat, node=nm)
        hx = cx + (wd / 2.0 - 40) * (1 if cx < 0 else -1)
        for hm in _handle_at(_bar_handle(280), hx, D / 2.0 + T_DOOR + 16, int(plinth_mm) + dh_bottom * 0.5):
            m.add(hm, material("metal_steel"), node=f"handle_{nm}")
    # 顶柜门
    if top_box:
        dh_top = H - split - 2 * REVEAL - T_PANEL
        for s in _seam_strips(W, dh_top, doors, T_DOOR, D / 2.0 + T_DOOR / 2.0, split + REVEAL):
            m.add(s, _shadow_of(mo), node="top_seam")
        for mesh, mat, nm, cx, wd in _door_bank(W, dh_top, doors, T_DOOR,
                                                D / 2.0 + T_DOOR / 2.0, split + REVEAL, mo):
            m.add(mesh, mat, node=f"top_{nm}")
    return m.build(dict(w_mm=W, d_mm=D, h_mm=H, doors=int(doors)))


def open_shelf_unit(w_mm: int = 770, d_mm: int = 390, h_mm: int = 770, cols: int = 2,
                    rows: int = 2, color: Any = None, *, body: str = "oak", **kw) -> trimesh.Scene:
    """開放格櫃（KALLAX 那类格子柜）。"""
    W, D, H = int(w_mm), int(d_mm), int(h_mm)
    mo = _resolve(body, color)
    m = Model("open_shelf_unit")
    t = 30
    for sx in (-1, 1):
        m.add(place(slab(t, D, H, 2.5), sx * (W / 2.0 - t / 2.0), 0, zb_mm=0), mo, node="side")
    for sz in (0, 1):
        zz = 0 if sz == 0 else H - t
        m.add(place(slab(W - 2 * t, D, t, 2.5), 0, 0, zb_mm=zz), mo, node="shelf")
    for i in range(1, max(1, int(rows))):
        m.add(place(slab(W - 2 * t, D, t, 2.5), 0, 0,
                    z_mm=t + (H - 2 * t) * i / rows), mo, node="shelf")
    for j in range(1, max(1, int(cols))):
        m.add(place(slab(t, D, H - 2 * t, 2.5), -W / 2.0 + t + (W - 2 * t) * j / cols, 0, zb_mm=t),
              mo, node="divider")
    m.add(place(slab(W - 2 * t, 6, H - 2 * t, 1.0), 0, -D / 2.0 + 3, zb_mm=t), mo, node="back")
    return m.build(dict(w_mm=W, d_mm=D, h_mm=H, cols=int(cols), rows=int(rows)))


def drawer_chest(w_mm: int = 800, d_mm: int = 480, h_mm: int = 1000, drawers: int = 4,
                 color: Any = None, *, body: str = "white", legs: bool = False, **kw) -> trimesh.Scene:
    """抽屜櫃（MALM 那类）。水平缝 + 横条把手。"""
    W, D, H = int(w_mm), int(d_mm), int(h_mm)
    mo = _resolve(body, color)
    mi = material(body)
    m = Model("drawer_chest")
    plinth = 0 if legs else 40
    for mesh, mat in _carcass(W, D, H, shelves=0, plinth=plinth, mat_out=mo, mat_in=mi):
        m.add(mesh, mat, node="body")
    n = max(1, int(drawers))
    top = H - T_PANEL - REVEAL
    z_bot = plinth + REVEAL
    hh = (top - z_bot - SEAM * (n - 1)) / n
    for s in _seam_strips(W - 4, hh * n + SEAM * (n - 1), n, T_DOOR, D / 2.0 + T_DOOR / 2.0,
                          z_bot, vertical=False):
        m.add(s, _shadow_of(mo), node="seam")
    for i in range(n):
        zz = z_bot + i * (hh + SEAM)
        m.add(place(slab(W - 4, T_DOOR, hh, 2.5), 0, D / 2.0 + T_DOOR / 2.0, zb_mm=zz), mo, node=f"drawer_{i}")
        for hm in _handle_at(_bar_handle(min(W - 120, 400), vertical=False),
                             0, D / 2.0 + T_DOOR + 16, zz + hh - 55):
            m.add(hm, material("metal_steel"), node=f"handle_{i}")
    if legs:
        for lm in _legs(W, D, 35, 60, 36, material("metal_black")):
            m.add(lm[0], lm[1], node="leg")
    return m.build(dict(w_mm=W, d_mm=D, h_mm=H, drawers=n))


def tv_console(w_mm: int = 1500, d_mm: int = 400, h_mm: int = 450, doors: int = 2,
               color: Any = None, *, body: str = "walnut", open_niche: bool = True, **kw) -> trimesh.Scene:
    """電視櫃 —— 低矮 + 细腿，**底下看得见地板**，是小户型显大的教科书动作。"""
    W, D, H = int(w_mm), int(d_mm), int(h_mm)
    mo = _resolve(body, color)
    mi = material(body)
    m = Model("tv_console")
    leg_h = 130
    bodyh = H - leg_h
    for mesh, mat in _carcass(W, D, bodyh, shelves=1, plinth=0, mat_out=mo, mat_in=mi):
        mesh.apply_translation([0, 0, leg_h * MM])
        m.add(mesh, mat, node="body")
    if open_niche:
        nw = int(W * 0.32)
        m.add(place(slab(nw, T_DOOR, bodyh - 2 * T_PANEL - 2 * REVEAL, 2.5),
                    0, D / 2.0 + T_DOOR / 2.0, zb_mm=leg_h + T_PANEL + REVEAL), mo, node="niche_door")
    side = (W - int(W * 0.32)) / 2.0 if open_niche else W
    dh = bodyh - 2 * T_PANEL - 2 * REVEAL
    if open_niche:      # 缝在中间暗格与两侧门之间，位置要对准，不能随便等分
        for sx in (-1, 1):
            s = slab(SEAM + 2, T_DOOR, dh, 0.4)
            place(s, sx * (nw / 2.0 + 1), D / 2.0 + T_DOOR / 2.0,
                  zb_mm=leg_h + T_PANEL + REVEAL)
            m.add(s, _shadow_of(mo), node="seam")
    for sx in (-1, 1):
        door = slab(side - 4, T_DOOR, dh, R_EDGE)
        place(door, sx * (W / 2.0 - side / 2.0), D / 2.0 + T_DOOR / 2.0, zb_mm=leg_h + T_PANEL + REVEAL)
        m.add(door, mo, node=f"door_{'l' if sx < 0 else 'r'}")
        for hm in _handle_at(_bar_handle(min(200, dh * 0.5)), sx * (side / 2.0 - 70),
                             D / 2.0 + T_DOOR + 16, leg_h + bodyh * 0.5):
            m.add(hm, material("metal_steel"), node="handle")
    for lm in _legs(W, D, 45, leg_h, 42, material("metal_black"), taper=0.7):
        m.add(lm[0], lm[1], node="leg")
    return m.build(dict(w_mm=W, d_mm=D, h_mm=H, doors=int(doors)))


def shoe_cabinet(w_mm: int = 900, d_mm: int = 350, h_mm: int = 1000, doors: int = 2,
                 color: Any = None, *, body: str = "white", shelves: int = 4,
                 legs: bool = True, **kw) -> trimesh.Scene:
    """鞋櫃 —— 香港玄关必备，浅进深 + 两层/多层斜层板 + 抬高踢脚（放常穿的鞋）。"""
    W, D, H = int(w_mm), int(d_mm), int(h_mm)
    mo = _resolve(body, color)
    mi = material(body)
    m = Model("shoe_cabinet")
    leg_h = 90 if legs else 0
    for mesh, mat in _carcass(W, D, H - leg_h, shelves=int(shelves), plinth=0, mat_out=mo, mat_in=mi):
        mesh.apply_translation([0, 0, leg_h * MM])
        m.add(mesh, mat, node="body")
    for s in _seam_strips(W, H - leg_h - 2 * REVEAL, doors, T_DOOR, D / 2.0 + T_DOOR / 2.0,
                          leg_h + REVEAL):
        m.add(s, _shadow_of(mo), node="seam")
    for mesh, mat, nm, cx, wd in _door_bank(W, H - leg_h - 2 * REVEAL, doors, T_DOOR,
                                            D / 2.0 + T_DOOR / 2.0, leg_h + REVEAL, mo):
        m.add(mesh, mat, node=nm)
        hx = cx + (wd / 2.0 - 40) * (1 if cx <= 0 else -1)
        for hm in _handle_at(_bar_handle(120), hx, D / 2.0 + T_DOOR + 15, leg_h + (H - leg_h) * 0.5):
            m.add(hm, material("metal_steel"), node=f"handle_{nm}")
    if legs:
        for lm in _legs(W, D, 35, leg_h, 34, material("metal_black"), taper=0.65):
            m.add(lm[0], lm[1], node="leg")
    else:
        m.add(place(slab(W - 40, D - 40, 60, 1.5), 0, 0, zb_mm=0), mi, node="plinth")
    return m.build(dict(w_mm=W, d_mm=D, h_mm=H, doors=int(doors)))


# =====================================================================
# 床 (5)
# =====================================================================
def _bed_frame(W: float, L: float, frame_h: float, *, mat_frame: PBRMaterial,
               mat_leg: PBRMaterial, headboard_mm: float = 0.0,
               slats: int = 6, leg_inset: float = 70) -> list[tuple[trimesh.Trimesh, PBRMaterial]]:
    """床架：四腿 + 前后横档 + 侧档 + 床板条。床下留空 -> 看得见地板。"""
    out: list[tuple[trimesh.Trimesh, PBRMaterial]] = []
    leg_h = frame_h - 90
    for sx in (-1, 1):
        for sy in (-1, 1):
            leg = slab(70, 70, leg_h, 4)
            place(leg, sx * (W / 2.0 - leg_inset), sy * (L / 2.0 - leg_inset), zb_mm=0)
            out.append((leg, mat_leg))
    for sy in (-1, 1):
        out.append((place(slab(W, 40, 90, 3), 0, sy * (L / 2.0 - 20), zb_mm=leg_h), mat_frame))
    for sx in (-1, 1):
        out.append((place(slab(40, L - 80, 90, 3), sx * (W / 2.0 - 20), 0, zb_mm=leg_h), mat_frame))
    for i in range(int(slats)):
        yy = -(L / 2.0 - 120) + (L - 240) * i / max(1, slats - 1)
        out.append((place(slab(W - 90, 55, 22, 2), 0, yy, zb_mm=leg_h + 25), mat_frame))
    if headboard_mm > 0:
        out.append((place(slab(W, 45, headboard_mm, 4), 0, -L / 2.0 + 22, zb_mm=0), mat_frame))
    return out


def bed_single(w_mm: int = 900, d_mm: int = 1900, h_mm: int = 400, color: Any = None,
               *, body: str = "birch", headboard_mm: int = 700, **kw) -> trimesh.Scene:
    """單人床（3 呎，900x1900）。"""
    W, L, H = int(w_mm), int(d_mm), int(h_mm)
    mf = _resolve(body, color)
    m = Model("bed_single")
    for mesh, mat in _bed_frame(W, L, H, mat_frame=mf, mat_leg=material("metal_black"),
                                headboard_mm=headboard_mm):
        m.add(mesh, mat, node="frame")
    m.add(place(soft_box(W - 30, L - 100, 200, 0.34), 0, 0, zb_mm=H + 30), material("mattress"), node="mattress")
    m.add(place(soft_box(W - 180, 420, 130, 0.5), 0, -L / 2.0 + 330, zb_mm=H + 225), material("bedding"), node="pillow")
    return m.build(dict(w_mm=W, d_mm=L, h_mm=H + 230))


def bed_double(w_mm: int = 1500, d_mm: int = 2000, h_mm: int = 400, color: Any = None,
               *, body: str = "oak", headboard_mm: int = 800, **kw) -> trimesh.Scene:
    """雙人床（4 呎半 1372 / 5 呎 1524 都行，默认 1500x2000）。"""
    W, L, H = int(w_mm), int(d_mm), int(h_mm)
    mf = _resolve(body, color)
    m = Model("bed_double")
    for mesh, mat in _bed_frame(W, L, H, mat_frame=mf, mat_leg=material("metal_black"),
                                headboard_mm=headboard_mm, slats=7):
        m.add(mesh, mat, node="frame")
    m.add(place(soft_box(W - 30, L - 100, 230, 0.34), 0, 0, zb_mm=H + 30), material("mattress"), node="mattress")
    for i, sx in enumerate((-1, 1)):
        m.add(place(soft_box(W / 2.0 - 120, 450, 140, 0.5), sx * W / 4.0, -L / 2.0 + 350, zb_mm=H + 260),
              material("bedding"), node=f"pillow_{i}")
    return m.build(dict(w_mm=W, d_mm=L, h_mm=H + 260))


def bed_loft(w_mm: int = 1000, d_mm: int = 1900, h_mm: int = 1800, color: Any = None,
             *, body: str = "birch", deck_h_mm: int = 1750, desk_under: bool = True,
             ladder: bool = True, **kw) -> trimesh.Scene:
    """高架床 —— 香港小户型的核心解法：**竖向堆叠**，床下放书桌。"""
    W, L, H = int(w_mm), int(d_mm), int(h_mm)
    mf = _resolve(body, color)
    mm_ = material("metal_black")
    m = Model("bed_loft")
    deck = min(int(deck_h_mm), H - 100)
    for sx in (-1, 1):
        for sy in (-1, 1):
            post = slab(60, 60, deck, 4)
            place(post, sx * (W / 2.0 - 30), sy * (L / 2.0 - 30), zb_mm=0)
            m.add(post, mf, node="post")
    for sy in (-1, 1):
        m.add(place(slab(W, 45, 90, 3), 0, sy * (L / 2.0 - 22), zb_mm=deck - 90), mf, node="rail")
    for sx in (-1, 1):
        m.add(place(slab(45, L - 90, 90, 3), sx * (W / 2.0 - 22), 0, zb_mm=deck - 90), mf, node="rail")
    for i in range(7):
        yy = -(L / 2.0 - 140) + (L - 280) * i / 6.0
        m.add(place(slab(W - 100, 55, 22, 2), 0, yy, zb_mm=deck - 60), mf, node="slat")
    m.add(place(soft_box(W - 30, L - 70, 190, 0.34), 0, 0, zb_mm=deck), material("mattress"), node="mattress")
    # 护栏（外侧长边，防止掉下来）
    m.add(place(slab(W - 60, 30, 320, 3), 0, L / 2.0 - 55, zb_mm=deck + 130), mf, node="guard")
    if ladder:
        for sy in (-1, 1):
            m.add(place(slab(40, 40, deck, 3), W / 2.0 - 130, sy * 190, zb_mm=0), mf, node="ladder_rail")
        for i in range(4):
            m.add(place(slab(30, 380, 30, 2), W / 2.0 - 130, 0, z_mm=deck * (i + 1) / 5.0),
                  mf, node="ladder_rung")
    if desk_under:
        dz = 720
        m.add(place(slab(W - 120, L - 200, 30, 3), 0, 0, zb_mm=dz), mf, node="desk_top")
        for sx in (-1, 1):
            m.add(place(slab(30, L - 300, dz - 30, 3), sx * (W / 2.0 - 90), 0, zb_mm=0), mf, node="desk_leg")
    return m.build(dict(w_mm=W, d_mm=L, h_mm=deck + 190))


def bed_storage(w_mm: int = 1500, d_mm: int = 2000, h_mm: int = 450, color: Any = None,
                *, body: str = "white_oak", base_mm: int = 300, **kw) -> trimesh.Scene:
    """收納床（油壓/氣壓掀床）—— 床底整个是储物箱，HK 核心品类。"""
    W, L = int(w_mm), int(d_mm)
    base = int(base_mm)
    mf = _resolve(body, color)
    m = Model("bed_storage")
    m.add(place(slab(W, L, base - 60, 3), 0, 0, zb_mm=60), mf, node="base")
    m.add(place(slab(W - 60, L - 60, 60, 2), 0, 0, zb_mm=0), material("charcoal"), node="plinth")
    # 掀床分缝（正面一条竖向暗缝）+ 液压杆
    m.add(place(slab(8, 14, base - 70, 1.0), 0, L / 2.0 + 3, z_mm=(base - 60) / 2.0 + 60),
          material("charcoal"), node="seam")
    m.add(place(slab(W - 40, 60, 30, 2), 0, 0, zb_mm=base - 30), material("metal_black"), node="lifter")
    m.add(place(slab(W - 30, L - 30, 90, 3), 0, 0, zb_mm=base), mf, node="deck")
    m.add(place(soft_box(W - 60, L - 90, 230, 0.34), 0, 0, zb_mm=base + 90), material("mattress"), node="mattress")
    m.add(place(slab(W, 45, 800, 4), 0, -L / 2.0 + 22, zb_mm=0), mf, node="headboard")
    for i, sx in enumerate((-1, 1)):
        m.add(place(soft_box(W / 2.0 - 120, 450, 140, 0.5), sx * W / 4.0, -L / 2.0 + 400,
                    zb_mm=base + 320), material("bedding"), node=f"pillow_{i}")
    return m.build(dict(w_mm=W, d_mm=L, h_mm=base + 320))


def bunk_bed(w_mm: int = 1000, d_mm: int = 1900, h_mm: int = 1700, color: Any = None,
             *, body: str = "birch", lower_h_mm: int = 350, **kw) -> trimesh.Scene:
    """上下舖 —— 竖向堆叠的第二解。"""
    W, L, H = int(w_mm), int(d_mm), int(h_mm)
    low = int(lower_h_mm)
    up = H - 220
    mf = _resolve(body, color)
    m = Model("bunk_bed")
    for sx in (-1, 1):
        for sy in (-1, 1):
            m.add(place(slab(60, 60, H, 4), sx * (W / 2.0 - 30), sy * (L / 2.0 - 30), zb_mm=0), mf, node="post")
    for deck, tag in ((low, "lower"), (up, "upper")):
        for sy in (-1, 1):
            m.add(place(slab(W, 45, 90, 3), 0, sy * (L / 2.0 - 22), zb_mm=deck - 90), mf, node=f"rail_{tag}")
        for sx in (-1, 1):
            m.add(place(slab(45, L - 90, 90, 3), sx * (W / 2.0 - 22), 0, zb_mm=deck - 90), mf, node=f"rail_{tag}")
        for i in range(6):
            yy = -(L / 2.0 - 150) + (L - 300) * i / 5.0
            m.add(place(slab(W - 100, 55, 22, 2), 0, yy, zb_mm=deck - 60), mf, node=f"slat_{tag}")
        m.add(place(soft_box(W - 30, L - 70, 180, 0.34), 0, 0, zb_mm=deck), material("mattress"), node=f"mattress_{tag}")
    m.add(place(slab(W - 60, 30, 320, 3), 0, L / 2.0 - 55, zb_mm=up + 130), mf, node="guard")
    for sy in (-1, 1):
        m.add(place(slab(40, 40, up, 3), W / 2.0 - 130, sy * 190, zb_mm=0), mf, node="ladder_rail")
    for i in range(5):
        m.add(place(slab(30, 380, 30, 2), W / 2.0 - 130, 0, z_mm=up * (i + 1) / 6.0), mf, node="ladder_rung")
    return m.build(dict(w_mm=W, d_mm=L, h_mm=H))


# =====================================================================
# 桌 (3)
# =====================================================================
def desk(w_mm: int = 1200, d_mm: int = 600, h_mm: int = 750, color: Any = None,
         *, body: str = "oak", hutch: bool = False, drawer: bool = True,
         hutch_h_mm: int = 1500, **kw) -> trimesh.Scene:
    """書桌；hutch=True 就是「書桌+吊櫃」（同一模板，差一个参数）。"""
    W, D, H = int(w_mm), int(d_mm), int(h_mm)
    mf = _resolve(body, color)
    ml = material("metal_black")
    m = Model("desk")
    m.add(place(slab(W, D, 30, 4), 0, 0, zb_mm=H - 30), mf, node="top")
    for sy in (-1, 1):
        m.add(place(slab(W - 60, 20, 110, 2), 0, sy * (D / 2.0 - 30), zb_mm=H - 140), mf, node="apron")
    for lm in _legs(W, D, 60, H - 30, 45, ml, taper=0.6):
        m.add(lm[0], lm[1], node="leg")
    if drawer:
        bw = min(420, W * 0.36)
        m.add(place(slab(bw, D - 80, 260, 3), W / 2.0 - bw / 2.0 - 40, 0, zb_mm=H - 30 - 270), mf, node="drawer_box")
        m.add(place(slab(bw - 4, T_DOOR, 250, 2.5), W / 2.0 - bw / 2.0 - 40,
                    D / 2.0 - 40 + T_DOOR / 2.0, zb_mm=H - 30 - 260), mf, node="drawer_0")
        for hm in _handle_at(_bar_handle(min(300, bw - 80), vertical=False),
                             W / 2.0 - bw / 2.0 - 40, D / 2.0 - 40 + T_DOOR + 16, H - 30 - 30):
            m.add(hm, material("metal_steel"), node="handle_d0")
    if hutch:
        zz = int(hutch_h_mm)
        hh = 700
        m.add(place(slab(W, D * 0.55, 25, 3), 0, -D / 2.0 + D * 0.275, zb_mm=zz + hh), mf, node="hutch_top")
        for sx in (-1, 1):
            m.add(place(slab(25, D * 0.55, hh, 3), sx * (W / 2.0 - 12.5), -D / 2.0 + D * 0.275, zb_mm=zz), mf, node="hutch_side")
        for i in range(1, 3):
            m.add(place(slab(W - 50, D * 0.55, 20, 3), 0, -D / 2.0 + D * 0.275, zb_mm=zz + hh * i / 3.0),
                  mf, node="hutch_shelf")
        m.add(place(slab(22, D * 0.55, hh - 30, 3), 0, -D / 2.0 + D * 0.275, zb_mm=zz + 15), mf, node="hutch_divider")
    return m.build(dict(w_mm=W, d_mm=D, h_mm=H, hutch=bool(hutch)))


def dining_table(w_mm: int = 1200, d_mm: int = 750, h_mm: int = 750, color: Any = None,
                 *, body: str = "oak", **kw) -> trimesh.Scene:
    """餐桌（小户型多用 1200x750 四人不撞墙）。"""
    W, D, H = int(w_mm), int(d_mm), int(h_mm)
    mf = _resolve(body, color)
    m = Model("dining_table")
    m.add(place(slab(W, D, 32, 5), 0, 0, zb_mm=H - 32), mf, node="top")
    for sy in (-1, 1):
        m.add(place(slab(W - 120, 25, 90, 2), 0, sy * (D / 2.0 - 60), zb_mm=H - 122), mf, node="apron")
    for sx in (-1, 1):
        m.add(place(slab(25, D - 120, 90, 2), sx * (W / 2.0 - 60), 0, zb_mm=H - 122), mf, node="apron")
    for lm in _legs(W, D, 80, H - 32, 55, mf, taper=0.62, sections=14):
        m.add(lm[0], lm[1], node="leg")
    return m.build(dict(w_mm=W, d_mm=D, h_mm=H))


def side_table(d_mm: int = 450, h_mm: int = 550, color: Any = None, *,
               body: str = "walnut", top_shape: str = "round", legs: int = 3, **kw) -> trimesh.Scene:
    """邊桌（圆几）；把 d_mm 放大到 800、h 降到 400 就是「茶几」。"""
    D, H = int(d_mm), int(h_mm)
    mf = _resolve(body, color)
    m = Model("side_table")
    t = 28
    if top_shape == "round":
        top = cyl(D, t, 40)
    else:
        top = slab(D, int(D * 0.62), t, 8)
    m.add(place(top, 0, 0, zb_mm=H - t), mf, node="top")
    n = 3 if legs == 3 else 4
    for i in range(n):
        a = (90.0 + 360.0 * i / n) * DEG
        r = D / 2.0 - 45
        lg = tapered_cyl(22, 30, H - t, 12)
        place(lg, r * math.cos(a), r * math.sin(a), zb_mm=0)
        m.add(lg, mf, node="leg")
    m.add(place(cyl(D - 90, 18, 20), 0, 0, z_mm=(H - t) * 0.35), mf, node="stretcher")
    return m.build(dict(d_mm=D, h_mm=H))


# =====================================================================
# 座 (4)
# =====================================================================
def chair(w_mm: int = 460, d_mm: int = 520, h_mm: int = 850, color: Any = None,
          *, body: str = "oak", seat_h_mm: int = 450, **kw) -> trimesh.Scene:
    """單椅（木腿 + 竖条靠背），四腿看得见地板。"""
    W, D, H = int(w_mm), int(d_mm), int(h_mm)
    mf = _resolve(body, color)
    m = Model("chair")
    sh = int(seat_h_mm)
    m.add(place(slab(W, D, 30, 5), 0, 0, zb_mm=sh), mf, node="seat")
    for sx in (-1, 1):
        for sy in (-1, 1):
            lg = tapered_cyl(28, 40, sh, 12)
            place(lg, sx * (W / 2.0 - 45), sy * (D / 2.0 - 45), zb_mm=0)
            m.add(lg, mf, node="leg")
    for sx in (-1, 1):
        m.add(place(slab(32, 32, H - sh - 20, 3), sx * (W / 2.0 - 45),
                    -D / 2.0 + 30, zb_mm=sh + 20), mf, node="back_post")
    bh = H - sh - 60
    for i in range(3):
        yy = -D / 2.0 + 30
        m.add(place(slab(W - 60, 22, 45, 2), 0, yy, z_mm=sh + 60 + bh * i / 2.6), mf, node="back_slat")
    for sy in (-1, 1):
        m.add(place(slab(W - 100, 22, 20, 2), 0, sy * (D / 2.0 - 45), zb_mm=sh - 180), mf, node="stretcher")
    return m.build(dict(w_mm=W, d_mm=D, h_mm=H))


def sofa(w_mm: int = 1500, d_mm: int = 850, h_mm: int = 780, seats: int = 2,
         color: Any = None, *, fabric: str = "fabric_grey", legs: bool = True,
         leg_h_mm: int = 110, **kw) -> trimesh.Scene:
    """雙人沙發 / 三人沙發 —— 同模板改 seats。短腿 + 坐垫软包，底缝看得见地板。"""
    W, D, H = int(w_mm), int(d_mm), int(h_mm)
    n = max(1, int(seats))
    mf = _resolve(fabric, color)
    mf2 = material(fabric, color=tuple(max(0, c - 18) for c in _norm_color(mf.baseColorFactor)[:3]))
    mw = material("walnut")
    m = Model("sofa")
    lh = int(leg_h_mm) if legs else 0
    frame_h = 260
    arm_w = 150
    seat_w = W - 2 * arm_w
    for lm in _legs(W, D, 70, lh, 45, mw, taper=0.62):
        m.add(lm[0], lm[1], node="leg")
    m.add(place(slab(seat_w + 40, D, frame_h, 4), 0, 0, zb_mm=lh), mf2, node="frame")
    for sx in (-1, 1):
        m.add(place(soft_box(arm_w, D, 260, 0.5), sx * (W / 2.0 - arm_w / 2.0), 0,
                    zb_mm=lh + frame_h - 20), mf, node="arm")
    cw = seat_w / n
    for i in range(n):
        cx = -seat_w / 2.0 + cw / 2.0 + i * cw
        m.add(place(soft_box(cw - 12, D - 210, 170, 0.46), cx, 40, zb_mm=lh + frame_h - 40),
              mf, node=f"seat_cushion_{i}")
    bh = H - (lh + frame_h) - 30
    for i in range(n):
        cx = -seat_w / 2.0 + cw / 2.0 + i * cw
        m.add(place(soft_box(cw - 12, 180, max(160, bh), 0.45), cx, -D / 2.0 + 100,
                    zb_mm=lh + frame_h + 20), mf, node=f"back_cushion_{i}")
    m.add(place(slab(seat_w + 40, 60, bh + 60, 4), 0, -D / 2.0 + 35, zb_mm=lh + frame_h - 40),
          mf2, node="back_frame")
    return m.build(dict(w_mm=W, d_mm=D, h_mm=H, seats=n))


def chaise_longue(w_mm: int = 1600, d_mm: int = 750, h_mm: int = 760, color: Any = None,
                  *, fabric: str = "fabric_sand", **kw) -> trimesh.Scene:
    """貴妃椅（一头靠背 + 一头扶手，躺平）。"""
    W, D, H = int(w_mm), int(d_mm), int(h_mm)
    mf = _resolve(fabric, color)
    mf2 = material(fabric, color=tuple(max(0, c - 18) for c in _norm_color(mf.baseColorFactor)[:3]))
    m = Model("chaise_longue")
    lh = 110
    for lm in _legs(W, D, 80, lh, 45, material("walnut"), taper=0.62):
        m.add(lm[0], lm[1], node="leg")
    m.add(place(slab(W - 100, D - 40, 240, 4), 0, 0, zb_mm=lh), mf2, node="frame")
    m.add(place(soft_box(W - 140, D - 180, 170, 0.44), 40, 30, zb_mm=lh + 200), mf, node="seat_cushion")
    m.add(place(soft_box(220, D - 180, 300, 0.46), -W / 2.0 + 130, 0, zb_mm=lh + 190), mf, node="back_cushion")
    m.add(place(soft_box(140, D - 60, 220, 0.5), W / 2.0 - 70, 0, zb_mm=lh + 200), mf, node="arm")
    m.add(place(soft_box(300, 130, 240, 0.52), W / 2.0 - 420, -D / 2.0 + 130, zb_mm=lh + 250),
          material("fabric_blue"), node="throw_pillow")
    return m.build(dict(w_mm=W, d_mm=D, h_mm=H))


# =====================================================================
# 收納 (4)
# =====================================================================
def shelf_unit(w_mm: int = 800, d_mm: int = 350, h_mm: int = 1800, shelves: int = 4,
               color: Any = None, *, body: str = "oak", legs: bool = True, **kw) -> trimesh.Scene:
    """層架（开放式，四腿抬高）。"""
    W, D, H = int(w_mm), int(d_mm), int(h_mm)
    mf = _resolve(body, color)
    m = Model("shelf_unit")
    lh = 90 if legs else 0
    t = 25
    for sx in (-1, 1):
        m.add(place(slab(t, D, H - lh, 3), sx * (W / 2.0 - t / 2.0), 0, zb_mm=lh), mf, node="side")
    n = max(1, int(shelves))
    for i in range(n + 1):
        zz = lh + (H - lh - t) * i / n
        m.add(place(slab(W - 2 * t, D, t, 3), 0, 0, zb_mm=zz), mf, node="shelf")
    m.add(place(slab(W - 2 * t, 8, H - lh - 2 * t, 1.0), 0, -D / 2.0 + 4, zb_mm=lh + t), mf, node="back")
    if legs:
        for lm in _legs(W, D, 40, lh, 36, material("metal_black"), taper=0.65):
            m.add(lm[0], lm[1], node="leg")
    return m.build(dict(w_mm=W, d_mm=D, h_mm=H, shelves=n))


def storage_box(w_mm: int = 400, d_mm: int = 350, h_mm: int = 300, color: Any = None,
                *, body: str = "fabric_linen", lid: bool = True, **kw) -> trimesh.Scene:
    """收納箱（布艺/塑料整理箱，带盖缝）。"""
    W, D, H = int(w_mm), int(d_mm), int(h_mm)
    mf = _resolve(body, color)
    m = Model("storage_box")
    body_h = H - (35 if lid else 0)
    m.add(place(soft_box(W, D, body_h, 0.30), 0, 0, zb_mm=0), mf, node="body")
    if lid:
        m.add(place(soft_box(W + 6, D + 6, 40, 0.22), 0, 0, zb_mm=body_h - 6), mf, node="lid")
        for sy in (-1, 1):
            m.add(place(slab(60, 30, 26, 3), 0, sy * (D / 2.0 + 12), z_mm=body_h + 12),
                  material("fabric_linen", color=(180, 174, 162)), node="clip")
    return m.build(dict(w_mm=W, d_mm=D, h_mm=H))


def wall_shelf(w_mm: int = 900, d_mm: int = 250, h_mm: int = 500, tiers: int = 2,
               color: Any = None, *, body: str = "white_oak", **kw) -> trimesh.Scene:
    """掛牆層架（挂墙件：自身 z=0 是底层板底面，布局引擎抬到 1200mm 之类）。"""
    W, D, H = int(w_mm), int(d_mm), int(h_mm)
    mf = _resolve(body, color)
    metal = material("metal_black")
    m = Model("wall_shelf")
    n = max(1, int(tiers))
    base = 130.0                       # 底层板下方要给托架留空间
    if n == 1:
        zs = [base]
    else:
        span = max(60.0, H - 30 - base)
        zs = [base + span * i / (n - 1) for i in range(n)]
    for i, zz in enumerate(zs):
        m.add(place(slab(W, D, 30, 3), 0, 0, zb_mm=zz), mf, node=f"board_{i}")
        for sx in (-1, 1):
            m.add(place(slab(22, D - 30, 130, 2), sx * (W / 2.0 - 60), -12, zb_mm=zz - 130),
                  metal, node="bracket")            # 板下托架（工字托）
        if i < n - 1:                              # 板与板之间的立柱
            up = max(30.0, zs[i + 1] - zz - 30)
            for sx in (-1, 1):
                m.add(place(slab(22, D - 30, up, 2), sx * (W / 2.0 - 60), -12, zb_mm=zz + 30),
                      metal, node="upright")
    return m.build(dict(w_mm=W, d_mm=D, h_mm=H, tiers=n))


def pegboard(w_mm: int = 800, d_mm: int = 18, h_mm: int = 600, color: Any = None,
             *, body: str = "birch", cols: int = 6, rows: int = 5, hooks: int = 6, **kw) -> trimesh.Scene:
    """洞洞板（掛牆；真打孔，用 manifold 布尔）。"""
    W, D, H = int(w_mm), int(d_mm), int(h_mm)
    mf = _resolve(body, color)
    m = Model("pegboard")
    board = slab(W, D, H, 4)
    holes = []
    hole_d = 8
    for i in range(int(cols)):
        for j in range(int(rows)):
            hx = -W / 2.0 + W * (i + 0.5) / cols
            hz = -H / 2.0 + H * (j + 0.5) / rows
            c = cyl(hole_d, D * 3, 10)
            c.apply_transform(trimesh.transformations.rotation_matrix(math.pi / 2, [1, 0, 0]))
            c.apply_translation([hx * MM, 0, hz * MM])
            holes.append(c)
    try:
        board = trimesh.boolean.difference([board] + holes, engine="manifold")
    except Exception:
        pass
    place(board, 0, 0, z_mm=H / 2.0)
    m.add(board, mf, node="board")
    for k in range(int(hooks)):
        hx = -W / 2.0 + W * (k + 0.5) / max(1, hooks)
        bar = cyl(7, 90, 8)
        bar.apply_transform(trimesh.transformations.rotation_matrix(math.pi / 2, [1, 0, 0]))
        place(bar, hx, D / 2.0 + 30, z_mm=H - 110)
        m.add(bar, material("metal_steel"), node=f"hook_{k}")
    return m.build(dict(w_mm=W, d_mm=D, h_mm=H))


# =====================================================================
# 電器 (4)
# =====================================================================
def fridge(w_mm: int = 600, d_mm: int = 650, h_mm: int = 1700, color: Any = None,
           *, body: str = "plastic_white", **kw) -> trimesh.Scene:
    """雪櫃（上下双门 + 竖长把手 + 顶部散热缝）。"""
    W, D, H = int(w_mm), int(d_mm), int(h_mm)
    mf = _resolve(body, color)
    metal = material("metal_steel")
    m = Model("fridge")
    m.add(place(slab(W, D, H, 6), 0, 0, zb_mm=0), mf, node="body")
    split = H * 0.32
    m.add(place(slab(W - 8, 30, split - 4, 3), 0, D / 2.0 + 15, zb_mm=4), mf, node="door_lower")
    m.add(place(slab(W - 8, 30, H - split - 8, 3), 0, D / 2.0 + 15, zb_mm=split + 4), mf, node="door_upper")
    for zz, ln in ((split * 0.55, split * 0.5), (split + (H - split) * 0.5, (H - split) * 0.45)):
        for hm in _handle_at(_bar_handle(int(ln)), W / 2.0 - 75, D / 2.0 + 45, zz):
            m.add(hm, metal, node="handle")
    m.add(place(slab(W - 60, 14, 12, 2), 0, D / 2.0 + 40, z_mm=H - 90),
          material("charcoal"), node="vent")
    m.add(place(slab(W - 80, D - 60, 60, 2), 0, 0, zb_mm=0), material("charcoal"), node="kick")
    return m.build(dict(w_mm=W, d_mm=D, h_mm=H))


def washer(w_mm: int = 600, d_mm: int = 600, h_mm: int = 850, color: Any = None,
           *, body: str = "plastic_white", **kw) -> trimesh.Scene:
    """洗衣機（顶开/前开通用外形：圆舱门 + 控制面板 + 通风格栅）。"""
    W, D, H = int(w_mm), int(d_mm), int(h_mm)
    mf = _resolve(body, color)
    metal = material("metal_chrome")
    m = Model("washer")
    m.add(place(slab(W, D, H, 6), 0, 0, zb_mm=0), mf, node="body")
    # 注意：贴在前脸上的零件必须**整块**在前脸之外（这里前脸 y=D/2）。
    # 跨在前脸上会和平面的三角形抢深度排序，渲染出莫名其妙的黑三角。
    m.add(place(slab(W - 60, 22, 120, 3), 0, D / 2.0 + 12, z_mm=H - 72),
          material("plastic_grey"), node="panel")
    ring = annulus(152, 192, 22, 36)
    ring.apply_transform(trimesh.transformations.rotation_matrix(math.pi / 2, [1, 0, 0]))
    m.add(place(ring, 0, D / 2.0 + 12, z_mm=H * 0.42), metal, node="door_ring")
    glass = cyl(152, 10, 36)                     # 半径 <= 圆环内径 -> 屏幕空间不重叠
    glass.apply_transform(trimesh.transformations.rotation_matrix(math.pi / 2, [1, 0, 0]))
    m.add(place(glass, 0, D / 2.0 + 6, z_mm=H * 0.42), material("glass_dark"), node="door_glass")
    m.add(place(slab(W - 40, 18, 90, 2), 0, D / 2.0 + 10, z_mm=H * 0.11),
          material("plastic_grey"), node="filter")
    knob = cyl(46, 16, 24)
    knob.apply_transform(trimesh.transformations.rotation_matrix(math.pi / 2, [1, 0, 0]))
    m.add(place(knob, W / 2.0 - 90, D / 2.0 + 19, z_mm=H - 72), metal, node="knob")
    return m.build(dict(w_mm=W, d_mm=D, h_mm=H))


def microwave(w_mm: int = 500, d_mm: int = 400, h_mm: int = 300, color: Any = None,
              *, body: str = "metal_black", **kw) -> trimesh.Scene:
    """微波爐（黑玻璃门 + 边框 + 把手）。"""
    W, D, H = int(w_mm), int(d_mm), int(h_mm)
    mf = _resolve(body, color)
    m = Model("microwave")
    m.add(place(slab(W, D, H, 5), 0, 0, zb_mm=0), mf, node="body")
    m.add(place(slab(W - 90, 12, H - 50, 2), -W / 2.0 + (W - 90) / 2.0 + 20, D / 2.0 + 7, z_mm=H / 2.0),
          material("glass_dark"), node="door_glass")
    m.add(place(slab(60, 24, H - 40, 3), W / 2.0 - 40, D / 2.0 + 14, z_mm=H / 2.0),
          material("plastic_grey"), node="panel")
    for hm in _handle_at(_bar_handle(H - 110), W / 2.0 - 118, D / 2.0 + 30, H / 2.0):
        m.add(hm, material("metal_steel"), node="handle")
    m.add(place(slab(W - 40, 14, 10, 2), 0, D / 2.0 + 13, z_mm=H - 18),
          material("metal_steel"), node="trim")
    return m.build(dict(w_mm=W, d_mm=D, h_mm=H))


def air_conditioner(w_mm: int = 850, d_mm: int = 200, h_mm: int = 300, color: Any = None,
                    *, body: str = "plastic_white", **kw) -> trimesh.Scene:
    """分體冷氣室內機（掛牆；z=0 是自身底面，布局引擎抬到 2100mm 梁下）。"""
    W, D, H = int(w_mm), int(d_mm), int(h_mm)
    mf = _resolve(body, color)
    m = Model("air_conditioner")
    m.add(place(slab(W, D, H, 12), 0, 0, zb_mm=0), mf, node="body")
    flap = slab(W - 60, 22, 90, 3)
    flap.apply_transform(trimesh.transformations.rotation_matrix(35 * DEG, [1, 0, 0]))
    m.add(place(flap, 0, D / 2.0 - 20, zb_mm=H - 130), mf, node="flap")
    m.add(place(slab(W - 80, 14, 60, 2), 0, D / 2.0 + 4, zb_mm=H - 110),
          material("plastic_grey"), node="intake")
    m.add(place(slab(70, 10, 18, 2), W / 2.0 - 60, D / 2.0 + 5, z_mm=H - 60),
          material("metal_black"), node="display")
    return m.build(dict(w_mm=W, d_mm=D, h_mm=H))


# =====================================================================
# 其他 (4)
# =====================================================================
def screen_divider(w_mm: int = 1600, d_mm: int = 400, h_mm: int = 1800, panels: int = 3,
                   color: Any = None, *, body: str = "birch", frame: str = "oak", **kw) -> trimesh.Scene:
    """屏風（摺屏）。角度严格遵守 {0,90,180,270} 的约束 —— 相邻板互相垂直，
    形成合法的手风琴锯齿，而不是随手一个 30 度。"""
    PW, PD, H = int(w_mm), int(d_mm), int(h_mm)
    mf = _resolve(body, color)
    mfr = material(frame)
    mfabric = material(body, color=color) if color is not None else material("fabric_linen")
    m = Model("screen_divider")
    n = max(2, int(panels))
    pw = PW / float(n)
    # 相邻板互相垂直（90 度）-> 合法的手风琴锯齿；角度只用 {0,90,180,270}
    xs, ys, yaw = 0.0, 0.0, 0
    for i in range(n):
        dx, dy = math.cos(yaw * DEG), math.sin(yaw * DEG)
        mx, my = xs + dx * pw / 2.0, ys + dy * pw / 2.0
        rot = trimesh.transformations.rotation_matrix(yaw * DEG, [0, 0, 1])
        p = slab(pw - 14, 30, H, 6)
        p.apply_transform(rot)
        place(p, mx, my, zb_mm=0)
        m.add(p, mfabric, node=f"panel_{i}")
        for sz in (0, 1):
            fr = slab(pw - 4, 38, 45, 4)
            fr.apply_transform(rot)
            place(fr, mx, my, zb_mm=0 if sz == 0 else H - 45)
            m.add(fr, mfr, node=f"frame_{i}_{sz}")
        xs, ys = xs + dx * pw, ys + dy * pw
        yaw = (yaw + 90) % 360
    # 把整组平移回原点（占地中心）
    allm = [m.scene.geometry[k] for k in m.parts]
    lo = np.min([b.bounds[0][:2] for b in allm], axis=0)
    hi = np.max([b.bounds[1][:2] for b in allm], axis=0)
    shift = -(lo + hi) / 2.0
    for gm in allm:
        gm.apply_translation([shift[0], shift[1], 0.0])
    return m.build(dict(w_mm=PW, d_mm=PD, h_mm=H, panels=n))


def mirror(w_mm: int = 600, d_mm: int = 60, h_mm: int = 1600, color: Any = None,
           *, body: str = "oak", lean: bool = False, **kw) -> trimesh.Scene:
    """全身鏡（木框 + 镜面）。小户型显大的作弊器。"""
    W, D, H = int(w_mm), int(d_mm), int(h_mm)
    mf = _resolve(body, color)
    m = Model("mirror")
    fr = 40
    m.add(place(slab(W, D, H, 6), 0, 0, zb_mm=0), mf, node="frame")
    glass = slab(W - 2 * fr, 12, H - 2 * fr, 4)
    place(glass, 0, D / 2.0 - 4, z_mm=H / 2.0)
    m.add(glass, material("mirror"), node="glass")
    if lean:
        for sx in (-1, 1):
            m.add(place(slab(50, D + 140, 40, 3), sx * (W / 2.0 - 40), 0, zb_mm=0), mf, node="foot")
    else:
        m.add(place(slab(W - 100, D + 100, 40, 3), 0, 0, zb_mm=0), mf, node="foot")
    return m.build(dict(w_mm=W, d_mm=D, h_mm=H))


def drying_rack(w_mm: int = 1200, d_mm: int = 600, h_mm: int = 1500, color: Any = None,
                *, body: str = "metal_alu", bars: int = 7, wings: int = 2, **kw) -> trimesh.Scene:
    """晾衫架（香港必备：铝合金 + 双侧翻翼 + 底下挂杆）。必须留得住晾衫位。"""
    W, D, H = int(w_mm), int(d_mm), int(h_mm)
    mf = _resolve(body, color)
    m = Model("drying_rack")
    ah = H - 120
    for sx in (-1, 1):
        for sy in (-1, 1):
            lg = cyl(20, ah, 10)
            place(lg, sx * (W / 2.0 - 40), sy * (D / 2.0 - 60), zb_mm=0)
            m.add(lg, mf, node="post")
        m.add(place(slab(24, D - 100, 20, 2), sx * (W / 2.0 - 40), 0, zb_mm=ah), mf, node="beam")
    n = max(2, int(bars))
    for i in range(n):
        yy = -(D / 2.0 - 90) + (D - 180) * i / (n - 1)
        bar = cyl(9, W - 80, 8)
        bar.apply_transform(trimesh.transformations.rotation_matrix(math.pi / 2, [0, 1, 0]))
        place(bar, 0, yy, z_mm=ah + 20)
        m.add(bar, mf, node=f"bar_{i}")
    for k, sx in enumerate((1, -1)):
        if k >= int(wings):
            break
        wing_d = D * 0.55
        for i in range(3):
            yy = (D / 2.0 + 40 + wing_d * (i + 0.5) / 3.0) * sx
            m.add(place(slab(24, 24, 20, 2), sx * (W / 2.0 - 40), yy, zb_mm=ah), mf, node="wing_rail")
            bar = cyl(8, W - 100, 8)
            bar.apply_transform(trimesh.transformations.rotation_matrix(math.pi / 2, [0, 1, 0]))
            place(bar, 0, yy, z_mm=ah + 10)
            m.add(bar, mf, node="wing_bar")
    m.add(place(slab(W - 120, 24, 20, 2), 0, 0, z_mm=400), mf, node="low_rail")
    for i in range(2):
        bar = cyl(8, W - 140, 8)
        bar.apply_transform(trimesh.transformations.rotation_matrix(math.pi / 2, [0, 1, 0]))
        place(bar, 0, -D / 2.0 + 80 + i * 120, z_mm=380)
        m.add(bar, mf, node="low_bar")
    for sx in (-1, 1):
        for sy in (-1, 1):
            m.add(place(cyl(38, 16, 14), sx * (W / 2.0 - 40), sy * (D / 2.0 - 60), zb_mm=0), material("plastic_grey"), node="foot")
    return m.build(dict(w_mm=W, d_mm=D, h_mm=H))


def platform_storage(w_mm: int = 2000, d_mm: int = 1500, h_mm: int = 400, color: Any = None,
                     *, body: str = "birch", seams: int = 3, **kw) -> trimesh.Scene:
    """地台收納（HK 劏房/细单位的终极杀器：把整块地板变成柜）。"""
    W, D, H = int(w_mm), int(d_mm), int(h_mm)
    mf = _resolve(body, color)
    mi = material("charcoal")
    m = Model("platform_storage")
    m.add(place(slab(W, D, H - 20, 3), 0, 0, zb_mm=0), mf, node="base")
    n = max(1, int(seams))
    for i in range(1, n):
        m.add(place(slab(W, 6, H - 20, 1.0), 0, -D / 2.0 + D * i / n, zb_mm=0), mi, node="seam")
    for i in range(1, 3):
        m.add(place(slab(6, D, H - 20, 1.0), -W / 2.0 + W * i / 3.0, 0, zb_mm=0), mi, node="seam")
    for i in range(n):
        cx = -W / 2.0 + W * (i + 0.5) / n
        m.add(place(slab(W / n - 12, D - 12, 20, 2), cx, 0, zb_mm=H - 20), mf, node=f"lid_{i}")
        if n > 1:
            m.add(place(slab(120, 40, 20, 2), cx, D / 2.0 - 40, z_mm=H - 10), mf, node="pull")
    m.add(place(slab(W, 30, 60, 2), 0, D / 2.0 - 15, zb_mm=0), mf, node="trim")
    return m.build(dict(w_mm=W, d_mm=D, h_mm=H))


# =====================================================================
# 模板注册表（catalog.py 是权威清单，这里只是函数名映射）
# =====================================================================
TEMPLATE_FUNCS = {
    "cabinet": cabinet,
    "wardrobe": wardrobe,
    "open_shelf_unit": open_shelf_unit,
    "drawer_chest": drawer_chest,
    "tv_console": tv_console,
    "shoe_cabinet": shoe_cabinet,
    "bed_single": bed_single,
    "bed_double": bed_double,
    "bed_loft": bed_loft,
    "bed_storage": bed_storage,
    "bunk_bed": bunk_bed,
    "desk": desk,
    "dining_table": dining_table,
    "side_table": side_table,
    "chair": chair,
    "sofa": sofa,
    "chaise_longue": chaise_longue,
    "shelf_unit": shelf_unit,
    "storage_box": storage_box,
    "wall_shelf": wall_shelf,
    "pegboard": pegboard,
    "fridge": fridge,
    "washer": washer,
    "microwave": microwave,
    "air_conditioner": air_conditioner,
    "screen_divider": screen_divider,
    "mirror": mirror,
    "drying_rack": drying_rack,
    "platform_storage": platform_storage,
}
