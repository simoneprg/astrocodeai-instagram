"""AstroCodice Instagram publisher (GitHub Actions, public repo).

Reads a JSON queue of posts, publishes every post whose publish_at has passed
and that is not yet in instagram/published.json, via the official Instagram API
(Instagram Login). Standard library only.

Queue sources (first found wins):
  - env QUEUE_URL  (public JSON URL, e.g. a Google Drive download link)
  - instagram/queue.json in this repo

Queue item:
  {"id": "2026-10-23-it", "publish_at": "2026-10-23T17:30:00Z", "market": "IT",
   "type": "IMAGE" | "CAROUSEL" | "REELS",
   "media": ["https://...jpg", ...],      # public URLs (video for REELS), or
   "slides": [{"kicker","title","body"}],   # rendered here to media/<id>/ and served via raw.githubusercontent
   "cover": "https://...jpg",             # optional, REELS only
   "caption": "..."}
"""
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone

API = "https://graph.instagram.com/v21.0"
TOKEN = os.environ["IG_ACCESS_TOKEN"]
IG_ID = os.environ["IG_USER_ID"]
HERE = os.path.dirname(os.path.abspath(__file__))
LOG = os.path.join(HERE, "published.json")
DRY = os.environ.get("DRY_RUN") == "1"
ONLY = os.environ.get("ONLY_ID", "").strip()
REPO = os.environ.get("GITHUB_REPOSITORY", "simoneprg/astrocodeai-instagram")
ROOT = os.path.dirname(HERE)
RAW = f"https://raw.githubusercontent.com/{REPO}/main/media"


def render_and_push(items):
    """Render slide specs to JPEGs, commit them, return {id: [urls]}."""
    import subprocess
    sys.path.insert(0, HERE)
    import carousel
    urls, changed = {}, False
    for item in items:
        if item.get("media") or not item.get("slides"):
            continue
        out = os.path.join(ROOT, "media", item["id"])
        if not os.path.isdir(out):
            os.makedirs(out)
            n = len(item["slides"])
            for i, s in enumerate(item["slides"]):
                carousel.slide(s, i, n, item.get("handle", "@astrocodeai")).save(
                    os.path.join(out, f"slide{i + 1:02d}.jpg"), "JPEG", quality=92)
            changed = True
        files = sorted(f for f in os.listdir(out) if f.endswith(".jpg"))
        urls[item["id"]] = [f"{RAW}/{item['id']}/{f}" for f in files]
    if changed and not DRY:
        subprocess.run(["git", "add", "media"], cwd=ROOT, check=True)
        subprocess.run(["git", "commit", "-m", "instagram: render media [skip ci]"], cwd=ROOT, check=True)
        subprocess.run(["git", "push"], cwd=ROOT, check=True)
        time.sleep(20)  # let raw.githubusercontent.com serve the new files
    return urls


def call(method, path, **params):
    params["access_token"] = TOKEN
    data = urllib.parse.urlencode(params).encode()
    url = f"{API}/{path}"
    if method == "GET":
        req = urllib.request.Request(url + "?" + data.decode())
    else:
        req = urllib.request.Request(url, data=data, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return json.loads(r.read())
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"{method} {path}: {e.code} {e.read().decode()[:500]}")


def fetch_json(url):
    req = urllib.request.Request(url, headers={"User-Agent": "astrocodice-publisher"})
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.loads(r.read())


def wait_ready(container_id, timeout=600):
    start = time.time()
    while time.time() - start < timeout:
        st = call("GET", container_id, fields="status_code,status").get("status_code")
        if st == "FINISHED":
            return
        if st in ("ERROR", "EXPIRED"):
            raise RuntimeError(f"container {container_id} status {st}")
        time.sleep(10)
    raise RuntimeError(f"container {container_id} not ready after {timeout}s")


def publish(item):
    kind = item["type"].upper()
    media = item.get("media") or item["_urls"]
    caption = item.get("caption", "")
    if kind == "IMAGE":
        c = call("POST", f"{IG_ID}/media", image_url=media[0], caption=caption)["id"]
    elif kind == "CAROUSEL":
        children = []
        for url in media[:10]:
            is_video = url.lower().split("?")[0].endswith((".mp4", ".mov"))
            p = {"is_carousel_item": "true"}
            if is_video:
                p.update(media_type="VIDEO", video_url=url)
            else:
                p["image_url"] = url
            cid = call("POST", f"{IG_ID}/media", **p)["id"]
            wait_ready(cid)
            children.append(cid)
        c = call("POST", f"{IG_ID}/media", media_type="CAROUSEL",
                 children=",".join(children), caption=caption)["id"]
    elif kind == "REELS":
        p = dict(media_type="REELS", video_url=media[0], caption=caption, share_to_feed="true")
        if item.get("cover"):
            p["cover_url"] = item["cover"]
        c = call("POST", f"{IG_ID}/media", **p)["id"]
    else:
        raise ValueError(f"unknown type {kind}")
    wait_ready(c)
    return call("POST", f"{IG_ID}/media_publish", creation_id=c)["id"]


def main():
    queue_url = os.environ.get("QUEUE_URL", "").strip()
    if queue_url:
        queue = fetch_json(queue_url)
    else:
        with open(os.path.join(ROOT, "content", "queue.json")) as f:
            queue = json.load(f)
    if isinstance(queue, dict):
        queue = queue.get("posts", [])
    log = {}
    if os.path.exists(LOG):
        with open(LOG) as f:
            log = json.load(f)
    now = datetime.now(timezone.utc)
    failures = 0
    def is_due(it):
        if it["id"] in log and (log[it["id"]].get("media_id") or log[it["id"]].get("attempts", 0) >= 3):
            return False
        if ONLY:
            return it["id"] == ONLY
        return datetime.fromisoformat(it["publish_at"].replace("Z", "+00:00")) <= now
    due_items = [it for it in queue if is_due(it)]
    rendered = render_and_push(due_items)
    for it in due_items:
        if it["id"] in rendered:
            it["_urls"] = rendered[it["id"]]
    for item in sorted(due_items, key=lambda x: x["publish_at"]):
        pid = item["id"]
        print(f"-> {pid} [{item.get('market','')}] {item['type']}")
        if DRY:
            continue
        try:
            media_id = publish(item)
            log[pid] = {"media_id": media_id, "published_at": now.isoformat(),
                        "market": item.get("market"), "type": item["type"]}
            print(f"   published media_id={media_id}")
        except Exception as e:  # keep going with the rest of the queue
            failures += 1
            prev = log.get(pid, {})
            log[pid] = {"attempts": prev.get("attempts", 0) + 1, "last_error": str(e)[:300],
                        "last_try": now.isoformat()}
            print(f"   FAILED: {e}", file=sys.stderr)
    with open(LOG, "w") as f:
        json.dump(log, f, indent=2, sort_keys=True)
    limit = call("GET", f"{IG_ID}/content_publishing_limit", fields="quota_usage")
    print("quota:", limit)
    sys.exit(1 if failures else 0)


if __name__ == "__main__":
    main()
