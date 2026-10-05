"""packages.templates — 参数化家具模板（hk-space demo）。

两种用法都支持：

    # 1) 脚本方式（推荐，export.py / contact_sheet.py 都这么干）
    import sys; sys.path.insert(0, "packages/templates")
    import catalog as C
    sc = C.build("wardrobe_tall", w_mm=1000, color="white")

    # 2) 包方式（服务层/布局引擎）
    from packages.templates import catalog as C
    sc = C.build("bed_double", w_mm=1372)

坐标：Z 向上，原点=占地矩形中心，+Y=正面；对外长度一律 int mm。
"""

from . import catalog, templates   # noqa: F401

__all__ = ["catalog", "templates"]
