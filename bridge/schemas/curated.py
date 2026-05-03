"""Pydantic schemas for /v1/curated/write endpoint."""

from pydantic import BaseModel
from typing import List, Optional


class ReviewItem(BaseModel):
    author: str
    text: str
    rating: int
    date: str


class FaqItem(BaseModel):
    q: str
    a: str


class ServiceItem(BaseModel):
    name: str
    price: str
    description: str
    duration_min: int


class AboutField(BaseModel):
    text: str
    show_images: bool = False


class MetaField(BaseModel):
    tagline: str
    name_override: Optional[str] = None


class CuratedDataWrite(BaseModel):
    slug: str
    meta: MetaField
    about: AboutField
    reviews: List[ReviewItem] = []
    faq: List[FaqItem] = []
    services: List[ServiceItem] = []


class CuratedWriteRequest(BaseModel):
    lead_id: int
    curated_data: CuratedDataWrite
