from fastapi import APIRouter

from app.api.routes import analytics, auth, backtests, connectors, health, memory, prompts, runs, schedules, trading

api_router = APIRouter()
api_router.include_router(health.router)
api_router.include_router(auth.router)
api_router.include_router(connectors.router)
api_router.include_router(prompts.router)
api_router.include_router(memory.router)
api_router.include_router(runs.router)
api_router.include_router(schedules.router)
api_router.include_router(backtests.router)
api_router.include_router(analytics.router)
api_router.include_router(trading.router)
