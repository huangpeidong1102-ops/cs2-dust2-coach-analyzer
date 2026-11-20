# -*- coding: utf-8 -*-
"""CS2 经济分析（MR12 竞技规则，2026 现行版本）。

规则要点（来源见使用说明）：
- 半场开局 $800，存款上限 $16000。
- 歼灭/CT 拖时间获胜 $3250；引爆/拆包获胜 $3500。
- 连败补偿阶梯 $1400/$1900/$2400/$2900/$3400；计数半场初为 1，
  输 +1（封顶 4）、赢 -1（保底 0），因此输手枪局拿 $1900。
- 下包/拆包个人 +$300；T 下包后仍输回合每人 +$600（CS2 由 $800 下调）。
- 2025-07 起 CT 每击杀一名 T，全队 +$50。
- 击杀奖励按武器：刀 $1500、多数霰弹 $900、冲锋枪(除P90)/XM1014 $600、
  步枪/手枪/P90/SSG08/投掷物 $300、AWP/Zeus $100。

起枪分档按“冻结期结束时的主武器 + 本回合消费”近似判断，
装备损失用本回合消费估算（阵亡即丢），均标注为近似。
"""

# 武器代码 -> 类别（active_weapon / player_death 用代码；item_purchase 用友好名）
PRIMARY_CLASS = {
    "ak47": "rifle", "m4a1": "rifle", "m4a1_s": "rifle",
    "aug": "rifle", "sg553": "rifle",
    "galilar": "cheap_rifle", "famas": "cheap_rifle",
    "awp": "awp", "ssg08": "scout",
    "mac10": "smg", "mp9": "smg", "mp7": "smg", "mp5sd": "smg",
    "ump45": "smg", "p90": "smg", "bizon": "smg",
    "nova": "shotgun", "xm1014": "shotgun", "mag7": "shotgun", "sawedoff": "shotgun",
    "deagle": "pistol", "elite": "pistol", "fiveseven": "pistol",
    "cz75a": "pistol", "tec9": "pistol", "p250": "pistol",
    "glock": "pistol", "usp_s": "pistol", "hkp2000": "pistol", "revolver": "pistol",
}
FRIENDLY_TO_CODE = {
    "AK-47": "ak47", "M4A4": "m4a1", "M4A1-S": "m4a1_s", "AUG": "aug", "SG 553": "sg553",
    "Galil AR": "galilar", "FAMAS": "famas", "AWP": "awp", "SSG 08": "ssg08",
    "MAC-10": "mac10", "MP9": "mp9", "MP7": "mp7", "MP5-SD": "mp5sd",
    "UMP-45": "ump45", "P90": "p90", "PP-Bizon": "bizon",
    "Nova": "nova", "XM1014": "xm1014", "MAG-7": "mag7", "Sawed-Off": "sawedoff",
    "Desert Eagle": "deagle", "Dual Berettas": "elite", "Five-SeveN": "fiveseven",
    "CZ75-Auto": "cz75a", "Tec-9": "tec9", "P250": "p250",
    "Glock-18": "glock", "USP-S": "usp_s", "P2000": "hkp2000", "R8 Revolver": "revolver",
}
ARMOR_ITEMS = {"Kevlar Vest", "Kevlar + Helmet", "Kevlar & Helmet", "Assault Suit"}
UTILITY_ITEMS = {"Smoke Grenade", "Flashbang", "High Explosive Grenade",
                 "Molotov", "Incendiary Grenade", "Decoy Grenade"}
KIT_ITEMS = {"Defuse Kit"}

# 武器别名（demo 里 M4A1-S/USP-S 常写作 silencer 变体）
WEAPON_ALIAS = {"m4a1_silencer": "m4a1_s", "usp_silencer": "usp_s",
                "m4a1_s": "m4a1_s", "usp_s": "usp_s"}

KILL_REWARD = {
    "knife": 1500, "knife_t": 1500,
    "nova": 900, "mag7": 900, "sawedoff": 900,
    "xm1014": 600, "mac10": 600, "mp9": 600, "mp7": 600, "mp5sd": 600,
    "ump45": 600, "bizon": 600,
    "awp": 100, "taser": 100,
}
DEFAULT_KILL_REWARD = 300   # 步枪/手枪/P90/SSG08/投掷物等

# 用于从带皮肤前缀的武器名（如 5e_2024pass3_ak47）里还原基础代码
_BASE_CODES = sorted(set(PRIMARY_CLASS) | set(KILL_REWARD) | set(WEAPON_ALIAS),
                   key=len, reverse=True)

LOSS_LADDER = [1400, 1900, 2400, 2900, 3400]
WIN_ELIM = 3250
WIN_OBJECTIVE = 3500
PLANT_PERSONAL = 300
DEFUSE_PERSONAL = 300
PLANT_LOSS_TEAM = 600       # CS2 现行
CT_KILL_TEAM = 50           # 2025-07 起
START_MONEY = 800
MONEY_CAP = 16000

TIER_LABEL = {"eco": "纯经济", "half": "半起/强起", "full": "全起"}


def weapon_code(name):
    """归一化武器名：友好名 / weapon_ 前缀 / 皮肤前缀（5e_xxx_ak47）/ silencer 变体。"""
    if not name:
        return ""
    s = str(name)
    if s in FRIENDLY_TO_CODE:
        s = FRIENDLY_TO_CODE[s]
    elif s.startswith("weapon_"):
        s = s[7:]
    s = WEAPON_ALIAS.get(s, s)
    if s in PRIMARY_CLASS or s in KILL_REWARD:
        return s
    # 带皮肤前缀：取能匹配上的最长基础代码后缀
    for code in _BASE_CODES:
        if s == code or s.endswith("_" + code):
            return WEAPON_ALIAS.get(code, code)
    return s


def kill_reward(weapon):
    code = weapon_code(weapon)
    if code in ("world", ""):
        return 0
    return KILL_REWARD.get(code, DEFAULT_KILL_REWARD)


def buy_tier(primary, spend):
    """按冻结期末主武器 + 本回合消费近似分档。"""
    cls = PRIMARY_CLASS.get(weapon_code(primary), "")
    if cls in ("rifle", "awp"):
        return "full"
    if cls in ("cheap_rifle", "scout", "smg", "shotgun"):
        return "half"
    if cls == "pistol":
        return "half" if spend >= 1500 else "eco"
    # 没有主武器信息时退而求其次用消费额
    if spend >= 4000:
        return "full"
    if spend >= 1500:
        return "half"
    return "eco"


def loss_bonus(counter):
    return LOSS_LADDER[max(0, min(4, counter))]


def round_win_reward(won, win_reason):
    if not won:
        return 0
    if win_reason and ("引爆" in win_reason or "拆除" in win_reason):
        return WIN_OBJECTIVE
    return WIN_ELIM


def summarize_purchases(purchases):
    """purchases: [{item, cost, tick}] -> 消费/护甲/道具数/钳子/本回合买的主武器。"""
    spend = 0
    armor = False
    util = 0
    kit = False
    primary = None
    for p in purchases:
        item = p.get("item", "")
        cost = int(p.get("cost", 0) or 0)
        spend += cost
        if item in ARMOR_ITEMS:
            armor = True
        elif item in UTILITY_ITEMS:
            util += 1
        elif item in KIT_ITEMS:
            kit = True
        else:
            code = FRIENDLY_TO_CODE.get(item)
            if code and PRIMARY_CLASS.get(code) not in (None, "pistol"):
                primary = code
            elif code and primary is None:
                primary = code
    return {"spend": spend, "armor": armor, "util": util, "kit": kit, "primary": primary}