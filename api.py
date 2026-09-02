from fastapi import FastAPI, HTTPException, Depends
from fastapi.responses import JSONResponse, FileResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import Optional
import os
import re
import httpx
import asyncio
from scrapers.instagram import InstagramScraper
from scrapers.bypass import CaptchaBypass
from utils.cache import cache
from utils.helpers import extract_video_id, validate_url
from config import settings

app = FastAPI(title="Instagram Downloader API", version="2.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

class DownloadRequest(BaseModel):
    url: str
    quality: Optional[str] = "high"

class DownloadResponse(BaseModel):
    success: bool
    video_url: Optional[str] = None
    thumbnail: Optional[str] = None
    title: Optional[str] = None
    duration: Optional[int] = None
    error: Optional[str] = None

@app.get("/")
async def root():
    return {
        "name": "Instagram Downloader API",
        "version": "2.0",
        "status": "online",
        "endpoints": {
            "/download": "POST - Download Instagram Reels/Video",
            "/health": "GET - Health check"
        }
    }

@app.get("/health")
async def health():
    return {"status": "healthy", "cache": cache.redis.ping()}

@app.post("/download", response_model=DownloadResponse)
async def download_reels(request: DownloadRequest):
    if not validate_url(request.url):
        raise HTTPException(status_code=400, detail="Invalid URL")
    
    cache_key = f"ig:{request.url}"
    cached = cache.get(cache_key)
    if cached:
        return DownloadResponse(**cached)
    
    try:
        scraper = InstagramScraper()
        result = await scraper.fetch_media(request.url)
        
        if result.get("error"):
            if "captcha" in result["error"].lower():
                bypass = CaptchaBypass()
                result = await bypass.solve_and_fetch(request.url)
        
        if not result.get("video_url"):
            raise HTTPException(status_code=404, detail="Video not found")
        
        cache.set(cache_key, result, 3600)
        return DownloadResponse(**result)
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/download/{video_id}")
async def download_file(video_id: str):
    filepath = os.path.join(settings.DOWNLOAD_DIR, f"{video_id}.mp4")
    if os.path.exists(filepath):
        return FileResponse(filepath, filename=f"{video_id}.mp4")
    raise HTTPException(status_code=404, detail="File not found")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("api:app", host="0.0.0.0", port=8000, reload=True)