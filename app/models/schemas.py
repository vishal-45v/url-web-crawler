from pydantic import BaseModel, HttpUrl
from typing import Optional, List
from datetime import datetime


class CrawlRequest(BaseModel):
    url: HttpUrl


class Topic(BaseModel):
    topic: str
    score: float


class CrawlResponse(BaseModel):
    url: str
    canonical_url: Optional[str] = None
    title: Optional[str] = None
    description: Optional[str] = None
    og_title: Optional[str] = None
    og_description: Optional[str] = None
    og_image: Optional[str] = None
    meta_keywords: List[str] = []
    topics: List[Topic] = []
    word_count: int = 0
    crawled_at: datetime
    classifier_used: str
