import os
import re
import json
import time
import uuid
import asyncio
import hashlib
import secrets
import random
import httpx
import redis
import jwt
from datetime import datetime, timedelta
from typing import Optional, Dict, Any, List
from fastapi import FastAPI, HTTPException, Depends, BackgroundTasks, Request
from fastapi.responses import JSONResponse, FileResponse, StreamingResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field, validator
from pyrogram import Client, filters
from pyrogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery
from pyrogram.enums import ParseMode
from contextlib import asynccontextmanager
import uvicorn
import logging

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
    title: Optional[str] = None
    duration: Optional[int] = None
    width: Optional[int] = None
    height: Optional[int] = None
    error: Optional[str] = None

class Cache:
    def __init__(self):
        try:
            self.redis = redis.from_url(REDIS_URL, decode_responses=True)
        except:
            self.redis = None
            logger.warning("Redis not available, using memory cache")
            self.memory_cache = {}
    
    def get(self, key: str):
        if self.redis:
            data = self.redis.get(key)
            return json.loads(data) if data else None
        return self.memory_cache.get(key)
    
    def set(self, key: str, value, ttl: int = 3600):
        if self.redis:
            self.redis.setex(key, ttl, json.dumps(value, default=str))
        else:
            self.memory_cache[key] = value
    
    def delete(self, key: str):
        if self.redis:
            self.redis.delete(key)
        elif key in self.memory_cache:
            del self.memory_cache[key]

cache = Cache()

class ProxyRotator:
    def __init__(self):
        self.proxies = [
            "http://proxy1:8080",
            "http://proxy2:8080",
            "http://proxy3:8080"
        ]
        self.current = 0
    
    def get_proxy(self):
        proxy = self.proxies[self.current]
        self.current = (self.current + 1) % len(self.proxies)
        return {"http": proxy, "https": proxy}
    
    def rotate(self):
        self.current = (self.current + 1) % len(self.proxies)

proxy_rotator = ProxyRotator()

class InstagramScraper:
    def __init__(self):
        self.user_agents = [
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
            "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36",
            "Mozilla/5.0 (iPhone; CPU iPhone OS 14_0 like Mac OS X) AppleWebKit/605.1.15",
            "Mozilla/5.0 (Android 11; Mobile) AppleWebKit/537.36"
        ]
    
    async def fetch_media(self, url: str) -> Dict[str, Any]:
        cache_key = f"ig:{url}"
        cached = cache.get(cache_key)
        if cached:
            return cached
        
        for attempt in range(5):
            try:
                headers = {
                    "User-Agent": random.choice(self.user_agents),
                    "Accept": "text/html,application/xhtml+xml,application/xml",
                    "Accept-Language": "en-US,en;q=0.9",
                    "Accept-Encoding": "gzip, deflate, br",
                    "Connection": "keep-alive"
                }
                
                proxy = proxy_rotator.get_proxy()
                
                async with httpx.AsyncClient(
                    headers=headers,
                    proxies=proxy,
                    timeout=30,
                    follow_redirects=True
                ) as client:
                    response = await client.get(url)
                    
                    if response.status_code == 429:
                        await asyncio.sleep(5)
                        continue
                    
                    if response.status_code == 403:
                        proxy_rotator.rotate()
                        continue
                    
                    html = response.text
                    
                    if "captcha" in html.lower():
                        html = await self._bypass_captcha(url)
                    
                    video_url = self._extract_video_url(html)
                    thumbnail = self._extract_thumbnail(html)
                    title = self._extract_title(html)
                    duration = self._extract_duration(html)
                    
                    if not video_url:
                        video_url = await self._fetch_via_embed(url)
                    
                    result = {
                        "success": True,
                        "video_url": video_url,
                        "thumbnail": thumbnail,
                        "title": title or "Instagram Video",
                        "duration": duration
                    }
                    
                    cache.set(cache_key, result, 300)
                    return result
                    
            except Exception as e:
                logger.error(f"Attempt {attempt + 1} failed: {e}")
                await asyncio.sleep(1)
        
        return {"success": False, "error": "Failed after 5 attempts"}
    
    def _extract_video_url(self, html: str) -> Optional[str]:
        patterns = [
            r'"video_url":"([^"]+)"',
            r'"videoUrl":"([^"]+)"',
            r'"playable_url":"([^"]+)"',
            r'"media_url":"([^"]+)"',
            r'<meta property="og:video" content="([^"]+)"',
            r'<video[^>]+src="([^"]+)"',
            r'<source[^>]+src="([^"]+)"'
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
            r'"display_url":"([^"]+)"'
        ]
        
        for pattern in patterns:
            match = re.search(pattern, html)
            if match:
                return match.group(1).replace("\\/", "/")
        return None
    
    def _extract_title(self, html: str) -> Optional[str]:
        patterns = [
            r'"edge_media_to_caption":{"edges":\[{"node":{"text":"([^"]+)"}}]}',
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
        match = re.search(r'"video_duration":(\d+)', html)
        if match:
            return int(match.group(1))
        
        match = re.search(r'"duration":(\d+)', html)
        if match:
            return int(match.group(1))
        
        return None
    
    async def _fetch_via_embed(self, url: str) -> Optional[str]:
        try:
            embed_url = f"https://api.instagram.com/oembed?url={url}"
            async with httpx.AsyncClient(timeout=10) as client:
                response = await client.get(embed_url)
                data = response.json()
                return data.get("thumbnail_url")
        except:
            return None
    
    async def _bypass_captcha(self, url: str) -> str:
        proxies = [
            "http://captcha_bypass1:8080",
            "http://captcha_bypass2:8080"
        ]
        
        for proxy in proxies:
            try:
                async with httpx.AsyncClient(
                    proxies={"http": proxy, "https": proxy},
                    timeout=30
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
            "/health": "GET - Health check"
        }
    }

@app.get("/health")
async def health():
    redis_status = cache.redis.ping() if cache.redis else "memory"
    return {"status": "healthy", "redis": redis_status}

@app.post("/download")
async def download_media(request: MediaRequest):
    if not re.search(r"instagram\.com/(reel|p|tv)/", request.url):
        raise HTTPException(status_code=400, detail="Invalid Instagram URL")
    
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

def generate_token(user_id: str) -> str:
    payload = {
        "sub": user_id,
        "exp": int(time.time()) + 86400,
        "iat": int(time.time())
    }
    return jwt.encode(payload, JWT_SECRET, algorithm="HS256")

def verify_token(token: str) -> Dict[str, Any]:
    try:
        payload = jwt.decode(token, JWT_SECRET, algorithms=["HS256"])
        if payload["exp"] < int(time.time()):
            return {"valid": False, "error": "Expired"}
        return {"valid": True, "user_id": payload["sub"]}
    except:
        return {"valid": False, "error": "Invalid"}

async def auth_middleware(request: Request, call_next):
    if request.url.path in ["/", "/health"]:
        return await call_next(request)
    
    auth = request.headers.get("Authorization")
    if not auth or not auth.startswith("Bearer "):
        return JSONResponse(status_code=401, content={"error": "Missing token"})
    
    token = auth.split(" ")[1]
    result = verify_token(token)
    if not result["valid"]:
        return JSONResponse(status_code=401, content={"error": result["error"]})
    
    return await call_next(request)

app.middleware("http")(auth_middleware)

if __name__ == "__main__":
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)

class TelegramBot:
    def __init__(self):
        self.app = Client(
            "instagram_bot",
            api_id=API_ID,
            api_hash=API_HASH,
            bot_token=BOT_TOKEN,
            workers=16
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
                "• Feed Videos\n\n"
                "**How to use:**\n"
                "Just paste any Instagram video/reel link in this chat\n\n"
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
                "**Note:** Only public content can be downloaded."
            )
        
        @self.app.on_message(filters.command("status"))
        async def status(client, message):
            await message.reply_text(
                "**📊 Bot Status**\n\n"
                "• Status: ✅ Online\n"
                "• API: Connected\n"
                "• Workers: 16\n"
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
                title = result.get("title", "Instagram Video")
                
                await status_msg.edit_text("📤 Uploading video...")
                
                await message.reply_video(
                    video=video_url,
                    caption=f"**{title}**\n\n⬇️ Downloaded by @{client.me.username}",
                    thumb=thumbnail if thumbnail else None
                )
                
                await status_msg.delete()
                
            except Exception as e:
                await status_msg.edit_text(f"❌ Error: {str(e)[:100]}")
        
        @self.app.on_callback_query()
        async def handle_callback(client, callback):
            await callback.answer()
            if callback.data == "guide":
                await help(client, callback.message)
    
    async def start(self):
        await self.app.start()
        logger.info("Bot started")
        await self.app.idle()
        await self.app.stop()

bot = TelegramBot()

async def run_bot():
    await bot.start()

if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1 and sys.argv[1] == "bot":
        asyncio.run(run_bot())
    else:
        uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)