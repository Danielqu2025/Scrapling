"""JWT authentication, user store, and approval workflow."""

from __future__ import annotations

import os
import re
import sqlite3
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jose import JWTError, jwt
from passlib.context import CryptContext
from pydantic import BaseModel, Field

from _paths import DATA_DIR
from app.security import limiter

AUTH_DB_PATH = DATA_DIR / "auth.db"
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_HOURS = 24
USERNAME_RE = re.compile(r"^[a-zA-Z0-9_\u4e00-\u9fff]{2,32}$")

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")
bearer_scheme = HTTPBearer(auto_error=False)
_db_lock = threading.Lock()

router = APIRouter(prefix="/api/auth", tags=["auth"])


def auth_enabled() -> bool:
    return os.getenv("SH_EIA_AUTH_ENABLED", "0").lower() in {"1", "true", "yes", "on"}


def jwt_secret() -> str:
    secret = os.getenv("SH_EIA_JWT_SECRET", "").strip()
    if auth_enabled() and not secret:
        raise RuntimeError(
            "SH_EIA_AUTH_ENABLED=1 时必须设置 SH_EIA_JWT_SECRET（建议使用长随机字符串）。"
        )
    return secret or "dev-only-insecure-secret"


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _connect() -> sqlite3.Connection:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(AUTH_DB_PATH), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_auth_db() -> None:
    with _db_lock:
        conn = _connect()
        try:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS users (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    username TEXT NOT NULL UNIQUE COLLATE NOCASE,
                    password_hash TEXT NOT NULL,
                    display_name TEXT NOT NULL DEFAULT '',
                    role TEXT NOT NULL DEFAULT 'user',
                    status TEXT NOT NULL DEFAULT 'pending',
                    created_at TEXT NOT NULL,
                    approved_at TEXT,
                    approved_by INTEGER,
                    last_login_at TEXT
                );
                CREATE TABLE IF NOT EXISTS audit_logs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    actor_id INTEGER,
                    actor_username TEXT,
                    action TEXT NOT NULL,
                    target TEXT,
                    detail TEXT,
                    created_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_users_status ON users(status);
                CREATE INDEX IF NOT EXISTS idx_audit_created ON audit_logs(created_at DESC);
                """
            )
            conn.commit()
        finally:
            conn.close()


def hash_password(password: str) -> str:
    return pwd_context.hash(password)


def verify_password(plain: str, hashed: str) -> bool:
    return pwd_context.verify(plain, hashed)


def create_access_token(user: dict[str, Any], expires_hours: int = ACCESS_TOKEN_EXPIRE_HOURS) -> str:
    expire = datetime.now(timezone.utc) + timedelta(hours=expires_hours)
    payload = {
        "sub": str(user["id"]),
        "username": user["username"],
        "role": user["role"],
        "exp": expire,
    }
    return jwt.encode(payload, jwt_secret(), algorithm=ALGORITHM)


def write_audit(
    action: str,
    *,
    actor_id: int | None = None,
    actor_username: str | None = None,
    target: str | None = None,
    detail: str | None = None,
) -> None:
    with _db_lock:
        conn = _connect()
        try:
            conn.execute(
                """
                INSERT INTO audit_logs (actor_id, actor_username, action, target, detail, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (actor_id, actor_username, action, target, detail, utc_now()),
            )
            conn.commit()
        finally:
            conn.close()


def get_user_by_id(user_id: int) -> dict[str, Any] | None:
    with _db_lock:
        conn = _connect()
        try:
            row = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
            return dict(row) if row else None
        finally:
            conn.close()


def get_user_by_username(username: str) -> dict[str, Any] | None:
    with _db_lock:
        conn = _connect()
        try:
            row = conn.execute(
                "SELECT * FROM users WHERE username = ? COLLATE NOCASE",
                (username,),
            ).fetchone()
            return dict(row) if row else None
        finally:
            conn.close()


def list_users(*, status: str | None = None) -> list[dict[str, Any]]:
    with _db_lock:
        conn = _connect()
        try:
            if status:
                rows = conn.execute(
                    "SELECT * FROM users WHERE status = ? ORDER BY created_at DESC",
                    (status,),
                ).fetchall()
            else:
                rows = conn.execute("SELECT * FROM users ORDER BY created_at DESC").fetchall()
            return [dict(r) for r in rows]
        finally:
            conn.close()


def count_users_by_status() -> dict[str, int]:
    with _db_lock:
        conn = _connect()
        try:
            rows = conn.execute(
                "SELECT status, COUNT(*) AS c FROM users GROUP BY status"
            ).fetchall()
            return {str(r["status"]): int(r["c"]) for r in rows}
        finally:
            conn.close()


def count_admins_active() -> int:
    with _db_lock:
        conn = _connect()
        try:
            row = conn.execute(
                "SELECT COUNT(*) AS c FROM users WHERE role = 'admin' AND status = 'active'"
            ).fetchone()
            return int(row["c"]) if row else 0
        finally:
            conn.close()


def create_user(
    username: str,
    password: str,
    *,
    display_name: str = "",
    role: str = "user",
    status_value: str = "pending",
) -> dict[str, Any]:
    if not USERNAME_RE.match(username):
        raise ValueError("用户名需为 2–32 位字母、数字、下划线或中文。")
    if len(password) < 8:
        raise ValueError("密码至少 8 位。")
    if get_user_by_username(username):
        raise ValueError("用户名已存在。")
    now = utc_now()
    with _db_lock:
        conn = _connect()
        try:
            cur = conn.execute(
                """
                INSERT INTO users (
                    username, password_hash, display_name, role, status,
                    created_at, approved_at, approved_by
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    username,
                    hash_password(password),
                    display_name or username,
                    role,
                    status_value,
                    now,
                    now if status_value == "active" else None,
                    None,
                ),
            )
            conn.commit()
            user_id = int(cur.lastrowid)
        finally:
            conn.close()
    user = get_user_by_id(user_id)
    assert user is not None
    return user


def update_user_status(
    user_id: int,
    status_value: str,
    *,
    actor: dict[str, Any] | None = None,
) -> dict[str, Any]:
    user = get_user_by_id(user_id)
    if not user:
        raise ValueError("用户不存在。")
    if (
        user["role"] == "admin"
        and user["status"] == "active"
        and status_value != "active"
        and count_admins_active() <= 1
    ):
        raise ValueError("不能停用最后一个活跃管理员。")
    approved_at = user.get("approved_at")
    approved_by = user.get("approved_by")
    if status_value == "active" and user["status"] != "active":
        approved_at = utc_now()
        approved_by = actor["id"] if actor else None
    with _db_lock:
        conn = _connect()
        try:
            conn.execute(
                """
                UPDATE users
                SET status = ?, approved_at = ?, approved_by = ?
                WHERE id = ?
                """,
                (status_value, approved_at, approved_by, user_id),
            )
            conn.commit()
        finally:
            conn.close()
    updated = get_user_by_id(user_id)
    assert updated is not None
    return updated


def reset_user_password(user_id: int, new_password: str) -> None:
    if len(new_password) < 8:
        raise ValueError("密码至少 8 位。")
    user = get_user_by_id(user_id)
    if not user:
        raise ValueError("用户不存在。")
    with _db_lock:
        conn = _connect()
        try:
            conn.execute(
                "UPDATE users SET password_hash = ? WHERE id = ?",
                (hash_password(new_password), user_id),
            )
            conn.commit()
        finally:
            conn.close()


def delete_user(user_id: int, *, actor: dict[str, Any] | None = None) -> dict[str, Any]:
    user = get_user_by_id(user_id)
    if not user:
        raise ValueError("用户不存在。")
    if actor and int(actor["id"]) == int(user_id):
        raise ValueError("不能删除当前登录的管理员账号。")
    if (
        user["role"] == "admin"
        and user["status"] == "active"
        and count_admins_active() <= 1
    ):
        raise ValueError("不能删除最后一个活跃管理员。")
    with _db_lock:
        conn = _connect()
        try:
            conn.execute("DELETE FROM users WHERE id = ?", (user_id,))
            conn.commit()
        finally:
            conn.close()
    return user


def touch_last_login(user_id: int) -> None:
    with _db_lock:
        conn = _connect()
        try:
            conn.execute(
                "UPDATE users SET last_login_at = ? WHERE id = ?",
                (utc_now(), user_id),
            )
            conn.commit()
        finally:
            conn.close()


def list_audit_logs(limit: int = 100, offset: int = 0) -> list[dict[str, Any]]:
    with _db_lock:
        conn = _connect()
        try:
            rows = conn.execute(
                """
                SELECT * FROM audit_logs
                ORDER BY id DESC
                LIMIT ? OFFSET ?
                """,
                (limit, offset),
            ).fetchall()
            return [dict(r) for r in rows]
        finally:
            conn.close()


def public_user(user: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": user["id"],
        "username": user["username"],
        "display_name": user.get("display_name") or user["username"],
        "role": user["role"],
        "status": user["status"],
        "created_at": user.get("created_at"),
        "approved_at": user.get("approved_at"),
        "last_login_at": user.get("last_login_at"),
    }


def bootstrap_admin() -> None:
    username = os.getenv("SH_EIA_ADMIN_USERNAME", "").strip()
    password = os.getenv("SH_EIA_ADMIN_PASSWORD", "").strip()
    if not username or not password:
        return
    existing = get_user_by_username(username)
    if existing:
        if existing["role"] != "admin" or existing["status"] != "active":
            with _db_lock:
                conn = _connect()
                try:
                    conn.execute(
                        """
                        UPDATE users
                        SET role = 'admin', status = 'active',
                            password_hash = ?, approved_at = COALESCE(approved_at, ?)
                        WHERE id = ?
                        """,
                        (hash_password(password), utc_now(), existing["id"]),
                    )
                    conn.commit()
                finally:
                    conn.close()
            write_audit(
                "bootstrap_admin_update",
                actor_username="system",
                target=username,
                detail="启动时更新管理员账号",
            )
        return
    create_user(username, password, display_name=username, role="admin", status_value="active")
    write_audit(
        "bootstrap_admin_create",
        actor_username="system",
        target=username,
        detail="启动时创建管理员账号",
    )


def ensure_auth_ready() -> None:
    init_auth_db()
    if auth_enabled():
        jwt_secret()  # validate early
        bootstrap_admin()
        if count_admins_active() == 0:
            raise RuntimeError(
                "认证已开启但没有活跃管理员。请设置 SH_EIA_ADMIN_USERNAME / SH_EIA_ADMIN_PASSWORD 后重启。"
            )
    else:
        # Still create schema so enabling later is seamless; bootstrap if env present.
        bootstrap_admin()


LOCAL_USER: dict[str, Any] = {
    "id": 0,
    "username": "local",
    "display_name": "本地用户",
    "role": "admin",
    "status": "active",
}


class RegisterRequest(BaseModel):
    username: str = Field(min_length=2, max_length=32)
    password: str = Field(min_length=8, max_length=128)
    display_name: str = Field(default="", max_length=64)


class LoginRequest(BaseModel):
    username: str = Field(min_length=1, max_length=32)
    password: str = Field(min_length=1, max_length=128)


class ChangePasswordRequest(BaseModel):
    current_password: str = Field(min_length=1, max_length=128)
    new_password: str = Field(min_length=8, max_length=128)


def _decode_token(token: str) -> dict[str, Any]:
    try:
        payload = jwt.decode(token, jwt_secret(), algorithms=[ALGORITHM])
    except JWTError as exc:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="无效或过期的令牌") from exc
    user_id = payload.get("sub")
    if not user_id:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="无效令牌")
    user = get_user_by_id(int(user_id))
    if not user:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="用户不存在")
    if user["status"] != "active":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="账号未激活或已停用")
    return user


async def get_current_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
) -> dict[str, Any]:
    if not auth_enabled():
        return dict(LOCAL_USER)
    if credentials is None or credentials.scheme.lower() != "bearer":
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="未登录")
    return _decode_token(credentials.credentials)


async def require_admin(user: dict[str, Any] = Depends(get_current_user)) -> dict[str, Any]:
    if user.get("role") != "admin":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="需要管理员权限")
    return user


def get_token_from_request(request: Request) -> str | None:
    auth = request.headers.get("Authorization") or ""
    if auth.lower().startswith("bearer "):
        return auth[7:].strip()
    return request.cookies.get("sh_eia_token")


def user_from_request(request: Request) -> dict[str, Any] | None:
    if not auth_enabled():
        return dict(LOCAL_USER)
    token = get_token_from_request(request)
    if not token:
        return None
    try:
        return _decode_token(token)
    except HTTPException:
        return None


@router.get("/config")
def api_auth_config() -> dict[str, Any]:
    return {"auth_enabled": auth_enabled()}


@router.post("/register")
@limiter.limit("5/minute")
def api_register(request: Request, body: RegisterRequest) -> dict[str, Any]:
    del request
    if not auth_enabled():
        raise HTTPException(status_code=400, detail="当前未开启认证，无需注册。")
    try:
        user = create_user(
            body.username.strip(),
            body.password,
            display_name=(body.display_name or body.username).strip(),
            role="user",
            status_value="pending",
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    write_audit(
        "register",
        actor_id=user["id"],
        actor_username=user["username"],
        target=user["username"],
        detail="用户提交注册申请",
    )
    return {
        "status": "pending",
        "message": "注册申请已提交，请等待管理员审批后再登录。",
        "user": public_user(user),
    }


@router.post("/login")
@limiter.limit("10/minute")
def api_login(request: Request, body: LoginRequest) -> dict[str, Any]:
    del request
    if not auth_enabled():
        raise HTTPException(status_code=400, detail="当前未开启认证。")
    user = get_user_by_username(body.username.strip())
    if not user or not verify_password(body.password, user["password_hash"]):
        write_audit(
            "login_failed",
            actor_username=body.username.strip(),
            target=body.username.strip(),
            detail="用户名或密码错误",
        )
        raise HTTPException(status_code=401, detail="用户名或密码错误")
    if user["status"] == "pending":
        raise HTTPException(status_code=403, detail="账号待审批，请联系管理员")
    if user["status"] == "disabled":
        raise HTTPException(status_code=403, detail="账号已停用，请联系管理员")
    if user["status"] != "active":
        raise HTTPException(status_code=403, detail="账号不可用")
    touch_last_login(user["id"])
    token = create_access_token(user)
    write_audit(
        "login",
        actor_id=user["id"],
        actor_username=user["username"],
        target=user["username"],
        detail="登录成功",
    )
    return {
        "access_token": token,
        "token_type": "bearer",
        "expires_hours": ACCESS_TOKEN_EXPIRE_HOURS,
        "user": public_user(user),
    }


@router.get("/me")
def api_me(user: dict[str, Any] = Depends(get_current_user)) -> dict[str, Any]:
    if not auth_enabled():
        return {"auth_enabled": False, "user": public_user(user)}
    return {"auth_enabled": True, "user": public_user(user)}


@router.post("/change-password")
@limiter.limit("10/minute")
def api_change_password(
    request: Request,
    body: ChangePasswordRequest,
    user: dict[str, Any] = Depends(get_current_user),
) -> dict[str, Any]:
    del request
    if not auth_enabled() or not user.get("id"):
        raise HTTPException(status_code=400, detail="当前未开启认证。")
    db_user = get_user_by_id(int(user["id"]))
    if not db_user:
        raise HTTPException(status_code=404, detail="用户不存在")
    if not verify_password(body.current_password, db_user["password_hash"]):
        write_audit(
            "change_password_failed",
            actor_id=db_user["id"],
            actor_username=db_user["username"],
            target=db_user["username"],
            detail="当前密码不正确",
        )
        raise HTTPException(status_code=400, detail="当前密码不正确")
    if body.current_password == body.new_password:
        raise HTTPException(status_code=400, detail="新密码不能与当前密码相同")
    try:
        reset_user_password(db_user["id"], body.new_password)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    write_audit(
        "change_password",
        actor_id=db_user["id"],
        actor_username=db_user["username"],
        target=db_user["username"],
        detail="用户自行修改密码",
    )
    return {"status": "ok", "message": "密码已修改，请使用新密码重新登录。"}
