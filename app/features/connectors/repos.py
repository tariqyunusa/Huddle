import logging, uuid
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.db.session import SessionLocal, get_db
from app.features.users.dependencies import get_current_user
from app.features.users.models import User, GitHubInstallation
from app.features.connectors.github_app import (
    installation_token, list_installation_repos, InstallationGone,
)
from app.features.connectors.github import download_repo, GitHubError
# Adjust these to wherever your models live (same place your WS route imports from):
# from ... import Repo, CodeFile, GroupSession, GroupParticipant
from app.features.group_session.models import GroupSession, GroupParticipant
from app.features.users.models import Repo, CodeFile

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/sessions/{session_id}/repos", tags=["repos"])


class AttachRepo(BaseModel):
    installation_id: int
    full_name: str
    confirm_private: bool = False


def require_member(db: Session, session_id: str, user_id) -> "GroupSession":
    s = db.query(GroupSession).filter(GroupSession.id == session_id).first()
    if not s:
        raise HTTPException(404, "Session not found")
    is_member = str(s.created_by) == str(user_id) or db.query(GroupParticipant).filter(
        GroupParticipant.session_id == session_id,
        GroupParticipant.user_id == user_id).first() is not None
    if not is_member:
        raise HTTPException(403, "Not a member of this session")
    return s


async def import_repo(repo_id: uuid.UUID):
    db = SessionLocal()
    try:
        repo = db.get(Repo, repo_id)
        repo.status = "syncing"
        db.commit()
        owner, name = repo.name.split("/", 1)
        token = await installation_token(repo.installation_id)
        sha, files = await download_repo(owner, name, repo.branch, token)
        db.query(CodeFile).filter(CodeFile.repo_id == repo.id).delete()
        db.add_all([CodeFile(id=uuid.uuid4(), repo_id=repo.id, **f) for f in files])
        repo.status, repo.error = "ready", None
        repo.file_count, repo.commit_sha = len(files), sha
        repo.last_synced_at = func.now()
        db.commit()
    except Exception as e:
        db.rollback()
        logger.exception("Repo import failed for %s", repo_id)
        msg = str(e) if isinstance(e, GitHubError) else (
            "GitHub access was revoked." if isinstance(e, InstallationGone) else "Import failed.")
        repo = db.get(Repo, repo_id)
        if repo:
            repo.status, repo.error = "failed", msg
            db.commit()
    finally:
        db.close()


@router.post("")
async def attach_repo(session_id: str, body: AttachRepo, bg: BackgroundTasks,
                      current_user: User = Depends(get_current_user),
                      db: Session = Depends(get_db)):
    require_member(db, session_id, current_user.id)
    sid = uuid.UUID(session_id)
    inst = db.query(GitHubInstallation).filter_by(
        user_id=current_user.id, installation_id=body.installation_id).first()
    if not inst:
        raise HTTPException(404, "GitHub installation not found")
    try:
        granted = await list_installation_repos(inst.installation_id)
    except InstallationGone:
        db.delete(inst); db.commit()
        raise HTTPException(410, "GitHub access was removed. Reconnect GitHub.")
    match = next((r for r in granted if r["full_name"].lower() == body.full_name.lower()), None)
    if not match:
        raise HTTPException(403, "That repo isn't granted to Huddle.")
    if match["private"] and not body.confirm_private:
        raise HTTPException(409, "confirm_private_required")
    if db.query(Repo).filter_by(scope_type="session", scope_id=sid, name=match["full_name"]).first():
        raise HTTPException(409, "Repo already attached to this session")
    repo = Repo(id=uuid.uuid4(), scope_type="session", scope_id=sid,
                name=match["full_name"], provider="github",
                url=f"https://github.com/{match['full_name']}",
                branch=match["default_branch"], installation_id=inst.installation_id,
                created_by=current_user.id, status="pending")
    db.add(repo); db.commit()
    bg.add_task(import_repo, repo.id)
    return {"id": str(repo.id), "name": repo.name, "status": repo.status}


@router.get("")
def list_repos(session_id: str, current_user: User = Depends(get_current_user),
               db: Session = Depends(get_db)):
    require_member(db, session_id, current_user.id)
    rows = db.query(Repo).filter_by(scope_type="session", scope_id=uuid.UUID(session_id)).all()
    return [{"id": str(r.id), "name": r.name, "status": r.status, "error": r.error,
             "file_count": r.file_count, "branch": r.branch,
             "last_synced_at": r.last_synced_at} for r in rows]


@router.delete("/{repo_id}")
def remove_repo(session_id: str, repo_id: str, current_user: User = Depends(get_current_user),
                db: Session = Depends(get_db)):
    s = require_member(db, session_id, current_user.id)
    repo = db.query(Repo).filter_by(id=uuid.UUID(repo_id), scope_type="session",
                                    scope_id=uuid.UUID(session_id)).first()
    if not repo:
        raise HTTPException(404, "Repo not found")
    if str(repo.created_by) != str(current_user.id) and str(s.created_by) != str(current_user.id):
        raise HTTPException(403, "Only the person who attached it, or the session owner, can remove it")
    db.delete(repo); db.commit()   # CASCADE removes files and chunks
    return {"ok": True}