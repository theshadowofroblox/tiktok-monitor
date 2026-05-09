#!/usr/bin/env python3
"""
TikTok Monitor — Discord Notifier
===================================
Monitors a TikTok account for new videos and sends a Discord
webhook notification after a configurable delay.

Runs forever — deploy to Railway, Render, or any VPS for
24/7 monitoring even when your laptop is off.

Setup:
    pip install requests beautifulsoup4

Run locally:
    python tiktok_monitor.py

Or set environment variables and deploy to Railway/Render.
"""

import json
import os
import time
from datetime import datetime
from pathlib import Path

import requests

# ─────────────────────────────────────────────
#  CONFIG — edit these OR set as env variables
# ─────────────────────────────────────────────

TIKTOK_USERNAME   = os.getenv("TIKTOK_USERNAME",   "@upminaa.cos")        # who to watch
DISCORD_WEBHOOK   = os.getenv("DISCORD_WEBHOOK",   "https://discord.com/api/webhooks/1502607374450692147/pbmj4fFkiM30yHK7mWtlY_G2i8CmZ3URfDyEhiMtssrF8QXnkF_Wava4IJJPx9CwnA9m") # your Discord webhook
POLL_INTERVAL     = int(os.getenv("POLL_INTERVAL", "60"))              # seconds between checks
REPOST_DELAY      = int(os.getenv("REPOST_DELAY",  "180"))             # seconds before notifying (3 min)
STATE_FILE        = "seen_videos.json"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "en-US,en;q=0.9",
}

# ─────────────────────────────────────────────
#  STATE
# ─────────────────────────────────────────────

def load_seen() -> set:
    if Path(STATE_FILE).exists():
        with open(STATE_FILE) as f:
            return set(json.load(f))
    return set()


def save_seen(seen: set):
    with open(STATE_FILE, "w") as f:
        json.dump(list(seen), f)

# ─────────────────────────────────────────────
#  TIKTOK SCRAPING
# ─────────────────────────────────────────────

def fetch_video_ids(username: str) -> list:
    """
    Fetches public video IDs from a TikTok profile page.
    Returns list of dicts with id and url.

    Note: TikTok blocks scraping aggressively. If this stops
    working, switch to Apify's TikTok scraper API (cheap & reliable).
    Apify: https://apify.com/clockworks/tiktok-scraper
    """
    user = username.lstrip("@")
    url  = f"https://www.tiktok.com/@{user}"

    try:
        resp = requests.get(url, headers=HEADERS, timeout=20)
        resp.raise_for_status()
    except requests.RequestException as e:
        log(f"WARN  Could not reach TikTok: {e}")
        return []

    from bs4 import BeautifulSoup
    soup = BeautifulSoup(resp.text, "html.parser")

    videos   = []
    seen_ids = set()

    for a in soup.find_all("a", href=True):
        href = a["href"]
        if f"/@{user}/video/" in href:
            vid_id = href.split("/video/")[-1].split("?")[0]
            if vid_id.isdigit() and vid_id not in seen_ids:
                seen_ids.add(vid_id)
                videos.append({
                    "id":  vid_id,
                    "url": f"https://www.tiktok.com/@{user}/video/{vid_id}",
                })

    return videos

# ─────────────────────────────────────────────
#  DISCORD NOTIFICATION
# ─────────────────────────────────────────────

def send_discord(video: dict, username: str, delay: int):
    """Sends a rich embed notification to your Discord webhook."""
    user = username.lstrip("@")
    mins = delay // 60
    secs = delay % 60

    payload = {
        "embeds": [
            {
                "title": "New TikTok Video Detected!",
        "description": (
        f"@everyone\n\n"
        f"**@{user}** just posted a new video.\n\n"
        f"[Open Video]({video['url']})"
            ),
                "color": 0xFE2C55,
                "fields": [
                    {
                        "name": "Detected after delay",
                        "value": f"{mins}m {secs}s",
                        "inline": True,
                    },
                    {
                        "name": "Video ID",
                        "value": f"`{video['id']}`",
                        "inline": True,
                    },
                    {
                        "name": "Direct Link",
                        "value": video["url"],
                        "inline": False,
                    },
                ],
                "footer": {
                    "text": f"TikTok Monitor • {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
                },
            }
        ]
    }

    try:
        r = requests.post(DISCORD_WEBHOOK, json=payload, timeout=10)
        if r.status_code in (200, 204):
            log(f"OK    Discord notified for video {video['id']}")
        else:
            log(f"WARN  Discord returned {r.status_code}: {r.text[:100]}")
    except requests.RequestException as e:
        log(f"ERR   Discord webhook failed: {e}")

# ─────────────────────────────────────────────
#  LOGGING
# ─────────────────────────────────────────────

def log(msg: str):
    print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] {msg}", flush=True)

# ─────────────────────────────────────────────
#  MAIN LOOP
# ─────────────────────────────────────────────

def main():
    log("START TikTok Monitor")
    log(f"      Watching  : {TIKTOK_USERNAME}")
    log(f"      Webhook   : {'set' if DISCORD_WEBHOOK != 'YOUR_WEBHOOK_URL' else 'NOT SET'}")
    log(f"      Poll      : every {POLL_INTERVAL}s")
    log(f"      Delay     : {REPOST_DELAY}s ({REPOST_DELAY // 60}m {REPOST_DELAY % 60}s)")

    if DISCORD_WEBHOOK == "YOUR_WEBHOOK_URL":
        log("WARN  Discord webhook not configured. Edit DISCORD_WEBHOOK at the top of this file.")

    seen    = load_seen()
    pending = {}  # video_id -> { video, detected_at }

    # First run: seed existing videos so we only alert on NEW ones
    if not seen:
        log("INFO  First run — seeding existing videos so we don't re-alert old ones...")
        initial = fetch_video_ids(TIKTOK_USERNAME)
        seen    = {v["id"] for v in initial}
        save_seen(seen)
        log(f"INFO  Seeded {len(seen)} existing video(s). Now watching for new ones.")

    log("INFO  Monitoring started. Press Ctrl+C to stop.\n")

    while True:
        try:
            # Check for new videos
            log(f"CHECK Fetching {TIKTOK_USERNAME}...")
            videos    = fetch_video_ids(TIKTOK_USERNAME)
            new_found = [
                v for v in videos
                if v["id"] not in seen and v["id"] not in pending
            ]

            for video in new_found:
                log(f"NEW   Video detected: {video['id']} — waiting {REPOST_DELAY}s before notifying")
                pending[video["id"]] = {
                    "video":       video,
                    "detected_at": time.time(),
                }
                seen.add(video["id"])
                save_seen(seen)

            if not new_found:
                log("NONE  No new videos.")

            # Fire any notifications whose delay has elapsed
            now     = time.time()
            to_fire = [
                vid_id for vid_id, item in pending.items()
                if now - item["detected_at"] >= REPOST_DELAY
            ]

            for vid_id in to_fire:
                item = pending.pop(vid_id)
                log(f"PING  Delay elapsed — sending Discord notification for {vid_id}...")
                send_discord(item["video"], TIKTOK_USERNAME, REPOST_DELAY)

        except KeyboardInterrupt:
            log("STOP  Monitor stopped.")
            break
        except Exception as e:
            log(f"ERR   Unexpected error: {e}")

        time.sleep(POLL_INTERVAL)


if __name__ == "__main__":
    main()
