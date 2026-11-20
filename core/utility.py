# -*- coding: utf-8 -*-
"""双方道具使用与围绕道具的打法分析。"""
from collections import Counter

A_AREAS = {"A包点", "A平台", "A大尽头", "A小顶", "A大坑", "A大", "A大坡", "A小", "A大拐角"}
B_AREAS = {"B包点", "B洞口", "B洞外", "B洞", "B洞深处", "下B洞", "B外", "B外北", "B洞口下"}
MID_AREAS = {"中路", "中门", "中路下", "中路匪口", "桥下", "警家中", "警家西侧"}
ENTRY_AREAS = A_AREAS | B_AREAS | MID_AREAS
TYPE_ORDER = [("smoke", "烟"), ("flash", "闪"), ("he", "雷"), ("fire", "火"), ("decoy", "诱饵")]


def _effect_text(u):
    e = u["effect"]
    parts = []
    if e["enemies_blinded"]:
        parts.append(f"白到 {e['enemies_blinded']} 名敌人，共 {e['enemy_blind_seconds']} 秒")
    if e["enemy_damage"]:
        parts.append(f"造成 {e['enemy_damage']} 点敌方伤害")
    if e["teammates_blinded"]:
        parts.append(f"白到队友 {e['teammates_blinded']} 人次")
    if e["team_damage"]:
        parts.append(f"误伤队友 {e['team_damage']} 点")
    return "；".join(parts)


def _burst(events, tickrate):
    """找 6 秒内至少 3 件、至少 2 人参与且含烟和闪/火的爆弹窗口。"""
    best = None
    for i, ev in enumerate(events):
        group = [x for x in events if 0 <= x["tick"] - ev["tick"] <= 6 * tickrate]
        types = {x["type"] for x in group}
        users = {x["user"] for x in group}
        if (len(group) >= 3 and len(users) >= 2 and "smoke" in types
                and ("flash" in types or "fire" in types)):
            if best is None or len(group) > len(best):
                best = group
    return best or []


def _location_notes(events):
    smokes = [x["det_landmark"] for x in events if x["type"] == "smoke"]
    flashes = [x["det_landmark"] for x in events if x["type"] == "flash"]
    notes = []
    if smokes:
        locs = "、".join(f"【{x}】" for x, _ in Counter(smokes).most_common(3))
        notes.append(f"烟雾主要落在{locs}，用于隔绝这些方向的枪线。")
    if flashes:
        locs = "、".join(f"【{x}】" for x, _ in Counter(flashes).most_common(3))
        notes.append(f"闪光主要覆盖{locs}。")
    return notes


def _effect_notes(events, deaths, tickrate):
    notes = []
    enemy_blind = sum(x["effect"]["enemies_blinded"] for x in events)
    blind_sec = sum(x["effect"]["enemy_blind_seconds"] for x in events)
    damage = sum(x["effect"]["enemy_damage"] for x in events)
    team_flash = sum(x["effect"]["teammates_blinded"] for x in events)
    team_damage = sum(x["effect"]["team_damage"] for x in events)
    if enemy_blind:
        notes.append(f"闪光累计致盲 {enemy_blind} 名敌人（合计 {round(blind_sec, 1)} 秒）。")
    if damage:
        notes.append(f"雷火合计造成 {damage} 点有效伤害。")
    if team_flash:
        notes.append(f"有 {team_flash} 人次队友被己方闪光影响，投掷沟通需要改进。")
    if team_damage:
        notes.append(f"己方雷火误伤队友 {team_damage} 点。")
    # 闪光后 5 秒内是否转化为击杀
    conversions = []
    for u in events:
        if u["type"] != "flash" or not u["effect"]["enemies_blinded"]:
            continue
        kills = [d for d in deaths if d["killer"] in {x["user"] for x in events}
                 and 0 <= d["tick"] - u["tick"] <= 5 * tickrate]
        if kills:
            conversions.append(f"{u['user']} 在【{u['det_landmark']}】的闪光后 5 秒内形成 {len(kills)} 个击杀")
    notes.extend(conversions[:2])
    return notes


def build_utility_report(rd, side_map, live, tickrate, landmark_fn):
    enriched = []
    for u in rd.get("utilities", []):
        x = dict(u)
        x["time"] = round((u["tick"] - live) / tickrate, 1)
        x["det_landmark"] = landmark_fn(*u["det_pos"][:2])
        x["throw_landmark"] = landmark_fn(*u["throw_pos"][:2]) if u["throw_pos"] else "未知"
        x["effect_text"] = _effect_text(u)
        x["is_own"] = False
        enriched.append(x)

    reports = {}
    for side in ("T", "CT"):
        events = [x for x in enriched if x["side"] == side]
        counts = {k: sum(1 for x in events if x["type"] == k) for k, _ in TYPE_ORDER}
        notes = _location_notes(events) + _effect_notes(events, rd["deaths"], tickrate)
        burst = _burst(events, tickrate)
        plant = rd["bomb"].get("plant_tick")
        plant_site = rd["bomb"].get("site")
        postplant = [x for x in events if plant and x["tick"] >= plant]

        if side == "T":
            if burst:
                zones = [x["det_landmark"] for x in burst]
                a_score = sum(z in A_AREAS for z in zones) + (2 if plant_site == "A" else 0)
                b_score = sum(z in B_AREAS for z in zones) + (2 if plant_site == "B" else 0)
                zone = "A" if a_score >= b_score else "B"
                tactic = f"{zone}区集中爆弹" + ("并完成下包" if plant else "，但未能完成下包")
                notes.insert(0, f"6 秒内由 {len({x['user'] for x in burst})} 名队员连续投入 {len(burst)} 件道具，形成烟闪/烟火组合。")
                if not plant:
                    notes.append("爆弹没有转化为下包，重点检查爆弹后第一人进点是否太慢、补枪是否脱节。")
            elif plant:
                tactic = f"道具掩护进入{plant_site}区并完成下包"
            elif len(events) >= 3:
                tactic = "默认控图与分路道具试探"
            elif events:
                tactic = "少量道具清点，以枪线推进为主"
            else:
                tactic = "未使用道具，基本依靠干拉推进"
            long_n = sum(x["det_landmark"] in {"A大", "A大坡", "A大拐角", "A大坑", "A大尽头"} for x in events)
            short_n = sum(x["det_landmark"] in {"A小", "A小顶"} for x in events)
            mid_n = sum(x["det_landmark"] in MID_AREAS for x in events)
            b_n = sum(x["det_landmark"] in B_AREAS for x in events)
            if long_n and short_n:
                notes.append("A大与A小都有道具动作，呈现夹A路线；应让两侧在同一句口令下同时接触。")
            elif mid_n and b_n:
                notes.append("中路与B区都有道具动作，呈现中路夹B或牵制B区的路线。")
            if plant_site == "A" and b_n and (long_n or short_n):
                notes.append("B区道具与A区下包同时出现，更像B点牵制/假打：真正终结回合的是A区进攻，不应把B区烟雾误判为进攻主线。")
            elif plant_site == "B" and (long_n or short_n) and b_n:
                notes.append("A区道具与B区下包同时出现，更像A点牵制/假打，B区才是实际终结方向。")
            if len(events) <= 1 and sum(d["victim"] in {n for n, s in side_map.items() if s == "T"} for d in rd["deaths"]) >= 2:
                notes.append("道具投入很少且进攻方阵亡较多，本回合更接近干拉，进点缺乏烟闪保护。")
        else:
            early_delay = [x for x in events if x["time"] <= 25 and x["type"] in {"smoke", "fire"}
                           and x["det_landmark"] in ENTRY_AREAS]
            if postplant:
                tactic = "下包后投入道具组织回防" + ("并成功拆包" if "defuse_tick" in rd["bomb"] else "")
                notes.insert(0, f"下包后使用 {len(postplant)} 件道具，重点服务于清点和贴近C4。")
            elif early_delay:
                tactic = "前点烟火拖延进攻，配合枪线防守"
                locs = "、".join(f"【{x}】" for x in sorted({x['det_landmark'] for x in early_delay}))
                notes.insert(0, f"开局 25 秒内在{locs}使用烟火，主要目的是拖慢第一波进攻。")
            elif len(events) >= 3:
                tactic = "分点防守与道具交换"
            elif events:
                tactic = "少量道具辅助防守"
            else:
                tactic = "未使用道具，完全依赖枪线防守"
            # 拆包时是否有烟覆盖包点
            if "defuse_tick" in rd["bomb"] and rd["bomb"].get("plant_pos"):
                pp = rd["bomb"]["plant_pos"]
                cover = []
                for x in events:
                    if x["type"] != "smoke":
                        continue
                    dist = ((x["det_pos"][0] - pp[0]) ** 2 + (x["det_pos"][1] - pp[1]) ** 2) ** 0.5
                    if dist <= 400:
                        cover.append(x)
                if cover:
                    notes.append("包点烟雾覆盖到C4附近，为拆包或近点回防提供了掩护。")

        reports[side] = {
            "label": "进攻方 T" if side == "T" else "防守方 CT",
            "total": len(events), "counts": counts,
            "tactic": tactic, "notes": notes,
            "events": events,
        }
    return reports
