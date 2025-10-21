#!/usr/bin/env python3
"""
query_laspinas.py

Query all rows from public.demographics where Municipality = 'Las Piñas'.

Usage:
  # ensure env vars are set (or create a .env with DATABASE_URL / PGHOST etc.)
  python query_laspinas.py
  # to write CSV:
  python query_laspinas.py --out laspinas.csv
"""

import os
import time
import csv
import argparse
from dotenv import load_dotenv
import psycopg2
from psycopg2.extras import RealDictCursor
from psycopg2.extras import RealDictCursor



load_dotenv()

def get_conn_params():
    """
    Prefer DATABASE_URL (Render's External Database URL).
    Falls back to individual fields if provided.
    """
    url = os.getenv("DATABASE_URL")
    if url:
        # psycopg2 accepts DSN string directly
        # ensure sslmode present; many managed DBs require sslmode=require
        if "sslmode=" in url:
            return dict(dsn=url)
        return dict(dsn=f"{url}?sslmode=require")

    host = os.getenv("PGHOST")
    port = int(os.getenv("PGPORT", "5432"))
    db   = os.getenv("PGDATABASE")
    user = os.getenv("PGUSER")
    pwd  = os.getenv("PGPASSWORD")

    missing = [k for k,v in {"PGHOST":host,"PGDATABASE":db,"PGUSER":user,"PGPASSWORD":pwd}.items() if not v]
    if missing:
        raise SystemExit(f"Missing env vars: {', '.join(missing)} (or set DATABASE_URL).")
    return dict(host=host, port=port, dbname=db, user=user, password=pwd, sslmode="require")

def connect_with_retries(retries=5, delay=2.0):
    params = get_conn_params()
    last_err = None
    for i in range(1, retries+1):
        try:
            conn = psycopg2.connect(cursor_factory=RealDictCursor, **params)
            conn.autocommit = True
            return conn
        except Exception as e:
            last_err = e
            print(f"[{i}/{retries}] connect failed: {e}")
            time.sleep(delay)
    raise last_err

def query_laspinas(conn):
    """
    Returns a list of dict rows for Municipality = 'Las Piñas'.
    Uses parameterized query to avoid injection and handle case-insensitivity.
    """
    sql = """
    SELECT *
    FROM public.demographics
    WHERE "Municipality" ILIKE %s;
    """
    pattern = "%las piñas%"   # include wildcards here

    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute(sql, (pattern,))
        rows = cur.fetchall()
    return rows

def write_csv(rows, path):
    if not rows:
        print("No rows to write.")
        return
    # Use keys of first row for header
    keys = list(rows[0].keys())
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=keys)
        writer.writeheader()
        for r in rows:
            # convert any non-serializable values if needed
            writer.writerow({k: (v if v is not None else "") for k, v in r.items()})
    print(f"Wrote {len(rows)} rows to {path}")

def main():
    parser = argparse.ArgumentParser(description="Query all demographics rows for Municipality = Las Pinas")
    parser.add_argument("--out", "-o", help="Optional CSV output path")
    parser.add_argument("--tries", type=int, default=5, help="Number of connection retries")
    args = parser.parse_args()

    print("Connecting to Postgres…")
    conn = connect_with_retries(retries=args.tries)
    try:
        rows = query_laspinas(conn)
        n = len(rows)
        print(f"Found {n} rows for Municipality = 'Las Pinas'.")
        if n:
            # Print a short preview (first 5 rows)
            preview = rows[:5]
            for i, r in enumerate(preview, start=1):
                barangay = r.get("Barangay") or r.get("barangay") or "(unknown)"
                total = r.get("Total_MF") or r.get("total_mf") or "-"
                print(f"{i}. Barangay: {barangay} — Total: {total}")
        if args.out:
            write_csv(rows, args.out)
    finally:
        conn.close()

if __name__ == "__main__":
    main()
