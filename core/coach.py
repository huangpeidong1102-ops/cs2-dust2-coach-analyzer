# -*- coding: utf-8 -*-
"""资深教练总评：职业指标（KAST/ADR/首杀/爆头率）、全场排名、
球员风格画像、状态曲线、关键回合与分级训练计划。

所有结论只由 demo 真实事件推导：death 事件（killer/victim/assister/
headshot/tick）+ 每回合伤害 + 逐回合分析结果，不编造任何数据。
"""
import numpy as np

TICKRATE = 64
TRADE_WINDOW = 4 * TICKRATE   # 补枪认定窗口 4 秒


def _side_maps(rounds_data):
    """name -> side（任一回合出现即可，半场换边后按回合取）。"""
    per_round = []
    for rd in rounds_data:
        per_round.append({r["name"]: r["side"] for r in rd.get("roster", [])})
    return per_round


def _team_stats(rounds_data, sides):
    """聚合全场每个玩家的击杀/死亡/助攻/伤害/首杀/首死/爆头。"""
    st = {}
    def get(n):
        if n not in st:
            st[n] = {"kills": 0, "deaths": 0, "assists": 0, "damage": 0,
                     "first_kills": 0, "first_deaths": 0, "hs_kills": 0}
        return st[n]
    for rd, sm in zip(rounds_data, sides):
        for name, dmg in (rd.get("damage") or {}).items():
            get(name)["damage"] += int(dmg)
        deaths = sorted(rd.get("deaths") or [], key=lambda d: d["tick"])
        for i, d in enumerate(deaths):
            victim, killer = d.get("victim"), d.get("killer")
            if victim:
                get(victim)["deaths"] += 1
            if killer and killer != victim:
                get(killer)["kills"] += 1
                if d.get("headshot"):
                    get(killer)["hs_kills"] += 1
            if d.get("assister"):
                get(d["assister"])["assists"] += 1
            if i == 0:  # 本回合首杀/首死
                if killer and killer != victim:
                    get(killer)["first_kills"] += 1
                if victim:
                    get(victim)["first_deaths"] += 1
    return st


def _rank_of(st, player, key, reverse=True):
    """全场排名（1 为最好）。返回 (rank, of)。"""
    if player not in st:
        return None, len(st)
    vals = sorted(st.items(), key=lambda kv: kv[1][key], reverse=reverse)
    for i, (n, _) in enumerate(vals, 1):
        if n == player:
            return i, len(st)
    return None, len(st)


def _kast_and_entries(rounds_data, out_rounds, sides, player):
    """逐回合 KAST 布尔 + 首杀/首死，返回 (kast_flags, fk, fd, per_round_detail)。"""
    flags, details = [], []
    fk = fd = 0
    for rd, sm, out in zip(rounds_data, sides, out_rounds):
        if out is None:
            continue
        my_side = sm.get(player)
        deaths = sorted(rd.get("deaths") or [], key=lambda d: d["tick"])
        kills = [d for d in deaths if d.get("killer") == player and d.get("victim") != player]
        assists = [d for d in deaths if d.get("assister") == player]
        survived = out["stats"]["survived"]
        traded = False
        my_death = next((d for d in deaths if d.get("victim") == player), None)
        if my_death and my_death.get("killer"):
            killer = my_death["killer"]
            for d in deaths:
                if (d.get("victim") == killer and d["tick"] > my_death["tick"]
                        and d["tick"] - my_death["tick"] <= TRADE_WINDOW):
                    avenger_side = sm.get(d.get("killer"))
                    if avenger_side == my_side and d.get("killer") != player:
                        traded = True
                        break
        k = bool(kills)
        a = bool(assists)
        flag = k or a or survived or traded
        flags.append(flag)
        if deaths:
            first = deaths[0]
            if first.get("killer") == player and first.get("victim") != player:
                fk += 1
            if first.get("victim") == player:
                fd += 1
        details.append({"round_index": out["round_index"], "k": k, "a": a,
                        "s": survived, "t": traded})
    return flags, fk, fd, details


def _style(metrics, out_rounds):
    """根据行为数据打风格标签（1 主 1 副）。"""
    n = max(len(out_rounds), 1)
    awp_rounds = sum(1 for r in out_rounds
                     if (r.get("economy") or {}).get("primary") == "awp")
    entry_rate = (metrics["first_kills"] + metrics["first_deaths"]) / n
    surv = metrics["survival_rate"]
    util_per_round = sum(r["stats"]["utility_used"] for r in out_rounds) / n
    avg_mate = float(np.mean([r["stats"]["avg_mate_meters"] for r in out_rounds]))
    trades = sum(r["stats"]["trades_done"] for r in out_rounds)

    if awp_rounds >= n * 0.3:
        main = "狙击手"
        desc = f"{awp_rounds} 个回合起 AWP，你是队伍的长枪支点，选位和存活就是你的生命线。"
    elif entry_rate >= 0.32 and metrics["first_kills"] > metrics["first_deaths"]:
        main = "突破手（Entry）"
        desc = (f"首杀 {metrics['first_kills']} 次多于首死 {metrics['first_deaths']} 次，"
                "你能为队伍撕开第一道防线，这是最难替代的价值。")
    elif entry_rate >= 0.32:
        main = "激进接触型"
        desc = (f"首杀首死合计占 {round(entry_rate * 100)}% 回合，你总是第一个接触敌人；"
                "方向没错，但首死偏多，需要把“第一个上”升级成“有接应地上”。")
    elif surv >= 0.55 and avg_mate <= 8:
        main = "团队型步枪手"
        desc = f"存活率 {round(surv * 100)}%、与队友平均距离 {avg_mate:.1f} 米，你习惯贴着团队打，是稳定的体系球员。"
    elif surv >= 0.55:
        main = "稳健 Anchor"
        desc = f"存活率 {round(surv * 100)}%，你不轻易送枪，擅长把回合拖入残局。"
    else:
        main = "全能自由人"
        desc = (f"KAST {metrics['kast']}%、存活率 {round(surv * 100)}%，回合参与度在线、打法没有明显极端；"
                "下一步是把一两项能力练成让队友可以依赖的招牌。")

    sub = None
    if util_per_round >= 2.2:
        sub = "道具位"
    elif trades >= n * 0.25:
        sub = "补枪手"
    return {"main": main, "sub": sub, "desc": desc}


def _key_rounds(out_rounds, rounds_data, sides, player):
    """挑出最值得回看的回合：eco 翻盘 / 独活残局 / 多杀 / 最佳 / 最差。"""
    keys = []
    used = set()
    def add(out, kind, title, note, weight):
        if out["round_index"] in used:
            return
        used.add(out["round_index"])
        keys.append({"round_index": out["round_index"], "round": out["round"],
                     "kind": kind, "title": title, "note": note, "weight": weight})

    for out, rd, sm in zip(out_rounds, rounds_data, sides):
        if out.get("round_label") == "手枪局":
            continue  # 手枪局是独立战术回合，不算 eco/强起翻盘
        eco = out.get("economy") or {}
        if out["won"] and eco.get("tier") == "eco":
            add(out, "eco", "eco 翻盘", "纯经济局拿下一分，这种回合最伤对手经济、最涨士气——复盘你是怎么用最小成本换到击杀和空间的。", 5)
        elif out["won"] and eco.get("tier") == "half":
            add(out, "force", "强起翻盘", "半起/强起赢下回合，说明这波强起决策是对的，值得记住当时的节奏和道具配合。", 4)
    keys.sort(key=lambda k: -k["weight"])
    keys = keys[:3]  # 翻盘类最多 3 条，给残局/最佳/最差留位置
    for out, rd, sm in zip(out_rounds, rounds_data, sides):
        if not (out["won"] and out["stats"]["survived"]):
            continue
        my_side = sm.get(player)
        mates = [n for n, s in sm.items() if s == my_side and n != player]
        dead = {d.get("victim") for d in (rd.get("deaths") or [])}
        if mates and all(m in dead for m in mates):
            add(out, "clutch", "独活残局拿下", "队友全部阵亡后你一个人收下回合，残局决策值得反复看：每一步站位和时机都是教科书素材。", 6)
    for out in out_rounds:
        if out["stats"]["kills"] >= 3:
            add(out, "multi", f"{out['stats']['kills']} 杀回合",
                f"单回合 {out['stats']['kills']} 杀、{out['stats']['damage']} 伤害，看你如何连续拿到击杀——选位和补枪节奏是重点。", 3)
    best = max(out_rounds, key=lambda r: r["scores"]["total"])
    worst = min(out_rounds, key=lambda r: r["scores"]["total"])
    add(best, "best", "全场最佳回合", f"总分 {best['scores']['total']}，把你这回合的选位和决策固化为习惯。", 2)
    add(worst, "worst", "全场最差回合", f"总分 {worst['scores']['total']}，教练建议第一个回看的就是这回合。", 1)
    keys.sort(key=lambda k: -k["weight"])
    # 最佳/最差回合必须保留（教练最看重），其余按权重填满 5 条
    must = [k for k in keys if k["kind"] in ("best", "worst")]
    rest = [k for k in keys if k["kind"] not in ("best", "worst")]
    final = (must + rest)[:5]
    final.sort(key=lambda k: -k["weight"])
    return final


def _strengths_weaknesses(metrics, out_rounds, ranks, side_summary):
    strengths, weaknesses = [], []
    n = max(len(out_rounds), 1)
    adr_rank, of = ranks.get("adr", (None, 10))
    if metrics["kast"] >= 70:
        strengths.append({"title": f"回合贡献稳定（KAST {metrics['kast']}%）",
                          "detail": "大多数回合你都有击杀、助攻、存活或被补枪兜底，是队伍可以依赖的稳定点。"})
    if adr_rank and adr_rank <= 3:
        strengths.append({"title": f"伤害输出全场第 {adr_rank}（ADR {metrics['adr']}）",
                          "detail": "你的输出是队伍前排水平，说明枪法和交火选择基本在线，问题更多在交火之后的处理。"})
    eco_wins = sum(1 for r in out_rounds if r["won"] and (r.get("economy") or {}).get("tier") == "eco"
                   and r.get("round_label") != "手枪局")
    if eco_wins >= 1:
        strengths.append({"title": f"{eco_wins} 次 eco 翻盘",
                          "detail": "没枪也能赢回合，说明你会用点位、时机和道具弥补装备差距，这是高分局最值钱的能力之一。"})
    if metrics["entry_success"] is not None and metrics["first_kills"] >= 3 and metrics["entry_success"] >= 55:
        strengths.append({"title": f"首杀能力强（{metrics['first_kills']} 次首杀，成功率 {metrics['entry_success']}%）",
                          "detail": "开局第一接触你占优，队伍可以围绕你设计开局战术。"})
    if metrics["hs_pct"] >= 50 and metrics["kills"] >= 8:
        strengths.append({"title": f"爆头率 {metrics['hs_pct']}%",
                          "detail": "击杀质量高，准星摆放和控枪是你的长处。"})
    if not strengths:
        strengths.append({"title": "有稳定的基本盘",
                          "detail": "从数据看你没有特别崩盘的短板维度，接下来是把一两项能力练成招牌。"})

    if metrics["first_deaths"] >= 4 and (metrics["entry_success"] or 0) < 50:
        weaknesses.append({"title": f"首死偏多（{metrics['first_deaths']} 次）",
                           "detail": "第一个倒下会让全队战术被动。第一接触前先确认身后补枪位，别用无接应的干拉去试探。", "priority": "P0"})
    if metrics["kast"] < 62:
        weaknesses.append({"title": f"回合参与度偏低（KAST {metrics['kast']}%）",
                           "detail": "太多回合没有击杀、助攻、存活也没被补枪，等于“隐身回合”。要么更早参与补枪，要么死得更有价值。", "priority": "P0"})
    avg_head = float(np.mean([r["stats"]["head_level_pct"] for r in out_rounds]))
    avg_facewall = float(np.mean([r["stats"]["facewall_pct"] for r in out_rounds]))
    if avg_head < 58:
        weaknesses.append({"title": f"头线保持不足（{round(avg_head)}%）",
                           "detail": "无接触时准星经常不在头线，遭遇战要先抬枪再开枪，天然慢半拍。", "priority": "P1"})
    if avg_facewall > 35:
        weaknesses.append({"title": f"准星贴脸墙偏多（{round(avg_facewall)}%）",
                           "detail": "准星长期贴在面前的墙上，出墙瞬间需要大幅甩枪。架枪时把准星放到墙外沿/烟边。", "priority": "P1"})
    iso = [r for r in out_rounds if "孤立阵亡" in r["tags"]]
    if len(iso) >= 3:
        weaknesses.append({"title": f"{len(iso)} 次孤立阵亡",
                           "detail": "阵亡时队友都来不及补枪。站位前先问“我死了谁能立刻补”，答不上来就换位置。", "priority": "P0"})
    eco_mismatch = [r for r in out_rounds
                    if ((r.get("economy") or {}).get("tier") in ("half", "full") and (r.get("economy") or {}).get("team_eco", 0) >= 3)
                    or ((r.get("economy") or {}).get("tier") == "eco" and (r.get("economy") or {}).get("team_full", 0) >= 3)]
    if len(eco_mismatch) >= 2:
        weaknesses.append({"title": f"{len(eco_mismatch)} 次起枪与全队脱节",
                           "detail": "队友省钱你独起、队友全起你独省，都会让团队连续几回合装备断层。买枪前按 Tab 对齐全队经济。", "priority": "P1"})
    ct = (side_summary.get("CT") or {}).get("avg_score")
    t = (side_summary.get("T") or {}).get("avg_score")
    if ct and t and abs(ct - t) >= 12:
        worse, better = ("T", "CT") if ct > t else ("CT", "T")
        weaknesses.append({"title": f"{worse} 侧状态明显弱于 {better} 侧（{t if worse=='T' else ct} vs {ct if worse=='T' else t} 分）",
                           "detail": f"你在 {better} 侧打得更舒服，说明问题是结构性的：{worse} 侧的默认走位和开局路线需要重新设计，而不是枪法问题。", "priority": "P1"})
    return strengths[:3], weaknesses[:3]


def _summary_text(style, metrics, ranks, side_summary, strengths, weaknesses):
    parts = [style["desc"]]
    adr_rank, of = ranks.get("adr", (None, 10))
    if adr_rank:
        parts.append(f"全场数据：ADR {metrics['adr']}（第 {adr_rank}/{of}）、"
                     f"KAST {metrics['kast']}%、K/D {metrics['kd']}、"
                     f"首杀 {metrics['first_kills']} / 首死 {metrics['first_deaths']}。")
    ct = (side_summary.get("CT") or {}).get("avg_score")
    t = (side_summary.get("T") or {}).get("avg_score")
    if ct and t:
        better = "CT" if ct >= t else "T"
        parts.append(f"状态曲线：{better} 侧场均 {max(ct, t)} 分，优于另一侧的 {min(ct, t)} 分。")
    if weaknesses:
        parts.append(f"最需要先改的一件事：{weaknesses[0]['title'].split('（')[0]}——{weaknesses[0]['detail']}")
    return "".join(parts)


def _prioritize_plan(plan, weaknesses):
    """给训练清单标 P0/P1/P2：与首要短板对应的项目优先。"""
    p0_hints = ("一换一", "无接应", "补枪")
    for item in plan:
        title = item.get("title", "")
        if any(h in title for h in ("一换一", "无接应")):
            item["priority"] = "P0"
        elif any(h in title for h in ("补枪", "起枪", "准星")):
            item["priority"] = "P1"
        else:
            item["priority"] = "P2"
    order = {"P0": 0, "P1": 1, "P2": 2}
    plan.sort(key=lambda it: order.get(it.get("priority"), 3))
    return plan


def _opponent_scout(rounds_data, out_rounds, sides, player):
    """像教练一样研究对手：威胁排名、进点偏好、entry/狙击手识别与针对性布防。"""
    enemy = {}
    def get(n):
        if n not in enemy:
            enemy[n] = {"kills": 0, "deaths": 0, "first_kills": 0, "awp_kills": 0, "damage": 0}
        return enemy[n]
    site_pref = {"A": {"plants": 0, "wins": 0}, "B": {"plants": 0, "wins": 0}}
    for rd, sm, out in zip(rounds_data, sides, out_rounds):
        my_side = sm.get(player)
        foes = [n for n, s in sm.items() if s != my_side]
        for n in foes:
            get(n)["damage"] += int((rd.get("damage") or {}).get(n, 0))
        deaths = sorted(rd.get("deaths") or [], key=lambda d: d["tick"])
        for i, d in enumerate(deaths):
            k, v = d.get("killer"), d.get("victim")
            if k in enemy:
                enemy[k]["kills"] += 1
                if "awp" in str(d.get("weapon", "")).lower():
                    enemy[k]["awp_kills"] += 1
                if i == 0:
                    enemy[k]["first_kills"] += 1
            if v in enemy:
                enemy[v]["deaths"] += 1
        # 对手为 T 时的进点偏好
        if my_side == "CT":
            site = (rd.get("bomb") or {}).get("site")
            if site in site_pref:
                site_pref[site]["plants"] += 1
                if out["winner"] == "T":
                    site_pref[site]["wins"] += 1
    if not enemy:
        return None
    top = max(enemy.items(), key=lambda kv: kv[1]["kills"])
    entry = max(enemy.items(), key=lambda kv: kv[1]["first_kills"])
    awper = max(enemy.items(), key=lambda kv: kv[1]["awp_kills"])
    threats = []
    if top[1]["kills"] > 0:
        threats.append({"role": "对方击杀王", "kills": top[1]["kills"],
                        "detail": f"全场 {top[1]['kills']} 杀、{top[1]['damage']} 伤害，是对手最稳定的输出点，残局优先躲他的枪线。"})
    if entry[1]["first_kills"] >= 2:
        threats.append({"role": "对方突破手", "kills": entry[1]["first_kills"],
                        "detail": f"拿到 {entry[1]['first_kills']} 次首杀，习惯第一个拉出来；CT 侧开局用交叉火力或前置闪光反制他的默认路线。"})
    if awper[1]["awp_kills"] >= 2:
        threats.append({"role": "对方狙击手", "kills": awper[1]["awp_kills"],
                        "detail": f"AWP {awper[1]['awp_kills']} 杀；进攻时用烟封他的长枪线、闪逼他换位，不要干拉长廊。"})
    pref = None
    total_plants = site_pref["A"]["plants"] + site_pref["B"]["plants"]
    if total_plants:
        a, b = site_pref["A"], site_pref["B"]
        dom = "A" if a["plants"] >= b["plants"] else "B"
        d = site_pref[dom]
        pref = {"site": dom, "plants": d["plants"], "wins": d["wins"],
                "a_plants": a["plants"], "b_plants": b["plants"],
                "note": f"对手进攻偏爱 {dom} 区（{d['plants']} 次下包、赢 {d['wins']} 次）；CT 侧把把向 {dom} 区多倾斜一名队友，或让 AWP 架 {dom} 区要道。"}
    advice = []
    if entry[1]["first_kills"] >= 2:
        advice.append(f"对手首杀最多者拿 {entry[1]['first_kills']} 次首杀：开局别和他对干拉，用道具或双人交叉位吃掉他的第一波。")
    if pref:
        advice.append(pref["note"])
    if awper[1]["awp_kills"] >= 2:
        advice.append(f"对方 AWP 有 {awper[1]['awp_kills']} 杀：过开阔地先封烟/给闪，逼他换点再进。")
    return {"threats": threats[:3], "site_pref": pref, "advice": advice[:3]}


def _momentum(out_rounds):
    """连败/连胜后的表现，衡量心态韧性。"""
    base = float(np.mean([r["scores"]["total"] for r in out_rounds]))
    after_loss, after_win = [], []
    streak = 0
    for r in out_rounds:
        if streak <= -2:
            after_loss.append(r["scores"]["total"])
        elif streak >= 2:
            after_win.append(r["scores"]["total"])
        streak = streak + 1 if r["won"] else streak - 1
        if r["won"] and streak < 0:
            streak = 1
        if not r["won"] and streak > 0:
            streak = -1
    res = {"baseline": round(base, 1)}
    if len(after_loss) >= 2:
        al = float(np.mean(after_loss))
        res["after_loss"] = round(al, 1)
        res["after_loss_n"] = len(after_loss)
        diff = al - base
        res["resilience"] = "good" if diff >= 3 else ("poor" if diff <= -6 else "normal")
        res["note"] = (f"连败≥2 局后的 {len(after_loss)} 个回合场均 {al:.1f} 分，"
                       + ("高于" if diff >= 0 else "低于") + f"全场均值 {abs(diff):.1f} 分，"
                       + ("心态越挫越勇，逆风局反而更专注。" if diff >= 3 else
                          ("逆风时明显掉状态，需要练连败后的重置节奏（默认 eco 稳一局再打）。" if diff <= -6 else
                           "逆风表现基本稳定。")))
    return res


ROUTE_GROUPS = {
    "A大": ("A大", "A大拐角", "A大坡", "A大尽头", "A大坑"),
    "A小": ("A小", "A小顶"),
    "B洞": ("B洞", "下B洞", "B洞外", "B洞口", "B洞深处", "B洞口下"),
    "中路": ("中路", "中门", "中路下", "中路匪口"),
}


def _routes(rounds_data, out_rounds, player, tickrate=TICKRATE):
    """T 侧开局 20 秒落位 → 进攻路线胜率。"""
    from core import mapdata
    agg = {}
    for rd, out in zip(rounds_data, out_rounds):
        if out["side"] != "T":
            continue
        pts = (rd.get("trajectories") or {}).get(player) or []
        alive = [p for p in pts if p[4]]
        if not alive:
            continue
        t0 = alive[0][3]
        target = t0 + 20 * tickrate
        pos = min(alive, key=lambda p: abs(p[3] - target))
        lm = mapdata.landmark_of("de_dust2", pos[0], pos[1])
        route = next((g for g, names in ROUTE_GROUPS.items() if lm in names), "其他")
        a = agg.setdefault(route, {"rounds": 0, "wins": 0, "score": 0})
        a["rounds"] += 1
        a["wins"] += 1 if out["won"] else 0
        a["score"] += out["scores"]["total"]
    routes = []
    for name, a in agg.items():
        if name == "其他" or a["rounds"] < 2:
            continue
        routes.append({"route": name, "rounds": a["rounds"], "wins": a["wins"],
                       "win_rate": round(a["wins"] / a["rounds"] * 100),
                       "avg_score": round(a["score"] / a["rounds"], 1)})
    routes.sort(key=lambda r: -r["rounds"])
    return routes[:4]


def build(data, player_name, out_rounds, plan):
    """主入口：返回 coach_report 字典（含带优先级的 plan）。"""
    rounds_data = [rd for rd in data["rounds"]]
    # out_rounds 与 data rounds 按 round_index 对齐
    by_idx = {r["round_index"]: r for r in out_rounds}
    aligned = [by_idx.get(rd["index"]) for rd in rounds_data]
    pairs = [(rd, out) for rd, out in zip(rounds_data, aligned) if out is not None]
    rounds_data = [p[0] for p in pairs]
    outs = [p[1] for p in pairs]
    sides = _side_maps(rounds_data)

    st = _team_stats(rounds_data, sides)
    me = st.get(player_name, {"kills": 0, "deaths": 0, "assists": 0, "damage": 0,
                              "first_kills": 0, "first_deaths": 0, "hs_kills": 0})
    n = max(len(outs), 1)
    kast_flags, fk, fd, kast_detail = _kast_and_entries(rounds_data, outs, sides, player_name)
    kills = sum(r["stats"]["kills"] for r in outs)
    deaths = sum(r["stats"]["death"] for r in outs)
    dmg = sum(r["stats"]["damage"] for r in outs)
    survival = sum(1 for r in outs if r["stats"]["survived"])
    entry_success = round(fk / (fk + fd) * 100) if (fk + fd) > 0 else None

    metrics = {
        "rounds": len(outs),
        "kills": kills, "deaths": deaths, "assists": me["assists"],
        "kd": round(kills / max(deaths, 1), 2),
        "adr": round(dmg / n, 1),
        "kast": round(sum(kast_flags) / n * 100),
        "kast_rounds": sum(kast_flags),
        "first_kills": fk, "first_deaths": fd,
        "entry_success": entry_success,
        "survival_rate": round(survival / n, 3),
        "hs_pct": round(me["hs_kills"] / max(me["kills"], 1) * 100),
    }
    ranks = {
        "adr": _rank_of({k: {"v": v["damage"] / n} for k, v in st.items()}, player_name, "v"),
        "kd": _rank_of({k: {"v": v["kills"] / max(v["deaths"], 1)} for k, v in st.items()}, player_name, "v"),
        "kills": _rank_of({k: {"v": v["kills"]} for k, v in st.items()}, player_name, "v"),
        "assists": _rank_of({k: {"v": v["assists"]} for k, v in st.items()}, player_name, "v"),
        "damage": _rank_of({k: {"v": v["damage"]} for k, v in st.items()}, player_name, "v"),
    }

    side_summary = {}
    for s in ("CT", "T"):
        rs = [r for r in outs if r["side"] == s]
        if rs:
            side_summary[s] = {"avg_score": round(float(np.mean([r["scores"]["total"] for r in rs])), 1),
                               "rounds": len(rs)}

    halves = []
    for label, lo, hi in (("上半场", 0, 12), ("下半场", 12, 24), ("加时", 24, 999)):
        rs = [r for r in outs if lo <= r["round_index"] < hi]
        if rs:
            fl = [f for f, r in zip(kast_flags, outs) if lo <= r["round_index"] < hi]
            halves.append({"label": label, "rounds": len(rs),
                           "avg_score": round(float(np.mean([r["scores"]["total"] for r in rs])), 1),
                           "kast": round(sum(fl) / len(rs) * 100),
                           "side": rs[0]["side"]})

    style = _style(metrics, outs)
    strengths, weaknesses = _strengths_weaknesses(metrics, outs, ranks, side_summary)
    key_rounds = _key_rounds(outs, rounds_data, sides, player_name)
    plan = _prioritize_plan(plan, weaknesses)
    summary = _summary_text(style, metrics, ranks, side_summary, strengths, weaknesses)
    opponent = _opponent_scout(rounds_data, outs, sides, player_name)
    momentum = _momentum(outs)
    routes = _routes(rounds_data, outs, player_name)

    return {
        "style": style,
        "metrics": metrics,
        "ranks": {k: {"rank": v[0], "of": v[1]} for k, v in ranks.items()},
        "side_summary": side_summary,
        "halves": halves,
        "kast_detail": kast_detail,
        "key_rounds": key_rounds,
        "strengths": strengths,
        "weaknesses": weaknesses,
        "summary": summary,
        "opponent_scout": opponent,
        "momentum": momentum,
        "routes": routes,
        "plan": plan,
    }
