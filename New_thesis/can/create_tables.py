#!/usr/bin/env python3
"""
create_tables.py

Create PostgreSQL schema for users, targets, competitors, foot_traffic, and versions.

Usage:
    export DATABASE_URL='postgres://user:pass@host:5432/dbname'
    python create_tables.py
"""

import os
import sys
import psycopg2
from dotenv import load_dotenv

load_dotenv()

DATABASE_URL = os.getenv("DATABASE_URL")
if not DATABASE_URL:
    print("Please set DATABASE_URL environment variable (postgres://...)", file=sys.stderr)
    sys.exit(1)

DDL = """
-- Users
CREATE TABLE IF NOT EXISTS users (
  id SERIAL PRIMARY KEY,
  email TEXT NOT NULL UNIQUE,
  password_hash TEXT NOT NULL,
  full_name TEXT,
  created_at TIMESTAMP WITH TIME ZONE DEFAULT now()
);

-- Targets: each saved target location belongs to a user
CREATE TABLE IF NOT EXISTS targets (
  id SERIAL PRIMARY KEY,
  user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  name TEXT, -- optional friendly name
  business_type TEXT,
  description TEXT,
  latitude DOUBLE PRECISION NOT NULL,
  longitude DOUBLE PRECISION NOT NULL,
  data JSONB DEFAULT '{}'::jsonb, -- stores population_summary, selected_barangays, ai_analysis, other flexible payload
  created_at TIMESTAMP WITH TIME ZONE DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_targets_user_id ON targets(user_id);
CREATE INDEX IF NOT EXISTS idx_targets_lat_lng ON targets(latitude, longitude);
CREATE INDEX IF NOT EXISTS idx_targets_data_gin ON targets USING GIN (data);

-- Competitors: normalized, one row per competitor (linked to target)
CREATE TABLE IF NOT EXISTS competitors (
  id SERIAL PRIMARY KEY,
  target_id INTEGER NOT NULL REFERENCES targets(id) ON DELETE CASCADE,
  name TEXT,
  vicinity TEXT,
  details JSONB DEFAULT '{}'::jsonb, -- can hold rating, place_id, geometry, types, etc.
  created_at TIMESTAMP WITH TIME ZONE DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_competitors_target_id ON competitors(target_id);

-- Foot traffic: one row per source venue or aggregated foot-traffic item
CREATE TABLE IF NOT EXISTS foot_traffic (
  id SERIAL PRIMARY KEY,
  target_id INTEGER NOT NULL REFERENCES targets(id) ON DELETE CASCADE,
  source_name TEXT,
  details JSONB DEFAULT '{}'::jsonb, -- store hourly arrays, counts, best times etc.
  created_at TIMESTAMP WITH TIME ZONE DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_foottraffic_target_id ON foot_traffic(target_id);
CREATE INDEX IF NOT EXISTS idx_foottraffic_details_gin ON foot_traffic USING GIN (details);

-- Optional: versions / history of the full payload (audit)
CREATE TABLE IF NOT EXISTS target_versions (
  id SERIAL PRIMARY KEY,
  target_id INTEGER NOT NULL REFERENCES targets(id) ON DELETE CASCADE,
  saved_at TIMESTAMP WITH TIME ZONE DEFAULT now(),
  data JSONB NOT NULL
);
"""

def main():
    conn = psycopg2.connect(DATABASE_URL)
    conn.autocommit = True
    cur = conn.cursor()
    try:
        print("Running DDL...")
        cur.execute(DDL)
        print("DDL executed successfully.")
    except Exception as e:
        print("Error executing DDL:", e)
        raise
    finally:
        cur.close()
        conn.close()

if __name__ == "__main__":
    main()
