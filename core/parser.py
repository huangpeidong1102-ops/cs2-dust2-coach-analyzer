# -*- coding: utf-8 -*-
"""Demo 解析层：把 .dem 一次性解析为结构化数据（字典，可 JSON 序列化）。

只处理 CS2（PBDEMS2）demo。热身（is_warmup_period=True）整体丢弃。
回合窗口以 freeze_end / prestart 事件为准（tick 内的回合计数器会提前跳转，
不能直接用它做边界）。
"""
import numpy as np
import pandas as pd

TICKRATE = 64
TRAJ_STRIDE = 16          # 轨迹抽稀步长（tick），64tick 下约每秒 4 个点
SIDE_NAME = {2: "T", 3: "CT"}


def _row_at_or_before(sub, tick):
    """在单个玩家的子表中取 <= tick 的最后一行。"""
    idx = sub["tick"].searchsorted(tick, side="right") - 1
    return sub.iloc[max(0, idx)]


def _pos_of(row):
    return [float(row["X"]), float(row["Y"]), float(row["Z"])]


def parse_demo(demo_path, progress=None):
    from demoparser2 import DemoParser
    dp = DemoParser(demo_path)

    def log(msg):
        if progress:
            progress(msg)

    def parse_ev(name):
        """不同 demo / 版本下无事件时 demoparser2 可能返回空列表，统一成 DataFrame。"""
        ev = dp.parse_event(name)
        if isinstance(ev, pd.DataFrame):
            return ev
        return pd.DataFrame(columns=["tick"])

    log("读取文件头...")
    header = dp.parse_header()
    map_name = header.get("map_name", "unknown")

    log("解析逐 tick 数据（位置/视角/血量/武器）...")
    tdf = dp.parse_ticks(["tick", "name", "steamid", "team_num", "is_alive", "health",
                          "X", "Y", "Z", "pitch", "yaw", "active_weapon", "velocity",
                          "total_rounds_played", "is_warmup_period", "balance"])
    tdf = tdf.sort_values(["name", "tick"]).reset_index(drop=True)

    log("解析比赛事件（击杀/炸弹/伤害）...")
    deaths_ev = parse_ev("player_death")
    hurt_ev = parse_ev("player_hurt")
    fire_ev = parse_ev("weapon_fire")
    prestart_all = parse_ev("round_prestart")["tick"].values
    freeze_all = parse_ev("round_freeze_end")["tick"].values
    end_ev = parse_ev("round_end")
    match_ev = parse_ev("begin_new_match")
    plant_ev = parse_ev("bomb_planted")
    defuse_ev = parse_ev("bomb_defused")
    explode_ev = parse_ev("bomb_exploded")
    drop_ev = parse_ev("bomb_dropped")
    log("解析道具事件（烟/闪/雷/火/诱饵与致盲）...")
    util_event_names = {
        "smoke": "smokegrenade_detonate",
        "flash": "flashbang_detonate",
        "he": "hegrenade_detonate",
        "fire": "inferno_startburn",
        "decoy": "decoy_detonate",
    }
    util_events = {k: parse_ev(v) for k, v in util_event_names.items()}
    blind_ev = parse_ev("player_blind")
    purchase_ev = parse_ev("item_purchase")

    # 比赛开始 tick（热身结束）
    match_tick = int(match_ev["tick"].iloc[0]) if len(match_ev) else 0
    real_prestart = [int(t) for t in prestart_all if t >= match_tick]
    real_freeze = [int(t) for t in freeze_all if t >= match_tick]

    # 丢弃热身
    tdf = tdf[tdf["is_warmup_period"] == False].copy()  # noqa: E712
    max_tick = int(tdf["tick"].max())

    # 每个玩家的子表
    subs = {n: g.reset_index(drop=True) for n, g in tdf.groupby("name")}

    # ---------- 事件驱动的回合窗口 ----------
    # 对每个 freeze_end：向前找最近的 prestart 作为回合起点，向后找下一个 prestart 作为窗口终点。
    # round_end 事件（winner/reason）作为真正的回合结束点，赛后庆祝/走路时间不计入统计。
    ROUND_REASON = {
        "t_killed": "T 被全歼", "ct_killed": "CT 被全歼",
        "target_bombed": "炸弹引爆", "bomb_defused": "炸弹被拆除",
        "target_saved": "时间耗尽，CT 守包成功",
        "1": "炸弹引爆", "7": "炸弹被拆除", "8": "CT 获胜", "9": "T 获胜",
        "12": "时间耗尽，CT 守包成功", "16": "比赛开始",
    }
    ends = []
    if len(end_ev):
        for _, e in end_ev.iterrows():
            w = e.get("winner", "")
            if pd.isna(w):
                w = ""
            elif isinstance(w, (int, float)):
                w = SIDE_NAME.get(int(w), "")
            else:
                w = str(w)
            ends.append({"tick": int(e["tick"]),
                         "winner": w if w in ("T", "CT") else None,
                         "reason": ROUND_REASON.get(str(e.get("reason", "")), str(e.get("reason", "")))})
    rounds = []
    for r, live in enumerate(real_freeze):
        pre_candidates = [t for t in real_prestart if t <= live]
        pre = pre_candidates[-1] if pre_candidates else live
        next_pre = [t for t in real_prestart if t > live]
        end = next_pre[0] if next_pre else max_tick
        re_ = next((x for x in ends if live <= x["tick"] <= end), None)
        rounds.append({"index": r, "prestart_tick": pre, "freeze_tick": live,
                       "live_start": live, "min_tick": pre, "max_tick": end,
                       "end_tick": re_["tick"] if re_ else end,
                       "winner": re_["winner"] if re_ else None,
                       "win_reason": re_["reason"] if re_ else None})

    # ---------- 自动锚点：出生点 / 包点 ----------
    log("计算地图锚点...")
    r0 = rounds[0]
    t_spawn = ct_spawn = None
    for nm, sub in subs.items():
        rr = _row_at_or_before(sub, r0["freeze_tick"] + 3)
        if rr["team_num"] == 2 and t_spawn is None:
            t_spawn = _pos_of(rr)[:2]
        if rr["team_num"] == 3 and ct_spawn is None:
            ct_spawn = _pos_of(rr)[:2]
    a_site = b_site = None
    if len(plant_ev):
        for site_id, grp in plant_ev.groupby("site"):
            pts = []
            for _, pr in grp.iterrows():
                if pr["user_name"] in subs:
                    rr = _row_at_or_before(subs[pr["user_name"]], int(pr["tick"]))
                    pts.append((rr["X"], rr["Y"]))
            if pts:
                med = np.median(np.array(pts), axis=0)
                if int(site_id) == 201:
                    a_site = [float(med[0]), float(med[1])]
                elif int(site_id) == 202:
                    b_site = [float(med[0]), float(med[1])]
    anchors = {}
    if t_spawn:
        anchors["匪家"] = (t_spawn[0], t_spawn[1], 380)
    if ct_spawn:
        anchors["警家"] = (ct_spawn[0], ct_spawn[1], 380)
    if a_site:
        anchors["A包点"] = (a_site[0], a_site[1], 360)
    if b_site:
        anchors["B包点"] = (b_site[0], b_site[1], 360)

    # ---------- 逐回合组装 ----------
    for rd in rounds:
        lo, hi = rd["min_tick"], rd["max_tick"]
        rtdf = tdf[(tdf["tick"] >= lo) & (tdf["tick"] <= hi)]

        # 玩家与阵营
        roster = []
        for nm, g in rtdf.groupby("name"):
            teams = g["team_num"].dropna()
            if not len(teams):
                continue
            side = int(teams.mode().iloc[0])
            if side in SIDE_NAME:
                roster.append({"name": nm, "steamid": str(g["steamid"].iloc[0]),
                               "side": SIDE_NAME[side]})
        rd["roster"] = roster

        # 死亡事件（含位置与全员快照）
        rd_deaths = []
        dsub = deaths_ev[(deaths_ev["tick"] >= lo) & (deaths_ev["tick"] <= hi)]
        for _, d in dsub.iterrows():
            t = int(d["tick"])
            victim, killer = d["user_name"], (
                d["attacker_name"] if pd.notna(d["attacker_name"]) else None)
            vpos = _pos_of(_row_at_or_before(subs[victim], t)) if victim in subs else None
            kpos = _pos_of(_row_at_or_before(subs[killer], t)) if killer and killer in subs else None
            snapshot = []
            for nm, sub in subs.items():
                rr = _row_at_or_before(sub, t)
                if lo - 200 <= rr["tick"] <= hi + 50:
                    snapshot.append({
                        "name": nm,
                        "side": SIDE_NAME.get(int(rr["team_num"]), "?") if pd.notna(rr["team_num"]) else "?",
                        "pos": _pos_of(rr),
                        "alive": bool(rr["is_alive"]),
                        "health": int(rr["health"]) if pd.notna(rr["health"]) else 0,
                    })
            rd_deaths.append({
                "tick": t, "victim": victim, "killer": killer,
                "weapon": d["weapon"], "headshot": bool(d["headshot"]),
                "distance": float(d["distance"]),
                "thrusmoke": bool(d["thrusmoke"]),
                "attackerinair": bool(d["attackerinair"]),
                "assister": d["assister_name"] if pd.notna(d["assister_name"]) else None,
                "assistedflash": bool(d["assistedflash"]),
                "victim_pos": vpos, "killer_pos": kpos,
                "snapshot": snapshot,
            })
        rd["deaths"] = rd_deaths

        # 炸弹事件
        bomb = {}
        pl = plant_ev[(plant_ev["tick"] >= lo) & (plant_ev["tick"] <= hi)]
        if len(pl):
            p0 = pl.iloc[0]; t = int(p0["tick"])
            rr = _row_at_or_before(subs[p0["user_name"]], t) if p0["user_name"] in subs else None
            bomb.update({"plant_tick": t,
                         "site": "A" if int(p0["site"]) == 201 else "B",
                         "planter": p0["user_name"],
                         "plant_pos": _pos_of(rr) if rr is not None else None})
        df0 = defuse_ev[(defuse_ev["tick"] >= lo) & (defuse_ev["tick"] <= hi)]
        if len(df0):
            bomb["defuse_tick"] = int(df0.iloc[0]["tick"])
            bomb["defuser"] = df0.iloc[0]["user_name"]
        ex0 = explode_ev[(explode_ev["tick"] >= lo) & (explode_ev["tick"] <= hi)]
        if len(ex0):
            bomb["explode_tick"] = int(ex0.iloc[0]["tick"])
        dr0 = drop_ev[(drop_ev["tick"] >= lo) & (drop_ev["tick"] <= hi)]
        if len(dr0):
            bomb["drop_ticks"] = [int(x) for x in dr0["tick"].values]
        rd["bomb"] = bomb

        # 伤害汇总
        dmg = {}
        hsub = hurt_ev[(hurt_ev["tick"] >= lo) & (hurt_ev["tick"] <= hi)]
        for h in hsub.itertuples(index=False):
            if pd.notna(h.attacker_name):
                dmg[h.attacker_name] = dmg.get(h.attacker_name, 0) + int(h.dmg_health)
        rd["damage"] = dmg

        # 经济：本回合购买明细 + 冻结期末/回合结束时的存款与主武器
        purchases = {}
        if len(purchase_ev):
            psub = purchase_ev[(purchase_ev["tick"] >= lo) & (purchase_ev["tick"] <= hi)]
            for p in psub.itertuples(index=False):
                if getattr(p, "was_sold", False):
                    continue
                purchases.setdefault(p.name, []).append(
                    {"tick": int(p.tick), "item": str(p.item_name), "cost": int(p.cost or 0)})
        rd["purchases"] = purchases
        freeze_bal, end_bal, freeze_wpn = {}, {}, {}
        for r in roster:
            nm = r["name"]
            if nm not in subs:
                continue
            fr = _row_at_or_before(subs[nm], rd["freeze_tick"])
            er = _row_at_or_before(subs[nm], rd["end_tick"])
            freeze_bal[nm] = int(fr["balance"]) if pd.notna(fr["balance"]) else 0
            end_bal[nm] = int(er["balance"]) if pd.notna(er["balance"]) else 0
            freeze_wpn[nm] = str(fr["active_weapon"]) if pd.notna(fr["active_weapon"]) else ""
        rd["freeze_balance"] = freeze_bal
        rd["end_balance"] = end_bal
        rd["freeze_weapon"] = freeze_wpn

        # 开火次数与开火弹道（位置/朝向从同 tick 最近采样帧取得）
        fsub = fire_ev[(fire_ev["tick"] >= lo) & (fire_ev["tick"] <= hi)]
        rd["shots"] = fsub.groupby("user_name").size().to_dict() if len(fsub) else {}
        shots_detail = []
        non_gun = {"weapon_knife", "weapon_c4", "weapon_hegrenade", "weapon_smokegrenade",
                   "weapon_flashbang", "weapon_molotov", "weapon_incgrenade",
                   "weapon_decoy", "weapon_taser"}
        for s in fsub.itertuples(index=False):
            nm, t = s.user_name, int(s.tick)
            wpn = str(s.weapon)
            if nm not in subs or wpn in non_gun:
                continue
            rr = _row_at_or_before(subs[nm], t)
            if pd.isna(rr["X"]) or pd.isna(rr["Y"]) or pd.isna(rr["Z"]):
                continue
            shots_detail.append({
                "tick": t, "user": nm, "weapon": wpn,
                "side": SIDE_NAME.get(int(rr["team_num"]), "?"),
                "pos": _pos_of(rr),
                "yaw": float(rr["yaw"]), "pitch": float(rr["pitch"]),
            })
        rd["shots_detail"] = shots_detail

        # 道具：爆点、投掷点、致盲与雷火伤害
        side_by_name = {p["name"]: p["side"] for p in roster}
        bsub = blind_ev[(blind_ev["tick"] >= lo) & (blind_ev["tick"] <= hi)]
        utilities = []
        util_specs = [
            ("smoke", "烟雾弹", ["weapon_smokegrenade"]),
            ("flash", "闪光弹", []),
            ("he", "手雷", ["weapon_hegrenade"]),
            ("fire", "燃烧弹", ["weapon_molotov", "weapon_incgrenade"]),
            ("decoy", "诱饵弹", ["weapon_decoy"]),
        ]
        for utype, uname, throw_weapons in util_specs:
            uev = util_events[utype]
            ud = uev[(uev["tick"] >= lo) & (uev["tick"] <= hi)]
            for _, u in ud.iterrows():
                det_tick = int(u["tick"])
                user = u["user_name"] if pd.notna(u["user_name"]) else "未知"
                uside = side_by_name.get(user, "?")
                # 用同一玩家最近一次投掷事件匹配弹道起点；闪光弹用爆点前 1.4 秒估算。
                throw_tick = None
                if throw_weapons and user != "未知":
                    cand = fsub[(fsub["user_name"] == user) &
                                (fsub["weapon"].isin(throw_weapons)) &
                                (fsub["tick"] < det_tick) &
                                (fsub["tick"] >= det_tick - 400)]
                    if len(cand):
                        throw_tick = int(cand["tick"].max())
                estimated = throw_tick is None
                if throw_tick is None:
                    throw_tick = det_tick - 90
                throw_pos = (_pos_of(_row_at_or_before(subs[user], throw_tick))
                             if user in subs else None)
                effect = {"enemies_blinded": 0, "teammates_blinded": 0,
                          "enemy_blind_seconds": 0.0, "enemy_damage": 0,
                          "team_damage": 0, "enemies_hit": 0}
                if utype == "flash" and len(bsub):
                    bb = bsub[(bsub["tick"] == det_tick) &
                              (bsub["attacker_name"] == user)]
                    enemy_names, durations = set(), []
                    for _, b in bb.iterrows():
                        victim_side = side_by_name.get(b["user_name"], "?")
                        dur = float(b["blind_duration"])
                        if uside != "?" and victim_side != uside:
                            enemy_names.add(b["user_name"]); durations.append(dur)
                        elif victim_side == uside and b["user_name"] != user:
                            effect["teammates_blinded"] += 1
                    effect["enemies_blinded"] = len(enemy_names)
                    effect["enemy_blind_seconds"] = round(sum(durations), 1)
                elif utype == "he" and len(hsub):
                    hh = hsub[(hsub["attacker_name"] == user) &
                              (hsub["weapon"] == "hegrenade") &
                              (hsub["tick"].between(det_tick - 4, det_tick + 4))]
                    victims = set()
                    for _, h in hh.iterrows():
                        vside = side_by_name.get(h["user_name"], "?")
                        if uside != "?" and vside != uside:
                            effect["enemy_damage"] += int(h["dmg_health"])
                            victims.add(h["user_name"])
                        elif vside == uside and h["user_name"] != user:
                            effect["team_damage"] += int(h["dmg_health"])
                    effect["enemies_hit"] = len(victims)
                elif utype == "fire" and len(hsub):
                    hh = hsub[(hsub["attacker_name"] == user) &
                              (hsub["weapon"].isin(["inferno", "molotov"])) &
                              (hsub["tick"].between(det_tick, det_tick + 7 * TICKRATE))]
                    victims = set()
                    for _, h in hh.iterrows():
                        vside = side_by_name.get(h["user_name"], "?")
                        if uside != "?" and vside != uside:
                            effect["enemy_damage"] += int(h["dmg_health"])
                            victims.add(h["user_name"])
                        elif vside == uside and h["user_name"] != user:
                            effect["team_damage"] += int(h["dmg_health"])
                    effect["enemies_hit"] = len(victims)
                utilities.append({
                    "tick": det_tick, "throw_tick": throw_tick,
                    "type": utype, "name": uname, "user": user, "side": uside,
                    "det_pos": [float(u["x"]), float(u["y"]), float(u["z"])],
                    "throw_pos": throw_pos, "estimated_throw": estimated,
                    "effect": effect,
                })
        rd["utilities"] = sorted(utilities, key=lambda x: x["tick"])

        # 采样帧：[x,y,z,tick,pitch,yaw,alive,health,weapon]
        traj = {}
        for nm, g in rtdf.groupby("name"):
            pts = g.sort_values("tick").iloc[::TRAJ_STRIDE]
            pts = pts[pts["X"].notna() & pts["Y"].notna() & pts["Z"].notna()]
            traj[nm] = [[float(a["X"]), float(a["Y"]), float(a["Z"]), int(a["tick"]),
                         float(a["pitch"]) if pd.notna(a["pitch"]) else 0.0,
                         float(a["yaw"]) if pd.notna(a["yaw"]) else 0.0,
                         bool(a["is_alive"]) if pd.notna(a["is_alive"]) else False,
                         int(a["health"]) if pd.notna(a["health"]) else 0,
                         str(a["active_weapon"]) if pd.notna(a["active_weapon"]) else ""]
                        for _, a in pts.iterrows()]
        rd["trajectories"] = traj

    # ---------- 玩家总览 ----------
    log("整理玩家清单...")
    players = []
    for nm, sub in subs.items():
        sides = {}
        for rd in rounds:
            g = sub[(sub["tick"] >= rd["min_tick"]) & (sub["tick"] <= rd["max_tick"])]
            teams = g["team_num"].dropna()
            if len(teams):
                s = int(teams.mode().iloc[0])
                if s in SIDE_NAME:
                    sides[rd["index"]] = SIDE_NAME[s]
        if sides:
            players.append({"name": nm, "steamid": str(sub["steamid"].iloc[0]), "sides": sides})

    return {"map": map_name, "tickrate": TICKRATE, "max_tick": max_tick,
            "anchors": anchors, "rounds": rounds, "players": players}
