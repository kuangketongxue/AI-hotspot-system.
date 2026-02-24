#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# Per-user persistence via GitHub Issues -> web/public/userdata/<login>.json

import os, json, sys, yaml, time
from datetime import datetime, timezone

ROOT = os.path.dirname(os.path.dirname(__file__))
USERDIR = os.path.join(ROOT, "web", "public", "userdata")
CONFIG = os.path.join(ROOT, "scripts", "config.yml")
os.makedirs(USERDIR, exist_ok=True)

def load_json(p, default):
    if not os.path.exists(p): return default
    try:
        return json.load(open(p, "r", encoding="utf-8"))
    except Exception:
        return default

def save_json(p, data):
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

def now_iso():
    return datetime.now(timezone.utc).isoformat()

def user_file(login:str) -> str:
    safe = "".join(ch for ch in login if ch.isalnum() or ch in "-_").strip() or "_unknown"
    return os.path.join(USERDIR, f"{safe}.json")

def handle_user_action(payload, login:str):
    path = user_file(login)
    data = load_json(path, {"updated_at": "", "favorites": {}, "ratings": {}})
    act = payload.get("action")
    itid = payload.get("id")
    if not itid: return "no-id"
    if act == "fav":
        data["favorites"][itid] = {
            "id": itid,
            "url": payload.get("url",""),
            "title": payload.get("title",""),
            "source": payload.get("source",""),
            "rating": payload.get("rating", None),
            "time": int(time.time())
        }
    elif act == "unfav":
        data["favorites"].pop(itid, None)
    elif act == "rate":
        rating = int(payload.get("rating", 0))
        rating = max(0, min(100, rating))
        data["ratings"][itid] = rating
        if itid in data["favorites"]:
            data["favorites"][itid]["rating"] = rating
    else:
        return "unknown-action"
    data["updated_at"] = now_iso()
    save_json(path, data)
    return f"ok:{login}"

def handle_update_sources(payload):
    with open(CONFIG, "r", encoding="utf-8") as f:
        cfg = yaml.safe_load(f)
    y = payload.get("youtube", {})
    r = payload.get("reddit", {})
    t = payload.get("twitter", {})
    if "channels" in y: cfg.setdefault("youtube", {})["channels"] = [str(x).strip() for x in y.get("channels", []) if str(x).strip()]
    if "subreddits" in r: cfg.setdefault("reddit", {})["subreddits"] = [str(x).strip() for x in r.get("subreddits", []) if str(x).strip()]
    if "users" in t: cfg.setdefault("twitter", {})["users"] = [str(x).strip() for x in t.get("users", []) if str(x).strip()]
    with open(CONFIG, "w", encoding="utf-8") as f:
        yaml.safe_dump(cfg, f, allow_unicode=True, sort_keys=False)
    src = {
        "youtube": {"channels": cfg.get("youtube", {}).get("channels", [])},
        "reddit": {"subreddits": cfg.get("reddit", {}).get("subreddits", [])},
        "twitter": {"users": cfg.get("twitter", {}).get("users", [])},
    }
    save_json(os.path.join(ROOT, "web", "public", "sources.json"), src)
    return "ok"

def main():
    payload_path = sys.argv[1] if len(sys.argv) > 1 else ""
    if not payload_path or not os.path.exists(payload_path):
        print("missing issue payload json"); sys.exit(1)

    issue = json.load(open(payload_path, "r", encoding="utf-8"))
    gh_login = (issue.get("user") or {}).get("login") or "_unknown"  # 以真实发起者为准
    body = (issue.get("body") or "").strip()

    try:
        data = json.loads(body)
    except Exception:
        print("body is not valid json"); sys.exit(0)  # 忽略

    action = data.get("action", "")
    if action in {"fav","unfav","rate"}:
        res = handle_user_action(data, gh_login)
    elif action == "update_sources":
        res = handle_update_sources(data.get("sources", {}))
    else:
        res = "ignored"
    print("sync_result:", res)

if __name__ == "__main__":
    main()
