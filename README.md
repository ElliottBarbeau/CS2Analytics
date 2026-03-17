# CS2 Analytics

Backend service for analyzing Counter-Strike 2 match and player statistics.

## Live API

API Docs: https://cs2analytics.onrender.com/docs

Health Check: https://cs2analytics.onrender.com/health

---

## Overview
CS2 Analytics is a FastAPI-based backend that ingests, processes, and serves high-frequency match and player data.
It is designed to support analytics queries with low latency using PostgreSQL

---

## Tech Stack

- **Backend:** FastAPI, Python
- **Database:** PostgreSQL (Neon)
- **ORM:** SQLAlchemy 2.0
- **Deployment:** Docker
- **Testing:** pytest
- **Caching:** Redis (used in local/dev environment for performance optimization, not deployed to Render)

---

## Architecture

Client (HTTP) -> FastAPI (Render) -> PostgreSQL (Neon)

---

## Key Features

- REST API for CS2 analytics
- Real-time data querying
- Containerized deployment with Docker
- Fully deployed cloud backend

---

## Example Endpoints

### Get Player Stats
GET /api/v1/players/{player_id}

### Get Match Data
GET /api/v1/matches/{match_id}
