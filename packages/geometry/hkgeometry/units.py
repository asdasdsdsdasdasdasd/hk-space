"""单位与数值精度规范 —— 技术方案 v3.2 §8（冻结，不再讨论）。

铁律：如果任何模块收到的长度类型是 float，就是 bug。
"""
from __future__ import annotations

FT_MM = 304.8
INCH_MM = 25.4

# §8.4 epsilon —— 这三条能消掉 90% 的假阳性「无解」
EPS_AREA_MM2 = 1e-6   # 仅当交面积 > 1e-6 才算真重叠（避免贴边误判）
EPS_DIST_MM = 0.5     # 差距 < 0.5mm 视为满足

# §8.5 安全余量
CLEARANCE_MARGIN_MM = 20   # 加在 clearance 计算上
ROOM_MARGIN_MM = 30        # 房间尺寸扣减（现场与图纸误差）

# §8.6 求解器约定
GRID_MM = 50           # 位置网格吸附 / 候选点集步长
ROT_STEPS = (0, 90, 180, 270)


def ft_in_to_mm(feet: float = 0, inch: float = 0) -> int:
    """「8 呎 6 吋」-> 2591 mm。

    转换只在输入边界做一次，之后全程 int mm。
    """
    return int(round(feet * FT_MM + inch * INCH_MM))


def to_mm(value: float, unit: str) -> int:
    """把图纸上的数值转成整数毫米。unit ∈ {mm, cm, m, ft, inch, ft_in}"""
    u = unit.lower()
    if u == "mm":
        return int(round(value))
    if u == "cm":
        return int(round(value * 10))
    if u == "m":
        return int(round(value * 1000))
    if u in ("ft", "feet", "foot"):
        return int(round(value * FT_MM))
    if u in ("inch", "in"):
        return int(round(value * INCH_MM))
    raise ValueError(f"unknown unit: {unit!r}")


def as_int_mm(x) -> int:
    """断言并转换成长度。float 会被拒绝——这是故意的。"""
    if isinstance(x, bool):
        raise TypeError("bool is not a length")
    if isinstance(x, int):
        return x
    if isinstance(x, float):
        if abs(x - round(x)) < 1e-9:
            return int(round(x))
        raise TypeError(
            f"length must be int mm, got float {x!r}. "
            f"按 §8 规范，核心模块不接受浮点长度——请在输入边界 round() 掉。"
        )
    raise TypeError(f"length must be int mm, got {type(x).__name__}")


def clamp01(v: float) -> float:
    return 0.0 if v < 0 else (1.0 if v > 1 else v)
