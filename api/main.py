"""
VidEdge Backend API
Run: python api/main.py
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Optional
from datetime import datetime

from fastapi import FastAPI, HTTPException, Depends
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from enum import Enum

# Create app
app = FastAPI(title="VidEdge API", version="1.0.0")

# CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── Models ────────────────────────────────────────────────────────────────────────

class ScriptRequest(BaseModel):
    topic: str
    niche: str
    script_type: str
    duration: str
    tone: str
    notes: Optional[str] = None

class ScriptResponse(BaseModel):
    id: str
    topic: str
    content: str
    word_count: int
    created_at: str
    status: str

class VideoIdea(BaseModel):
    id: str
    title: str
    niche: str
    tags: list[str]
    potential: str
    created_at: str

class ChannelRequest(BaseModel):
    name: str
    url: Optional[str] = None

class StylePreferences(BaseModel):
    tone: str
    audience_level: str
    video_length: str
    content_type: str

class ThumbnailRequest(BaseModel):
    title: str
    style: str = "bold"

class ThumbnailResponse(BaseModel):
    id: str
    title: str
    image_url: str
    created_at: str

# ── In-Memory Storage (Replace with database in production) ─────────────────

scripts_db: list[dict] = []
ideas_db: list[dict] = []
channels_db: list[dict] = [
    {"id": "1", "name": "MrBeast", "url": "https://youtube.com/@MrBeast", "subscribers": "300M", "niche": "Entertainment"},
    {"id": "2", "name": "MKBHD", "url": "https://youtube.com/@mkbhd", "subscribers": "20M", "niche": "Tech"},
]
thumbnails_db: list[dict] = []
user_prefs = {
    "tone": "Casual & Entertaining",
    "audience_level": "Beginner-friendly",
    "video_length": "10-20 minutes",
    "content_type": "List/Top 10 videos",
    "channels": ["MrBeast", "MKBHD"]
}

# ── Helpers ─────────────────────────────────────────────────────────────────────

def generate_id() -> str:
    return str(datetime.now().timestamp())

# ── Scripts API ────────────────────────────────────────────────────────────────

@app.get("/api/scripts", response_model=list[ScriptResponse])
async def get_scripts():
    """Get all scripts"""
    return [
        ScriptResponse(
            id=s["id"],
            topic=s["topic"],
            content=s["content"],
            word_count=s["word_count"],
            created_at=s["created_at"],
            status=s["status"]
        )
        for s in scripts_db
    ]

@app.post("/api/scripts", response_model=ScriptResponse)
async def create_script(req: ScriptRequest):
    """Generate a new script using AI"""
    # In production, this would call an AI API (OpenAI, Anthropic, etc.)
    script_content = f"""# Script: {req.topic}

## Hook
[Attention-grabbing opening about {req.topic}]

## Introduction
Hey everyone! Today we're diving into {req.topic}. This is something that affects all of us, so let's get right into it.

## Main Content

### Point 1: [Key insight about {req.topic}]
[Detailed explanation with examples]

### Point 2: [Second key insight]
[Supporting information and data]

### Point 3: [Third key insight]
[Practical applications]

## Conclusion
So there you have it - {req.topic} broken down. If you found this helpful, make sure to like and subscribe!

---
*Niche: {req.niche} | Type: {req.script_type} | Duration: {req.duration} | Tone: {req.tone}*
"""
    
    script = {
        "id": generate_id(),
        "topic": req.topic,
        "content": script_content,
        "word_count": len(script_content.split()),
        "created_at": datetime.now().isoformat(),
        "status": "completed"
    }
    scripts_db.append(script)
    
    return ScriptResponse(**script)

@app.delete("/api/scripts/{script_id}")
async def delete_script(script_id: str):
    """Delete a script"""
    global scripts_db
    scripts_db = [s for s in scripts_db if s["id"] != script_id]
    return {"status": "deleted"}

# ── Video Ideas API ────────────────────────────────────────────────────────────

@app.get("/api/ideas", response_model=list[VideoIdea])
async def get_ideas(niche: Optional[str] = None):
    """Get video ideas, optionally filtered by niche"""
    if niche:
        return [VideoIdea(**i) for i in ideas_db if i["niche"].lower() == niche.lower()]
    return [VideoIdea(**i) for i in ideas_db]

@app.post("/api/ideas/generate")
async def generate_ideas(niche: str = "tech", count: int = 5):
    """Generate new video ideas using AI"""
    idea_templates = [
        "10 {niche} Things That Will Change Your Life in 2025",
        "How I Made $10,000 With {niche} (Step by Step)",
        "The Future of {niche}: 5 Predictions",
        "{niche} Mistakes Everyone Makes (And How to Fix Them)",
        "Best {niche} Products of 2025 - Ultimate Guide",
        "Why {niche} Is Exploding Right Now",
        "{niche} Tips That Actually Work in 2025",
        "Hidden {niche} Secrets Experts Don't Want You to Know",
    ]
    
    new_ideas = []
    for i, template in enumerate(idea_templates[:count]):
        idea = {
            "id": generate_id(),
            "title": template.format(niche=niche.capitalize()),
            "niche": niche.capitalize(),
            "tags": ["List", "High Potential"] if i % 2 == 0 else ["Tutorial", "Trending"],
            "potential": ["High Potential", "Viral Potential", "Trending"][i % 3],
            "created_at": datetime.now().isoformat()
        }
        ideas_db.append(idea)
        new_ideas.append(VideoIdea(**idea))
    
    return new_ideas

# ── Channels API ────────────────────────────────────────────────────────────────

@app.get("/api/channels")
async def get_channels():
    """Get all saved channels"""
    return channels_db

@app.post("/api/channels")
async def add_channel(req: ChannelRequest):
    """Add a new channel"""
    channel = {
        "id": generate_id(),
        "name": req.name,
        "url": req.url or "",
        "subscribers": "0",
        "niche": "Unknown"
    }
    channels_db.append(channel)
    return channel

@app.delete("/api/channels/{channel_id}")
async def delete_channel(channel_id: str):
    """Delete a channel"""
    global channels_db
    channels_db = [c for c in channels_db if c["id"] != channel_id]
    return {"status": "deleted"}

# ── Style/Profile API ──────────────────────────────────────────────────────────

@app.get("/api/style")
async def get_style_preferences():
    """Get user style preferences"""
    return user_prefs

@app.post("/api/style")
async def update_style_preferences(prefs: StylePreferences):
    """Update user style preferences"""
    user_prefs["tone"] = prefs.tone
    user_prefs["audience_level"] = prefs.audience_level
    user_prefs["video_length"] = prefs.video_length
    user_prefs["content_type"] = prefs.content_type
    return {"status": "saved", "preferences": user_prefs}

@app.post("/api/style/channels")
async def add_style_channel(name: str):
    """Add channel to style profile"""
    if name not in user_prefs["channels"]:
        user_prefs["channels"].append(name)
    return {"status": "added", "channels": user_prefs["channels"]}

@app.delete("/api/style/channels/{name}")
async def remove_style_channel(name: str):
    """Remove channel from style profile"""
    if name in user_prefs["channels"]:
        user_prefs["channels"].remove(name)
    return {"status": "removed", "channels": user_prefs["channels"]}

# ── Thumbnails API ────────────────────────────────────────────────────────────

@app.get("/api/thumbnails")
async def get_thumbnails():
    """Get all thumbnails"""
    return thumbnails_db

@app.post("/api/thumbnails", response_model=ThumbnailResponse)
async def create_thumbnail(req: ThumbnailRequest):
    """Generate a thumbnail (AI integration point)"""
    thumbnail = {
        "id": generate_id(),
        "title": req.title,
        "image_url": f"/api/thumbnails/{generate_id()}/image",
        "created_at": datetime.now().isoformat()
    }
    thumbnails_db.append(thumbnail)
    return ThumbnailResponse(**thumbnail)

# ── Production Board API ────────────────────────────────────────────────────────

class BoardItem(BaseModel):
    id: str
    title: str
    column: str  # ideas, writing, editing, done
    created_at: str

board_db: list[dict] = [
    {"id": "1", "title": "AI Tools Comparison 2025", "column": "ideas", "created_at": datetime.now().isoformat()},
    {"id": "2", "title": "Tech Trends 2025", "column": "writing", "created_at": datetime.now().isoformat()},
    {"id": "3", "title": "Morning Routine", "column": "editing", "created_at": datetime.now().isoformat()},
    {"id": "4", "title": "2024 Year Review", "column": "done", "created_at": datetime.now().isoformat()},
]

@app.get("/api/board")
async def get_board():
    """Get production board items"""
    return board_db

@app.post("/api/board")
async def add_board_item(title: str, column: str = "ideas"):
    """Add item to production board"""
    item = {
        "id": generate_id(),
        "title": title,
        "column": column,
        "created_at": datetime.now().isoformat()
    }
    board_db.append(item)
    return item

@app.put("/api/board/{item_id}")
async def move_board_item(item_id: str, column: str):
    """Move item to different column"""
    for item in board_db:
        if item["id"] == item_id:
            item["column"] = column
            return item
    raise HTTPException(status_code=404, item="Item not found")

@app.delete("/api/board/{item_id}")
async def delete_board_item(item_id: str):
    """Delete board item"""
    global board_db
    board_db = [i for i in board_db if i["id"] != item_id]
    return {"status": "deleted"}

# ── Team API ────────────────────────────────────────────────────────────────────

team_db: list[dict] = [
    {"id": "1", "name": "Peter H", "email": "peter@example.com", "role": "Owner", "status": "online"},
    {"id": "2", "name": "Sarah", "email": "sarah@example.com", "role": "Editor", "status": "offline"},
    {"id": "3", "name": "Mike", "email": "mike@example.com", "role": "Writer", "status": "online"},
]

@app.get("/api/team")
async def get_team():
    """Get team members"""
    return team_db

@app.post("/api/team")
async def invite_team_member(name: str, email: str, role: str = "Writer"):
    """Invite a new team member"""
    member = {
        "id": generate_id(),
        "name": name,
        "email": email,
        "role": role,
        "status": "pending"
    }
    team_db.append(member)
    return member

# ── Billing API ────────────────────────────────────────────────────────────────

billing_db = {
    "plan": "caleb_ai",
    "price": 29,
    "billing_cycle": "monthly",
    "next_billing": "2025-02-15",
    "payment_method": {
        "type": "card",
        "last4": "4242",
        "expiry": "12/26"
    },
    "usage": {
        "scripts_used": 0,
        "scripts_limit": 30,
        "thumbnails_used": 0,
        "thumbnails_limit": 120
    }
}

@app.get("/api/billing")
async def get_billing():
    """Get billing info"""
    return billing_db

# ── Health Check ────────────────────────────────────────────────────────────────

@app.get("/health")
async def health_check():
    """Health check endpoint"""
    return {"status": "healthy", "timestamp": datetime.now().isoformat()}

# ── Run ─────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import uvicorn
    print("\n" + "="*50)
    print("VidEdge API Server")
    print("="*50)
    print("API: http://localhost:8000")
    print("Docs: http://localhost:8000/docs")
    print("="*50 + "\n")
    uvicorn.run(app, host="0.0.0.0", port=8000)