"""Supabase client — shared singleton for the price engine API."""
import os
from functools import lru_cache

from supabase import create_client, Client

SUPABASE_URL = os.getenv(
    "SUPABASE_URL",
    "https://azwdbrtfykocwazbktpc.supabase.co",
)
SUPABASE_KEY = os.getenv(
    "SUPABASE_KEY",
    "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6ImF6d2RicnRmeWtvY3dhemJrdHBjIiwicm9sZSI6ImFub24iLCJpYXQiOjE3ODI0MzU1NDgsImV4cCI6MjA5ODAxMTU0OH0.alQnSPlZphCKiT3y5gL8Y6vB837c1XFLrFyuKBjnJAs",
)


@lru_cache(maxsize=1)
def get_client() -> Client:
    return create_client(SUPABASE_URL, SUPABASE_KEY)
