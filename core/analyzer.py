# -*- coding: utf-8 -*-
"""逐回合分析引擎。

输入 parser.parse_demo 的结构化数据 + 目标玩家名，
输出每回合的量化指标、评分、死亡复盘、更优解与自然语言点评。

说明：游戏内 40 单位约合 1 米；敌方角度按“无墙体遮挡”的理论值计算，
烟雾/墙壁会影响真实视野，报告中会结合 thrusmoke 等标记人工提示。
"""
import math
import numpy as np
from . import mapdata, mapvision
from .utility import build_utility_report
from .economy import (summarize_purchases, buy_tier, kill_reward, loss_bonus,
                      round_win_reward, TIER_LABEL, PLANT_PERSONAL, DEFUSE_PERSONAL,
                      PLANT_LOSS_TEAM, CT_KILL_TEAM)

EYE = 64.0                 # 眼睛相对脚底高度（单位）
U2M = 1.0 / 40.0           # 单位 -> 米

# 帧列索引：x,y,z,tick,pitch,yaw,alive,health,weapon
FX, FY, FZ, FT, FPITCH, FYAW, FALIVE, FHP, FWP = range(9)

TRADE_LO, TRADE_HI, EDGE_HI = 48.0, 200.0, 360.0     # 补枪距离分档（单位）
TRADE_WINDOW = 4.0 * 64                              # 补枪窗口（tick）

NON_GUN = {"weapon_knife", "weapon_c4", "weapon_hegrenade", "weapon_smokegrenade",
           "weapon_flashbang", "weapon_molotov", "weapon_incgrenade",
           "weapon_decoy", "weapon_taser"}


def _norm(a):
    return (a + 180.0) % 360.0 - 180.0


def _yaw_to(px, py, tx, ty):
    return math.degrees(math.atan2(ty - py, tx - px))


def _d2(ax, ay, bx, by):
    return math.hypot(ax - bx, ay - by)


def _clamp(v, lo=0, hi=100):
    return max(lo, min(hi, v))


def _is_gun(weapon):
    return bool(weapon) and weapon not in NON_GUN


def _frame_at(frames, tick):
    """取该玩家 <= tick 的最近采样帧。"""
    arr = frames
    i = np.searchsorted(arr[:, FT], tick, side="right") - 1
    return arr[max(0, i)]


def _aim_errors(fr, target):
    """返回 (yaw偏差, 3D总偏差, 水平距离)。target: 目标脚底坐标。"""
    px, py, pz = fr[FX], fr[FY], fr[FZ]
    tx, ty, tz = target
    ex, ey, ez = px, py, pz + EYE
    tx_, ty_, tz_ = tx, ty, tz + EYE
    dx, dy, dz = tx_ - ex, ty_ - ey, tz_ - ez
    yaw_err = abs(_norm(fr[FYAW] - _yaw_to(px, py, tx, ty)))
    horiz = math.hypot(dx, dy)
    dist = math.hypot(horiz, dz)
    # 视角方向（pitch 正=朝下，故 forward z = -sin(pitch)）
    p = math.radians(fr[FPITCH]); y = math.radians(fr[FYAW])
    fwd = np.array([math.cos(p) * math.cos(y), math.cos(p) * math.sin(y), -math.sin(p)])
    tvec = np.array([dx, dy, dz]) / max(dist, 1e-6)
    total = math.degrees(math.acos(_clamp(float(np.dot(fwd, tvec)), -1.0, 1.0)))
    return yaw_err, total, horiz


def analyze(data, player_name):
    map_name = data["map"]
    anchors = data.get("anchors", {})

    def lm(x, y):
        return mapdata.landmark_of(map_name, x, y, anchors)

    chokes = mapdata.get_chokes(map_name)
    out_rounds = []
    tag_counter = {}
    # 经济/比分跨回合状态：连败计数（半场初为 1）、保枪结转、累计比分、经济汇总
    loss_counter = {"T": 1, "CT": 1}
    carried = {}
    run_score = {"T": 0, "CT": 0}
    eco_sum = {"spend": 0, "lost": 0, "kill_reward": 0, "eco_rounds": 0,
               "eco_wins": 0, "half_rounds": 0, "full_rounds": 0, "damage": 0}

    for rd in data["rounds"]:
        # ---- 该回合采样帧与阵营 ----
        frames = {}
        for nm, pts in rd["trajectories"].items():
            frames[nm] = np.array(pts, dtype=object)
        side_map = {r["name"]: r["side"] for r in rd["roster"]}
        if player_name not in side_map:
            continue
        side = side_map[player_name]
        mates = [n for n in side_map if side_map[n] == side and n != player_name]
        enemies = [n for n in side_map if side_map[n] != side]
        live = rd["live_start"]
        end = rd["max_tick"]
        stat_end = rd.get("end_tick") or end   # 真正的回合结束点（round_end 事件）
        tickrate = data["tickrate"]

        def sec(t):
            return (t - live) / tickrate

        my_frames = frames[player_name]
        # 统计只用“回合进行中”的帧：剔除买枪冻结期与回合结束后的走路/庆祝时间。
        in_round = ((my_frames[:, FT] >= live) & (my_frames[:, FT] <= stat_end))
        alive_frames = my_frames[in_round & my_frames[:, FALIVE].astype(bool)]
        gun_frames = alive_frames[np.array([_is_gun(w) for w in alive_frames[:, FWP]])]

        kills = [d for d in rd["deaths"] if d["killer"] == player_name]
        my_death = next((d for d in rd["deaths"] if d["victim"] == player_name), None)
        survived = my_death is None
        damage = rd.get("damage", {}).get(player_name, 0)

        # ---- 1. 选位 / 队友间距 ----
        buckets = {"trade": 0, "edge": 0, "isolated": 0, "stack": 0}
        nearest_dists = []
        for fr in alive_frames:
            t = fr[FT]
            dlist = []
            for m in mates:
                mf = _frame_at(frames[m], t)
                if mf[FALIVE]:
                    dlist.append(_d2(fr[FX], fr[FY], mf[FX], mf[FY]))
            if dlist:
                d = min(dlist)
                nearest_dists.append(d)
                if d < TRADE_LO:
                    buckets["stack"] += 1
                elif d <= TRADE_HI:
                    buckets["trade"] += 1
                elif d <= EDGE_HI:
                    buckets["edge"] += 1
                else:
                    buckets["isolated"] += 1
        n_b = max(sum(buckets.values()), 1)
        trade_ratio = buckets["trade"] / n_b
        iso_ratio = buckets["isolated"] / n_b
        stack_ratio = buckets["stack"] / n_b
        edge_ratio = buckets["edge"] / n_b
        avg_nearest = float(np.mean(nearest_dists)) if nearest_dists else 0.0

        # ---- 2. 准星 ----
        aim_ready = 0          # 近距敌人在准星 5° 内
        head_level = 0         # 无近敌时保持头线
        bad_pitch = 0
        choke_errs = []
        wall_face = 0          # 无接触时准星正前方 2 米内就是墙
        wall_face_checked = 0
        n_aim = max(len(gun_frames), 1)
        for fi, fr in enumerate(gun_frames):
            t = fr[FT]
            enemy_states = []
            for en in enemies:
                ef = _frame_at(frames[en], t)
                if ef[FALIVE]:
                    yaw_err, total, horiz = _aim_errors(fr, (ef[FX], ef[FY], ef[FZ]))
                    enemy_states.append((yaw_err, total, horiz))
            front = [e for e in enemy_states if e[0] < 45 and e[2] < 1200]
            if front and min(e[0] for e in front) < 5:
                aim_ready += 1
            close_front = [e for e in enemy_states if e[0] < 45 and e[2] < 1000]
            if not close_front:
                if abs(fr[FPITCH]) <= 6:
                    head_level += 1
                if fr[FPITCH] > 15 or fr[FPITCH] < -15:
                    bad_pitch += 1
                # 每 4 帧做一次墙体投射，控制解析耗时。
                if fi % 4 == 0:
                    wall_face_checked += 1
                    hit = mapvision.ray_wall_hit(fr[FX], fr[FY], fr[FYAW], max_units=800)
                    if hit is not None and hit < 80:
                        wall_face += 1
            # 要道预瞄：必须和准星方向大致一致，并且中间没有明显墙体切断。
            best_c = None
            for name, cx, cy in chokes:
                d = _d2(fr[FX], fr[FY], cx, cy)
                if d < 2200:
                    ye = abs(_norm(fr[FYAW] - _yaw_to(fr[FX], fr[FY], cx, cy)))
                    if ye < 70 and (best_c is None or ye < best_c):
                        if not mapvision.segment_blocked((fr[FX], fr[FY], fr[FZ]),
                                                         (cx, cy, fr[FZ] + 32), ignore_m=0.3):
                            best_c = ye
            if best_c is not None:
                choke_errs.append(best_c)
        aim_ready_ratio = aim_ready / n_aim
        head_level_ratio = head_level / n_aim
        bad_pitch_ratio = bad_pitch / n_aim
        wall_face_ratio = wall_face / max(wall_face_checked, 1)
        avg_choke = float(np.mean(choke_errs)) if choke_errs else None

        # ---- 3. 补枪 / 配合 ----
        trades_done = 0
        for dth in rd["deaths"]:
            if dth["killer"] == player_name:
                # 此前 4 秒内是否有队友阵亡
                if any(rd["min_tick"] <= d2["tick"] <= dth["tick"] and
                       d2["victim"] in mates for d2 in rd["deaths"]
                       if 0 < dth["tick"] - d2["tick"] <= TRADE_WINDOW):
                    trades_done += 1
        got_traded = False
        if my_death:
            got_traded = any(d["killer"] in mates and d["victim"] in enemies
                             and 0 < d["tick"] - my_death["tick"] <= TRADE_WINDOW
                             for d in rd["deaths"])

        # ---- 4. 死亡复盘 ----
        death_review = None
        death_yaw = None
        isolated_death = False
        if my_death:
            t = my_death["tick"]
            myfr = _frame_at(my_frames, t)
            vp = my_death["victim_pos"]
            if my_death["killer_pos"]:
                death_yaw, _, _ = _aim_errors(myfr, tuple(my_death["killer_pos"]))
            cross_wall = mapvision.ray_wall_hit(myfr[FX], myfr[FY], myfr[FYAW],
                                                max_units=1600)
            los_blocked = (bool(vp and my_death["killer_pos"] and
                                mapvision.segment_blocked(vp, my_death["killer_pos"])))
            nearest_wall = mapvision.nearest_wall_distance(myfr[FX], myfr[FY], max_units=600)
            mates_review, enemies_review = [], []
            for s in my_death["snapshot"]:
                item = {"name": s["name"], "landmark": lm(s["pos"][0], s["pos"][1]),
                        "distance": round(_d2(vp[0], vp[1], s["pos"][0], s["pos"][1]) * U2M, 1),
                        "alive": s["alive"], "health": s["health"]}
                if s["side"] == side and s["name"] != player_name:
                    mates_review.append(item)
                elif s["side"] != side:
                    enemies_review.append(item)
            alive_mates = [m for m in mates_review if m["alive"]]
            nearest_mate = min(alive_mates, key=lambda x: x["distance"], default=None)
            isolated_death = nearest_mate is not None and nearest_mate["distance"] > 9.0
            death_review = {
                "time": round(sec(t), 1),
                "place": lm(vp[0], vp[1]),
                "victim_pos": vp,
                "killer": my_death["killer"],
                "killer_place": lm(my_death["killer_pos"][0], my_death["killer_pos"][1])
                                if my_death["killer_pos"] else None,
                "killer_pos": my_death["killer_pos"],
                "killer_distance": round(my_death["distance"], 1),
                "weapon": my_death["weapon"].replace("weapon_", ""),
                "headshot": my_death["headshot"],
                "thrusmoke": my_death["thrusmoke"],
                "yaw_to_killer": round(death_yaw, 1) if death_yaw is not None else None,
                "teammates": sorted(alive_mates, key=lambda x: x["distance"]),
                "enemies": sorted([e for e in enemies_review if e["alive"]],
                                  key=lambda x: x["distance"]),
                "nearest_mate": nearest_mate,
                "alive_mate_count": len(alive_mates),
                "isolated": isolated_death,
                "got_traded": got_traded,
                "crosshair_wall_m": round(cross_wall / 40.0, 1) if cross_wall else None,
                "nearest_wall_m": round(nearest_wall / 40.0, 1) if nearest_wall else None,
                "los_blocked": los_blocked,
            }
            mate_txt = "；".join(f"{m['name']}在{m['landmark']}（{m['distance']}m）"
                                for m in sorted(alive_mates, key=lambda x: x["distance"])[:4]) or "无存活队友"
            dead_mate_txt = "；".join(f"{m['name']}已阵亡"
                                     for m in mates_review if not m["alive"])
            enemy_txt = "；".join(f"{e['name']}在{e['landmark']}（{e['distance']}m）"
                                 for e in sorted([x for x in enemies_review if x["alive"]],
                                                 key=lambda x: x["distance"])[:4]) or "无存活敌人"
            death_review["position_summary"] = (
                f"你倒在【{death_review['place']}】；队友：{mate_txt}"
                + (f"；{dead_mate_txt}" if dead_mate_txt else "")
                + f"。敌人：{enemy_txt}。"
            )

        # ---- 5. 回合胜负：优先用 round_end 事件的真实结果，事件缺失时才回退推断 ----
        winner = rd.get("winner")
        if winner is None:
            if "explode_tick" in rd["bomb"]:
                winner = "T"
            elif "defuse_tick" in rd["bomb"]:
                winner = "CT"
            else:
                last = rd["deaths"][-1]["snapshot"] if rd["deaths"] else []
                alive_sides = {s["side"] for s in last if s["alive"] and s["name"] != (
                    rd["deaths"][-1]["victim"] if rd["deaths"] else None)}
                if len(alive_sides) == 1:
                    winner = next(iter(alive_sides))
                elif "plant_tick" not in rd["bomb"]:
                    winner = "CT"    # 无炸弹且无法判断时按时间耗尽处理
        won = winner == side

        # ---- 5.5 双方道具与战术 ----
        utility_report = build_utility_report(rd, side_map, live, tickrate, lm)
        own_utils = [u for u in utility_report[side]["events"] if u["user"] == player_name]
        for u in utility_report[side]["events"]:
            u["is_own"] = u["user"] == player_name
        for u in utility_report["CT" if side == "T" else "T"]["events"]:
            u["is_own"] = False
        own_util_damage = sum(u["effect"]["enemy_damage"] for u in own_utils)
        own_flash_enemies = sum(u["effect"]["enemies_blinded"] for u in own_utils)
        own_flash_seconds = sum(u["effect"]["enemy_blind_seconds"] for u in own_utils)
        own_team_flash = sum(u["effect"]["teammates_blinded"] for u in own_utils)
        own_team_damage = sum(u["effect"]["team_damage"] for u in own_utils)
        util_support = min(10, own_flash_enemies * 3 + own_util_damage // 20)

        # ---- 6. 阶段判定与规则标签 ----
        tags = []
        plant_tick = rd["bomb"].get("plant_tick")
        post_plant = bool(plant_tick)

        # ---- 6.5 经济与累计比分 ----
        ridx = rd["index"]
        # 半场/加时半场开始：连败计数重置为 1，装备不结转
        if ridx == 12 or (ridx >= 24 and (ridx - 24) % 3 == 0):
            loss_counter = {"T": 1, "CT": 1}
            carried = {}
        purchases = rd.get("purchases", {})
        buys = {}
        for nm in side_map:
            ps = summarize_purchases(purchases.get(nm, []))
            primary = ps["primary"] or carried.get(nm) or ""
            buys[nm] = (ps, primary, buy_tier(primary, ps["spend"]))
        my_ps, my_primary, my_tier = buys[player_name]
        mate_tiers = [buys[m][2] for m in mates]
        n_full_mates = sum(t == "full" for t in mate_tiers)
        n_eco_mates = sum(t == "eco" for t in mate_tiers)

        win_reward = round_win_reward(won, rd.get("win_reason"))
        kill_rw = sum(kill_reward(k["weapon"]) for k in kills)
        plant_personal = PLANT_PERSONAL if rd["bomb"].get("planter") == player_name else 0
        defuse_personal = DEFUSE_PERSONAL if rd["bomb"].get("defuser") == player_name else 0
        t_plant_loss = PLANT_LOSS_TEAM if (side == "T" and plant_tick and not won) else 0
        ct_kill_team = (CT_KILL_TEAM * sum(1 for d in rd["deaths"]
                        if side_map.get(d["victim"]) == "T")) if side == "CT" else 0
        my_loss_bonus = 0 if won else loss_bonus(loss_counter[side])
        equip_lost = my_ps["spend"] if my_death else 0

        if winner in ("T", "CT"):
            loser = "CT" if winner == "T" else "T"
            loss_counter[loser] = min(4, loss_counter[loser] + 1)
            loss_counter[winner] = max(0, loss_counter[winner] - 1)
            run_score[winner] += 1
        next_loss_if_lose = loss_bonus(loss_counter[side])
        death_names = {d["victim"] for d in rd["deaths"]}
        for nm in side_map:
            carried[nm] = buys[nm][1] if nm not in death_names else None

        eco = {
            "tier": my_tier, "tier_label": TIER_LABEL[my_tier],
            "spend": my_ps["spend"], "armor": my_ps["armor"], "util": my_ps["util"],
            "primary": my_primary,
            "freeze_balance": rd.get("freeze_balance", {}).get(player_name, 0),
            "end_balance": rd.get("end_balance", {}).get(player_name, 0),
            "win_reward": win_reward, "kill_reward": kill_rw,
            "plant_personal": plant_personal, "defuse_personal": defuse_personal,
            "plant_loss_team": t_plant_loss, "ct_kill_team": ct_kill_team,
            "loss_bonus": my_loss_bonus, "next_loss_bonus": next_loss_if_lose,
            "loss_counter": loss_counter[side], "equip_lost": equip_lost,
            "team_full": n_full_mates, "team_eco": n_eco_mates, "mates": len(mates),
            "score_after": dict(run_score),
        }
        eco_advice = []
        if my_tier in ("half", "full") and n_eco_mates >= 3:
            eco_advice.append("队友都在 eco 你却强起/全起，下一回合全队没钱补枪。经济要同步——要么一起起、要么一起省。")
        if my_tier == "eco" and n_full_mates >= 3:
            eco_advice.append("队友全起你却纯经济，正面少一条枪；存款够时应跟起，至少补甲+道具帮队友拉枪线。")
        if my_death and my_tier == "full" and not won:
            eco_advice.append(f"全起阵亡又输回合，估算损失 ${equip_lost} 装备；这种回合更要讲究交换价值或及时保枪。")
        if side == "T" and not won and not plant_tick:
            eco_advice.append("输回合且没下包，少了每人 $600 下包补偿；残局哪怕换不到人，摸到下包也能给下一回合续经济。")
        if my_tier == "eco" and won:
            eco_advice.append("纯经济局偷赢，经济大赚——下一回合可以全起压制对手经济。")
        eco["advice"] = eco_advice

        is_pistol = ridx in (0, 12)  # 手枪局单独归类，不计入 eco 口径
        eco_sum["spend"] += my_ps["spend"]
        eco_sum["lost"] += equip_lost
        eco_sum["kill_reward"] += kill_rw
        eco_sum["damage"] += damage
        eco_sum["eco_rounds"] += (my_tier == "eco" and not is_pistol)
        eco_sum["eco_wins"] += (my_tier == "eco" and won and not is_pistol)
        eco_sum["half_rounds"] += (my_tier == "half" and not is_pistol)
        eco_sum["full_rounds"] += (my_tier == "full" and not is_pistol)
        round_label = "手枪局" if ridx in (0, 12) else ("加时" if ridx >= 24 else "")
        death_phase = None
        if my_death:
            if plant_tick and my_death["tick"] >= plant_tick:
                death_phase = "postplant"
            elif sec(my_death["tick"]) < 15:
                death_phase = "early"
            else:
                death_phase = "mid"
        # 死亡时双方存活数
        if my_death:
            n_mates = len([s for s in my_death["snapshot"]
                           if s["side"] == side and s["alive"] and s["name"] != player_name])
            n_enemies = len([s for s in my_death["snapshot"]
                             if s["side"] != side and s["alive"]])
        else:
            # 存活时用回合结束瞬间的帧统计队友/敌人存活，而不是恒为 0
            n_mates = 0
            for m in mates:
                mf = _frame_at(frames[m], stat_end)
                if mf[FALIVE] and mf[FT] >= stat_end - 32:
                    n_mates += 1
            n_enemies = 0
            for en in enemies:
                ef = _frame_at(frames[en], stat_end)
                if ef[FALIVE] and ef[FT] >= stat_end - 32:
                    n_enemies += 1

        first_death = min(rd["deaths"], key=lambda d: d["tick"], default=None)
        opening_death = bool(my_death and first_death and my_death["tick"] == first_death["tick"])
        opening_kill = bool(first_death and first_death["victim"] in enemies and
                            first_death["killer"] == player_name)
        team_loss_after = []
        team_kill_after = []
        if my_death:
            after_window = 6 * tickrate
            team_loss_after = [d for d in rd["deaths"]
                               if d["victim"] in mates and
                               0 < d["tick"] - my_death["tick"] <= after_window]
            team_kill_after = [d for d in rd["deaths"]
                               if d["killer"] in mates and d["victim"] in enemies and
                               0 < d["tick"] - my_death["tick"] <= after_window]
        plant_fast_after = bool(my_death and plant_tick and
                                0 <= plant_tick - my_death["tick"] <= 12 * tickrate)
        sites = [anchors[k] for k in ("A包点", "B包点") if k in anchors]

        def site_gap(pos):
            return min((_d2(pos[0], pos[1], s[0], s[1]) for s in sites), default=9999)

        death_action_dist = site_gap(my_death["victim_pos"]) if my_death else 9999
        choke_near_death = False
        if my_death:
            choke_near_death = any(_d2(my_death["victim_pos"][0], my_death["victim_pos"][1], cx, cy) < 450
                                   for _, cx, cy in chokes)
        mate_near_site = 0
        if my_death and sites:
            for m in [x for x in my_death["snapshot"] if x["side"] == side and x["name"] != player_name]:
                if m["alive"] and site_gap(m["pos"]) < 1600:
                    mate_near_site += 1
        # 交换价值：同样是 1 换 1，后续队友有没有被打穿决定价值完全不同。
        exchange = {"label": "无", "value": 0, "reason": "", "later": ""}
        if my_death:
            if n_mates == 0:
                later_text = "你倒下时队友已全部阵亡，已经进入残局，不能再按常规 1 换 1 评估。"
            elif team_loss_after and team_kill_after:
                later_text = f"你死后 6 秒内队友 {len(team_loss_after)} 死 {len(team_kill_after)} 换，交换链没有继续扩大收益。"
            elif team_loss_after:
                later_text = f"你死后 6 秒内队友又倒下 {len(team_loss_after)} 人，点位被连锁打穿。"
            elif team_kill_after:
                later_text = f"你死后 6 秒内队友反打 {len(team_kill_after)} 人，交换仍然延续。"
            else:
                later_text = "你死后队友暂时稳住，没有再被连续换掉。"

            if side == "CT":
                if n_mates == 0:
                    exchange = {"label": "残局阵亡", "value": 0,
                                "reason": "队友已全部阵亡，你是在残局里尝试；拿到人头值得肯定，但已经没有“后续队友”可以保护。",
                                "later": later_text}
                elif kills and got_traded:
                    exchange = {"label": "一换二未成立的防守交换",
                                "value": -4 if team_loss_after else 2,
                                "reason": "你先拿到击杀，但自己也被补掉，随后队友又有人倒下。",
                                "later": later_text}
                elif kills and not team_loss_after and n_mates >= 2:
                    exchange = {"label": "有效一换一（后续防线稳）",
                                "value": 8 if not plant_fast_after else 3,
                                "reason": "你用一条命换掉进攻方一人；更关键的是后续至少还有两名队友在结构里，防线没有被连锁打穿。",
                                "later": later_text}
                elif kills and not team_loss_after and n_mates == 1:
                    exchange = {"label": "换命后仅剩单人防线（价值有限）",
                                "value": 2,
                                "reason": "你换掉一人，但倒下的那一刻队里只剩一名队友；这个一换一没有保住防线厚度，不能让队友变成孤点。",
                                "later": later_text}
                elif kills and (team_loss_after or plant_fast_after):
                    exchange = {"label": "低价值一换一（后续被打穿）",
                                "value": -8,
                                "reason": "数字上是 1 换 1，但你死后队友接连掉点/包点很快失守，这一换没有给队伍换来时间和结构。",
                                "later": later_text}
                elif got_traded and not team_loss_after and n_mates >= 2:
                    exchange = {"label": "可交换锚点（队友补枪）",
                                "value": 5,
                                "reason": "你没有拿到击杀，但选位可被队友立刻补枪，队友完成交换且没有继续崩盘。",
                                "later": later_text}
                elif got_traded and not team_loss_after and n_mates == 1:
                    exchange = {"label": "单人防线补枪（价值有限）",
                                "value": 2,
                                "reason": "队友补掉了杀你的人，但队里只剩一名防守者；这次交换只止血，没有恢复防线。",
                                "later": later_text}
                elif not n_mates:
                    exchange = {"label": "残局阵亡", "value": 0,
                                "reason": "队友已全部阵亡，你是在残局里尝试，不应按普通脱节阵亡重罚。",
                                "later": later_text}
                elif plant_fast_after:
                    exchange = {"label": "掉点后包点失守", "value": -10,
                                "reason": "你阵亡后 12 秒内对方完成下包，你的位置没有换来足够回防时间。",
                                "later": later_text}
                elif team_kill_after and not team_loss_after:
                    exchange = {"label": "延迟反杀（交换部分成立）", "value": 0,
                                "reason": "你没换到人，队友也没能立刻补掉杀你的人，但在 6 秒内反杀了其他敌人；人数没有继续恶化，只算打平。",
                                "later": later_text}
                else:
                    exchange = {"label": "普通阵亡", "value": -4,
                                "reason": "没有形成交换，队友也没有立刻补到你的位置。",
                                "later": later_text}
            elif side == "T":
                if opening_death and (kills or got_traded):
                    exchange = {"label": "换命首死（有后续收益）",
                                "value": 6 if (plant_fast_after or mate_near_site) else 2,
                                "reason": "你作为第一个接触的人换掉了防守方，队伍没有因为首死直接断节奏。",
                                "later": later_text}
                elif opening_death and (plant_fast_after or team_kill_after):
                    exchange = {"label": "抢点型首死（有价值）",
                                "value": 9,
                                "reason": "首死不是白给：你死在进点/要道，吸住了防守枪线，队伍在你倒地后拿到击杀或快速下包，换来了空间。",
                                "later": later_text}
                elif opening_death and mate_near_site and choke_near_death:
                    exchange = {"label": "抢点型首死（价值有限）",
                                "value": 2,
                                "reason": "你死在要点附近且队友已经压上，但队伍没有立刻换到人头或下包，只能算用身体换了压力。",
                                "later": later_text}
                elif opening_death:
                    exchange = {"label": "无效首死", "value": -11,
                                "reason": "你是本回合第一个死的人，没有换到人头、没有队友立刻补枪，也没有换来下包或站点控制。",
                                "later": later_text}
                elif got_traded:
                    exchange = {"label": "可交换阵亡", "value": 4,
                                "reason": "你先死但队友在同一交火窗口完成补枪，交换成立。",
                                "later": later_text}
                elif team_loss_after:
                    exchange = {"label": "连续掉人", "value": -8,
                                "reason": "你的阵亡和后续队友阵亡连在一起，队伍在短时间内少人。",
                                "later": later_text}
                elif team_kill_after:
                    exchange = {"label": "延迟反杀（交换部分成立）", "value": 1,
                                "reason": "你没有立刻被补枪，但队友在 6 秒内反杀了敌人，人数没有继续恶化。",
                                "later": later_text}
                elif not n_mates:
                    exchange = {"label": "残局阵亡", "value": 0,
                                "reason": "队友已全部倒下，这是被迫的残局尝试，不按普通脱节阵亡重罚。",
                                "later": later_text}
                else:
                    exchange = {"label": "普通阵亡", "value": -3,
                                "reason": "没有形成交换，但队友还有后续结构。",
                                "later": later_text}
        exchange_value = exchange["value"]
        if exchange_value >= 6:
            tags.append("高价值阵亡")
        elif exchange_value <= -8:
            tags.append("低价值阵亡")
        elif exchange["label"] == "残局阵亡":
            tags.append("残局阵亡")

        # 存活价值：不能只因为活着就加分，要看残局结构和包点压力。
        survival_ctx = {"label": "", "value": 0, "reason": ""}
        if survived:
            if side == "T" and plant_tick:
                survival_ctx = {"label": "下包后存活", "value": 6,
                                "reason": "包已安放，你活着能继续守包或接应，队伍保留了人数优势。"}
            elif side == "CT" and plant_tick:
                far = 999
                if rd["bomb"].get("plant_pos") and len(my_frames):
                    end_fr = _frame_at(my_frames, stat_end)   # 回合结束瞬间的位置，不能用下回合出生点
                    far = _d2(end_fr[FX], end_fr[FY],
                              rd["bomb"]["plant_pos"][0], rd["bomb"]["plant_pos"][1]) * U2M
                if far > 12 and not won:
                    survival_ctx = {"label": "包点失守后保枪", "value": -6,
                                    "reason": f"包点已被安放且回合告负，你存活但离 C4 约 {round(far)} 米，没有形成回防压力；若队伍当时还有人能拆，这属于过度保守。"}
                else:
                    survival_ctx = {"label": "守包存活", "value": 5,
                                    "reason": "包点被安放后你仍在有效回防/守包距离内，存活有实际价值。"}
            elif side == "T" and not won and n_mates == 0:
                survival_ctx = {"label": "理智保枪", "value": 5,
                                "reason": "队友已全部阵亡且没有下包，你活着把枪和甲留到下一回合，比强行送掉更合理。"}
            elif side == "CT" and won:
                survival_ctx = {"label": "赢下回合并存活", "value": 6,
                                "reason": "回合获胜且保住枪械，经济收益最大化。"}
            elif side == "T" and not plant_tick and not won:
                survival_ctx = {"label": "无压下包失败后保枪", "value": 2,
                                "reason": "没下包且回合已无法挽回，保枪是合理的止损。"}
            else:
                survival_ctx = {"label": "普通存活", "value": 2,
                                "reason": "存活本身不是错误，但没有额外战术收益。"}

        wall_evidence = {
            "facewall_pct": round(wall_face_ratio * 100),
            "death_crosshair_wall_m": death_review.get("crosshair_wall_m") if death_review else None,
            "kill_line_blocked": bool(my_death and death_review and death_review.get("los_blocked")),
            "note": "基于沙二雷达可行走面的二维遮挡近似，不能区分上下层/可穿透物。",
        }

        # ---- 7. 评分 ----
        # 分散站位本身不等于错误；只有接触/阵亡时无人可补才重罚。
        spacing_q = (trade_ratio * 1.0 + edge_ratio * 0.78 +
                     iso_ratio * 0.50 + stack_ratio * 0.35)
        isolated_penalty = 12 if (isolated_death and exchange_value < 3) else 3
        pos_score = _clamp(45 + 45 * spacing_q - isolated_penalty +
                           (5 if exchange_value >= 6 else 0))
        team_score = _clamp(55 + min(trades_done, 2) * 12 +
                            (15 if got_traded else 0) +
                            (8 if trade_ratio > 0.7 else 0) + util_support +
                            exchange_value * 0.8 -
                            (15 if isolated_death and exchange_value < 3 else 0))
        dec_score = (58 + (12 if won else 0) + (6 if len(kills) >= 2 else 0) +
                     (7 if opening_kill else 0) + exchange_value * 0.9 + survival_ctx["value"])
        if my_death:
            if (death_phase == "postplant" and n_mates + 1 >= n_enemies
                    and exchange_value < 3 and (death_yaw is None or death_yaw < 45)):
                dec_score -= 18; tags.append("优势期硬送")
            if death_phase == "postplant" and side == "T" and n_mates >= 1 and exchange_value >= 6:
                dec_score += 3
        dec_score = _clamp(dec_score)
        cross_score = 40 + 35 * aim_ready_ratio + 20 * head_level_ratio - \
            10 * bad_pitch_ratio - (10 if wall_face_ratio > 0.4 else 0)
        if avg_choke is not None and avg_choke < 8:
            cross_score += 10
        if death_yaw is not None and death_yaw > 60:
            cross_score -= 12; tags.append("被偷袭")
        cross_score = _clamp(cross_score)
        total_score = _clamp(pos_score * 0.28 + team_score * 0.27 +
                             dec_score * 0.25 + cross_score * 0.20)

        # ---- 8. 自然语言：亮点 / 问题 / 更优解 ----
        good, issues, better = [], [], []

        if trade_ratio > 0.6:
            good.append(f"全队距离保持得好：你 {round(trade_ratio*100)}% 的存活时间与最近队友在 1.2–5 米补枪距离内（平均 {round(avg_nearest*U2M,1)} 米），阵亡有人能补、吃雷不会一起吃。")
        if exchange_value >= 6:
            good.append(f"教练判定：{exchange['label']}。{exchange['reason']}{exchange['later']}")
        if opening_kill:
            if my_death and exchange_value < 3:
                good.append(f"你拿下本回合首杀，先把局面打成 5v4；但随后自己的阵亡没有保住这个优势，首杀的价值打了折扣（见教练判词）。")
            else:
                good.append(f"你拿下本回合首杀，直接把局面打成 5v4；首杀方向来自【{lm(first_death['killer_pos'][0], first_death['killer_pos'][1]) if first_death.get('killer_pos') else '未知位置'}】，这是打开局面的关键动作。")
        if trades_done:
            good.append(f"成功补枪 {trades_done} 次：队友掉点后你在 4 秒内顶住并击杀敌人，没有让对方形成多打少。")
        if got_traded and exchange_value >= 3:
            good.append("你虽然先阵亡，但队友在补枪窗口内完成了交换，说明你的站位是可被补枪的，而不是孤立白给。")
        if len(kills) >= 2:
            hs = sum(1 for k in kills if k["headshot"])
            good.append(f"本回合拿下 {len(kills)} 个击杀（其中爆头 {hs} 个），对枪胜率高，主动找人的时机正确。")
        if aim_ready_ratio > 0.5:
            good.append(f"交火时准星已经在敌人身上：近距接敌 {round(aim_ready_ratio*100)}% 的帧准星偏差小于 5°，几乎不用临时甩枪。")
        if head_level_ratio > 0.75:
            good.append(f"头线保持稳定：无接触时 {round(head_level_ratio*100)}% 的时间准星停在头线高度（俯仰角 ≤6°），遇到人就是爆头线。")
        if avg_choke is not None and avg_choke < 8:
            good.append(f"拐角预瞄到位：经过要道时准星平均只偏离拐角 {round(avg_choke,1)}°，架的是敌人会出现的位置。")
        if survived and survival_ctx["value"] >= 2:
            good.append(f"存活判断：{survival_ctx['label']}。{survival_ctx['reason']}")
        elif survived and survival_ctx["value"] < 0:
            issues.append(f"存活判断：{survival_ctx['reason']}")
        if own_flash_enemies:
            good.append(f"你的闪光实际白到 {own_flash_enemies} 名敌人（合计 {round(own_flash_seconds,1)} 秒），不是无效乱扔，为队伍创造了对枪窗口。")
        if own_util_damage >= 30:
            good.append(f"你的手雷/燃烧弹造成 {own_util_damage} 点有效伤害，削弱了敌方血线，道具转化成了实际收益。")
        if side == "T" and "集中爆弹" in utility_report["T"]["tactic"] and won:
            good.append(f"进攻道具衔接有效：本回合呈现【{utility_report['T']['tactic']}】，多人烟闪/烟火组合帮助团队拿下回合。")
        if side == "CT" and "defuse_tick" in rd["bomb"] and any(u["tick"] >= plant_tick for u in utility_report["CT"]["events"] if plant_tick):
            good.append("回防道具使用有效：下包后队伍继续用烟闪清点并贴近C4，最终完成拆包。")
        if side == "CT" and plant_tick and not won and any(u["tick"] >= plant_tick for u in utility_report["CT"]["events"]):
            issues.append("团队回防已经投入道具，但没能转化为拆包或守包击杀，回防节奏仍然失败。")
            better.append("下包后的回防不要边赶路边散扔：先等队友到位，用闪清近点、烟封交叉枪线，再一起贴近C4；没有拆包烟时更要保留人手掩护。")
        if side == "T" and "集中爆弹" in utility_report["T"]["tactic"] and "并完成下包" not in utility_report["T"]["tactic"]:
            issues.append("团队完成了一轮集中爆弹，但没有完成下包，道具投入与进点动作脱节。")
            better.append("爆弹要提前约好人、点、顺序：烟落位后第一突破立刻进点，第二人只盯补枪，第三人带包跟进；不要等闪光效果结束才启动。")
        if my_death and exchange_value <= -8:
            issues.append(f"教练判定：{exchange['label']}。{exchange['reason']}{exchange['later']}")
            if side == "CT":
                better.append(f"更优解不是无条件接受一换一：如果你在【{death_review['place']}】换掉一人后已经无法保证队友继续守点，就应先用掩体拖时间或直接退到【{death_review['nearest_mate']['landmark'] if death_review.get('nearest_mate') else '队友所在区域'}】让队友接手，不要把自己变成第二个免费人头。")
            else:
                better.append("首死要死得有价值：必须有人同步补枪、有人带包跟进或能立刻下包；如果队友还在后点、没有任何道具掩护，这个位置就不该第一个接触。真正抢空间的首死要有队友马上接上，而不是五秒后还在赶路。")
        if my_death and exchange_value == 0 and exchange["label"] == "残局阵亡":
            if not kills:
                issues.append("你在残局里没有换到人头就倒下；不是致命错误，但可以考虑用掩体和时间逼对面先动，而不是直接接受对枪。")
            else:
                good.append("残局里你仍然换到了人头，属于合理的高风险尝试，不应简单按“阵亡”扣分。")
        if my_death and death_review["alive_mate_count"] == 0 and not kills:
            issues.append(f"你在【{death_review['place']}】是最后一个倒下的；对面还有 {len(death_review['enemies'])} 人，残局信息不足时可以更早收缩到掩体拖时间。")
        elif isolated_death and exchange_value <= -3:
            tags.append("孤立阵亡")
            nm_ = death_review["nearest_mate"]
            issues.append(f"你在【{death_review['place']}】阵亡时，最近的队友 {nm_['name']} 在【{nm_['landmark']}】，距离 {nm_['distance']} 米，来不及补枪；这不是“死得壮烈”，而是站位没有换来后续结构。")
            better.append(f"具体改法：这回合应从【{death_review['place']}】退向【{nm_['landmark']}】，与 {nm_['name']} 保持约 2–5 米，形成一个先接触、一个补枪的角度；双方到位后再一起 peek。")
        if stack_ratio > 0.25:
            issues.append(f"{round(stack_ratio*100)}% 的时间和队友贴得太近（<1.2 米），容易被一颗雷炸两个，也无法形成交叉火力。")
            better.append("更优解：和队友拉开 2–4 米，一个人先架枪、另一个人补，形成交叉火力而不是重叠站位。")
        if death_yaw is not None and death_yaw > 60:
            issues.append(f"打死你的【{death_review['killer']}】出现在你准星 {death_review['yaw_to_killer']}° 之外，你根本没看这个方向，属于被偷袭。")
            better.append(f"更优解：这个角度没人帮你看——要么请队友覆盖【{death_review['killer_place']}】方向，要么自己先清掉这个点再推进，不要把侧身留给敌人。")
        if death_review and death_review["thrusmoke"]:
            issues.append("你是在烟雾中被击杀的，烟后视野全失还要硬架。")
            better.append("更优解：烟雾升起后换一个有掩体的位置等烟散，或配合闪光再穿，不要在烟后原地不动。")
        if "优势期硬送" in tags:
            issues.append("下包后人数均势/优势，你却主动对枪阵亡，把好局送了回去。")
            better.append("更优解：下包后有优势就该存活拖时间，利用掩体让对面来拆，等对方露破绽再打，而不是主动找人。")
        if wall_face_ratio > 0.4:
            issues.append(f"墙体解析：{round(wall_face_ratio*100)}% 的无接触移动时间里，准星正前方 2 米内就是墙。贴墙瞄一条“墙缝”会让准星吃满遮挡，转角后需要临时修正。")
            better.append("更优解：不要把准星压在近墙上；架直角位留 0.5–1 个身位给敌人拉出的距离，远距离架点则贴近墙角边缘。准星可以穿墙“预瞄”，但视线焦点要放在墙后的枪位。")
        elif avg_choke is not None and avg_choke < 8:
            good.append("墙体解析：你的预瞄方向确实落在要道开口上，视线没有被近墙吃掉，属于有效架枪而不是对着墙发呆。")
        if bad_pitch_ratio > 0.2:
            issues.append(f"{round(bad_pitch_ratio*100)}% 的时间准星明显看地或看天（俯仰超过 15°），遇到人需要先拉回头线。")
            better.append("更优解：移动和清角时把准星抬到头线高度，让视角保持水平，把调整时间留给水平方向的瞄准。")
        if own_team_flash:
            issues.append(f"你的闪光影响了 {own_team_flash} 人次队友，会短暂破坏己方枪线和补枪。")
            better.append("更优解：丢闪前报点和报方向，采用队友背身后再爆的闪光，别让己方一起吃白。")
        if own_team_damage:
            issues.append(f"你的雷火误伤队友 {own_team_damage} 点，抵消了道具收益。")
            better.append("更优解：确认队友推进路线后再扔雷火，燃烧弹优先封敌人路线，不要落在队友脚下。")
        if my_death and side == "T" and sec(my_death["tick"]) < 25 and not kills:
            support = [u for u in utility_report["T"]["events"]
                       if 0 <= my_death["tick"] - u["tick"] <= 4 * tickrate
                       and u["type"] in {"flash", "smoke", "fire"}]
            if not support:
                issues.append("你作为进攻方在首次接触前 4 秒内没有任何烟、闪或火的掩护，属于高风险干拉。")
                better.append("更优解：先等队伍的闪光爆开或烟雾落位，再同步 peek；没有道具时也应让队友先架住交叉枪线。")
        if my_death and my_death.get("assistedflash"):
            issues.append("你阵亡时受到敌方闪光辅助影响，致盲期间无法有效还击。")
            better.append("更优解：听到闪光出手先背身或退回掩体，等致盲结束再反打，不要在全白时原地站桩。")
        if death_review and death_review["headshot"] and death_yaw is not None and death_yaw < 15:
            issues.append("你对枪时准星方向是对的（偏差很小），但被对方爆头，这是枪法/反应问题而非选位问题。")
            better.append("更优解：选位无需调整，建议针对性练急停和点头线，对枪时先站稳再开火。")
        if my_death:
            issues.append("死亡位置全景：" + death_review["position_summary"])
            has_pos_advice = any(("退向" in x) or ("先让" in x) or ("先缩" in x) or ("首死要死" in x)
                                 for x in better)
            if not has_pos_advice:
                mate = death_review.get("nearest_mate")
                if mate:
                    better.append(f"具体更优解：在【{death_review['place']}】接【{death_review.get('killer_place') or '未知方向'}】之前，先让 {mate['name']} 在【{mate['landmark']}】架住补枪角度，你只负责第一枪或只露半身；如果队友还在 {mate['distance']} 米外，就应先缩回掩体等同步。")
                else:
                    better.append("具体更优解：这回合你已进入残局，优先用地形和声音逼对手先露，不要在没信息时主动拉大身位。")

        # 保证好回合至少有证据、差回合至少有可执行建议
        if won and len(good) < 2:
            good.append(f"团队获胜；你本回合贡献 {len(kills)} 个击杀、{damage} 点伤害，没有出现影响局势的重大决策失误。")
        if not good and not issues:
            good.append("本回合表现中规中矩，没有明显失误。")

        # ---- 9. 时间线 ----
        timeline = []
        for k in kills:
            timeline.append({"t": round(sec(k["tick"]), 1),
                             "event": f"击杀 {k['victim']}", "type": "kill"})
        if my_death:
            timeline.append({"t": round(sec(my_death["tick"]), 1),
                             "event": f"被 {my_death['killer']} 击杀", "type": "death"})
        if plant_tick:
            timeline.append({"t": round(sec(plant_tick), 1),
                             "event": f"炸弹安放({rd['bomb'].get('site')}区)", "type": "plant"})
        if "defuse_tick" in rd["bomb"]:
            timeline.append({"t": round(sec(rd["bomb"]["defuse_tick"]), 1),
                             "event": "炸弹被拆除", "type": "defuse"})
        if "explode_tick" in rd["bomb"]:
            timeline.append({"t": round(sec(rd["bomb"]["explode_tick"]), 1),
                             "event": "炸弹爆炸", "type": "explode"})
        for u in own_utils:
            timeline.append({"t": u["time"],
                             "event": f"{u['name']}爆开于{u['det_landmark']}", "type": "utility"})
        if rd.get("win_reason") or winner:
            timeline.append({"t": round(sec(stat_end), 1),
                             "event": f"回合结束：{winner or '?'} 胜"
                                      + (f"（{rd['win_reason']}）" if rd.get("win_reason") else "")
                                      + f" · 比分 {run_score['T']}:{run_score['CT']}",
                             "type": "roundend"})
        timeline.sort(key=lambda x: x["t"])

        # ---- 9.5 回合叙事：把回合讲成一段因果故事（教练解说口径） ----
        story = []
        story.append("防守方开局。" if side == "CT" else "进攻方开局。")
        if own_utils:
            u0 = min(own_utils, key=lambda u: u["time"])
            story.append(f"第 {u0['time']} 秒你在{u0['det_landmark']}交出{u0['name']}，为队伍争取第一步空间。")
        contacts = [(k["tick"], "kill") for k in kills]
        if my_death:
            contacts.append((my_death["tick"], "death"))
        contacts.sort(key=lambda c: c[0])
        if contacts:
            t0, kind = contacts[0]
            if kind == "kill":
                story.append(f"第 {round(sec(t0), 1)} 秒你先手换掉一名敌人，拿到开局主动。")
            else:
                story.append(f"第 {round(sec(t0), 1)} 秒你第一个倒下，队伍开局陷入人数劣势。")
        if plant_tick:
            story.append(f"第 {round(sec(plant_tick), 1)} 秒炸弹安放在{rd['bomb'].get('site')}区，回合进入守包/回防阶段。")
        if my_death and contacts and contacts[0][1] == "kill":
            story.append(f"但第 {round(sec(my_death['tick']), 1)} 秒你被敌人补掉，交换没有继续扩大。")
        elif my_death and not contacts:
            story.append(f"第 {round(sec(my_death['tick']), 1)} 秒你阵亡。")
        if "defuse_tick" in rd["bomb"]:
            story.append("炸弹被拆除，进攻方节奏被打断。")
        elif "explode_tick" in rd["bomb"]:
            story.append("炸弹引爆，进攻方收下回合。")
        story.append(f"最终{'拿下' if won else '丢掉'}这一回合"
                     + (f"（{rd['win_reason']}）" if rd.get("win_reason") else "")
                     + f"；你本回合贡献 {len(kills)} 杀、{damage} 伤害。")
        story = "".join(story)

        if total_score >= 80:
            verdict = "优秀"
        elif total_score >= 65:
            verdict = "良好"
        elif total_score >= 50:
            verdict = "一般"
        else:
            verdict = "失误"

        for tg in tags:
            tag_counter[tg] = tag_counter.get(tg, 0) + 1

        coach_headline = exchange["label"] if my_death else survival_ctx["label"]
        if my_death and opening_kill and exchange_value < 3:
            coach_headline = "首杀没有换来结构"
        elif opening_kill and (not my_death or exchange_value >= 3 or won):
            coach_headline = "首杀打开局面"
        if won and coach_headline in ("普通存活", "无压下包失败后保枪"):
            coach_headline = "赢下回合且存活"
        if not my_death:
            coach_phase = "完整回合"
        elif n_mates == 0:
            coach_phase = "残局"
        elif plant_tick and my_death["tick"] >= plant_tick:
            coach_phase = "下包后"
        elif sec(my_death["tick"]) < 15:
            coach_phase = "开局"
        else:
            coach_phase = "中盘"
        coach_note = exchange["reason"] + exchange["later"] if my_death else survival_ctx["reason"]

        # ---- 选位 / 道具 一句话建议 ----
        position_advice = []
        nm_ = death_review.get("nearest_mate") if death_review else None
        if my_death and nm_ and nm_["distance"] > 15:
            position_advice.append(
                f"你倒在【{death_review['place']}】时，最近队友在 {nm_['distance']} 米外的【{nm_['landmark']}】，"
                f"这是个没人能补枪的孤点。下一回合要么提前约好同步 peek，要么改站【{nm_['landmark']}】附近 2–5 米的交叉位。")
        if my_death and death_yaw is not None and death_yaw > 60:
            position_advice.append(
                f"【{death_review['place']}】会被【{death_review.get('killer_place') or '侧翼'}】方向偷袭。"
                "站这里要么预瞄来敌口子，要么请队友帮你看侧翼，不要把背身留给没人清的方向。")
        if wall_face_ratio > 0.4:
            position_advice.append(
                "架枪时准星离墙太近，敌人拉出来你得先修准星。直角位留半个到一个身位，让敌人自己撞进你的准星。")
        if stack_ratio > 0.25:
            position_advice.append("和队友拉开 2–4 米做交叉火力，别叠在一个点——一颗雷炸两个，也互相挡枪线。")
        if not position_advice:
            if won and survived:
                position_advice.append(
                    f"本回合【{lm(*_frame_at(my_frames, stat_end)[:2])}】附近的站位换来了胜利和存活，"
                    "记住这个点和队友的相对位置，下一回合可以复用。")
            else:
                position_advice.append(
                    "选位三问：能不能看见要道？身后有没有掩体？队友 15 米内能不能补枪？至少占两条再站。")

        utility_advice = []
        own_flashes = [u for u in own_utils if u["type"] == "flash"]
        if my_death and not own_utils:
            utility_advice.append(
                "整回合你没交一件道具就倒下了。烟封枪线、闪抢对枪、火清死角——进点或回防前，先想好用哪件道具替自己挡第一枪。")
        if (my_death and side == "T" and sec(my_death["tick"]) < 25 and not kills
                and not any(0 <= my_death["tick"] - u["tick"] <= 4 * tickrate
                            for u in utility_report["T"]["events"]
                            if u["type"] in {"flash", "smoke", "fire"})):
            utility_advice.append(
                "突破前先给一颗常规闪（A 大蓝箱闪、中路 Xbox 闪、B 洞出口闪任选），等闪爆开再拉，不要干拉第一枪位。")
        if my_death and my_death.get("assistedflash"):
            utility_advice.append(
                "你被敌方闪光辅助击杀。听到闪出手先背身或缩回掩体，等白结束再反打；关键枪位可以提前备一颗反闪。")
        if (my_death and side == "CT" and sec(my_death["tick"]) < 25
                and not any(u["type"] in {"smoke", "fire", "he"} and u["time"] <= 25 for u in own_utils)):
            utility_advice.append(
                "防守开局先用火/雷拖慢第一波进攻（B 洞火、A 大雷），给自己争取架枪时间，不要空手等对面干拉。")
        if own_flash_enemies > 0 and not any(
                0 <= k["tick"] - u["tick"] <= 3 * tickrate for u in own_flashes for k in kills):
            utility_advice.append(
                "你的闪白到了敌人，但没在 2 秒内拉出去收割。闪光的价值窗口只有 1–2 秒，丢闪前先想好谁跟、从哪拉。")
        if not utility_advice:
            if own_utils and won:
                utility_advice.append("本回合道具用在了节奏上，继续按这个思路打：道具服务于进点/回防，而不是攒着不用。")
            else:
                utility_advice.append(
                    "每回合至少留一颗闪给关键对枪、一颗烟给转点或下包；道具留在包里阵亡，等于白买。")

        coach = {
            "headline": coach_headline,
            "phase": coach_phase,
            "exchange": exchange,
            "survival": survival_ctx,
            "note": coach_note,
            "team_after_death": {"mate_deaths": len(team_loss_after),
                                 "mate_kills": len(team_kill_after),
                                 "fast_plant": plant_fast_after},
            "wall": wall_evidence,
            "position_advice": position_advice[:2],
            "utility_advice": utility_advice[:2],
            "economy_advice": eco_advice[:2],
        }

        out_rounds.append({
            "round": rd["index"] + 1,
            "round_index": rd["index"],
            "side": side,
            "verdict": verdict,
            "won": won,
            "winner": winner,
            "win_reason": rd.get("win_reason"),
            "scores": {"positioning": round(pos_score),
                       "teamplay": round(team_score),
                       "decision": round(dec_score),
                       "crosshair": round(cross_score),
                       "total": round(total_score)},
            "stats": {
                "kills": len(kills), "death": 0 if survived else 1,
                "damage": damage,
                "alive_seconds": round(sec(my_death["tick"]) if my_death else sec(stat_end), 1),
                "survived": survived,
                "tradeable_pct": round(trade_ratio * 100),
                "isolated_pct": round(iso_ratio * 100),
                "avg_mate_meters": round(avg_nearest * U2M, 1),
                "aim_ready_pct": round(aim_ready_ratio * 100),
                "head_level_pct": round(head_level_ratio * 100),
                "avg_choke_err": round(avg_choke, 1) if avg_choke is not None else None,
                "trades_done": trades_done,
                "utility_used": len(own_utils),
                "utility_damage": own_util_damage,
                "flash_enemies": own_flash_enemies,
                "facewall_pct": round(wall_face_ratio * 100),
                "exchange_value": exchange_value,
            },
            "tags": tags,
            "coach": coach,
            "utility_report": utility_report,
            "good_points": good,
            "issues": issues,
            "better_options": better,
            "death_review": death_review,
            "timeline": timeline,
            "story": story,
            "roster": [{"name": r["name"], "side": r["side"]} for r in rd["roster"]],
            "economy": eco,
            "score_after": dict(run_score),
            "round_label": round_label,
        })

    # ---- 总览 ----
    if not out_rounds:
        raise ValueError(f"玩家 {player_name} 在正式回合中没有可分析的数据，请确认玩家名或 demo 是否完整")
    sum_kills = sum(r["stats"]["kills"] for r in out_rounds)
    sum_deaths = sum(r["stats"]["death"] for r in out_rounds)
    sum_dmg = 0
    for rd in data["rounds"]:
        sum_dmg += rd.get("damage", {}).get(player_name, 0)
    avg_total = round(float(np.mean([r["scores"]["total"] for r in out_rounds])), 1)
    side_rounds = {"CT": [r for r in out_rounds if r["side"] == "CT"],
                   "T": [r for r in out_rounds if r["side"] == "T"]}
    side_summary = {}
    for s, rs in side_rounds.items():
        if rs:
            side_summary[s] = {
                "rounds": len(rs),
                "kills": sum(r["stats"]["kills"] for r in rs),
                "deaths": sum(r["stats"]["death"] for r in rs),
                "avg_score": round(float(np.mean([r["scores"]["total"] for r in rs])), 1),
                "win_rate": round(sum(1 for r in rs if r["won"]) / len(rs) * 100),
            }
    best = max(out_rounds, key=lambda r: r["scores"]["total"], default=None)
    worst = min(out_rounds, key=lambda r: r["scores"]["total"], default=None)
    top_tags = sorted(tag_counter.items(), key=lambda x: -x[1])[:5]
    match_score = {"T": sum(1 for r in out_rounds if r["winner"] == "T"),
                   "CT": sum(1 for r in out_rounds if r["winner"] == "CT")}

    # ---- 教练训练清单：从全场共性里挑 3 个最值得练的点，而不是只罗列问题 ----
    def _rs_with(tag):
        return [r for r in out_rounds if tag in r["tags"]]

    low_value = _rs_with("低价值阵亡")
    isolated = _rs_with("孤立阵亡")
    high_value = _rs_with("高价值阵亡")
    avg_tp = float(np.mean([r["scores"]["teamplay"] for r in out_rounds])) if out_rounds else 0
    avg_ch = float(np.mean([r["scores"]["crosshair"] for r in out_rounds])) if out_rounds else 0
    avg_head = float(np.mean([r["stats"]["head_level_pct"] for r in out_rounds])) if out_rounds else 0
    avg_facewall = float(np.mean([r["stats"]["facewall_pct"] for r in out_rounds])) if out_rounds else 0
    total_rounds = len(out_rounds)
    lost_rounds = [r for r in out_rounds if not r["won"]]
    plan = []
    if low_value:
        plan.append({
            "title": "一换一之前，先看换完之后还剩几个人",
            "why": f"{len(low_value)} 个回合属于低价值阵亡：有些是数字上的 1 换 1，有些是首死没有换到空间；共同点是你死后队友被连锁打穿或包点很快失守。问题不在“有没有换”，而在交换后的防线厚度。",
            "drill": "每次准备对枪前，先报“我后面是谁、他离我几米、我死后他能不能补枪”；如果最近队友超过 8 米，就先退到可补枪角度，不要用命去换一个没有后续的击杀。",
            "rounds": [r["round_index"] for r in low_value[:5]],
        })
    if isolated:
        plan.append({
            "title": "减少无接应的第一接触",
            "why": f"{len(isolated)} 个回合阵亡时与最近队友距离过远，队友来不及补枪，你的死直接变成人数劣势。",
            "drill": "进攻时把自己定义为“第一身位或第二身位”，第一身位只负责拉枪线并报点，第二身位保持 3–8 米准备补枪；防守时不要站成孤点，至少留一条队友能立刻补到的角度。",
            "rounds": [r["round_index"] for r in isolated[:5]],
        })
    if avg_head < 62 or avg_facewall > 35:
        plan.append({
            "title": "准星高度与离墙距离",
            "why": f"全场平均头线率 {round(avg_head)}%，贴脸墙比例 {round(avg_facewall)}%。准星偏低或长期贴墙，会让你先看到墙、后看到人。",
            "drill": "无接触移动时准星保持在头线；直角架枪离墙至少一个半身位，斜角离墙近一些；过点前把准星放在烟边/墙外沿，不要贴着脸前那面墙。",
            "rounds": [r["round_index"] for r in out_rounds if r["stats"]["facewall_pct"] > 35][:5],
        })
    if avg_tp < 58 and total_rounds:
        plan.append({
            "title": "把“补枪”变成默认动作",
            "why": f"配合项平均只有 {round(avg_tp)} 分，说明你更多在打自己的枪，而不是和队友形成一前一后的结构。",
            "drill": "队友先接触时，你只做两件事：盯补枪线、报敌人数量；队友倒下后 1 秒内决定补枪还是退守，不要在同一角度重复送。",
            "rounds": [r["round_index"] for r in lost_rounds[:5]],
        })
    if high_value and len(plan) < 3:
        plan.append({
            "title": "保留有价值的抢点/换命",
            "why": f"有 {len(high_value)} 个回合的阵亡其实换来了空间或下包，这类主动接触不要因为“死了”就完全否定。",
            "drill": "抢点位时确保身后有人跟包/跟枪，死了也要让队友拿到你吸出来的枪线；如果没有第二人跟进，就不要把它当成战术首死。",
            "rounds": [r["round_index"] for r in high_value[:5]],
        })
    eco_mismatch = [r for r in out_rounds
                    if (r["economy"]["tier"] in ("half", "full") and r["economy"]["team_eco"] >= 3)
                    or (r["economy"]["tier"] == "eco" and r["economy"]["team_full"] >= 3)]
    if eco_mismatch and len(plan) < 3:
        plan.append({
            "title": "起枪先看队友经济，别一个人强起/一个人 eco",
            "why": f"{len(eco_mismatch)} 个回合你的起枪档位和全队脱节：要么队友都省你独起，要么队友全起你独省。经济不同步会让全队连续两三回合都凑不齐长枪。",
            "drill": "买枪前按 Tab 看全队存款，听 IGL 的 buy/eco 口令统一行动；钱多就帮队友发枪，钱少就一起省，把强起留给“输了也能接受”的关键局。",
            "rounds": [r["round_index"] for r in eco_mismatch[:5]],
        })
    plan = plan[:3]

    n_eco = eco_sum["eco_rounds"]
    eco_summary = {
        "total_spend": eco_sum["spend"],
        "equip_lost": eco_sum["lost"],
        "kill_reward": eco_sum["kill_reward"],
        "eco_rounds": n_eco,
        "eco_wins": eco_sum["eco_wins"],
        "half_rounds": eco_sum["half_rounds"],
        "full_rounds": eco_sum["full_rounds"],
        "avg_spend": round(eco_sum["spend"] / max(total_rounds, 1)),
        "damage_per_1000": round(eco_sum["damage"] / max(eco_sum["spend"], 1) * 1000, 1),
    }

    from core import coach as coach_mod
    coach_report = coach_mod.build(data, player_name, out_rounds, plan)

    return {
        "player": player_name,
        "map": data["map"],
        "overview": {
            "rounds": len(out_rounds),
            "kills": sum_kills,
            "deaths": sum_deaths,
            "kd": round(sum_kills / max(sum_deaths, 1), 2),
            "damage": sum_dmg,
            "utility_used": sum(r["stats"]["utility_used"] for r in out_rounds),
            "utility_damage": sum(r["stats"]["utility_damage"] for r in out_rounds),
            "flash_enemies": sum(r["stats"]["flash_enemies"] for r in out_rounds),
            "avg_score": avg_total,
            "side_summary": side_summary,
            "best_round": best["round"] if best else None,
            "worst_round": worst["round"] if worst else None,
            "top_problem_tags": top_tags,
            "match_score": match_score,
            "economy": eco_summary,
        },
        "rounds": out_rounds,
        "coaching_plan": coach_report["plan"],
        "coach_report": coach_report,
    }
