import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field, field_validator
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.features.group_session.models import GroupParticipant, GroupSession
from app.features.memory.models import MemoryDoc
from app.features.memory.procedures import (
    MAX_PROCEDURAL_CONTENT_CHARS,
    MAX_PROCEDURAL_DESCRIPTION_CHARS,
    MAX_PROCEDURAL_DOCS,
    normalize_procedural_text,
    validate_procedural_path,
)
from app.features.users.dependencies import get_verified_user
from app.features.users.models import User

router = APIRouter()


class ProceduralDocWrite(BaseModel):
    description: str
    content: str
    if_version: int | None = Field(default=None, ge=1)

    @field_validator("description", "content")
    @classmethod
    def normalize_and_check_size(cls, value: str, info):
        value = normalize_procedural_text(value)
        limit = MAX_PROCEDURAL_DESCRIPTION_CHARS if info.field_name == "description" else MAX_PROCEDURAL_CONTENT_CHARS
        if not value.strip() or len(value) > limit:
            raise ValueError(f"{info.field_name} must be non-empty and at most {limit} characters")
        return value


def _serialize(doc: MemoryDoc) -> dict:
    return {
        "id": doc.id,
        "scope_type": doc.scope_type,
        "scope_id": doc.scope_id,
        "kind": doc.kind,
        "path": doc.path,
        "description": doc.description,
        "content": doc.content,
        "version": doc.version,
        "author_user_id": doc.author_user_id,
        "created_at": doc.created_at,
        "updated_at": doc.updated_at,
    }


def _is_session_member(db: Session, session_id, user_id) -> bool:
    session = db.query(GroupSession).filter(GroupSession.id == session_id).first()
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")
    return session.created_by == user_id or db.query(GroupParticipant).filter(
        GroupParticipant.session_id == session_id,
        GroupParticipant.user_id == user_id,
    ).first() is not None


@router.get("/memory/docs")
def list_user_memory_docs(
    kind: str = Query(default="all", pattern="^(all|semantic|procedural)$"),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_verified_user),
):
    query = db.query(MemoryDoc).filter(
        MemoryDoc.scope_type == "user", MemoryDoc.scope_id == current_user.id,
    )
    if kind != "all":
        query = query.filter(MemoryDoc.kind == kind)
    docs = query.order_by(MemoryDoc.updated_at.desc()).all()
    return [_serialize(doc) for doc in docs]


@router.get("/sessions/{session_id}/memory/docs")
def list_session_memory_docs(
    session_id: uuid.UUID,
    kind: str = Query(default="all", pattern="^(all|semantic|procedural)$"),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_verified_user),
):
    if not _is_session_member(db, session_id, current_user.id):
        raise HTTPException(status_code=403, detail="Not a member of this session")
    query = db.query(MemoryDoc).filter(
        MemoryDoc.scope_type == "session", MemoryDoc.scope_id == session_id,
    )
    if kind != "all":
        query = query.filter(MemoryDoc.kind == kind)
    docs = query.order_by(MemoryDoc.updated_at.desc()).all()
    return [_serialize(doc) for doc in docs]


def _put_procedural_doc(db: Session, scope_type: str, scope_id, path: str, body: ProceduralDocWrite, actor_id):
    try:
        validate_procedural_path(path)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    doc = db.query(MemoryDoc).filter_by(
        scope_type=scope_type, scope_id=scope_id, path=path, kind="procedural",
    ).first()
    if doc:
        if body.if_version is not None and body.if_version != doc.version:
            raise HTTPException(status_code=409, detail={"message": "Version conflict", "current_version": doc.version})
        doc.description = body.description
        doc.content = body.content
        doc.version += 1
        doc.author_user_id = actor_id
        doc.updated_at = datetime.utcnow()
    else:
        count = db.query(MemoryDoc).filter_by(
            scope_type=scope_type, scope_id=scope_id, kind="procedural",
        ).count()
        if count >= MAX_PROCEDURAL_DOCS:
            raise HTTPException(status_code=422, detail=f"A scope may have at most {MAX_PROCEDURAL_DOCS} procedural docs")
        doc = MemoryDoc(
            id=uuid.uuid4(), scope_type=scope_type, scope_id=scope_id, kind="procedural",
            path=path, description=body.description, content=body.content, version=1,
            author_user_id=actor_id, created_at=datetime.utcnow(), updated_at=datetime.utcnow(),
        )
        db.add(doc)
    db.commit()
    db.refresh(doc)
    return _serialize(doc)


@router.get("/memory/procedures")
def list_user_procedures(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_verified_user),
):
    docs = db.query(MemoryDoc).filter_by(
        scope_type="user", scope_id=current_user.id, kind="procedural",
    ).order_by(MemoryDoc.updated_at.desc()).all()
    return [{key: value for key, value in _serialize(doc).items() if key in {
        "id", "path", "description", "content", "version", "updated_at",
    }} for doc in docs]


@router.put("/memory/procedures/{path:path}")
def put_user_procedure(
    path: str,
    body: ProceduralDocWrite,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_verified_user),
):
    return _put_procedural_doc(db, "user", current_user.id, path, body, current_user.id)


@router.get("/sessions/{session_id}/procedures")
def list_session_procedures(
    session_id: uuid.UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_verified_user),
):
    if not _is_session_member(db, session_id, current_user.id):
        raise HTTPException(status_code=403, detail="Not a member of this session")
    docs = db.query(MemoryDoc).filter_by(
        scope_type="session", scope_id=session_id, kind="procedural",
    ).order_by(MemoryDoc.updated_at.desc()).all()
    return [_serialize(doc) for doc in docs]


@router.put("/sessions/{session_id}/procedures/{path:path}")
def put_session_procedure(
    session_id: uuid.UUID,
    path: str,
    body: ProceduralDocWrite,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_verified_user),
):
    session = db.query(GroupSession).filter(GroupSession.id == session_id).first()
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")
    if session.created_by != current_user.id:
        raise HTTPException(status_code=403, detail="Only the session creator may write procedural docs")
    return _put_procedural_doc(db, "session", session_id, path, body, current_user.id)


@router.delete("/memory/docs/{doc_id}")
def delete_memory_doc(
    doc_id: uuid.UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_verified_user),
):
    doc = db.query(MemoryDoc).filter(MemoryDoc.id == doc_id).first()
    if not doc:
        raise HTTPException(status_code=404, detail="Memory document not found")
    if doc.scope_type == "user":
        if doc.scope_id != current_user.id:
            raise HTTPException(status_code=403, detail="Not allowed to delete this memory document")
    elif doc.scope_type == "session":
        if doc.kind == "procedural":
            session = db.query(GroupSession).filter(GroupSession.id == doc.scope_id).first()
            if not session:
                raise HTTPException(status_code=404, detail="Session not found")
            if session.created_by != current_user.id:
                raise HTTPException(status_code=403, detail="Only the session creator may delete this procedural document")
        elif not _is_session_member(db, doc.scope_id, current_user.id):
            raise HTTPException(status_code=403, detail="Not a member of this session")
    else:
        raise HTTPException(status_code=403, detail="Not allowed to delete this memory document")
    db.delete(doc)
    db.commit()
    return {"message": "Memory document deleted"}
