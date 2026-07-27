"""
Job poller — checks all confirmed ATS boards, filters to relevant AND
non-senior roles, diffs against the last snapshot, and reports newly-posted
jobs along with how long ago each was actually posted.
"""

import json
import os
import sys
from datetime import datetime, timezone
import requests

HEADERS = {"User-Agent": "Mozilla/5.0 (job-poller/1.0)"}
CONFIG_PATH = "companies.json"
STATE_PATH = "seen_jobs.json"


def parse_iso(ts):
    if not ts:
        return None
    try:
        return datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except Exception:
        return None


def parse_epoch_ms(ms):
    if not ms:
        return None
    try:
        return datetime.fromtimestamp(int(ms) / 1000, tz=timezone.utc)
    except Exception:
        return None


def age_str(posted_dt):
    if posted_dt is None:
        return "unknown age"
    now = datetime.now(timezone.utc)
    delta = now - posted_dt
    hours = delta.total_seconds() / 3600
    if hours < 1:
        return f"{int(delta.total_seconds() / 60)}m ago"
    if hours < 24:
        return f"{int(hours)}h ago"
    return f"{int(hours / 24)}d ago"


def fetch_greenhouse(slug):
    url = f"https://boards-api.greenhouse.io/v1/boards/{slug}/jobs?content=true"
    r = requests.get(url, headers=HEADERS, timeout=10)
    r.raise_for_status()
    jobs = r.json().get("jobs", [])
    return [
        {"id": str(j["id"]), "title": j["title"], "url": j.get("absolute_url", ""),
         "posted": parse_iso(j.get("updated_at"))}
        for j in jobs
    ]


def fetch_lever(slug):
    url = f"https://api.lever.co/v0/postings/{slug}?mode=json"
    r = requests.get(url, headers=HEADERS, timeout=10)
    r.raise_for_status()
    jobs = r.json()
    return [
        {"id": j["id"], "title": j["text"], "url": j.get("hostedUrl", ""),
         "posted": parse_epoch_ms(j.get("createdAt"))}
        for j in jobs
    ]


def fetch_ashby(slug):
    url = f"https://api.ashbyhq.com/posting-api/job-board/{slug}"
    r = requests.get(url, headers=HEADERS, timeout=10)
    r.raise_for_status()
    jobs = r.json().get("jobs", [])
    return [
        {"id": j["id"], "title": j["title"], "url": j.get("jobUrl", ""),
         "posted": parse_iso(j.get("publishedAt"))}
        for j in jobs
    ]


def fetch_smartrecruiters(slug):
    url = f"https://api.smartrecruiters.com/v1/companies/{slug}/postings"
    r = requests.get(url, headers=HEADERS, timeout=10)
    r.raise_for_status()
    jobs = r.json().get("content", [])
    return [
        {"id": j["id"], "title": j["name"], "url": j.get("ref", ""),
         "posted": parse_iso(j.get("releasedDate"))}
        for j in jobs
    ]


FETCHERS = {
    "greenhouse": fetch_greenhouse,
    "lever": fetch_lever,
    "ashby": fetch_ashby,
    "smartrecruiters": fetch_smartrecruiters,
}


def matches_keywords(title, keywords):
    t = title.lower()
    return any(k in t for k in keywords)


def is_senior(title, exclude_keywords):
    t = f" {title.lower()} "
    return any(k in t for k in exclude_keywords)


def load_state():
    if os.path.exists(STATE_PATH):
        with open(STATE_PATH) as f:
            return json.load(f)
    return {}


def save_state(state):
    with open(STATE_PATH, "w") as f:
        json.dump(state, f, indent=2)


def main():
    with open(CONFIG_PATH) as f:
        config = json.load(f)

    keywords = [k.lower() for k in config["keywords"]]
    exclude_keywords = [k.lower() for k in config.get("exclude_keywords", [])]
    state = load_state()
    new_matches = []

    for company in config["companies"]:
        name = company["name"]
        ats = company["ats"]
        slug = company["slug"]
        fetcher = FETCHERS.get(ats)
        if not fetcher:
            continue
        try:
            jobs = fetcher(slug)
        except Exception as e:
            print(f"[warn] {name} ({ats}/{slug}) fetch failed: {e}", file=sys.stderr)
            continue

        seen_ids = set(state.get(f"{ats}:{slug}", []))
        current_ids = set()

        for job in jobs:
            current_ids.add(job["id"])
            if job["id"] in seen_ids:
                continue
            if not matches_keywords(job["title"], keywords):
                continue
            if is_senior(job["title"], exclude_keywords):
                continue
            new_matches.append({
                "company": name, "title": job["title"],
                "url": job["url"], "posted": age_str(job["posted"]),
            })

        state[f"{ats}:{slug}"] = list(current_ids)

    save_state(state)
    for match in new_matches:
        print(json.dumps(match))
    if not new_matches:
        print("[info] no new matching non-senior postings this run", file=sys.stderr)


if __name__ == "__main__":
    main()
