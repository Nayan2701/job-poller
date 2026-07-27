"""
Job poller — checks all confirmed ATS boards, filters to relevant roles,
diffs against the last snapshot, and reports newly-posted jobs.

Usage (local):
    pip install requests
    python3 poller.py

Files it uses:
    companies.json   -- input, company/ATS/slug + keyword list (checked in)
    seen_jobs.json    -- state, job IDs already seen (auto-created/updated)

Exit behavior:
    Prints new matches to stdout as JSON lines. In the GitHub Actions
    workflow, these get piped into the Telegram notifier step.
"""

import json
import os
import sys
import requests

HEADERS = {"User-Agent": "Mozilla/5.0 (job-poller/1.0)"}
CONFIG_PATH = "companies.json"
STATE_PATH = "seen_jobs.json"


def fetch_greenhouse(slug):
    url = f"https://boards-api.greenhouse.io/v1/boards/{slug}/jobs?content=true"
    r = requests.get(url, headers=HEADERS, timeout=10)
    r.raise_for_status()
    jobs = r.json().get("jobs", [])
    return [
        {"id": str(j["id"]), "title": j["title"], "url": j.get("absolute_url", "")}
        for j in jobs
    ]


def fetch_lever(slug):
    url = f"https://api.lever.co/v0/postings/{slug}?mode=json"
    r = requests.get(url, headers=HEADERS, timeout=10)
    r.raise_for_status()
    jobs = r.json()
    return [
        {"id": j["id"], "title": j["text"], "url": j.get("hostedUrl", "")}
        for j in jobs
    ]


def fetch_ashby(slug):
    url = f"https://api.ashbyhq.com/posting-api/job-board/{slug}"
    r = requests.get(url, headers=HEADERS, timeout=10)
    r.raise_for_status()
    jobs = r.json().get("jobs", [])
    return [
        {"id": j["id"], "title": j["title"], "url": j.get("jobUrl", "")}
        for j in jobs
    ]


def fetch_smartrecruiters(slug):
    url = f"https://api.smartrecruiters.com/v1/companies/{slug}/postings"
    r = requests.get(url, headers=HEADERS, timeout=10)
    r.raise_for_status()
    jobs = r.json().get("content", [])
    return [
        {
            "id": j["id"],
            "title": j["name"],
            "url": j.get("ref", ""),
        }
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
            new_matches.append(
                {
                    "company": name,
                    "title": job["title"],
                    "url": job["url"],
                }
            )

        # Update state for this board regardless of keyword match,
        # so we don't re-flag irrelevant postings we've already seen.
        state[f"{ats}:{slug}"] = list(current_ids)

    save_state(state)

    for match in new_matches:
        print(json.dumps(match))

    if not new_matches:
        print("[info] no new matching postings this run", file=sys.stderr)


if __name__ == "__main__":
    main()
