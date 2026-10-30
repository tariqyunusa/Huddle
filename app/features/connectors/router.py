import os, time, uuid

from app.features.users.models import User

from fastapi import APIRouter, Depends
from app.features.users.dependencies import get_current_user
from jose import jwt

from app.db.session import get_db
from sqlalchemy.orm import Session
from app.features.users.models import GitHubInstallation
from fastapi.responses import RedirectResponse
from app.features.connectors.github.github_app import (
    verify_installation, list_installation_repos, InstallationGone,
)


router = APIRouter(prefix="/github", tags=["github"])
@router.get("/install-url")
def install_url(current_user: User = Depends(get_current_user)):
    state = jwt.encode({"sub": str(current_user.id), "purpose": "github_install", "exp": int(time.time()) + 600}, os.environ["JWT_SECRET_KEY"], algorithm="HS256")
    return {"url": f"https://github.com/apps/{os.environ['GITHUB_APP_SLUG']}/installations/new?state={state}"}


@router.get("/callback")
async def callback(installation_id: int, state: str, code: str | None = None,
                   db: Session = Depends(get_db)):
    front = os.environ["FRONTEND_URL"]
    try:
        payload = jwt.decode(state, os.environ["JWT_SECRET_KEY"], algorithms=["HS256"])
        if payload.get("purpose") != "github_install":
            raise ValueError
    except Exception:
        return RedirectResponse(f"{front}/settings?github=error")
    inst = await verify_installation(code, installation_id) if code else None
    if not inst:
        return RedirectResponse(f"{front}/settings?github=error")
    row = db.query(GitHubInstallation).filter_by(
        user_id=payload["sub"], installation_id=installation_id).first()
    if row:
        row.repository_selection = inst["repository_selection"]
    else:
        db.add(GitHubInstallation(id=uuid.uuid4(), user_id=payload["sub"], **inst))
    db.commit()
    return RedirectResponse(f"{front}/settings?github=connected")

@router.get("/repos")
async def repos(current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    out = []
    for inst in db.query(GitHubInstallation).filter_by(user_id=current_user.id).all():
        try:
            for r in await list_installation_repos(inst.installation_id):
                out.append({**r, "installation_id": inst.installation_id, "account": inst.account_login})
        except InstallationGone:       # user uninstalled it on GitHub
            db.delete(inst)
    db.commit()
    return out