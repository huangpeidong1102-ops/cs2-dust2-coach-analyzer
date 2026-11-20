# -*- coding: utf-8 -*-
"""本地 CS2 Demo 分析器 —— Flask 入口。

运行：双击 启动分析器.bat，或 python app.py，然后浏览器打开 http://127.0.0.1:8765
"""
import os
import math
import uuid
import traceback

from flask import Flask, request, jsonify, render_template

from core.parser import parse_demo
from core.analyzer import analyze

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
UPLOAD_DIR = os.path.join(BASE_DIR, "uploads")
os.makedirs(UPLOAD_DIR, exist_ok=True)

app = Flask(__name__)
app.json.compact = True
app.json.sort_keys = False
app.config["MAX_CONTENT_LENGTH"] = 800 * 1024 * 1024     # 最大 800MB

# 会话级解析缓存：session_id -> 结构化数据
SESSIONS = {}


def _json_safe(obj):
    """把 NaN/Infinity 统一转成 null，保证标准 JSON.parse 能读。"""
    if isinstance(obj, float):
        return obj if math.isfinite(obj) else None
    if isinstance(obj, dict):
        return {k: _json_safe(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_json_safe(v) for v in obj]
    return obj


def _pos(p):
    return [round(float(p[0]), 1), round(float(p[1]), 1), round(float(p[2]), 1)]


def _slim_round(rd):
    """提取前端绘图需要的轻量数据；坐标/角度做一位小数压缩，控制响应体积。"""
    traj = {}
    for nm, pts in rd["trajectories"].items():
        # [x,y,z,tick,alive,pitch,yaw,weapon]
        traj[nm] = [[round(p[0], 1), round(p[1], 1), round(p[2], 1), p[3], p[6],
                     round(p[4], 1), round(p[5], 1), p[8]] for p in pts]
    deaths = []
    for d in rd["deaths"]:
        deaths.append({
            "tick": d["tick"], "victim": d["victim"], "killer": d["killer"],
            "victim_pos": _pos(d["victim_pos"]) if d["victim_pos"] else None,
            "killer_pos": _pos(d["killer_pos"]) if d["killer_pos"] else None,
            "headshot": d["headshot"], "thrusmoke": d["thrusmoke"],
            "assister": d.get("assister"), "assistedflash": d.get("assistedflash", False),
            "weapon": d["weapon"],
            "snapshot": d["snapshot"],
        })
    shots = []
    for s in rd.get("shots_detail", []):
        # [tick, user, side, x, y, yaw]
        shots.append([s["tick"], s["user"], s["side"],
                      round(s["pos"][0], 1), round(s["pos"][1], 1), round(s["yaw"], 1)])
    bomb = dict(rd["bomb"])
    if bomb.get("plant_pos"):
        bomb["plant_pos"] = _pos(bomb["plant_pos"])
    return {"round_index": rd["index"],
            "prestart_tick": rd["prestart_tick"],
            "live_start": rd["live_start"],
            "max_tick": rd["max_tick"],
            "end_tick": rd.get("end_tick") or rd["max_tick"],
            "bomb": bomb,
            "roster": rd["roster"],
            "trajectories": traj,
            "shots": shots,
            "utilities": rd.get("utilities", []),
            "deaths": deaths}


@app.get("/")
def index():
    return render_template("index.html")


@app.post("/api/upload")
def upload():
    f = request.files.get("demo")
    if not f or not f.filename:
        return jsonify({"error": "未收到 demo 文件"}), 400
    if not f.filename.lower().endswith(".dem"):
        return jsonify({"error": "请上传 .dem 格式的 CS2 demo 文件"}), 400

    session_id = uuid.uuid4().hex
    path = os.path.join(UPLOAD_DIR, f"{session_id}.dem")
    f.save(path)

    try:
        data = parse_demo(path)
    except Exception as e:
        traceback.print_exc()
        return jsonify({"error": f"解析失败：{e}"}), 500

    if data.get("map") != "de_dust2":
        return jsonify({"error": f"本工具只分析 de_dust2，当前 demo 地图为 {data.get('map')}"}), 400

    SESSIONS[session_id] = data
    players = []
    for p in data["players"]:
        sides = sorted(set(p["sides"].values()))
        players.append({"name": p["name"], "sides": sides})
    return jsonify({
        "session": session_id,
        "map": data["map"],
        "round_count": len(data["rounds"]),
        "players": players,
    })


@app.post("/api/analyze")
def do_analyze():
    payload = request.get_json(force=True)
    sid = payload.get("session")
    player = payload.get("player")
    data = SESSIONS.get(sid)
    if data is None:
        return jsonify({"error": "会话已失效，请重新上传 demo"}), 400
    if not player:
        return jsonify({"error": "请选择要分析的玩家"}), 400

    try:
        result = analyze(data, player)
    except Exception as e:
        traceback.print_exc()
        return jsonify({"error": f"分析失败：{e}"}), 500

    from core import mapdata
    result["landmarks"] = mapdata.LANDMARKS
    result["vision"] = {
        "mode": "radar2d",
        "scale": 4.4,
        "pos_x": -2476,
        "pos_y": 3239,
        "wallmask": "/static/dust2_wallmask.png",
        "note": "墙体为沙二雷达可行走面提取的二维遮挡近似，不区分上下层和可穿透物。",
    }
    result["round_count"] = len(data["rounds"])
    result["map_rounds"] = []
    resp = jsonify(_json_safe(result))
    print(f"[analyze] {player}: {len(resp.get_data()) / 1024 / 1024:.2f} MB")
    return resp


@app.get("/api/round")
def round_detail():
    sid = request.args.get("session")
    raw_idx = request.args.get("index", "0")
    data = SESSIONS.get(sid)
    if data is None:
        return jsonify({"error": "会话已失效，请重新上传 demo"}), 400
    try:
        idx = int(raw_idx)
    except ValueError:
        return jsonify({"error": "回合编号无效"}), 400
    rd = next((x for x in data["rounds"] if x["index"] == idx), None)
    if rd is None:
        return jsonify({"error": "回合不存在"}), 404
    resp = jsonify(_json_safe(_slim_round(rd)))
    print(f"[round] {sid[:8]} #{idx}: {len(resp.get_data()) / 1024:.0f} KB")
    return resp


if __name__ == "__main__":
    print("CS2 Demo 分析器已启动，请在浏览器打开：http://127.0.0.1:8765")
    app.run(host="127.0.0.1", port=8765, debug=False, threaded=True)
