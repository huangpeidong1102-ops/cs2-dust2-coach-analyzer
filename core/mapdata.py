# -*- coding: utf-8 -*-
"""沙二（de_dust2）点位数据与区域命名。

命名规则：取距离最近的地标；若最近地标超过 450 单位，使用方位兜底。
地标坐标由真实 demo 中出生点、下包点和大量死亡位置聚类校准。
"""
import math

# de_dust2 地标：名称 -> (x, y)
# 标定 px=(x+2476)/4.4, py=(3239-y)/4.4（1024 雷达）。
# 四锚点（匪家/警家/A包点/B包点）由真实 demo 出生点与下包事件实测校准；
# 其余点位按标准 callout 图（csbepro / skin.land / win.gg）相对布局标定，
# 并经可行走面掩膜校验不落在墙体内。
LANDMARKS = {
    "匪家": (-760, -807),       # T spawn 实测
    "匪家北口": (-538, -98),
    "匪家东口": (-12, -501),
    "中路匪口": (-496, 71),      # T→mid 出口 / suicide 上
    "A大拐角": (604, 775),       # Long Doors（A大铁门）
    "中路": (-408, 1127),        # Mid 中段
    "B洞口下": (-936, 1545),     # Lower Tunnels 口
    "中门": (-320, 1721),        # Mid Doors（中门双门）
    "中路下": (-430, 599),       # Top Mid / Palm
    "B洞深处": (-1354, 1083),    # Upper Tunnels 深处
    "下B洞": (-936, 1545),       # Lower Tunnels
    "B洞": (-1411, 1312),        # Upper Tunnels（B洞）
    "B洞外": (-1596, 599),       # Outside Tunnels
    "B洞口": (-1266, 2139),      # B Doors（B site 入口）
    "B外北": (-1684, 1127),
    "B外": (-1763, 784),
    "B包点": (-1528, 2539),      # B site 实测
    "警家西侧": (-584, 2315),    # CT→B 口
    "警家中": (-188, 2183),      # CT Mid
    "桥下": (-12, 1479),         # Xbox（mid 右侧箱）
    "A小": (274, 1523),          # Catwalk / A Short
    "警家": (258, 2433),         # CT spawn 实测
    "A大坡": (1132, 1985),       # A Ramp（A大坡）
    "A大": (1189, 1514),         # Long A
    "A大坑": (1418, 511),        # Pit（A大坑）
    "A平台": (1132, 2337),       # A site 平台
    "A大尽头": (428, 247),       # Outside Long
    "A小顶": (560, 2051),        # Short 进 A 口
    "A包点": (1156, 2500),       # A site 实测
}

# 预瞄要道（名称, x, y）
CHOKES = [
    ("A大拐角", 604, 775),
    ("中门", -320, 1721),
    ("B洞外", -1596, 599),
    ("A小", 274, 1523),
    ("A平台", 1132, 2337),
    ("B洞口", -1266, 2139),
]

FALLBACK_RADIUS = 450.0


def _dist(x1, y1, x2, y2):
    return math.hypot(x1 - x2, y1 - y2)


def get_landmarks(map_name, auto_anchors=None):
    """返回 {名称: (x,y)}；自动锚点（出生点/包点实测值）覆盖同名地标。"""
    out = dict(LANDMARKS)
    if auto_anchors:
        for k, v in auto_anchors.items():
            out[k] = (v[0], v[1])
    return out


def get_chokes(map_name):
    return CHOKES


def nearest_landmark(map_name, x, y, auto_anchors=None):
    lm = get_landmarks(map_name, auto_anchors)
    best_name, best_d = None, None
    for name, (lx, ly) in lm.items():
        d = _dist(x, y, lx, ly)
        if best_d is None or d < best_d:
            best_name, best_d = name, d
    if best_d is not None and best_d <= FALLBACK_RADIUS:
        return best_name, best_d
    # 方位兜底
    if auto_anchors and "匪家" in lm and "警家" in lm:
        cx = (lm["匪家"][0] + lm["警家"][0]) / 2
        cy = (lm["匪家"][1] + lm["警家"][1]) / 2
    else:
        cx, cy = 0, 0
    bearing = ("北" if y >= cy else "南") + ("东" if x >= cx else "西")
    return f"地图{bearing}部", best_d if best_d else _dist(x, y, cx, cy)


def landmark_of(map_name, x, y, auto_anchors=None):
    return nearest_landmark(map_name, x, y, auto_anchors)[0]
