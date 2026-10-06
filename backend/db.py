import os
import psycopg
from psycopg.rows import dict_row

DSN = os.environ.get("DATABASE_URL", "postgresql://app:app@localhost:54402/pvivscan")


def connect():
    return psycopg.connect(DSN, row_factory=dict_row)


SCHEMA = """
CREATE TABLE IF NOT EXISTS combiner_boxes (
    id serial PRIMARY KEY,
    name text NOT NULL UNIQUE,
    seat_limit integer NOT NULL CHECK (seat_limit >= 0),
    created_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS iv_scans (
    id serial PRIMARY KEY,
    box_id integer REFERENCES combiner_boxes(id),
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

-- 占位随单冻住：一笔扫描一个座，箱内座号唯一；旧单座位不随后续上限调整而变动
CREATE TABLE IF NOT EXISTS seat_holds (
    id serial PRIMARY KEY,
    box_id integer NOT NULL REFERENCES combiner_boxes(id),
    seat_no integer NOT NULL,
    scan_id integer NOT NULL REFERENCES iv_scans(id),
    held_by text NOT NULL,
    held_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (box_id, seat_no),
    UNIQUE (scan_id)
);

-- 满员整笔退回时留下挡回痕迹
CREATE TABLE IF NOT EXISTS seat_rejections (
    id serial PRIMARY KEY,
    box_id integer NOT NULL REFERENCES combiner_boxes(id),
    string_code text NOT NULL,
    voc_v double precision NOT NULL,
    isc_a double precision NOT NULL,
    fill_factor double precision NOT NULL,
    rejected_by text NOT NULL,
    reason text NOT NULL,
    rejected_at timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_seat_holds_box ON seat_holds(box_id);
CREATE INDEX IF NOT EXISTS idx_iv_scans_box ON iv_scans(box_id);
CREATE INDEX IF NOT EXISTS idx_seat_rejections_box ON seat_rejections(box_id);

-- 兼容旧库：若 iv_scans 已存在则补上 box_id
ALTER TABLE iv_scans ADD COLUMN IF NOT EXISTS box_id integer REFERENCES combiner_boxes(id);

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
