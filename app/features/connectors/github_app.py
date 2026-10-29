import base64, os, time
import httpx
from jose import jwt

API = "https://api.github.com"
MAX_TOTAL_BYTES = 15_000_000

class InstallationGone(Exception):
    pass

def _hdrs(token: str) -> dict:
    return {"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28"}
    
def app_jwt() -> str:
    key = base64.b64decode(os.environ["GITHUB_APP_PRIVATE_KEY_B64"]).decode()
    now = int(time.time())
    return jwt.encode({"iat": now - 60, "exp": now + 540, "iss": str(os.environ["GITHUB_APP_ID"])}, key, algorithm="RS256")

async def installation_token(installation_id: int) -> str:
    async with httpx.AsyncClient(timeout=20) as c:
        r = await c.post(f"{API}/app/installations/{installation_id}/access_tokens", headers=_hdrs(app_jwt()))
    
    if r.status_code in (401, 404):
        raise InstallationGone()
    r.raise_for_status()
    return r.json()["token"]

async def list_installation_repos(installation_id: int) -> list[dict]:
    token = await installation_token(installation_id)
    out, page = [], 1
    async with httpx.AsyncClient(timeout=20) as c:
        while True:
            r = await c.get(f"{API}/installation/repositories", params={"per_page": 100, "page": page}, headers=_hdrs(token))
            
            r.raise_for_status()
            data= r.json()
            out += [{"id": x["id"], "full_name": x["full_name"], "private": x["private"],
                     "default_branch": x["default_branch"]} for x in data["repositories"]]
            if not data["repositories"] or len(out) >= data["total_count"]:
                return out
            page += 1
            
async def verify_installation(code: str, installation_id: int) -> dict | None:
    """Prove this github user actually owns the installation_id from the redirect."""
    async with httpx.AsyncClient(timeout=20) as c:
        r = await c.post("https://github.com/login/oauth/access_token",
                         headers={"Accept": "application/json"},
                         data={"client_id": os.environ["GITHUB_CLIENT_ID"],
                               "client_secret": os.environ["GITHUB_CLIENT_SECRET"], "code": code})
        user_token = r.json().get("access_token")
        if not user_token:
            return None
        r = await c.get(f"{API}/user/installations", params={"per_page": 100}, headers=_hdrs(user_token))
        if r.status_code != 200:
            return None
    for i in r.json()["installations"]:
        if i["id"] == installation_id:
            return {"installation_id": i["id"], "account_login": i["account"]["login"],
                    "account_type": i["account"]["type"],
                    "repository_selection": i["repository_selection"]}
    return None

