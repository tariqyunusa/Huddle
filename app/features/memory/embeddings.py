import asyncio
from concurrent.futures import ThreadPoolExecutor

_model = None
_executor = ThreadPoolExecutor(max_workers=1)


def _embed_sync(texts: list[str]) -> list[list[float]]:
    global _model
    if _model is None:
        from fastembed import TextEmbedding
        _model = TextEmbedding(model_name="BAAI/bge-small-en-v1.5")
    return [vector.tolist() for vector in _model.embed(texts)]


async def embed(texts: list[str]) -> list[list[float]]:
    return await asyncio.get_running_loop().run_in_executor(_executor, _embed_sync, texts)
