import os
import re
import requests
from app.utils import random_filename

def download_reel(insta_url: str) -> str:
    """
    Downloads Instagram Reels video using public endpoints.
    """

    # Ensure URL is valid
    if "instagram.com/reel/" not in insta_url:
        raise ValueError("Invalid Instagram reel URL")

    # Simulate a real browser request (to bypass basic filters)
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/119.0 Safari/537.36"
        )
    }

    # Get HTML page
    response = requests.get(insta_url, headers=headers)
    if response.status_code != 200:
        raise RuntimeError("Failed to fetch reel page")

    # Extract video URL from HTML (simplified JSON pattern)
    match = re.search(r'"video_url":"(https:[^"]+)"', response.text)
    if not match:
        raise RuntimeError("Couldn't find video URL (Reel might be private)")

    video_url = match.group(1).replace("\\u0026", "&")

    # Download video binary
    video_data = requests.get(video_url, headers=headers).content

    # Save file
    filename = random_filename(".mp4")
    output_dir = "downloads"
    os.makedirs(output_dir, exist_ok=True)
    file_path = os.path.join(output_dir, filename)

    with open(file_path, "wb") as f:
        f.write(video_data)

    return file_path
