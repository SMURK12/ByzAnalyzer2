#!/usr/bin/env python3
# create_otp_table.py - Creates OTP verification table in PostgreSQL

import psycopg2
import os
from dotenv import load_dotenv

load_dotenv()

DATABASE_URL = os.getenv("DATABASE_URL")

if not DATABASE_URL:
    raise SystemExit("Please set DATABASE_URL environment variable")

def create_otp_table():
    """
    Creates the otp_verifications table for storing email verification codes.
    """
    conn = None
    try:
        conn = psycopg2.connect(DATABASE_URL)
        conn.autocommit = True
        
        with conn.cursor() as cur:
            # Create the OTP table
            cur.execute("""
                CREATE TABLE IF NOT EXISTS otp_verifications (
                    id SERIAL PRIMARY KEY,
                    email VARCHAR(255) NOT NULL,
                    otp_code VARCHAR(6) NOT NULL,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    expires_at TIMESTAMP NOT NULL,
                    is_verified BOOLEAN DEFAULT FALSE,
                    attempts INTEGER DEFAULT 0,
                    CONSTRAINT unique_active_otp UNIQUE (email, is_verified)
                );
            """)
            
            # Create index for faster lookups
            cur.execute("""
                CREATE INDEX IF NOT EXISTS idx_otp_email_verified 
                ON otp_verifications(email, is_verified);
            """)
            
            # Create index for expiration cleanup
            cur.execute("""
                CREATE INDEX IF NOT EXISTS idx_otp_expires 
                ON otp_verifications(expires_at);
            """)
            
            print("✅ OTP table created successfully!")
            
            # Show table structure
            cur.execute("""
                SELECT column_name, data_type, character_maximum_length, is_nullable
                FROM information_schema.columns
                WHERE table_name = 'otp_verifications'
                ORDER BY ordinal_position;
            """)
            
            print("\n📋 Table Structure:")
            print("-" * 70)
            for row in cur.fetchall():
                print(f"  {row[0]:20} {row[1]:15} {str(row[2]):10} NULL: {row[3]}")
            print("-" * 70)
            
    except Exception as e:
        print(f"❌ Error creating OTP table: {e}")
        raise
    finally:
        if conn:
            conn.close()

def cleanup_expired_otps():
    """
    Optional: Clean up expired OTP records (can be run as a cron job)
    """
    conn = None
    try:
        conn = psycopg2.connect(DATABASE_URL)
        conn.autocommit = True
        
        with conn.cursor() as cur:
            cur.execute("""
                DELETE FROM otp_verifications
                WHERE expires_at < CURRENT_TIMESTAMP
                AND is_verified = FALSE;
            """)
            deleted = cur.rowcount
            print(f"🧹 Cleaned up {deleted} expired OTP records")
            
    except Exception as e:
        print(f"❌ Error cleaning up OTPs: {e}")
    finally:
        if conn:
            conn.close()

if __name__ == "__main__":
    print("🚀 Creating OTP verification table...\n")
    create_otp_table()
    print("\n🧹 Cleaning up any expired OTPs...")
    cleanup_expired_otps()
    print("\n✅ Setup complete!")