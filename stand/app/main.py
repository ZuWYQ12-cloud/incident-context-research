import json
import time
import uuid
from datetime import datetime, timezone

import os
from typing import Literal

import psycopg
from psycopg.rows import dict_row
from fastapi import FastAPI, HTTPException, Request
from pydantic import BaseModel, Field, field_validator


app = FastAPI(title="Production Tasks", version="0.1.0")

@app.middleware("http")
async def log_request(request: Request, call_next):
    request_id = uuid.uuid4().hex
    started_at = datetime.now(timezone.utc).isoformat()
    started = time.perf_counter()
    status_code = 500
    error_type = None

    try:
        response = await call_next(request)
        status_code = response.status_code
        response.headers["X-Request-ID"] = request_id
        return response
    except Exception as exc:
        error_type = type(exc).__name__
        raise
    finally:
        event = {
            "event": "http_request",
            "request_id": request_id,
            "started_at": started_at,
            "finished_at": datetime.now(timezone.utc).isoformat(),
            "method": request.method,
            "path": request.url.path,
            "status_code": status_code,
            "duration_ms": round(
                (time.perf_counter() - started) * 1000, 2
            ),
            "error_type": error_type,
        }
        print(json.dumps(event, ensure_ascii=False), flush=True)

class TaskCreate(BaseModel):
    title: str = Field(min_length=1, max_length=200)

    @field_validator("title")
    @classmethod
    def clean_title(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("Название не должно быть пустым")
        return value


class TaskUpdate(BaseModel):
    status: Literal["pending", "in_progress", "completed"]


def connect_db():
    return psycopg.connect(
        os.environ["DATABASE_URL"],
        row_factory=dict_row,
        connect_timeout=3,
        options="-c statement_timeout=5000",
    )


@app.get("/tasks")
def list_tasks():
    with connect_db() as conn:
        rows = conn.execute(
            "SELECT * FROM production_tasks ORDER BY id LIMIT 100"
        ).fetchall()
    return rows


@app.post("/tasks", status_code=201)
def create_task(task: TaskCreate):
    with connect_db() as conn:
        row = conn.execute(
            """
            INSERT INTO production_tasks (title)
            VALUES (%s)
            RETURNING *
            """,
            (task.title,),
        ).fetchone()
    return row


@app.patch("/tasks/{task_id}")
def update_task(task_id: int, task: TaskUpdate):
    with connect_db() as conn:
        row = conn.execute(
            """
            UPDATE production_tasks
            SET status = %s, updated_at = CURRENT_TIMESTAMP
            WHERE id = %s
            RETURNING *
            """,
            (task.status, task_id),
        ).fetchone()

    if row is None:
        raise HTTPException(status_code=404, detail="Задание не найдено")
    return row

@app.get("/health/live")
def health_live():
    return {"status": "alive"}


@app.get("/health/ready")
def health_ready():
    try:
        with connect_db() as conn:
            conn.execute("SELECT id FROM production_tasks LIMIT 1")
    except psycopg.Error:
        raise HTTPException(
            status_code=503,
            detail="Database readiness check failed",
        )
    return {"status": "ready"}
