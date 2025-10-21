#!/usr/bin/env python3
"""
create_ai_analysis_table.py

Adds an ai_analysis table to store AI analysis rows per target.
Usage:
  export DATABASE_URL='postgres://user:pw@host:5432/db'
  python create_ai_analysis_table.py
"""
import os, sys, psycopg2
from dotenv import load_dotenv
load_dotenv()

DATABASE_URL = os.getenv("DATABASE_URL")
if not DATABASE_URL:
    print("Please set DATABASE_URL env var", file=sys.stderr)
    sys.exit(1)

DDL = """
-- AI analysis table (one or many analysis entries per target)
CREATE TABLE IF NOT EXISTS ai_analysis (
  id SERIAL PRIMARY KEY,
  target_id INTEGER NOT NULL REFERENCES targets(id) ON DELETE CASCADE,
  user_id INTEGER REFERENCES users(id) ON DELETE SET NULL,
  analysis_text TEXT,        -- human-readable analysis
  analysis_json JSONB,       -- structured analysis if available
  model_meta JSONB DEFAULT '{}'::jsonb, -- optional: model name, version, scoring, etc.
  created_at TIMESTAMP WITH TIME ZONE DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_ai_analysis_target_id ON ai_analysis(target_id);
CREATE INDEX IF NOT EXISTS idx_ai_analysis_created_at ON ai_analysis(created_at);
CREATE INDEX IF NOT EXISTS idx_ai_analysis_text_gin ON ai_analysis USING GIN (to_tsvector('english', coalesce(analysis_text,'')));
"""
def main():
    conn = psycopg2.connect(DATABASE_URL)
    conn.autocommit = True
    cur = conn.cursor()
    try:
        print("Running DDL for ai_analysis...")
        cur.execute(DDL)
        print("ai_analysis table created/verified.")
    finally:
        cur.close()
        conn.close()

if __name__ == "__main__":
    main()
