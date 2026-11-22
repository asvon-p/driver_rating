import os

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from .scoring import POLICY, POLICY_ID


def connect():
    return psycopg.connect(
        os.environ["DRIVER_DATABASE_URL"], row_factory=dict_row, connect_timeout=5
    )


def initialize():
    with connect() as db:
        db.execute("""
            CREATE TABLE IF NOT EXISTS policy_versions (
                id text PRIMARY KEY, settings jsonb NOT NULL
            );
            CREATE TABLE IF NOT EXISTS analyses (
                id uuid PRIMARY KEY, owner text NOT NULL, state text NOT NULL,
                duration_ms integer NOT NULL, processed_ms integer NOT NULL DEFAULT 0,
                model jsonb NOT NULL, policy text NOT NULL REFERENCES policy_versions(id),
                attempts integer NOT NULL DEFAULT 0, result jsonb, error text,
                created timestamptz NOT NULL DEFAULT now(),
                expires timestamptz NOT NULL DEFAULT now()+interval '7 days'
            );
            CREATE INDEX IF NOT EXISTS analyses_queue ON analyses(state,created);
            CREATE TABLE IF NOT EXISTS segments (
                analysis_id uuid NOT NULL REFERENCES analyses(id) ON DELETE CASCADE,
                start_ms integer NOT NULL, end_ms integer NOT NULL CHECK(end_ms>start_ms),
                label text, reason text, PRIMARY KEY(analysis_id,start_ms),
                CHECK ((label IS NULL) <> (reason IS NULL))
            );
            CREATE TABLE IF NOT EXISTS worker_status (
                singleton boolean PRIMARY KEY DEFAULT true CHECK(singleton),
                state text NOT NULL, model jsonb, updated timestamptz NOT NULL, error text
            );
        """)
        db.execute(
            "INSERT INTO policy_versions VALUES (%s,%s) ON CONFLICT DO NOTHING",
            (POLICY_ID, Jsonb(POLICY)),
        )


def worker_status():
    with connect() as db:
        row = db.execute("""SELECT state,model,error, updated > now()-interval '60 seconds' AS live
                            FROM worker_status WHERE singleton""").fetchone()
    if row and row["state"] == "error":
        return {"state": "error", "message": row["error"]}
    if not row or not row["live"] or row["state"] != "ready":
        return {"state": "starting", "message": "분석 모델을 준비하고 있습니다."}
    return {"state": "ready", "model": row["model"]}
