import os
import psycopg
from psycopg.rows import dict_row

DSN = os.environ.get("DATABASE_URL", "postgresql://app:app@localhost:54402/pvivscan")


def connect():
    return psycopg.connect(DSN, row_factory=dict_row)


SCHEMA = """
CREATE TABLE IF NOT EXISTS iv_scans (
    id serial PRIMARY KEY,
    string_code text NOT NULL,
    voc_v double precision NOT NULL,
    isc_a double precision NOT NULL,
    fill_factor double precision NOT NULL,
    status text NOT NULL DEFAULT 'pending',
    verdict text,
    reason text,
    created_by text NOT NULL,
    created_at timestamptz NOT NULL,
    processed_at timestamptz
);
ALTER TABLE iv_scans ADD COLUMN IF NOT EXISTS box_code text;
ALTER TABLE iv_scans ADD COLUMN IF NOT EXISTS seat_no integer;
-- 同一汇流箱同一座位只许一笔扫描占住，数据库层兜底末席对撞
CREATE UNIQUE INDEX IF NOT EXISTS uq_iv_scans_box_seat
    ON iv_scans (box_code, seat_no)
    WHERE box_code IS NOT NULL;

CREATE TABLE IF NOT EXISTS combiner_boxes (
    box_code text PRIMARY KEY,
    seat_capacity integer NOT NULL CHECK (seat_capacity > 0),
    created_by text NOT NULL,
    created_at timestamptz NOT NULL,
    updated_at timestamptz NOT NULL
);

CREATE TABLE IF NOT EXISTS seat_rejections (
    id serial PRIMARY KEY,
    box_code text,
    seat_no integer,
    string_code text,
    voc_v double precision,
    isc_a double precision,
    fill_factor double precision,
    reason text NOT NULL,
    created_by text NOT NULL,
    created_at timestamptz NOT NULL
);

CREATE OR REPLACE FUNCTION notify_iv_scan() RETURNS trigger AS $$
BEGIN
  PERFORM pg_notify('iv_scan_new', NEW.id::text);
  RETURN NEW;
END;
$$ LANGUAGE plpgsql;
DROP TRIGGER IF EXISTS trg_iv_scan_notify ON iv_scans;
CREATE TRIGGER trg_iv_scan_notify
AFTER INSERT ON iv_scans
FOR EACH ROW EXECUTE FUNCTION notify_iv_scan();
"""
