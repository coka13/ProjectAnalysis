"""Shared pytest fixtures: isolated data dir, sample project, desktop bridge."""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

import pytest

_TMP = tempfile.mkdtemp(prefix="aai-tests-")
os.environ.setdefault("AAI_DATA_DIR", _TMP)
os.environ.setdefault("AAI_DATABASE_URL", f"sqlite:///{Path(_TMP).as_posix()}/test.sqlite3")
os.environ.setdefault("AAI_AI_BASE_URL", "")
os.environ.setdefault("AAI_AI_MODEL", "")

SAMPLE_PYTHON = '''\
"""Sample service layer."""
from abc import ABC, abstractmethod
from dataclasses import dataclass
from enum import Enum


class OrderStatus(Enum):
    NEW = "new"
    PAID = "paid"
    SHIPPED = "shipped"
    CLOSED = "closed"


class Repository(ABC):
    @abstractmethod
    def get(self, key: str): ...


class OrderRepository(Repository):
    def __init__(self, connection):
        self.connection = connection

    def get(self, key: str):
        return self.connection.query(key)

    def save(self, order):
        return self.connection.write(order)


@dataclass
class Order:
    id: str
    status: OrderStatus


class OrderService:
    def __init__(self, repository: OrderRepository):
        self.repository = repository

    def place(self, order: Order) -> Order:
        self.repository.save(order)
        return order

    def find(self, key: str) -> Order:
        return self.repository.get(key)
'''

SAMPLE_API = '''\
from fastapi import FastAPI

from services import OrderService

app = FastAPI()
service = OrderService(None)


@app.get("/orders/{order_id}")
def read_order(order_id: str):
    return service.find(order_id)


@app.post("/orders")
def create_order(payload: dict):
    return service.place(payload)
'''

SAMPLE_SQL = """
CREATE TABLE customers (
    id INTEGER PRIMARY KEY,
    email TEXT NOT NULL UNIQUE
);

CREATE TABLE orders (
    id INTEGER PRIMARY KEY,
    customer_id INTEGER NOT NULL REFERENCES customers(id),
    total NUMERIC NOT NULL
);

CREATE INDEX idx_orders_customer ON orders (customer_id);
"""

SAMPLE_DOCKERFILE = """
FROM python:3.11-slim
EXPOSE 8080
ENTRYPOINT ["uvicorn", "api:app"]
"""

SAMPLE_COMPOSE = """
services:
  api:
    image: sample/api:1.0
    depends_on:
      - db
    ports:
      - "8080:8080"
  db:
    image: postgres:16
"""

SAMPLE_TS = """
export interface Notifier {
  send(message: string): Promise<void>;
}

export class EmailNotifier implements Notifier {
  constructor(private readonly transport: string) {}

  async send(message: string): Promise<void> {
    await fetch(this.transport, { method: 'POST', body: message });
  }
}
"""


@pytest.fixture(scope="session")
def sample_project(tmp_path_factory) -> Path:
    root = tmp_path_factory.mktemp("sample-project")
    (root / "services.py").write_text(SAMPLE_PYTHON, encoding="utf-8")
    (root / "api.py").write_text(SAMPLE_API, encoding="utf-8")
    (root / "schema.sql").write_text(SAMPLE_SQL, encoding="utf-8")
    (root / "Dockerfile").write_text(SAMPLE_DOCKERFILE, encoding="utf-8")
    (root / "docker-compose.yml").write_text(SAMPLE_COMPOSE, encoding="utf-8")
    (root / "notifier.ts").write_text(SAMPLE_TS, encoding="utf-8")
    return root


@pytest.fixture(scope="session")
def analysis(sample_project):
    from app.engine.pipeline import analyze_project

    graph, report, warnings = analyze_project(sample_project)
    return graph, report, warnings


@pytest.fixture(scope="session")
def graph(analysis):
    return analysis[0]


@pytest.fixture()
def bridge():
    """The JavaScript API object, exercised exactly as the UI calls it."""
    from app.db import init_db
    from app.desktop.bridge import Api

    init_db()
    return Api()
