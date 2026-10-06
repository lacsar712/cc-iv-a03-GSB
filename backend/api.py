import os
from datetime import datetime, timedelta, timezone

from jose import JWTError, jwt
from litestar import Litestar, Request, get, post, put
from litestar.exceptions import HTTPException
from litestar.status_codes import HTTP_401_UNAUTHORIZED, HTTP_403_FORBIDDEN, HTTP_409_CONFLICT
from passlib.context import CryptContext

from db import SCHEMA, connect
from rules import judge

SECRET = os.environ.get("JWT_SECRET", "pvivscan-dev-secret")
pwd = CryptContext(schemes=["bcrypt"], deprecated="auto")
USERS = {
    "scanner": {"role": "writer", "password_hash": pwd.hash("scan123456")},
    "watcher": {"role": "reader", "password_hash": pwd.hash("watch123456")},
}


def dump(row):
    out = dict(row)
    for key, val in list(out.items()):
        if hasattr(val, "isoformat"):
            out[key] = val.isoformat()
    return out


def seed():
    with connect() as conn:
        conn.execute(SCHEMA)
        # 甲、乙两个汇流箱；甲箱留空，便于把上限调成 1 后做满员验收
        for name, limit in [("甲箱", 4), ("乙箱", 4)]:
            conn.execute(
                "INSERT INTO combiner_boxes (name, seat_limit) VALUES (%s,%s) "
                "ON CONFLICT (name) DO NOTHING",
                (name, limit),
            )
        box_rows = conn.execute("SELECT id, name FROM combiner_boxes").fetchall()
        box_id = {r["name"]: r["id"] for r in box_rows}

        n = conn.execute("SELECT COUNT(*) AS n FROM iv_scans").fetchone()["n"]
        if n == 0:
            now = datetime.now(timezone.utc)
            # 历史种子单都落在乙箱并已占位，甲箱初始 0 占位
            samples = [
                ("阵列A-串03", 41.2, 9.1, 0.78, "合格"),
                ("阵列B-串11", 38.0, 8.4, 0.61, "衰减"),
            ]
            yi = box_id["乙箱"]
            seat = 0
            for code, voc, isc, ff, expect in samples:
                verdict, reason = judge(ff)
                assert verdict == expect
                scan = conn.execute(
                    """INSERT INTO iv_scans
                       (box_id, string_code, voc_v, isc_a, fill_factor, status, verdict,
                        reason, created_by, created_at, processed_at)
                       VALUES (%s,%s,%s,%s,%s,'done',%s,%s,'scanner',%s,%s)
                       RETURNING id""",
                    (yi, code, voc, isc, ff, verdict, reason, now, now),
                ).fetchone()
                seat += 1
                conn.execute(
                    """INSERT INTO seat_holds (box_id, seat_no, scan_id, held_by, held_at)
                       VALUES (%s,%s,%s,'scanner',%s)""",
                    (yi, seat, scan["id"], now),
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
        raise HTTPException(status_code=HTTP_401_UNAUTHORIZED, detail="未登录")
    return user


def need_writer(request: Request):
    user = need_login(request)
    if user["role"] != "writer":
        raise HTTPException(status_code=HTTP_403_FORBIDDEN, detail="仅扫描员可操作")
    return user


def fetch_boxes(conn):
    """各箱已占座位 + 上限，供端子座位专页与提交选座使用。"""
    rows = conn.execute(
        """SELECT b.id, b.name, b.seat_limit,
                  h.id AS hold_id, h.seat_no, h.held_by, h.held_at,
                  h.scan_id, s.string_code, s.status, s.verdict
           FROM combiner_boxes b
           LEFT JOIN seat_holds h ON h.box_id = b.id
           LEFT JOIN iv_scans s ON s.id = h.scan_id
           ORDER BY b.id, h.seat_no"""
    ).fetchall()
    boxes = {}
    for r in rows:
        box = boxes.get(r["id"])
        if box is None:
            box = {
                "id": r["id"],
                "name": r["name"],
                "seat_limit": r["seat_limit"],
                "occupied": 0,
                "holds": [],
            }
            boxes[r["id"]] = box
        if r["hold_id"] is not None:
            box["occupied"] += 1
            box["holds"].append(
                {
                    "hold_id": r["hold_id"],
                    "seat_no": r["seat_no"],
                    "scan_id": r["scan_id"],
                    "string_code": r["string_code"],
                    "status": r["status"],
                    "verdict": r["verdict"],
                    "held_by": r["held_by"],
                    "held_at": r["held_at"].isoformat(),
                }
            )
    return [boxes[k] for k in sorted(boxes)]


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
        raise HTTPException(status_code=HTTP_401_UNAUTHORIZED, detail="用户名或密码错误")
    exp = datetime.now(timezone.utc) + timedelta(hours=8)
    token = jwt.encode(
        {"sub": username, "role": user["role"], "exp": exp}, SECRET, algorithm="HS256"
    )
    return {"access_token": token, "username": username, "role": user["role"]}


@get("/api/logs")
async def list_logs(request: Request) -> list:
    need_login(request)
    with connect() as conn:
        rows = conn.execute(
            """SELECT s.id, s.box_id, b.name AS box_name, s.string_code, s.voc_v, s.isc_a,
                      s.fill_factor, s.status, s.verdict, s.reason,
                      s.created_by, s.created_at, s.processed_at
               FROM iv_scans s
               LEFT JOIN combiner_boxes b ON b.id = s.box_id
               ORDER BY s.id DESC"""
        ).fetchall()
        return [dump(r) for r in rows]


@get("/api/boxes")
async def list_boxes(request: Request) -> list:
    need_login(request)
    with connect() as conn:
        return fetch_boxes(conn)


@put("/api/boxes/{box_id:int}")
async def update_box(request: Request, box_id: int) -> dict:
    need_writer(request)
    data = await request.json()
    raw = data.get("seat_limit")
    # 只改上限这一个数字，绝不触碰旧单座位
    if isinstance(raw, bool) or not isinstance(raw, int) or raw < 0:
        raise HTTPException(status_code=400, detail="座位上限必须是不小于 0 的整数")
    with connect() as conn:
        row = conn.execute(
            "UPDATE combiner_boxes SET seat_limit=%s WHERE id=%s "
            "RETURNING id, name, seat_limit",
            (raw, box_id),
        ).fetchone()
        if row is None:
            conn.rollback()
            raise HTTPException(status_code=404, detail="汇流箱不存在")
        conn.commit()
        boxes = {b["id"]: b for b in fetch_boxes(conn)}
        return boxes[row["id"]]


@get("/api/rejections")
async def list_rejections(request: Request) -> list:
    need_login(request)
    with connect() as conn:
        rows = conn.execute(
            """SELECT r.id, r.box_id, b.name AS box_name, r.string_code, r.voc_v, r.isc_a,
                      r.fill_factor, r.rejected_by, r.reason, r.rejected_at
               FROM seat_rejections r
               LEFT JOIN combiner_boxes b ON b.id = r.box_id
               ORDER BY r.id DESC"""
        ).fetchall()
        return [dump(r) for r in rows]


@post("/api/logs", status_code=201)
async def create_log(request: Request) -> dict:
    user = need_writer(request)
    data = await request.json()
    box_id = data.get("box_id")
    if isinstance(box_id, bool) or not isinstance(box_id, int):
        raise HTTPException(status_code=400, detail="必须点选汇流箱座位后再交扫描")
    code = (data.get("string_code") or "").strip()
    if not code:
        raise HTTPException(status_code=400, detail="组串编号不能为空")
    try:
        voc = float(data.get("voc_v"))
        isc = float(data.get("isc_a"))
        ff = float(data.get("fill_factor"))
    except (TypeError, ValueError):
        raise HTTPException(status_code=400, detail="电压电流与填充因子必须是数字")

    now = datetime.now(timezone.utc)
    conn = connect()
    try:
        # 锁箱行：末席对撞的两笔在此排队，只可能有一笔读到未满
        box = conn.execute(
            "SELECT id, name, seat_limit FROM combiner_boxes WHERE id=%s FOR UPDATE",
            (box_id,),
        ).fetchone()
        if box is None:
            conn.rollback()
            raise HTTPException(status_code=400, detail="汇流箱不存在")
        occupied = conn.execute(
            "SELECT COUNT(*) AS n FROM seat_holds WHERE box_id=%s", (box_id,)
        ).fetchone()["n"]

        if occupied >= box["seat_limit"]:
            # 满员：不写扫描单，只留挡回痕迹，整笔退回
            reason = (
                f"{box['name']}端子座位已满（{occupied}/{box['seat_limit']}），本笔整笔退回，未占座"
            )
            conn.execute(
                """INSERT INTO seat_rejections
                   (box_id, string_code, voc_v, isc_a, fill_factor, rejected_by, reason, rejected_at)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s)""",
                (box_id, code, voc, isc, ff, user["username"], reason, now),
            )
            conn.commit()
            raise HTTPException(status_code=HTTP_409_CONFLICT, detail=reason)

        row = conn.execute(
            """INSERT INTO iv_scans
               (box_id, string_code, voc_v, isc_a, fill_factor, status, created_by, created_at)
               VALUES (%s,%s,%s,%s,%s,'pending',%s,%s)
               RETURNING id, box_id, string_code, voc_v, isc_a, fill_factor, status, verdict,
                         reason, created_by, created_at, processed_at""",
            (box_id, code, voc, isc, ff, user["username"], now),
        ).fetchone()
        seat_no = conn.execute(
            "SELECT COALESCE(MAX(seat_no), 0) + 1 AS next_seat FROM seat_holds WHERE box_id=%s",
            (box_id,),
        ).fetchone()["next_seat"]
        # 占位与扫描单同生共死，在同一事务提交，随单冻住
        conn.execute(
            """INSERT INTO seat_holds (box_id, seat_no, scan_id, held_by, held_at)
               VALUES (%s,%s,%s,%s,%s)""",
            (box_id, seat_no, row["id"], user["username"], now),
        )
        conn.commit()
        out = dict(row)
        out["box_name"] = box["name"]
        out["seat_no"] = seat_no
        return dump(out)
    finally:
        conn.close()


app = Litestar(
    route_handlers=[
        health,
        login,
        list_logs,
        list_boxes,
        update_box,
        list_rejections,
        create_log,
    ]
)
