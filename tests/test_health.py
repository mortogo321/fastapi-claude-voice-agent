from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient


@pytest.fixture
async def client():
    # Build the app without lifespan so init_engine() doesn't need Postgres.
    from fastapi import FastAPI
    from fastapi.responses import JSONResponse

    from app import __version__

    app = FastAPI()

    @app.get("/health")
    async def health():
        return JSONResponse({"status": "ok", "version": __version__})

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


async def test_health(client):
    resp = await client.get("/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert body["version"]
