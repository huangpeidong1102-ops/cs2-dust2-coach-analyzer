# -*- coding: utf-8 -*-
"""沙二二维墙体/视线近似层。

以官方 dust2 雷达中提取的可行走地面掩膜为底：地面像素可通行，其余像素视为墙体/屋顶/地图外。
它不是 3D 碰撞网格，无法区分上下层和可穿透木箱；因此所有结论都按“雷达级遮挡”输出，
只用于判断准星是否贴墙、要道预瞄是否被墙切断、两人之间有无明显隔墙，不作为穿墙/爆头判定。
"""
import math
import os
from functools import lru_cache

import numpy as np

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
MASK_PATH = os.path.join(BASE_DIR, "dust2_floor.npz")
SCALE = 4.4
POS_X = -2476.0
POS_Y = 3239.0
SIZE = 1024


@lru_cache(maxsize=1)
def _floor_mask():
    return np.load(MASK_PATH)["floor"]


def world_to_px(x, y):
    return (x - POS_X) / SCALE, (POS_Y - y) / SCALE


def px_to_world(px, py):
    return px * SCALE + POS_X, POS_Y - py * SCALE


def is_floor_px(px, py):
    m = _floor_mask()
    ix, iy = int(round(px)), int(round(py))
    if ix < 0 or iy < 0 or ix >= SIZE or iy >= SIZE:
        return False
    return bool(m[iy, ix])


def is_walkable(x, y):
    return is_floor_px(*world_to_px(x, y))


def _blocked_along(px1, py1, px2, py2, ignore_start_px=5, ignore_end_px=5,
                   need_run=2):
    dx, dy = px2 - px1, py2 - py1
    dist = math.hypot(dx, dy)
    if dist < 1e-6:
        return False
    n = max(2, int(dist // 2) + 1)
    run = 0
    for i in range(n + 1):
        d = dist * i / n
        if d < ignore_start_px or dist - d < ignore_end_px:
            continue
        if is_floor_px(px1 + dx * i / n, py1 + dy * i / n):
            run = 0
        else:
            run += 1
            if run >= need_run:
                return True
    return False


def segment_blocked(p1, p2, ignore_m=0.5):
    """p1/p2 为 [x,y,z] 源单位；返回二维雷达线是否被墙切断。"""
    px1, py1 = world_to_px(p1[0], p1[1])
    px2, py2 = world_to_px(p2[0], p2[1])
    return _blocked_along(px1, py1, px2, py2,
                          ignore_start_px=int(ignore_m * 40 / SCALE),
                          ignore_end_px=int(ignore_m * 40 / SCALE))


def ray_wall_hit(x, y, yaw_deg, max_units=2000.0, ignore_units=40.0):
    """从眼睛沿水平 yaw 投射，返回撞到雷达墙体的距离（源单位）；无墙返回 None。"""
    px, py = world_to_px(x, y)
    rad = math.radians(yaw_deg)
    dx, dy = math.cos(rad), -math.sin(rad)   # 雷达图 y 轴向下，世界 y 朝上
    max_px = max_units / SCALE
    step = 2.0
    run = 0
    d = ignore_units / SCALE
    while d <= max_px:
        if is_floor_px(px + dx * d, py + dy * d):
            run = 0
        else:
            run += 1
            if run >= 2:
                return d * SCALE
        d += step
    return None


def sector_visible(x, y, angles, max_units=1800.0):
    """批量返回每个角度的第一道墙距离，供前端视野扇形使用。"""
    return [ray_wall_hit(x, y, a, max_units=max_units) for a in angles]


def nearest_wall_distance(x, y, max_units=300.0):
    """四方向最小墙体距离，用于判断是否把自己塞进死角。"""
    ds = []
    for a in (0, 90, 180, -90, 45, 135, -45, -135):
        d = ray_wall_hit(x, y, a, max_units=max_units, ignore_units=16.0)
        if d is not None:
            ds.append(d)
    return min(ds) if ds else None
