import os
import re
import html
import requests
from datetime import datetime
import uuid

def random_filename(ext=".mp4"):
    return f"reel_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:6]}{ext}"

def download_reel(insta_url: str) -> dict:
    """
    Downloads an Instagram Reel video using public endpoints and saves locally.
    Returns metadata including local file path.
    """

    if "instagram.com/reel/" not in insta_url:
        raise ValueError("Invalid Instagram reel URL")

    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/119.0 Safari/537.36"
        )
    }

    response = requests.get(insta_url, headers=headers, timeout=10, allow_redirects=True)
    response.raise_for_status()

    # Try multiple extraction methods
    match = re.search(r'"video_url":"(https:[^"]+)"', response.text)
    if match:
        video_url = html.unescape(match.group(1))
    else:
        meta_match = re.search(r'<meta property="og:video" content="([^"]+)"', response.text)
        if meta_match:
            video_url = meta_match.group(1)
        else:
            raise RuntimeError("Couldn't find video URL (Reel may be private or structure changed)")

    video_response = requests.get(video_url, headers=headers, timeout=10)
    video_response.raise_for_status()

    output_dir = "downloads"
    os.makedirs(output_dir, exist_ok=True)
    file_path = os.path.join(output_dir, random_filename(".mp4"))

    with open(file_path, "wb") as f:
        f.write(video_response.content)

    return {
        "status": "ok",
        "file_path": file_path,
        "video_url": video_url
    }
