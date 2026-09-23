"""Run routes: create, list, get, events SSE (`id: <seq>`, `event: trace`, then `event: end`) (PLAN §3.21 amendment 4;
`$DRAFTS/06 §8.3`). Stub; CTL-API."""

from fastapi import APIRouter

router = APIRouter()
