import asyncio, hashlib, os, tarfile, tempfile
import httpx

API = "https://api.github.com"
MAX_FILE_BYTES = 200_000
MAX_TOTAL_BYTES = 15_000_000
MAX_FILES = 3000

SKIP_DIRS = {".git", "node_modules", "dist", "build", ".next", "__pycache__",
             "venv", ".venv", "target", "vendor", ".idea", ".vscode", "coverage"}
SKIP_FILES = {"package-lock.json", "yarn.lock", "pnpm-lock.yaml", "poetry.lock",
              "Cargo.lock", "uv.lock"}
SKIP_EXT = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".ico", ".svg", ".pdf",
            ".zip", ".gz", ".tar", ".woff", ".woff2", ".ttf", ".eot", ".mp4",
            ".mp3", ".mov", ".exe", ".dll", ".so", ".dylib", ".bin", ".pyc",
            ".class", ".jar"}

class GitHubError(Exception):
    pass

def _headers(token: str) -> dict:
    return {"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28"}
    
def _skip(path: str, size: int) -> bool:
    parts = path.split("/")
    if any(p in SKIP_DIRS for p in parts[:-1]):
        return True
    name = parts[-1]
    if name in SKIP_FILES or name.endswith((".min.js", ".min.css", ".map")):
        return True
    if os.path.splitext(name)[1].lower() in SKIP_EXT:
        return True
    return size > MAX_FILE_BYTES

def _extract(tmp) -> tuple[str | None, list[dict]]:
    files, commit_sha, total = [], None, 0
    with tarfile.open(fileobj=tmp, mode="r:gz") as tar:
        for member in tar:
            if not member.isfile():
                continue
            root, _, path = member.name.partition("/")
            if not path:
                continue
            if commit_sha is None:
                commit_sha = root.rsplit("-", 1)[-1]   # root dir is "owner-repo-<sha>"
            if _skip(path, member.size):
                continue
            data = tar.extractfile(member).read()
            if b"\x00" in data[:1024]:                 # binary
                continue
            try:
                text = data.decode("utf-8")
            except UnicodeDecodeError:
                continue
            if total + len(data) > MAX_TOTAL_BYTES:
                break
            total += len(data)
            files.append({
                "path": path,
                "content": text,
                "content_hash": hashlib.sha256(data).hexdigest(),
                "size": len(data),
            })
            if len(files) >= MAX_FILES:
                break
    return commit_sha, files

async def download_repo(owner: str, repo: str, ref: str, token: str) -> tuple[str | None, list[dict]]:
    with tempfile.TemporaryFile() as tmp:
        async with httpx.AsyncClient(timeout=60, follow_redirects=True) as c:
            async with c.stream("GET", f"{API}/repos/{owner}/{repo}/tarball/{ref}",
                                headers=_headers(token)) as r:
                if r.status_code == 404:
                    raise GitHubError("Repo or branch not found.")
                if r.status_code in (403, 429):
                    raise GitHubError("GitHub rate limit hit. Try again in a bit.")
                if r.status_code != 200:
                    raise GitHubError(f"GitHub returned {r.status_code} while downloading.")
                async for chunk in r.aiter_bytes():
                    tmp.write(chunk)
        tmp.seek(0)
        return await asyncio.to_thread(_extract, tmp)