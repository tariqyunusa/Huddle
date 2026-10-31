import logging, uuid
from sqlalchemy import func
from app.db.session import SessionLocal
from app.features.memory.embeddings import embed
from app.features.users.models import Repo, CodeFile, CodeChunk

logger = logging.getLogger(__name__)

CHUNK_LINES = 60
OVERLAP = 10
MAX_CHUNK_CHARS = 2000
MAX_CHUNK_PER_REPO = 8000
BATCH = 32


def chunk_file(content: str) -> list[tuple[int, int, str]]:
    lines = content.splitlines()
    out, i = [], 0
    while i < len(lines):
        j = min(i + CHUNK_LINES, len(lines))
        text = "\n".join(lines[i:j])
        while len(text) > MAX_CHUNK_CHARS and j - i > 1:    # long lines: shrink the window
            j = i + max(1, (j - i) // 2)
            text = "\n".join(lines[i:j])
        if text.strip():
            out.append((i + 1, j, text[:MAX_CHUNK_CHARS]))  # single huge line: hard cut
        if j >= len(lines):
            break
        i = max(j - OVERLAP, i + 1)
    return out


async def index_repo(repo_id: uuid.UUID):
    db = SessionLocal()
    try:
        repo = db.get(Repo, repo_id)
        db.query(CodeChunk).filter(CodeChunk.repo_id == repo.id).delete()
        db.commit()

        pending = []   # (file_id, path, start, end, text)
        for f in db.query(CodeFile.id, CodeFile.path, CodeFile.content).filter(CodeFile.repo_id == repo.id):
            for s, e, t in chunk_file(f.content):
                pending.append((f.id, f.path, s, e, t))
            if len(pending) >= MAX_CHUNK_PER_REPO:
                pending = pending[:MAX_CHUNK_PER_REPO]
                break

        for b in range(0, len(pending), BATCH):
            batch = pending[b:b + BATCH]
            vectors = await embed([f"{p}\n{t}" for _, p, _, _, t in batch])
            db.add_all([CodeChunk(id=uuid.uuid4(), repo_id=repo.id, file_id=fid,
                                  start_line=s, end_line=e, content=t, embedding=v)
                        for (fid, _, s, e, t), v in zip(batch, vectors)])
            db.commit()

        repo.chunk_count, repo.indexed_at = len(pending), func.now()
        db.commit()
        logger.info("Indexed %s: %d chunks", repo.name, len(pending))
    finally:
        db.close()