"""AstroCodice TikTok publisher (GitHub Actions, PC off).

Same queue as Instagram (content/queue.json + public Drive folder). For every due
post not yet in tiktok/published.json it builds a 1080x1920 video from the slides
(with music) and posts it with the Content Posting API (FILE_UPLOAD, Direct Post).

Secrets:   TIKTOK_CLIENT_KEY, TIKTOK_CLIENT_SECRET
Variables: TIKTOK_PRIVACY (default SELF_ONLY until the app is audited, then PUBLIC_TO_EVERYONE)
           TIKTOK_MARKETS (default "IT,US")
Token:     tiktok/token.enc = refresh token encrypted with the client secret
           (written by tiktok-auth.yml, rotated here on every run).

Usage: python3 tiktok/publish_tiktok.py            normal run
       python3 tiktok/publish_tiktok.py auth CODE  exchange an authorization code
"""
import json
import os
import random
import re
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
TOKEN_FILE = os.path.join(HERE, "token.enc")
LOG = os.path.join(HERE, "published.json")
API = "https://open.tiktokapis.com/v2"
REDIRECT_URI = "https://astrocodice.com/tiktok"
KEY = os.environ.get("TIKTOK_CLIENT_KEY", "")
SECRET = os.environ.get("TIKTOK_CLIENT_SECRET", "")
PRIVACY = os.environ.get("TIKTOK_PRIVACY", "").strip() or "SELF_ONLY"
MARKETS = {m.strip().upper() for m in (os.environ.get("TIKTOK_MARKETS", "") or "IT,US").split(",")}
DRY = os.environ.get("DRY_RUN") == "1"
ONLY = os.environ.get("ONLY_ID", "").strip()


# ---------- token storage ----------
def _openssl(args, data):
    env = dict(os.environ, TTPASS=SECRET)
    return subprocess.run(["openssl", "enc", "-aes-256-cbc", "-pbkdf2", "-iter", "200000", "-a", "-A",
                           "-pass", "env:TTPASS", *args], input=data, capture_output=True, env=env,
                          check=True).stdout


def save_refresh(tok):
    with open(TOKEN_FILE, "wb") as f:
        f.write(_openssl([], tok.encode()) + b"\n")


def load_refresh():
    with open(TOKEN_FILE, "rb") as f:
        return _openssl(["-d"], f.read().strip() + b"\n").decode().strip()


def git_commit(msg, *paths):
    if DRY:
        return
    subprocess.run(["git", "add", *paths], cwd=ROOT, check=True)
    if subprocess.run(["git", "diff", "--cached", "--quiet"], cwd=ROOT).returncode == 0:
        return
    subprocess.run(["git", "commit", "-m", msg], cwd=ROOT, check=True)
    for _ in range(3):
        if subprocess.run(["git", "pull", "--rebase"], cwd=ROOT).returncode == 0 and \
                subprocess.run(["git", "push"], cwd=ROOT).returncode == 0:
            return
        time.sleep(5)
    raise RuntimeError("git push failed")


# ---------- HTTP ----------
def oauth(**form):
    form.update(client_key=KEY, client_secret=SECRET)
    req = urllib.request.Request(f"{API}/oauth/token/", data=urllib.parse.urlencode(form).encode(),
                                 headers={"Content-Type": "application/x-www-form-urlencoded"}, method="POST")
    with urllib.request.urlopen(req, timeout=60) as r:
        data = json.loads(r.read())
    if "access_token" not in data:
        raise RuntimeError(f"oauth error: {data.get('error')} {data.get('error_description')}")
    return data


def api(path, token, body=None):
    req = urllib.request.Request(f"{API}/{path}", data=json.dumps(body or {}).encode(), method="POST",
                                 headers={"Authorization": f"Bearer {token}",
                                          "Content-Type": "application/json; charset=UTF-8"})
    try:
        with urllib.request.urlopen(req, timeout=120) as r:
            data = json.loads(r.read())
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"{path}: {e.code} {e.read().decode()[:500]}")
    err = data.get("error", {})
    if err.get("code") not in (None, "ok"):
        raise RuntimeError(f"{path}: {err}")
    return data.get("data", {})


def access_token():
    data = oauth(grant_type="refresh_token", refresh_token=load_refresh())
    if data.get("refresh_token"):
        save_refresh(data["refresh_token"])  # TikTok may rotate it
        git_commit("tiktok: rotate token [skip ci]", "tiktok/token.enc")
    return data["access_token"]


# ---------- queue ----------
def fetch_json(url):
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.loads(r.read())


def load_queue():
    with open(os.path.join(ROOT, "content", "queue.json")) as f:
        q = json.load(f)
    q = q.get("posts", []) if isinstance(q, dict) else q
    folder = os.environ.get("DRIVE_FOLDER_ID", "").strip()
    if folder:
        try:
            req = urllib.request.Request(f"https://drive.google.com/embeddedfolderview?id={folder}",
                                         headers={"User-Agent": "Mozilla/5.0"})
            html = urllib.request.urlopen(req, timeout=60).read().decode("utf-8", "replace")
            for fid in dict.fromkeys(re.findall(r"/file/d/([A-Za-z0-9_-]{20,})", html)):
                try:
                    d = fetch_json(f"https://drive.google.com/uc?export=download&id={fid}")
                    q += d.get("posts", []) if isinstance(d, dict) else d
                except Exception as e:
                    print(f"skip drive file {fid}: {e}", file=sys.stderr)
        except Exception as e:
            print(f"drive folder unreadable: {e}", file=sys.stderr)
    out = {}
    for it in q:
        if it.get("id"):
            out[it["id"]] = it
    return list(out.values())


# ---------- video ----------
def slide_paths(item):
    out = os.path.join(ROOT, "media", item["id"])
    files = sorted(f for f in os.listdir(out) if f.startswith("slide") and f.endswith(".jpg")) \
        if os.path.isdir(out) else []
    if files:
        return [os.path.join(out, f) for f in files]
    sys.path.insert(0, os.path.join(ROOT, "instagram"))
    import carousel
    tmp = os.path.join("/tmp", "tt_" + item["id"])
    os.makedirs(tmp, exist_ok=True)
    slides = [dict(s) for s in item["slides"]]
    if not slides[-1].get("rows"):
        slides[-1].setdefault("badge", "it" if item.get("market", "IT").upper() == "IT" else "en")
    paths = []
    for i, s in enumerate(slides):
        p = os.path.join(tmp, f"slide{i + 1:02d}.jpg")
        carousel.slide(s, i, len(slides), item.get("handle", "@astrocodeai")).save(p, "JPEG", quality=92)
        paths.append(p)
    return paths


def make_video(paths, out_mp4, seed):
    """Slides -> 1080x1920 MP4 (slide centered on the dark background) with music."""
    import glob
    n = len(paths)
    durs = [4.0] + [7.0] * (n - 2) + [5.0] if n > 1 else [8.0]
    total = sum(durs)
    tracks = sorted(glob.glob(os.path.join(ROOT, "assets", "music", "*.mp3")))
    lst = out_mp4 + ".txt"
    with open(lst, "w") as f:
        for p, d in zip(paths, durs):
            f.write(f"file '{os.path.abspath(p)}'\nduration {d}\n")
        f.write(f"file '{os.path.abspath(paths[-1])}'\n")
    cmd = ["ffmpeg", "-y", "-v", "error", "-f", "concat", "-safe", "0", "-i", lst]
    if tracks:
        cmd += ["-ss", "8", "-i", random.Random(seed).choice(tracks)]
    cmd += ["-vf", "scale=1080:1350,pad=1080:1920:0:285:color=0x06060a,fps=30,format=yuv420p", "-t", str(total)]
    if tracks:
        cmd += ["-af", f"afade=t=in:d=0.5,afade=t=out:st={total - 2}:d=2", "-c:a", "aac", "-b:a", "128k",
                "-ar", "48000", "-map", "0:v", "-map", "1:a"]
    else:
        cmd += ["-an"]
    cmd += ["-c:v", "libx264", "-preset", "medium", "-crf", "21", "-profile:v", "high",
            "-movflags", "+faststart", out_mp4]
    subprocess.run(cmd, check=True)
    os.remove(lst)


# ---------- publish ----------
def post_video(token, path, caption):
    info = api("post/publish/creator_info/query/", token)
    options = info.get("privacy_level_options") or []
    privacy = PRIVACY if PRIVACY in options or not options else ("SELF_ONLY" if "SELF_ONLY" in options else options[0])
    size = os.path.getsize(path)
    body = {
        "post_info": {"title": caption[:2200], "privacy_level": privacy, "disable_comment": False,
                      "disable_duet": False, "disable_stitch": False, "video_cover_timestamp_ms": 1000},
        "source_info": {"source": "FILE_UPLOAD", "video_size": size, "chunk_size": size, "total_chunk_count": 1},
    }
    init = api("post/publish/video/init/", token, body)
    with open(path, "rb") as f:
        data = f.read()
    req = urllib.request.Request(init["upload_url"], data=data, method="PUT", headers={
        "Content-Type": "video/mp4", "Content-Length": str(size),
        "Content-Range": f"bytes 0-{size - 1}/{size}"})
    urllib.request.urlopen(req, timeout=300).read()
    pid = init["publish_id"]
    for _ in range(60):
        time.sleep(10)
        st = api("post/publish/status/fetch/", token, {"publish_id": pid})
        status = st.get("status")
        if status == "PUBLISH_COMPLETE":
            return pid, privacy, (st.get("publicaly_available_post_id") or [None])[0]
        if status == "FAILED":
            raise RuntimeError(f"publish failed: {st.get('fail_reason')}")
    return pid, privacy, None  # still processing; TikTok finishes it on its side


def auth(code):
    code = urllib.parse.unquote(code.strip())
    data = oauth(grant_type="authorization_code", code=code, redirect_uri=REDIRECT_URI)
    save_refresh(data["refresh_token"])
    print(f"connected open_id={data.get('open_id')} scope={data.get('scope')} "
          f"refresh_expires_in={data.get('refresh_expires_in')}s")
    git_commit("tiktok: store encrypted token [skip ci]", "tiktok/token.enc")


def main():
    if not (KEY and SECRET):
        print("TikTok secrets missing: skip"); return
    if not os.path.exists(TOKEN_FILE):
        print("TikTok not connected yet (tiktok/token.enc missing): skip"); return
    log = json.load(open(LOG)) if os.path.exists(LOG) else {}
    now = datetime.now(timezone.utc)
    started = datetime(2026, 10, 23, tzinfo=timezone.utc)  # never back-post older items

    def due(it):
        e = log.get(it["id"], {})
        if e.get("publish_id") or e.get("attempts", 0) >= 3 or not it.get("slides"):
            return False
        if ONLY:
            return it["id"] == ONLY
        if it.get("market", "IT").upper() not in MARKETS:
            return False
        at = datetime.fromisoformat(it["publish_at"].replace("Z", "+00:00"))
        return started <= at <= now

    items = sorted((i for i in load_queue() if due(i)), key=lambda x: x["publish_at"])
    if not items:
        print("nothing due"); return
    token = None if DRY else access_token()
    failures = 0
    for it in items:
        print(f"-> {it['id']} [{it.get('market')}]")
        out = f"/tmp/{it['id']}.mp4"
        try:
            make_video(slide_paths(it), out, it["id"])
            if DRY:
                print(f"   dry run: {out} {os.path.getsize(out)} bytes"); continue
            pid, privacy, post_id = post_video(token, out, it.get("caption", ""))
            log[it["id"]] = {"publish_id": pid, "post_id": post_id, "privacy": privacy,
                             "published_at": now.isoformat(), "market": it.get("market")}
            print(f"   published publish_id={pid} privacy={privacy}")
        except Exception as e:
            failures += 1
            prev = log.get(it["id"], {})
            log[it["id"]] = {"attempts": prev.get("attempts", 0) + 1, "last_error": str(e)[:300],
                             "last_try": now.isoformat()}
            print(f"   FAILED: {e}", file=sys.stderr)
    if not DRY:
        with open(LOG, "w") as f:
            json.dump(log, f, indent=2, sort_keys=True)
        git_commit("tiktok: publish log [skip ci]", "tiktok/published.json")
    sys.exit(1 if failures else 0)


if __name__ == "__main__":
    if len(sys.argv) > 2 and sys.argv[1] == "auth":
        auth(sys.argv[2])
    else:
        main()
