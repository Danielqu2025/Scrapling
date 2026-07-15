"""Admin APIs: user approval, status, password reset, delete, stats, audit logs."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from app.auth import (
    count_users_by_status,
    delete_user,
    get_user_by_id,
    list_audit_logs,
    list_users,
    public_user,
    require_admin,
    reset_user_password,
    update_user_status,
    write_audit,
)

router = APIRouter(prefix="/api/admin", tags=["admin"])


class PasswordResetRequest(BaseModel):
    new_password: str = Field(min_length=8, max_length=128)


class StatusRequest(BaseModel):
    status: str = Field(pattern="^(pending|active|disabled)$")


@router.get("/users")
def admin_list_users(
    status: str | None = Query(default=None),
    admin: dict[str, Any] = Depends(require_admin),
) -> dict[str, Any]:
    del admin
    if status and status not in {"pending", "active", "disabled"}:
        raise HTTPException(status_code=400, detail="无效的状态过滤")
    users = [public_user(u) for u in list_users(status=status)]
    return {"count": len(users), "users": users}


@router.get("/users/pending")
def admin_pending_users(admin: dict[str, Any] = Depends(require_admin)) -> dict[str, Any]:
    del admin
    users = [public_user(u) for u in list_users(status="pending")]
    return {"count": len(users), "users": users}


@router.post("/users/{user_id}/approve")
def admin_approve_user(
    user_id: int,
    admin: dict[str, Any] = Depends(require_admin),
) -> dict[str, Any]:
    try:
        user = update_user_status(user_id, "active", actor=admin)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    write_audit(
        "approve_user",
        actor_id=admin["id"],
        actor_username=admin["username"],
        target=user["username"],
        detail=f"批准用户 #{user_id}",
    )
    return {"status": "ok", "user": public_user(user)}


@router.post("/users/{user_id}/disable")
def admin_disable_user(
    user_id: int,
    admin: dict[str, Any] = Depends(require_admin),
) -> dict[str, Any]:
    try:
        user = update_user_status(user_id, "disabled", actor=admin)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    write_audit(
        "disable_user",
        actor_id=admin["id"],
        actor_username=admin["username"],
        target=user["username"],
        detail=f"停用用户 #{user_id}",
    )
    return {"status": "ok", "user": public_user(user)}


@router.post("/users/{user_id}/activate")
def admin_activate_user(
    user_id: int,
    admin: dict[str, Any] = Depends(require_admin),
) -> dict[str, Any]:
    try:
        user = update_user_status(user_id, "active", actor=admin)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    write_audit(
        "activate_user",
        actor_id=admin["id"],
        actor_username=admin["username"],
        target=user["username"],
        detail=f"激活用户 #{user_id}",
    )
    return {"status": "ok", "user": public_user(user)}


@router.post("/users/{user_id}/reset-password")
def admin_reset_password(
    user_id: int,
    body: PasswordResetRequest,
    admin: dict[str, Any] = Depends(require_admin),
) -> dict[str, Any]:
    try:
        reset_user_password(user_id, body.new_password)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    user = get_user_by_id(user_id)
    if not user:
        raise HTTPException(status_code=404, detail="用户不存在")
    write_audit(
        "reset_password",
        actor_id=admin["id"],
        actor_username=admin["username"],
        target=user["username"],
        detail=f"重置用户 #{user_id} 密码",
    )
    return {"status": "ok", "message": "密码已重置", "user": public_user(user)}


@router.delete("/users/{user_id}")
def admin_delete_user(
    user_id: int,
    admin: dict[str, Any] = Depends(require_admin),
) -> dict[str, Any]:
    try:
        user = delete_user(user_id, actor=admin)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    write_audit(
        "delete_user",
        actor_id=admin["id"],
        actor_username=admin["username"],
        target=user["username"],
        detail=f"删除用户 #{user_id}",
    )
    return {"status": "ok", "message": "用户已删除", "user": public_user(user)}


@router.patch("/users/{user_id}/status")
def admin_set_status(
    user_id: int,
    body: StatusRequest,
    admin: dict[str, Any] = Depends(require_admin),
) -> dict[str, Any]:
    try:
        user = update_user_status(user_id, body.status, actor=admin)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    write_audit(
        "set_status",
        actor_id=admin["id"],
        actor_username=admin["username"],
        target=user["username"],
        detail=f"将用户 #{user_id} 状态设为 {body.status}",
    )
    return {"status": "ok", "user": public_user(user)}


@router.get("/stats")
def admin_stats(admin: dict[str, Any] = Depends(require_admin)) -> dict[str, Any]:
    del admin
    by_status = count_users_by_status()
    users = list_users()
    return {
        "users_total": len(users),
        "users_by_status": {
            "pending": by_status.get("pending", 0),
            "active": by_status.get("active", 0),
            "disabled": by_status.get("disabled", 0),
        },
        "admins": sum(1 for u in users if u["role"] == "admin" and u["status"] == "active"),
    }


@router.get("/audit")
def admin_audit(
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    admin: dict[str, Any] = Depends(require_admin),
) -> dict[str, Any]:
    del admin
    logs = list_audit_logs(limit=limit, offset=offset)
    return {"count": len(logs), "logs": logs}


@router.get("/login-logs")
def admin_login_logs(
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    admin: dict[str, Any] = Depends(require_admin),
) -> dict[str, Any]:
    del admin
    logs = list_audit_logs(
        limit=limit,
        offset=offset,
        actions=["login", "login_failed"],
    )
    return {"count": len(logs), "logs": logs}
