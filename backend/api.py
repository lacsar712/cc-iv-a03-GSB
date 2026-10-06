import os
from datetime import datetime, timedelta, timezone

from jose import JWTError, jwt
from litestar import Litestar, Request, get, post
from litestar.exceptions import HTTPException
from psycopg.errors import UniqueViolation

from db import SCHEMA, connect
from rules import judge

SECRET = os.environ.get("JWT_SECRET", "pvivscan-dev-secret")

from passlib.context import CryptContext
pwd = CryptContext(schemes=["bcrypt"], deprecated="auto")
USERS = {
    "scanner": {"role": "writer", "password_hash": pwd.hash("scan123456")},
    "watcher": {"role": "reader", "password_hash": pwd.hash("watch123456")},
}


class SeatRejected(Exception):
    """座位占位失败：整笔扫描退回，并留挡回痕迹。"""

    def __init__(self, reason: str, status: int = 409):
        super().__init__(reason)
        self.reason = reason
        self.status = status


def now():
    return datetime.now(timezone.utc)


def dump(row):
    out = dict(row)
    for key, val in list(out.items()):
        if hasattr(val, "isoformat"):
            out[key] = val.isoformat()
    return out


def seed():
    with connect() as conn:
        conn.execute(SCHEMA)
        n = conn.execute("SELECT COUNT(*) AS n FROM iv_scans").fetchone()["n"]
        if n == 0:
            ts = now()
            samples = [
                ("阵列A-串03", 41.2, 9.1, 0.78, "合格"),
                ("阵列B-串11", 38.0, 8.4, 0.61, "衰减"),
            ]
            for code, voc, isc, ff, expect in samples:
                verdict, reason = judge(ff)
                assert verdict == expect
                conn.execute(
                    """INSERT INTO iv_scans
                       (string_code, voc_v, isc_a, fill_factor, status, verdict, reason,
                        created_by, created_at, processed_at)
                       VALUES (%s,%s,%s,%s,'done',%s,%s,'scanner',%s,%s)""",
                    (code, voc, isc, ff, verdict, reason, ts, ts),
                )
        conn.commit()


seed()


def user_from(request: Request):
    auth = request.headers.get("authorization", "")
    if not auth.lower().startswith("bearer "):
        return None
    try:
        payload = jwt.decode(auth.split(" ", 1)[1].strip(), SECRET, algorithms=["HS256"])
    except JWTError:
        return None
    sub = payload.get("sub")
    if sub not in USERS:
        return None
    return {"username": sub, "role": payload.get("role")}


def need_login(request: Request):
    user = user_from(request)
    if user is None:
        raise HTTPException(status_code=401, detail="未登录")
    return user


def need_writer(request: Request, action: str = "提交IV扫描"):
    user = need_login(request)
    if user["role"] != "writer":
        raise HTTPException(status_code=403, detail=f"仅扫描员可{action}")
    return user


@get("/api/health")
async def health() -> dict:
    return {"status": "ok", "service": "pv-string-iv-scan"}


@post("/api/auth/login")
async def login(request: Request) -> dict:
    data = await request.json()
    username = (data.get("username") or "").strip()
    password = data.get("password") or ""
    user = USERS.get(username)
    if not user or not pwd.verify(password, user["password_hash"]):
        raise HTTPException(status_code=401, detail="用户名或密码错误")
    exp = now() + timedelta(hours=8)
    token = jwt.encode(
        {"sub": username, "role": user["role"], "exp": exp}, SECRET, algorithm="HS256"
    )
    return {"access_token": token, "username": username, "role": user["role"]}


@get("/api/boxes")
async def list_boxes(request: Request) -> list:
    need_login(request)
    with connect() as conn:
        boxes = conn.execute(
            """SELECT box_code, seat_capacity, created_by, created_at, updated_at
               FROM combiner_boxes ORDER BY box_code"""
        ).fetchall()
        occ = conn.execute(
            """SELECT s.box_code, s.seat_no, s.id AS scan_id, s.string_code,
                      s.status, s.created_by, s.created_at
               FROM iv_scans s
               WHERE s.box_code IS NOT NULL
               ORDER BY s.box_code, s.seat_no"""
        ).fetchall()
    by_box = {}
    for row in occ:
        by_box.setdefault(row["box_code"], []).append(
            {
                "seat_no": row["seat_no"],
                "scan_id": row["scan_id"],
                "string_code": row["string_code"],
                "status": row["status"],
                "created_by": row["created_by"],
                "created_at": row["created_at"].isoformat(),
            }
        )
    out = []
    for b in boxes:
        item = dump(b)
        item["occupations"] = by_box.get(b["box_code"], [])
        item["occupied"] = len(item["occupations"])
        out.append(item)
    return out


@post("/api/boxes", status_code=201)
async def upsert_box(request: Request) -> dict:
    user = need_writer(request, "设置汇流箱座位上限")
    data = await request.json()
    box_code = (data.get("box_code") or "").strip()
    if not box_code:
        raise HTTPException(status_code=400, detail="汇流箱编号不能为空")
    capacity = data.get("seat_capacity")
    if not isinstance(capacity, int) or isinstance(capacity, bool) or capacity <= 0:
        raise HTTPException(status_code=400, detail="座位上限必须是正整数")
    ts = now()
    with connect() as conn:
        # 只改上限本身：旧单占住的座位随单冻结，不会被动到
        row = conn.execute(
            """INSERT INTO combiner_boxes
                 (box_code, seat_capacity, created_by, created_at, updated_at)
               VALUES (%s,%s,%s,%s,%s)
               ON CONFLICT (box_code) DO UPDATE
                 SET seat_capacity = EXCLUDED.seat_capacity,
                     updated_at = EXCLUDED.updated_at
               RETURNING box_code, seat_capacity, created_by, created_at, updated_at""",
            (box_code, capacity, user["username"], ts, ts),
        ).fetchone()
        conn.commit()
        return dump(row)


@get("/api/rejections")
async def list_rejections(request: Request) -> list:
    need_login(request)
    with connect() as conn:
        rows = conn.execute(
            """SELECT id, box_code, seat_no, string_code, voc_v, isc_a, fill_factor,
                      reason, created_by, created_at
               FROM seat_rejections ORDER BY id DESC LIMIT 100"""
        ).fetchall()
        return [dump(r) for r in rows]


@get("/api/logs")
async def list_logs(request: Request) -> list:
    need_login(request)
    with connect() as conn:
        rows = conn.execute(
            """SELECT id, string_code, box_code, seat_no, voc_v, isc_a, fill_factor,
                      status, verdict, reason, created_by, created_at, processed_at
               FROM iv_scans ORDER BY id DESC"""
        ).fetchall()
        return [dump(r) for r in rows]


@post("/api/logs", status_code=201)
async def create_log(request: Request) -> dict:
    user = need_writer(request)
    data = await request.json()
    code = (data.get("string_code") or "").strip()
    if not code:
        raise HTTPException(status_code=400, detail="组串编号不能为空")
    try:
        voc = float(data.get("voc_v"))
        isc = float(data.get("isc_a"))
        ff = float(data.get("fill_factor"))
    except (TypeError, ValueError):
        raise HTTPException(status_code=400, detail="电压电流与填充因子必须是数字")

    box_code = (data.get("box_code") or "").strip()
    seat_no = data.get("seat_no")
    # 交扫描必须点座位：没点箱子或没点座位，整笔退回
    if not box_code or not isinstance(seat_no, int) or isinstance(seat_no, bool):
        _trace(box_code or None, None, code, voc, isc, ff, user["username"],
               "未点选端子座位，整笔退回", status=400)
    if seat_no <= 0:
        _trace(box_code, seat_no if isinstance(seat_no, int) else None, code,
               voc, isc, ff, user["username"], "座位号必须从1开始，整笔退回", status=400)

    ts = now()
    with connect() as conn:
        try:
            with conn.transaction():
                # 锁住箱子行：同箱并发提交在此串行，末席对撞只可能一笔通过
                box = conn.execute(
                    """SELECT box_code, seat_capacity FROM combiner_boxes
                       WHERE box_code = %s FOR UPDATE""",
                    (box_code,),
                ).fetchone()
                if box is None:
                    raise SeatRejected(f"汇流箱 {box_code} 尚未设置座位上限，整笔退回", 400)
                capacity = box["seat_capacity"]
                if seat_no > capacity:
                    raise SeatRejected(
                        f"{box_code}箱座位上限 {capacity}，没有第 {seat_no} 席，整笔退回", 400
                    )
                taken = conn.execute(
                    """SELECT seat_no FROM iv_scans
                       WHERE box_code = %s AND seat_no = %s FOR UPDATE""",
                    (box_code, seat_no),
                ).fetchone()
                if taken is not None:
                    raise SeatRejected(
                        f"{box_code}箱第 {seat_no} 席已被占用，整笔退回"
                    )
                row = conn.execute(
                    """INSERT INTO iv_scans
                       (string_code, box_code, seat_no, voc_v, isc_a, fill_factor,
                        status, created_by, created_at)
                       VALUES (%s,%s,%s,%s,%s,%s,'pending',%s,%s)
                       RETURNING id, string_code, box_code, seat_no, voc_v, isc_a,
                                 fill_factor, status, verdict, reason,
                                 created_by, created_at, processed_at""",
                    (code, box_code, seat_no, voc, isc, ff, user["username"], ts),
                ).fetchone()
        except SeatRejected as rej:
            conn.rollback()
            conn.execute(
                """INSERT INTO seat_rejections
                   (box_code, seat_no, string_code, voc_v, isc_a, fill_factor,
                    reason, created_by, created_at)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                (box_code, seat_no, code, voc, isc, ff, rej.reason,
                 user["username"], now()),
            )
            conn.commit()
            raise HTTPException(status_code=rej.status, detail=rej.reason)
        except UniqueViolation:
            # 唯一索引兜底：末席对撞的败者同样整笔退回并留痕
            conn.rollback()
            reason = f"{box_code}箱第 {seat_no} 席对撞落败（名额已满），整笔退回"
            conn.execute(
                """INSERT INTO seat_rejections
                   (box_code, seat_no, string_code, voc_v, isc_a, fill_factor,
                    reason, created_by, created_at)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                (box_code, seat_no, code, voc, isc, ff, reason, user["username"], now()),
            )
            conn.commit()
            raise HTTPException(status_code=409, detail=reason)
        conn.commit()
        return dump(row)


def _trace(box_code, seat_no, code, voc, isc, ff, username, reason, status):
    with connect() as conn:
        conn.execute(
            """INSERT INTO seat_rejections
               (box_code, seat_no, string_code, voc_v, isc_a, fill_factor,
                reason, created_by, created_at)
               VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
            (box_code, seat_no, code, voc, isc, ff, reason, username, now()),
        )
        conn.commit()
    raise HTTPException(status_code=status, detail=reason)


app = Litestar(route_handlers=[
    health, login, list_logs, create_log, list_boxes, upsert_box, list_rejections,
])
