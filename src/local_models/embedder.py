from __future__ import annotations

import threading

import torch
import torch.nn.functional as F
from transformers import AutoModel, AutoTokenizer

from src.config import settings
from src.logging_config import get_logger

logger = get_logger("embedder")

_device = "cuda" if torch.cuda.is_available() else "cpu"
_tokenizer: AutoTokenizer | None = None
_model: AutoModel | None = None
_load_lock = threading.Lock()


def _load() -> None:
    global _tokenizer, _model
    if _tokenizer is not None:
        return
    with _load_lock:
        if _tokenizer is not None:
            return
        logger.debug("Loading BGE-M3 embedder model")
        _tokenizer = AutoTokenizer.from_pretrained(settings.bge_embedder_path)
        _model = AutoModel.from_pretrained(settings.bge_embedder_path)
        _model.eval()
        logger.debug("BGE-M3 embedder model loaded")


def _mean_pooling(last_hidden: torch.Tensor, attention_mask: torch.Tensor) -> torch.Tensor:
    s = attention_mask.unsqueeze(-1).float()
    return (last_hidden * s).sum(dim=1) / s.sum(dim=1)


def embed_text(text: str | list[str]) -> list[float] | list[list[float]]:
    """用 bge-m3 生成嵌入（无 sentence-transformers 依赖）。"""
    global _model
    _load()
    if next(_model.parameters()).device.type != _device:
        _model = _model.to(_device)
        logger.debug("Embedder moved to GPU for inference")
    single = isinstance(text, str)
    texts = [text] if single else text
    encoded = _tokenizer(texts, padding=True, truncation=True, return_tensors="pt", max_length=512)
    encoded = {k: v.to(_device) for k, v in encoded.items()}
    with torch.no_grad():
        outputs = _model(**encoded)
        emb = _mean_pooling(outputs.last_hidden_state, encoded["attention_mask"])
        emb = F.normalize(emb, p=2, dim=1)
    result = [e.tolist() for e in emb]
    return result[0] if single else result


def embed_query(text: str) -> list[float]:
    """为查询生成嵌入（加 instruction 前缀）。"""
    query_text = f"为这个句子生成表示以用于检索相关文章：{text}"
    return embed_text(query_text)  # type: ignore


def release_gpu() -> None:
    global _model
    if _model is not None:
        _model = _model.to("cpu")
        torch.cuda.empty_cache()
        logger.debug("Embedder moved to CPU")
