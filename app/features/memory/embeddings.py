import asyncio
import logging
import os
from concurrent.futures import ThreadPoolExecutor
from time import perf_counter

_model = None
_executor = ThreadPoolExecutor(max_workers=1)
logger = logging.getLogger(__name__)


def _load_model_sync():
    global _model
    if _model is None:
        from fastembed import TextEmbedding

        cache_dir = os.getenv("FASTEMBED_CACHE_PATH", "/app/.fastembed_cache")
        _model = TextEmbedding(model_name="BAAI/bge-small-en-v1.5", cache_dir=cache_dir)
    return _model


async def preload_model() -> None:
    started = perf_counter()
    await asyncio.get_running_loop().run_in_executor(_executor, _load_model_sync)
    logger.info("Fastembed model loaded in %.2f seconds", perf_counter() - started)


def _embed_sync(texts: list[str]) -> list[list[float]]:
    started = perf_counter()
    model = _load_model_sync()
    vectors = [vector.tolist() for vector in model.embed(texts)]
    logger.info("Fastembed request completed in %.2f seconds", perf_counter() - started)
    return vectors


async def embed(texts: list[str]) -> list[list[float]]:
    return await asyncio.get_running_loop().run_in_executor(_executor, _embed_sync, texts)
