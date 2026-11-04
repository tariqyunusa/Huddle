import uuid
import logging

from app.db.session import SessionLocal

from app.features.users.models import Repo, CodeFile, CodeChunk
from app.features.memory.embeddings import embed

logger = logging.getLogger(__name__)
MAX_READ_CHARS = 12_000
DEFAULT_LINES = 300

CODE_TOOLS = [
    {"type": "function", "function": {
        "name": "list_files",
        "description": "List files in the repositories connected to this session. Optionally filter by path prefix.",
        "parameters": {"type": "object", "properties": {
            "prefix": {"type": "string", "description": "Only paths starting with this, e.g. 'src/'"}},
            "required": []}}},
    {"type": "function", "function": {
        "name": "read_file",
        "description": "Read a file from a connected repository. Returns numbered lines. Use start_line/end_line for big files.",
        "parameters": {"type": "object", "properties": {
            "path": {"type": "string"},
            "start_line": {"type": "integer"},
            "end_line": {"type": "integer"},
            "repo": {"type": "string", "description": "owner/name; only needed when several repos are connected"}},
            "required": ["path"]}}},
]


SEARCH_TOOL = {"type": "function", "function": {
    "name": "search_code",
    "description": "Semantic search over the connected repositories' code. Use it to find where something is implemented, then read_file for full context.",
    "parameters": {"type": "object", "properties": {
        "query": {"type": "string"},
        "top_k": {"type": "integer", "description": "1-10, default 6"}},
        "required": ["query"]}}}

CODE_TOOL_NAMES = {"list_files", "read_file", "search_code"}


def ready_repos(session_id) -> list:
    db = SessionLocal()
    try:
        return db.query(Repo).filter_by(scope_type="session", scope_id=uuid.UUID(str(session_id)),
                                        status="ready").all()
    finally:
        db.close()


async def run_code_tool(session_id, name: str, args: dict) -> str:
    # session_id comes from the server, never from the model's arguments.
    db = SessionLocal()
    try:
        repos = db.query(Repo).filter_by(scope_type="session", scope_id=uuid.UUID(str(session_id)),
                                         status="ready").all()
        if not repos:
            return "No repositories are connected and ready."
        by_name = {r.name.lower(): r for r in repos}

        if name == "list_files":
            prefix = (args.get("prefix") or "").lstrip("/")
            out = []
            for r in repos:
                rows = (db.query(CodeFile.path, CodeFile.size)
                        .filter(CodeFile.repo_id == r.id, CodeFile.path.startswith(prefix))
                        .order_by(CodeFile.path).limit(300).all())
                out.append(f"{r.name} ({len(rows)} shown):\n" + "\n".join(f"  {p} ({s} B)" for p, s in rows))
            return "\n\n".join(out)

        if name == "read_file":
            path = (args.get("path") or "").lstrip("/")
            wanted = (args.get("repo") or "").lower()
            if wanted and wanted not in by_name:
                return f"Unknown repo '{args.get('repo')}'. Connected: {', '.join(r.name for r in repos)}"
            if not wanted and len(repos) > 1:
                matches = [r for r in repos if db.query(CodeFile.id).filter_by(repo_id=r.id, path=path).first()]
                if len(matches) != 1:
                    return "Multiple repos connected. Specify 'repo': " + ", ".join(r.name for r in repos)
                repo = matches[0]
            else:
                repo = by_name[wanted] if wanted else repos[0]
            f = db.query(CodeFile).filter_by(repo_id=repo.id, path=path).first()
            if not f:
                return f"File not found: {path}. Call list_files to see paths."
            lines = f.content.splitlines()
            start = max(1, int(args.get("start_line") or 1))
            end = min(len(lines), int(args.get("end_line") or start + DEFAULT_LINES - 1))
            body, used = [], 0
            for i in range(start, end + 1):
                row = f"{i}: {lines[i - 1]}"
                if used + len(row) > MAX_READ_CHARS:
                    return f"{repo.name}/{path} lines {start}-{i - 1} of {len(lines)} (truncated, continue from line {i}):\n" + "\n".join(body)
                body.append(row); used += len(row) + 1
            return f"{repo.name}/{path} lines {start}-{end} of {len(lines)}:\n" + "\n".join(body)
        
        if name == "search_code":
            query = (args.get("query") or "").strip()
            ids = [r.id for r in repos if r.indexed_at]
            if not query or not ids:
                return "Search isn't available. Use list_files and read_file."
            k = min(max(int(args.get("top_k") or 6), 1), 10)
            qvec = (await embed([query]))[0]
            rows = (db.query(CodeChunk.start_line, CodeChunk.end_line, CodeChunk.content,
                             CodeFile.path, Repo.name)
                    .join(CodeFile, CodeChunk.file_id == CodeFile.id)
                    .join(Repo, CodeChunk.repo_id == Repo.id)
                    .filter(CodeChunk.repo_id.in_(ids))
                    .order_by(CodeChunk.embedding.cosine_distance(qvec))
                    .limit(k).all())
            out, used = [], 0
            for s, e, content, path, rname in rows:
                block = f"{rname}/{path}:{s}-{e}\n{content}"
                if used + len(block) > 10_000:
                    break
                out.append(block); used += len(block)
            return "\n\n".join(out) or "No matches."

        return f"Unknown tool: {name}"
    except Exception:
        logger.exception("Code tool %s failed", name)
        return "Tool error."
    finally:
        db.close()
        
def code_tools_for(repos) -> list:
    return CODE_TOOLS + ([SEARCH_TOOL] if any(r.indexed_at for r in repos) else [])