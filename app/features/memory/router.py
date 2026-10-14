import uuid

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.features.group_session.models import GroupParticipant, GroupSession
from app.features.memory.models import MemoryDoc
from app.features.users.dependencies import get_verified_user
from app.features.users.models import User

router = APIRouter()


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
    db: Session = Depends(get_db),
    current_user: User = Depends(get_verified_user),
):
    docs = db.query(MemoryDoc).filter(
        MemoryDoc.scope_type == "user", MemoryDoc.scope_id == current_user.id,
    ).order_by(MemoryDoc.updated_at.desc()).all()
    return [_serialize(doc) for doc in docs]


@router.get("/sessions/{session_id}/memory/docs")
def list_session_memory_docs(
    session_id: uuid.UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_verified_user),
):
    if not _is_session_member(db, session_id, current_user.id):
        raise HTTPException(status_code=403, detail="Not a member of this session")
    docs = db.query(MemoryDoc).filter(
        MemoryDoc.scope_type == "session", MemoryDoc.scope_id == session_id,
    ).order_by(MemoryDoc.updated_at.desc()).all()
    return [_serialize(doc) for doc in docs]


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
        if not _is_session_member(db, doc.scope_id, current_user.id):
            raise HTTPException(status_code=403, detail="Not a member of this session")
    else:
        raise HTTPException(status_code=403, detail="Not allowed to delete this memory document")
    db.delete(doc)
    db.commit()
    return {"message": "Memory document deleted"}
