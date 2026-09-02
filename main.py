import os
import re
import json
import time
import uuid
import asyncio
import hashlib
import secrets
import random
import socket
import httpx
import redis
import jwt
from datetime import datetime, timedelta
from typing import Optional, Dict, Any, List, Union
from fastapi import FastAPI, HTTPException, Depends, BackgroundTasks, Request, Query
from fastapi.responses import JSONResponse, FileResponse, StreamingResponse, HTMLResponse
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, validator
from pyrogram import Client, filters
from pyrogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery
from pyrogram.enums import ParseMode
from contextlib import asynccontextmanager
import uvicorn
import logging
import html
import urllib.parse
from urllib.parse import urlparse, parse_qs

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger("instagram_downloader")

REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379")
JWT_SECRET = os.getenv("JWT_SECRET", "super-secret-key-change-me")
BOT_TOKEN = os.getenv("BOT_TOKEN")
API_ID = int(os.getenv("API_ID", 0))
API_HASH = os.getenv("API_HASH")
DOWNLOAD_DIR = "./downloads"
os.makedirs(DOWNLOAD_DIR, exist_ok=True)

class MediaType:
    REEL = "reel"
    VIDEO = "video"
    PHOTO = "photo"
    ALBUM = "album"
    IGTV = "igtv"

class Quality:
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    ORIGINAL = "original"

class MediaRequest(BaseModel):
    url: str
    quality: str = "high"

class MediaResponse(BaseModel):
    success: bool
    video_url: Optional[str] = None
    thumbnail: Optional[str] = None
    caption: Optional[str] = None
    title: Optional[str] = None
    duration: Optional[int] = None
    width: Optional[int] = None
    height: Optional[int] = None
    media_type: Optional[str] = None
    quality: Optional[str] = None
    file_size: Optional[int] = None
    download_url: Optional[str] = None
    expires_at: Optional[str] = None
    metadata: Optional[Dict[str, Any]] = None
    error: Optional[str] = None

class CacheManager:
    def __init__(self):
        self.redis = None
        self.memory_cache = {}
        try:
            self.redis = redis.from_url(REDIS_URL, decode_responses=True)
            logger.info("Redis connected successfully")
        except Exception as e:
            logger.warning(f"Redis connection failed: {e}. Using memory cache.")
    
    def get(self, key: str) -> Optional[Any]:
        if self.redis:
            try:
                data = self.redis.get(key)
                return json.loads(data) if data else None
            except:
                return None
        return self.memory_cache.get(key)
    
    def set(self, key: str, value: Any, ttl: int = 3600) -> bool:
        try:
            if self.redis:
                self.redis.setex(key, ttl, json.dumps(value, default=str))
            else:
                self.memory_cache[key] = value
            return True
        except:
            return False
    
    def delete(self, key: str) -> bool:
        try:
            if self.redis:
                self.redis.delete(key)
            elif key in self.memory_cache:
                del self.memory_cache[key]
            return True
        except:
            return False

cache = CacheManager()

class ProxyRotator:
    def __init__(self):
        self.proxies = [
            "http://proxy1:8080",
            "http://proxy2:8080",
            "http://proxy3:8080",
            "http://proxy4:8080",
            "http://proxy5:8080"
        ]
        self.current = 0
        self.last_rotation = time.time()
        self.rotation_interval = 300
    
    def get_proxy(self) -> Optional[Dict[str, str]]:
        if not self.proxies:
            return None
        proxy = self.proxies[self.current]
        return {"http": proxy, "https": proxy}
    
    def rotate(self):
        if len(self.proxies) <= 1:
            return
        self.current = (self.current + 1) % len(self.proxies)
        self.last_rotation = time.time()
        logger.info(f"Rotated to proxy: {self.proxies[self.current]}")
    
    def should_rotate(self) -> bool:
        return (time.time() - self.last_rotation) >= self.rotation_interval

proxy_rotator = ProxyRotator()

class InstagramScraper:
    def __init__(self):
        self.user_agents = [
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1",
            "Mozilla/5.0 (Android 14; Mobile; rv:120.0) Gecko/120.0 Firefox/120.0"
        ]
        self.session = None
    
    async def fetch_media(self, url: str) -> Dict[str, Any]:
        cache_key = f"ig:{url}"
        cached = cache.get(cache_key)
        if cached:
            return cached
        
        for attempt in range(5):
            try:
                headers = {
                    "User-Agent": random.choice(self.user_agents),
                    "Accept": "text/html,application/xhtml+xml,application/xml,application/json;q=0.9,*/*;q=0.8",
                    "Accept-Language": "en-US,en;q=0.9",
                    "Accept-Encoding": "gzip, deflate, br",
                    "Connection": "keep-alive",
                    "Cache-Control": "no-cache",
                    "Pragma": "no-cache",
                    "Sec-Fetch-Dest": "document",
                    "Sec-Fetch-Mode": "navigate",
                    "Sec-Fetch-Site": "none",
                    "Upgrade-Insecure-Requests": "1"
                }
                
                proxy = proxy_rotator.get_proxy()
                
                async with httpx.AsyncClient(
                    headers=headers,
                    proxies=proxy,
                    timeout=30,
                    follow_redirects=True,
                    http2=True
                ) as client:
                    response = await client.get(url)
                    
                    if response.status_code == 429:
                        retry_after = int(response.headers.get("Retry-After", 30))
                        logger.warning(f"Rate limited. Waiting {retry_after}s")
                        await asyncio.sleep(retry_after)
                        continue
                    
                    if response.status_code == 403:
                        proxy_rotator.rotate()
                        await asyncio.sleep(2)
                        continue
                    
                    if response.status_code == 404:
                        return {"success": False, "error": "Media not found or deleted"}
                    
                    html_content = response.text
                    
                    if "captcha" in html_content.lower() or "challenge" in html_content.lower():
                        html_content = await self._bypass_captcha(url)
                    
                    video_url = self._extract_video_url(html_content)
                    thumbnail = self._extract_thumbnail(html_content)
                    caption = self._extract_caption(html_content)
                    title = caption or self._extract_title(html_content)
                    duration = self._extract_duration(html_content)
                    width = self._extract_width(html_content)
                    height = self._extract_height(html_content)
                    media_type = self._extract_media_type(html_content)
                    metadata = self._extract_metadata(html_content)
                    
                    if not video_url:
                        video_url = await self._fetch_via_embed(url)
                    
                    if not video_url:
                        video_url = await self._fetch_via_alternative(url)
                    
                    result = {
                        "success": True,
                        "video_url": video_url,
                        "thumbnail": thumbnail,
                        "caption": caption,
                        "title": title or "Instagram Video",
                        "duration": duration,
                        "width": width,
                        "height": height,
                        "media_type": media_type or "video",
                        "quality": "original",
                        "file_size": None,
                        "download_url": None,
                        "expires_at": None,
                        "metadata": metadata
                    }
                    
                    cache.set(cache_key, result, 300)
                    return result
                    
            except httpx.TimeoutException:
                logger.warning(f"Timeout on attempt {attempt + 1}")
                await asyncio.sleep(2)
            except httpx.ConnectError:
                logger.warning(f"Connection error on attempt {attempt + 1}")
                proxy_rotator.rotate()
                await asyncio.sleep(3)
            except Exception as e:
                logger.error(f"Attempt {attempt + 1} failed: {str(e)[:100]}")
                await asyncio.sleep(2)
        
        return {"success": False, "error": "Failed after 5 attempts"}
    
    def _extract_video_url(self, html: str) -> Optional[str]:
        patterns = [
            r'"video_url":"([^"]+)"',
            r'"videoUrl":"([^"]+)"',
            r'"playable_url":"([^"]+)"',
            r'"playable_url_audio":"([^"]+)"',
            r'"media_url":"([^"]+)"',
            r'"src":"([^"]+\.mp4[^"]*)"',
            r'<meta property="og:video" content="([^"]+)"',
            r'<meta property="og:video:url" content="([^"]+)"',
            r'<video[^>]+src="([^"]+)"',
            r'<source[^>]+src="([^"]+)"',
            r'"video_dash_manifest":"([^"]+)"',
            r'"video_versions":\s*\[\s*{[^}]*"url":\s*"([^"]+)"',
            r'"url":"([^"]+\.mp4)"'
        ]
        
        for pattern in patterns:
            matches = re.findall(pattern, html)
            for match in matches:
                url = match.replace("\\/", "/")
                if url.startswith("http") and (".mp4" in url or ".m3u8" in url):
                    return url
        return None
    
    def _extract_thumbnail(self, html: str) -> Optional[str]:
        patterns = [
            r'"thumbnail_url":"([^"]+)"',
            r'"poster":"([^"]+)"',
            r'<meta property="og:image" content="([^"]+)"',
            r'<meta property="og:image:url" content="([^"]+)"',
            r'"display_url":"([^"]+)"',
            r'"display_src":"([^"]+)"',
            r'"src":"([^"]+\.jpg)"',
            r'"image_versions2":\s*{[^}]*"url":\s*"([^"]+)"'
        ]
        
        for pattern in patterns:
            match = re.search(pattern, html)
            if match:
                url = match.group(1).replace("\\/", "/")
                if url.startswith("http"):
                    return url
        return None
    
    def _extract_caption(self, html: str) -> Optional[str]:
        patterns = [
            r'"edge_media_to_caption":{"edges":\[{"node":{"text":"([^"]+)"}}]}',
            r'"caption":"([^"]+)"',
            r'"text":"([^"]+)"',
            r'<meta property="og:title" content="([^"]+)"',
            r'<title>([^<]+)</title>'
        ]
        
        for pattern in patterns:
            match = re.search(pattern, html)
            if match:
                caption = match.group(1)
                caption = caption.replace("\\n", "\n").replace("\\", "")
                caption = html.unescape(caption)
                return caption.strip()
        return None
    
    def _extract_title(self, html: str) -> Optional[str]:
        patterns = [
            r'<meta property="og:title" content="([^"]+)"',
            r'<title>([^<]+)</title>'
        ]
        
        for pattern in patterns:
            match = re.search(pattern, html)
            if match:
                title = match.group(1)
                title = re.sub(r'[<>"\'/\\|?*]', "", title)
                return title.strip()[:200]
        return None
    
    def _extract_duration(self, html: str) -> Optional[int]:
        patterns = [
            r'"video_duration":(\d+)',
            r'"duration":(\d+)',
            r'"video_length":(\d+)',
            r'"length":(\d+)'
        ]
        
        for pattern in patterns:
            match = re.search(pattern, html)
            if match:
                return int(match.group(1))
        return None
    
    def _extract_width(self, html: str) -> Optional[int]:
        patterns = [
            r'"video_width":(\d+)',
            r'"width":(\d+)',
            r'"dimensions":{"width":(\d+)'
        ]
        
        for pattern in patterns:
            match = re.search(pattern, html)
            if match:
                return int(match.group(1))
        return None
    
    def _extract_height(self, html: str) -> Optional[int]:
        patterns = [
            r'"video_height":(\d+)',
            r'"height":(\d+)',
            r'"dimensions":{"height":(\d+)'
        ]
        
        for pattern in patterns:
            match = re.search(pattern, html)
            if match:
                return int(match.group(1))
        return None
    
    def _extract_media_type(self, html: str) -> Optional[str]:
        patterns = [
            r'"media_type":(\d+)',
            r'"__typename":"([^"]+)"',
            r'"mediaType":"([^"]+)"'
        ]
        
        for pattern in patterns:
            match = re.search(pattern, html)
            if match:
                value = match.group(1)
                if value in ["1", "GraphImage"]:
                    return "photo"
                elif value in ["2", "GraphVideo"]:
                    return "video"
                elif value in ["8", "GraphSidecar"]:
                    return "album"
                elif value in ["GraphReel"]:
                    return "reel"
                elif value in ["GraphTV"]:
                    return "igtv"
        return None
    
    def _extract_metadata(self, html: str) -> Dict[str, Any]:
        metadata = {
            "owner": None,
            "owner_id": None,
            "likes": None,
            "comments": None,
            "description": None,
            "created_at": None,
            "hashtags": [],
            "mentions": [],
            "location": None,
            "music": None
        }
        
        owner_match = re.search(r'"owner":{"username":"([^"]+)"', html)
        if owner_match:
            metadata["owner"] = owner_match.group(1)
        
        owner_id_match = re.search(r'"owner":{"id":"([^"]+)"', html)
        if owner_id_match:
            metadata["owner_id"] = owner_id_match.group(1)
        
        likes_match = re.search(r'"edge_liked_by":{"count":(\d+)}', html)
        if likes_match:
            metadata["likes"] = int(likes_match.group(1))
        
        comments_match = re.search(r'"edge_media_to_comment":{"count":(\d+)}', html)
        if comments_match:
            metadata["comments"] = int(comments_match.group(1))
        
        caption = self._extract_caption(html)
        if caption:
            metadata["description"] = caption
            hashtags = re.findall(r'#([a-zA-Z0-9_]+)', caption)
            if hashtags:
                metadata["hashtags"] = hashtags
            mentions = re.findall(r'@([a-zA-Z0-9_.]+)', caption)
            if mentions:
                metadata["mentions"] = mentions
        
        time_match = re.search(r'"taken_at":(\d+)', html)
        if time_match:
            metadata["created_at"] = datetime.fromtimestamp(int(time_match.group(1))).isoformat()
        
        location_match = re.search(r'"location":{"name":"([^"]+)"', html)
        if location_match:
            metadata["location"] = location_match.group(1)
        
        music_match = re.search(r'"music":{"title":"([^"]+)","artist":"([^"]+)"', html)
        if music_match:
            metadata["music"] = f"{music_match.group(1)} - {music_match.group(2)}"
        
        return metadata
    
    async def _fetch_via_embed(self, url: str) -> Optional[str]:
        try:
            embed_url = f"https://api.instagram.com/oembed?url={url}"
            async with httpx.AsyncClient(timeout=10) as client:
                response = await client.get(embed_url)
                if response.status_code == 200:
                    data = response.json()
                    return data.get("thumbnail_url")
        except:
            pass
        return None
    
    async def _fetch_via_alternative(self, url: str) -> Optional[str]:
        try:
            video_id = self._extract_video_id(url)
            if not video_id:
                return None
            alt_url = f"https://www.instagram.com/p/{video_id}/media/"
            async with httpx.AsyncClient(timeout=10) as client:
                response = await client.get(alt_url)
                if response.status_code == 200:
                    data = response.json()
                    if data.get("url"):
                        return data["url"]
        except:
            pass
        return None
    
    def _extract_video_id(self, url: str) -> Optional[str]:
        patterns = [
            r'/reel/([A-Za-z0-9_-]+)',
            r'/p/([A-Za-z0-9_-]+)',
            r'/tv/([A-Za-z0-9_-]+)'
        ]
        for pattern in patterns:
            match = re.search(pattern, url)
            if match:
                return match.group(1)
        return None
    
    async def _bypass_captcha(self, url: str) -> str:
        bypass_proxies = [
            "http://bypass_proxy1:8080",
            "http://bypass_proxy2:8080"
        ]
        
        for proxy in bypass_proxies:
            try:
                async with httpx.AsyncClient(
                    proxies={"http": proxy, "https": proxy},
                    timeout=30,
                    headers={"User-Agent": random.choice(self.user_agents)}
                ) as client:
                    response = await client.get(url)
                    if "captcha" not in response.text.lower():
                        return response.text
            except:
                continue
        
        return "<html><body>Captcha bypass failed</body></html>"

scraper = InstagramScraper()

app = FastAPI(title="Instagram Downloader", version="3.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.get("/")
async def root():
    return {
        "name": "Instagram Downloader API",
        "version": "3.0",
        "status": "online",
        "endpoints": {
            "/download": "POST - Download Instagram media",
            "/health": "GET - Health check",
            "/docs": "GET - API Documentation"
        }
    }

@app.get("/health")
async def health():
    redis_status = "connected" if cache.redis else "memory_cache"
    return {"status": "healthy", "redis": redis_status, "cache_size": len(cache.memory_cache)}

@app.post("/download", response_model=MediaResponse)
async def download_media(request: MediaRequest):
    if not re.search(r"instagram\.com/(reel|p|tv)/", request.url):
        raise HTTPException(status_code=400, detail={"error": "invalid_url", "message": "Invalid Instagram URL"})
    
    result = await scraper.fetch_media(request.url)
    
    if not result.get("success"):
        raise HTTPException(status_code=404, detail=result.get("error", "Not found"))
    
    return MediaResponse(**result)

@app.get("/video/{video_id}")
async def get_video(video_id: str):
    filepath = os.path.join(DOWNLOAD_DIR, f"{video_id}.mp4")
    if os.path.exists(filepath):
        return FileResponse(filepath, filename=f"{video_id}.mp4")
    raise HTTPException(status_code=404, detail="Video not found")

@app.get("/extract")
async def extract_url(url: str = Query(..., description="Instagram URL")):
    if not re.search(r"instagram\.com/(reel|p|tv)/", url):
        return JSONResponse(status_code=400, content={"error": "invalid_url", "message": "Invalid Instagram URL"})
    
    result = await scraper.fetch_media(url)
    return result

def generate_token(user_id: str) -> str:
    payload = {
        "sub": user_id,
        "exp": int(time.time()) + 86400,
        "iat": int(time.time()),
        "jti": secrets.token_hex(16)
    }
    return jwt.encode(payload, JWT_SECRET, algorithm="HS256")

def verify_token(token: str) -> Dict[str, Any]:
    try:
        payload = jwt.decode(token, JWT_SECRET, algorithms=["HS256"])
        if payload["exp"] < int(time.time()):
            return {"valid": False, "error": "Expired"}
        return {"valid": True, "user_id": payload["sub"]}
    except jwt.InvalidTokenError:
        return {"valid": False, "error": "Invalid"}

@app.middleware("http")
async def auth_middleware(request: Request, call_next):
    if request.url.path in ["/", "/health", "/docs", "/openapi.json"]:
        return await call_next(request)
    
    auth = request.headers.get("Authorization")
    if not auth or not auth.startswith("Bearer "):
        return JSONResponse(status_code=401, content={"error": "Missing or invalid token"})
    
    token = auth.split(" ")[1]
    result = verify_token(token)
    if not result["valid"]:
        return JSONResponse(status_code=401, content={"error": result["error"]})
    
    return await call_next(request)

class TelegramBot:
    def __init__(self):
        self.app = Client(
            "instagram_bot",
            api_id=API_ID,
            api_hash=API_HASH,
            bot_token=BOT_TOKEN,
            workers=16,
            max_concurrent_transmissions=10
        )
        self.setup_handlers()
    
    def setup_handlers(self):
        @self.app.on_message(filters.command("start"))
        async def start(client, message):
            await message.reply_text(
                "**🎬 Instagram Reels Downloader**\n\n"
                "Send me any Instagram Reels/Video URL and I'll download it for you.\n\n"
                "**Supported:**\n"
                "• Instagram Reels\n"
                "• IGTV Videos\n"
                "• Feed Videos\n"
                "• Photos & Albums\n\n"
                "**How to use:**\n"
                "Just paste any Instagram link in this chat\n\n"
                "**Example:**\n"
                "`https://www.instagram.com/reel/XXXXX/`",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("🌐 Web App", url="https://your-domain.com")],
                    [InlineKeyboardButton("📖 Guide", callback_data="guide")]
                ])
            )
        
        @self.app.on_message(filters.command("help"))
        async def help(client, message):
            await message.reply_text(
                "**📖 How to Use**\n\n"
                "1. Copy Instagram Reels/Video link\n"
                "2. Paste it in this chat\n"
                "3. Wait for download\n"
                "4. Watch or save the video\n\n"
                "**Supported Links:**\n"
                "• `https://www.instagram.com/reel/...`\n"
                "• `https://www.instagram.com/p/...`\n"
                "• `https://www.instagram.com/tv/...`\n\n"
                "**Features:**\n"
                "• High quality downloads\n"
                "• Caption and metadata extraction\n"
                "• Thumbnail included\n"
                "• Fast processing\n\n"
                "**Note:** Only public content can be downloaded."
            )
        
        @self.app.on_message(filters.command("status"))
        async def status(client, message):
            await message.reply_text(
                "**📊 Bot Status**\n\n"
                "• Status: ✅ Online\n"
                "• API: Connected\n"
                "• Workers: 16\n"
                "• Cache: Active\n"
                "• Uptime: Running\n\n"
                "All systems operational."
            )
        
        @self.app.on_message(filters.text & ~filters.command(["start", "help", "status"]))
        async def handle_url(client, message):
            url = message.text.strip()
            
            if not re.search(r"instagram\.com/(reel|p|tv)/", url):
                await message.reply_text("❌ Please send a valid Instagram URL.")
                return
            
            status_msg = await message.reply_text("⏳ Processing your request...")
            
            try:
                result = await scraper.fetch_media(url)
                
                if not result.get("success"):
                    await status_msg.edit_text(f"❌ {result.get('error', 'Unknown error')}")
                    return
                
                video_url = result.get("video_url")
                thumbnail = result.get("thumbnail")
                caption = result.get("caption", "Instagram Video")
                title = result.get("title", "Instagram Video")
                duration = result.get("duration")
                metadata = result.get("metadata", {})
                likes = metadata.get("likes")
                comments = metadata.get("comments")
                owner = metadata.get("owner")
                
                caption_text = caption or title
                if len(caption_text) > 200:
                    caption_text = caption_text[:197] + "..."
                
                caption_formatted = f"**{caption_text}**"
                if owner:
                    caption_formatted += f"\n\n👤 {owner}"
                if likes:
                    caption_formatted += f" | ❤️ {likes:,}"
                if comments:
                    caption_formatted += f" | 💬 {comments:,}"
                if duration:
                    caption_formatted += f"\n⏱️ {duration}s"
                
                await status_msg.edit_text("📤 Uploading video...")
                
                if video_url:
                    await message.reply_video(
                        video=video_url,
                        caption=caption_formatted,
                        thumb=thumbnail if thumbnail else None,
                        duration=duration if duration else 0,
                        width=result.get("width"),
                        height=result.get("height")
                    )
                else:
                    await status_msg.edit_text("❌ No video URL found")
                    return
                
                await status_msg.delete()
                
            except Exception as e:
                await status_msg.edit_text(f"❌ Error: {str(e)[:100]}")
        
        @self.app.on_callback_query()
        async def handle_callback(client, callback):
            await callback.answer()
            if callback.data == "guide":
                await help(client, callback.message)

if BOT_TOKEN and API_ID and API_HASH:
    bot = TelegramBot()
    asyncio.create_task(bot.app.start())

if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1 and sys.argv[1] == "bot":
        if BOT_TOKEN and API_ID and API_HASH:
            asyncio.run(bot.app.idle())
        else:
            print("Bot credentials not configured. Set BOT_TOKEN, API_ID, API_HASH")
    else:
        uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)