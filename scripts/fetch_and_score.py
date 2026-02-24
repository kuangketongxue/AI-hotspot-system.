#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# Fetch -> score -> dedupe(rapidfuzz) -> summarize(TextRank) -> curate -> emit web/public/{feed.json,sources.json,site.json}

import os, re, json, time, math, hashlib
from datetime import datetime, timezone
from urllib.parse import quote_plus
import yaml, requests, feedparser
from bs4 import BeautifulSoup
from dateutil import parser as dtp
from rapidfuzz import fuzz
import networkx as nx
import jieba

ROOT = os.path.dirname(os.path.dirname(__file__))
OUT_DIR = os.path.join(ROOT, "web", "public")
os.makedirs(OUT_DIR, exist_ok=True)

def now_iso():
    return datetime.now(timezone.utc).isoformat()

def sha1(s: str) -> str:
    return hashlib.sha1(s.encode("utf-8", "ignore")).hexdigest()[:16]

def to_ts(s):
    try: return int(dtp.parse(s).timestamp())
    except Exception: return int(time.time())

def clean_text(x):
    if not x: return ""
    x = re.sub(r"\s+", " ", BeautifulSoup(x, "lxml").get_text(" ").strip())
    return x

def norm_title(t):
    t = t.lower()
    t = re.sub(r"[^a-z0-9\u4e00-\u9fff]+", " ", t)
    toks = [w for w in t.split() if w not in {"the","a","to","of","for","and","with"}]
    return " ".join(toks[:30])

def clamp(x, lo=0, hi=100):
    return max(lo, min(hi, int(round(x))))

# ---------------- TextRank summarizer ----------------
_SENT_SPLIT = re.compile(r"(?<=[。！？!?])\s*|(?<=[\.\?\!])\s+")
def sent_split(text):
    text = clean_text(text)
    if not text: return []
    sents = [s.strip() for s in _SENT_SPLIT.split(text) if s and len(s.strip())>2]
    if not sents and text:
        sents = [text[:120]]
    return sents

def tokenize(s):
    if re.search(r"[\u4e00-\u9fff]", s):
        return [w for w in jieba.cut(s) if re.match(r"[\u4e00-\u9fffA-Za-z0-9]+$", w)]
    else:
        return re.findall(r"[A-Za-z0-9]+", s.lower())

def sent_sim(a, b):
    ta, tb = set(tokenize(a)), set(tokenize(b))
    if not ta or not tb: return 0.0
    return len(ta & tb) / float(max(1, min(len(ta), len(tb))))

def textrank_summary(text, n=2):
    sents = sent_split(text)
    if not sents: return ""
    if len(sents) <= n: return " ".join(sents)
    g = nx.Graph()
    for i in range(len(sents)):
        g.add_node(i)
    for i in range(len(sents)):
        for j in range(i+1, len(sents)):
            w = sent_sim(sents[i], sents[j])
            if w > 0.05:
                g.add_edge(i, j, weight=w)
    pr = nx.pagerank(g, weight="weight")
    top_idx = sorted(range(len(sents)), key=lambda i: pr.get(i,0), reverse=True)[:n]
    top_idx.sort()
    return " ".join(sents[i] for i in top_idx)

# ---------------- domain dictionaries ----------------
AI_TERMS = {
    "ai": 3, "artificial intelligence": 4, "machine learning": 5, "ml": 3, "deep learning": 5,
    "llm": 6, "large language model": 6, "agent": 4, "rlhf": 4, "finetune": 5,
    "inference": 4, "quantization": 4, "benchmark": 5, "sota": 6,
    "模型": 6, "发布": 6, "大模型": 6, "评测": 5, "微调": 5, "蒸馏": 4, "推理": 4, "开源": 5,
}
VENDORS = ["OpenAI","Anthropic","Google","DeepMind","Mistral","Meta","Microsoft","NVIDIA",
           "Midjourney","Stability","StabilityAI","HuggingFace","Qwen","阿里","百度","智谱","清华","字节","腾讯","xAI","Moonshot","MiniMax","DeepSeek","Llama","Gemini","Claude","GPT","Mixtral","SDXL"]
VENDOR_SET = set([v.lower() for v in VENDORS])
STRONG_RELEASE = [r"\brelease\b", r"\bv\d+(\.\d+)?\b", r"发布", r"上线", r"\blaunch", r"正式版", r"open-source", r"开源", r"支持.*token", r"benchmark", r"SOTA", r"性能提升", r"升级", r"新模型"]

CATEGORY_RULES = [
    ("model_release", [r"发布", r"release", r"新模型", r"v\d", r"升级", r"Claude|GPT|Llama|Gemini|Qwen|Mistral"]),
    ("paper", [r"paper", r"arxiv", r"论文", r"\[paper\]"]),
    ("benchmark", [r"benchmark", r"评测", r"SOTA"]),
    ("industry", [r"投融资|并购|合作|生态|大会|峰会|roadmap"]),
    ("devtool", [r"SDK|API|Tool|工具|库|插件|cli|notebook|jupyter|datasets|inference server"]),
]

def match_any(patterns, text):
    for p in patterns:
        if re.search(p, text, re.I): return True
    return False

def detect_category(title, content):
    t = f"{title} {content}"
    for cat, pats in CATEGORY_RULES:
        if match_any(pats, t): return cat
    return "default"

def vendor_tags(text):
    tags = []
    low = text.lower()
    for v in VENDORS:
        if v.lower() in low: tags.append(v)
    return list(dict.fromkeys(tags))[:5]

def ai_relevance(title, content):
    text = f"{title} {content}".lower()
    score = 0
    for k, w in AI_TERMS.items():
        if k.lower() in text: score += w
    for v in VENDORS:
        if v.lower() in text: score += 4
    return clamp(100 * min(1.0, score / 30.0))

def importance(title, content, meta):
    e = 0
    if meta.get("upvotes"): e += min(50, math.log1p(meta["upvotes"]) * 10)
    if meta.get("retweets"): e += min(40, math.log1p(meta["retweets"]) * 10)
    if meta.get("likes"): e += min(20, math.log1p(meta["likes"]) * 6)
    if meta.get("views"): e += min(40, math.log1p(meta["views"]) * 6)
    b = 0
    low = f"{title} {content}".lower()
    b += 15 if any(v in low for v in VENDOR_SET) else 0
    n = 0
    n += 15 if match_any(STRONG_RELEASE, low) else 0
    n += 8 if "benchmark" in low or "评测" in low else 0
    raw = 0.6*e + 0.25*b + 0.15*n
    return clamp(raw)

def is_model_selected(title, content):
    low = f"{title} {content}".lower()
    return match_any(STRONG_RELEASE, low)

def extract_tags(title, content):
    tags = vendor_tags(f"{title} {content}")
    if re.search(r"\bagent\b|智能体|Agent", f"{title} {content}", re.I): tags.append("Agent")
    if re.search(r"benchmark|评测|SOTA", f"{title} {content}", re.I): tags.append("Benchmark")
    if re.search(r"开源|open-source", f"{title} {content}", re.I): tags.append("开源")
    return list(dict.fromkeys(tags))[:6]

# ---------------- fetchers ----------------
HEADERS = {"User-Agent": "hot-ai/1.1 (github actions; contact: none)"}

def fetch_youtube(cfg):
    items = []
    channels = cfg.get("channels", [])
    for cid in channels:
        url = f"https://www.youtube.com/feeds/videos.xml?channel_id={cid}"
        feed = feedparser.parse(url)
        for e in feed.entries:
            title = clean_text(e.get("title"))
            link = e.get("link")
            published = e.get("published") or e.get("updated") or now_iso()
            desc = clean_text(e.get("summary", ""))
            thumb = None
            media = e.get("media_thumbnail") or []
            if media:
                thumb = media[0].get("url")
            items.append({
                "id": sha1(f"yt:{link}"),
                "source": "YouTube",
                "url": link,
                "title": title,
                "content": desc,
                "published": to_ts(published),
                "author": e.get("author", ""),
                "metrics": {},
                "thumbnail": thumb,
            })
    return items

def fetch_reddit(cfg):
    subs = "+".join(cfg.get("subreddits", []))
    sort = cfg.get("sort", "new")
    limit = int(cfg.get("limit", 30))
    if not subs: return []
    url = f"https://www.reddit.com/r/{subs}/{sort}.json?limit={limit}"
    r = requests.get(url, headers=HEADERS, timeout=20)
    r.raise_for_status()
    j = r.json()
    items = []
    for c in j.get("data",{}).get("children", []):
        d = c.get("data", {})
        title = clean_text(d.get("title"))
        selftext = clean_text(d.get("selftext", ""))
        link = f"https://www.reddit.com{d.get('permalink')}"
        ups = d.get("ups", 0)
        thumb = d.get("thumbnail") if isinstance(d.get("thumbnail"), str) else None
        items.append({
            "id": sha1(f"rd:{d.get('id')}"),
            "source": "Reddit",
            "url": link,
            "title": title,
            "content": selftext,
            "published": int(d.get("created_utc", time.time())),
            "author": d.get("author", ""),
            "metrics": {"upvotes": ups},
            "thumbnail": thumb if thumb and thumb.startswith("http") else None,
        })
    return items

def _first_alive_host(hosts):
    for h in hosts:
        try:
            r = requests.get(h, headers=HEADERS, timeout=6)
            if r.status_code < 500: return h
        except Exception:
            continue
    return hosts[0] if hosts else "https://nitter.net"

def fetch_twitter(cfg):
    users = cfg.get("users", [])
    searches = cfg.get("searches", [])
    hosts = cfg.get("nitter_hosts", ["https://nitter.net"])
    base = _first_alive_host(hosts)
    items = []
    for u in users:
        url = f"{base}/{u}/rss"
        feed = feedparser.parse(url)
        for e in feed.entries:
            title = clean_text(e.get("title", ""))
            link = e.get("link")
            published = e.get("published") or now_iso()
            desc = clean_text(e.get("summary", ""))
            items.append({
                "id": sha1(f"tw:{link}"),
                "source": "Twitter",
                "url": link,
                "title": title,
                "content": desc,
                "published": to_ts(published),
                "author": u,
                "metrics": {},
                "thumbnail": None,
            })
    for q in searches:
        url = f"{base}/search/rss?f=tweets&q={quote_plus(q)}"
        feed = feedparser.parse(url)
        for e in feed.entries[:20]:
            title = clean_text(e.get("title",""))
            link = e.get("link")
            desc = clean_text(e.get("summary",""))
            pub = e.get("published") or now_iso()
            items.append({
                "id": sha1(f"twq:{link}"),
                "source": "Twitter",
                "url": link,
                "title": title,
                "content": desc,
                "published": to_ts(pub),
                "author": "",
                "metrics": {},
                "thumbnail": None,
            })
    return items

# ---------------- pipeline ----------------
def rf_sim(a, b):
    return fuzz.token_set_ratio(a, b)

def dedupe(items):
    items = sorted(items, key=lambda x: (x["published"], x.get("importance",0)), reverse=True)
    kept = []
    norms = []
    for it in items:
        t = norm_title(it["title"])
        if not t: continue
        drop = False
        for s in norms:
            if rf_sim(t, s) >= 88:
                drop = True; break
        if not drop:
            kept.append(it); norms.append(t)
    return kept

def categorize_and_score(items, rules):
    out = []
    for it in items:
        title = it["title"]; content = it["content"]
        cat = detect_category(title, content)
        ai = ai_relevance(title, content)
        imp = importance(title, content, it.get("metrics", {}))
        sel = is_model_selected(title, content)
        tags = extract_tags(title, content)
        summary = textrank_summary(f"{title}。{content}", n=2)
        it.update({
            "category": cat,
            "ai_relevance": ai,
            "importance": imp,
            "model_selected": sel,
            "tags": tags,
            "summary": summary,
        })
        out.append(it)
    floor_map = rules.get("importance_floor", {})
    ai_min = int(rules.get("ai_relevance_min", 60))
    enforce = bool(rules.get("enforce_model_selected", False))
    curated = []
    for it in out:
        if it["ai_relevance"] < ai_min: continue
        if enforce and not it["model_selected"]: continue
        floor = int(floor_map.get(it["category"], floor_map.get("default", 75)))
        if it["importance"] < floor: continue
        curated.append(it)
    return curated, out

def group_for_frontend(items):
    for it in items:
        dt = datetime.fromtimestamp(it["published"], timezone.utc)
        try:
            it["date_str"] = f"{dt.month}月{dt.day}日"
        except Exception:
            it["date_str"] = dt.strftime("%m/%d")
        it["time_str"] = dt.strftime("%H:%M")
    return items

def write_sources_json(cfg):
    src = {
        "youtube": {"channels": cfg.get("youtube", {}).get("channels", [])},
        "reddit": {"subreddits": cfg.get("reddit", {}).get("subreddits", [])},
        "twitter": {"users": cfg.get("twitter", {}).get("users", [])},
    }
    with open(os.path.join(OUT_DIR, "sources.json"), "w", encoding="utf-8") as f:
        json.dump(src, f, ensure_ascii=False, indent=2)

def write_site_json():
    repo = os.environ.get("GITHUB_REPOSITORY","")
    owner, name = (repo.split("/",1)+[""])[:2] if repo else ("","")
    meta = {"repo": {"owner": owner, "name": name}}
    with open(os.path.join(OUT_DIR, "site.json"), "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False)

def main():
    cfg = yaml.safe_load(open(os.path.join(ROOT, "scripts", "config.yml"), "r", encoding="utf-8"))
    all_items = []
    try: all_items += fetch_youtube(cfg.get("youtube", {}))
    except Exception as e: print("YouTube fetch error:", e)
    try: all_items += fetch_reddit(cfg.get("reddit", {}))
    except Exception as e: print("Reddit fetch error:", e)
    try: all_items += fetch_twitter(cfg.get("twitter", {}))
    except Exception as e: print("Twitter fetch error:", e)

    _, scored_tmp = categorize_and_score(all_items, cfg.get("rules", {}))
    scored_tmp = sorted(scored_tmp, key=lambda x: (x["published"], x["importance"]), reverse=True)
    scored_tmp = dedupe(scored_tmp)

    curated, _ = categorize_and_score(scored_tmp, cfg.get("rules", {}))
    curated = sorted(curated, key=lambda x: (x["published"], x["importance"]), reverse=True)
    curated = group_for_frontend(curated)

    out = {
        "generated_at": now_iso(),
        "total_fetched": len(all_items),
        "total_curated": len(curated),
        "items": curated[:200],
    }
    with open(os.path.join(OUT_DIR, "feed.json"), "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False)
    write_sources_json(cfg)
    write_site_json()
    print(f"done. curated={len(curated)} -> web/public/feed.json")

if __name__ == "__main__":
    main()
