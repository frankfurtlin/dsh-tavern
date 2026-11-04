"""Feedback / notice / diagnostic APIs were removed together with their UI (single-user build)."""
from fastapi import APIRouter

router = APIRouter(prefix='/experience', tags=['experience'])
