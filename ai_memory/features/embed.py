"""Embeddings computed INSIDE this application — no LM Studio, no cloud, no network.

A small sentence-transformer (all-MiniLM-L6-v2, 22M parameters) in ONNX form, running
on CPU. It is a single forward pass per record, not token-by-token generation, which
is why it is fast enough on weak hardware: a small NAS with a 2-core Ryzen R1600 handles the
whole store in well under a second.

WHY IT NEVER TOUCHES THE NETWORK AT RUNTIME
The model is downloaded ONCE by an explicit command the user runs (`ai-memory-manage embed
--setup`). Nothing else here ever opens a socket: if the model is absent, embedding is
simply unavailable and the caller is told so. That keeps "this application does not
reach out to the internet" a property of the code rather than a promise.

WHY THE VECTORS ARE ENCRYPTED (see store.py)
An embedding is partially invertible — enough of the original text can be recovered
from one to matter. Storing vectors in the clear beside an encrypted payload would
undo the whole point of encrypting the store, so they go through the same cipher.

OPTIONAL DEPENDENCY
onnxruntime/tokenizers/numpy are an extra (`pip install -e '.[semantic]'`). Everything
else in the project works without them; imports here are deliberately lazy so a plain
install never pays for them.
"""
from __future__ import annotations

import os
from pathlib import Path

from ai_memory.core import paths
MODEL_DIR = paths.models_dir()

# MULTILINGUAL SINCE 1.1.0, and the reason is a measurement rather than a preference.
# The previous model, all-MiniLM-L6-v2, is English. The owner writes in Czech while the
# records are written in English (his own convention), so every Czech query was being
# compared across a wall: "kytara a zesilovač" scored 0.251 against an unrelated record
# while "guitar and amplifier" scored 0.463 against the right one. Semantic search was
# quietly useless in the language it was actually being used in, and nobody noticed
# because a wrong hit looks exactly like a right one.
#
# Measured on the real store, five queries, 2026-08-30:
#     all-MiniLM-L6-v2                     4/5 correct, Czech scores 0.144-0.421
#     paraphrase-multilingual-MiniLM-L12   5/5 correct, Czech scores 0.487-0.610
#
# Chosen over multilingual-e5-small, which scores better on benchmarks but requires
# "query: " / "passage: " prefixes -- one forgotten prefix degrades results silently,
# which is the same class of failure this change exists to fix. Same 384 dimensions as
# before, so nothing about the storage format changes.
#
# The cost is size: 449 MB against 87 MB. It is downloaded once, by an explicit command.
#
# Pinned by revision rather than `main`: the point of downloading once is that the
# vectors stay comparable, and a model that silently changes underneath makes every
# stored vector incomparable with every new one.
_REPO = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"
# The commit of 2026-01-28; the same files (by SHA-256) the image bakes in, see
# docker/fetch-model.py.
_REVISION = "e8f8c211226b894fcb81acc59f3b34ba3efd5f42"
_FILES = {"model.onnx": f"onnx/model.onnx", "tokenizer.json": "tokenizer.json"}

# Identity of the model that produced a vector. It goes into the staleness fingerprint
# and into every stored vector, so vectors from a different model are never silently
# compared with vectors from this one -- they would be numbers of the same shape and no
# shared meaning. Changing the model therefore invalidates the index rather than
# corrupting it, and `embed --index` rebuilds it.
MODEL_ID = "paraphrase-multilingual-MiniLM-L12-v2"

DIM = 384
MAX_TOKENS = 256

_session = None
_tokenizer = None


class EmbeddingUnavailable(RuntimeError):
    """Raised when the model or the optional dependencies are missing. Callers are
    expected to degrade — never to fail the whole operation."""


def is_ready() -> bool:
    """Whether embedding can run right now, without importing anything heavy."""
    if not all((MODEL_DIR / f).exists() for f in _FILES):
        return False
    try:
        import numpy, onnxruntime, tokenizers  # noqa: F401
    except ImportError:
        return False
    return True


def status() -> dict:
    """A short, honest report of what is and is not available."""
    have_files = {f: (MODEL_DIR / f).exists() for f in _FILES}
    try:
        import numpy, onnxruntime, tokenizers  # noqa: F401
        deps = True
    except ImportError:
        deps = False
    return {"ready": is_ready(), "model_dir": str(MODEL_DIR),
            "model_files": have_files, "dependencies_installed": deps,
            "dimensions": DIM, "max_tokens": MAX_TOKENS}


def _fetch(url: str, target, progress, timeout: int, attempts: int) -> None:
    """Stream one file to disk, resuming where a previous attempt stopped.

    Written this way after the first version hung: `urlretrieve` has no timeout, so a
    connection that dies quietly (HuggingFace redirects to a CDN, where it happens)
    blocks forever with nothing on screen — indistinguishable from a slow download.
    Here every read has a deadline, a stall is retried, and the partial file is kept
    so a retry resumes instead of starting the 87 MB again.
    """
    import urllib.error
    import urllib.request

    tmp = target.with_suffix(target.suffix + ".part")
    for attempt in range(1, attempts + 1):
        have = tmp.stat().st_size if tmp.exists() else 0
        req = urllib.request.Request(url)
        if have:
            req.add_header("Range", f"bytes={have}-")
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                # a server ignoring Range answers 200 and resends from zero
                resuming = resp.status == 206
                if have and not resuming:
                    have = 0
                total = int(resp.headers.get("Content-Length") or 0) + have
                mode = "ab" if resuming else "wb"
                with open(tmp, mode) as f:
                    while chunk := resp.read(1 << 16):
                        f.write(chunk)
                        have += len(chunk)
                        if total:
                            progress(f"  {have / 1e6:6.1f} / {total / 1e6:.1f} MB"
                                     f"  ({have * 100 // total}%)", end="\r")
                        else:
                            progress(f"  {have / 1e6:6.1f} MB", end="\r")
            if total and have < total:
                raise OSError(f"connection closed at {have} of {total} bytes")
            progress("")
            tmp.rename(target)  # rename last: an interrupted download is never used
            return
        except (urllib.error.URLError, OSError, TimeoutError) as e:
            if attempt == attempts:
                raise RuntimeError(
                    f"download failed after {attempts} attempts: {e}. "
                    f"The partial file is kept at {tmp}, so running --setup again "
                    f"resumes rather than starting over.") from e
            progress(f"\n  attempt {attempt} failed ({e}) — retrying, resuming from "
                     f"{(tmp.stat().st_size if tmp.exists() else 0) / 1e6:.1f} MB")


def download(progress=print, *, timeout: int = 30, attempts: int = 4) -> None:
    """Fetch the model. THE ONLY function in this project that uses the network, and
    it runs only when the user asks for it on the command line."""
    MODEL_DIR.mkdir(parents=True, exist_ok=True)
    for local, remote in _FILES.items():
        target = MODEL_DIR / local
        if target.exists():
            progress(f"{local}: already here, skipping")
            continue
        url = f"https://huggingface.co/{_REPO}/resolve/{_REVISION}/{remote}"
        progress(f"{local}: downloading from {url}")
        _fetch(url, target, progress, timeout, attempts)
        progress(f"{local}: done, {target.stat().st_size / 1e6:.1f} MB")


def _load():
    global _session, _tokenizer
    if _session is not None:
        return _session, _tokenizer
    if not all((MODEL_DIR / f).exists() for f in _FILES):
        raise EmbeddingUnavailable(
            f"the embedding model is not downloaded (looked in {MODEL_DIR}). "
            f"Run: ai-memory-manage embed --setup")
    try:
        import onnxruntime as ort
        from tokenizers import Tokenizer
    except ImportError as e:
        raise EmbeddingUnavailable(
            f"optional dependencies are missing ({e}). "
            f"Run: pip install -e '.[semantic]'") from e

    tok = Tokenizer.from_file(str(MODEL_DIR / "tokenizer.json"))
    tok.enable_padding()
    tok.enable_truncation(max_length=MAX_TOKENS)
    # one thread by default: this shares a NAS with other containers, and grabbing
    # every core for a job nobody is waiting on is rude
    opts = ort.SessionOptions()
    opts.intra_op_num_threads = int(os.environ.get("AI_MEMORY_EMBED_THREADS", "1"))
    _session = ort.InferenceSession(str(MODEL_DIR / "model.onnx"),
                                    sess_options=opts, providers=["CPUExecutionProvider"])
    _tokenizer = tok
    return _session, _tokenizer


def embed(texts: list[str]):
    """Embed a batch, returning L2-normalised vectors (so a dot product is cosine)."""
    import numpy as np

    if not texts:
        return np.zeros((0, DIM), dtype=np.float32)
    sess, tok = _load()
    enc = tok.encode_batch(texts)
    ids = np.array([e.ids for e in enc], dtype=np.int64)
    mask = np.array([e.attention_mask for e in enc], dtype=np.int64)
    feed = {"input_ids": ids, "attention_mask": mask}
    if any(i.name == "token_type_ids" for i in sess.get_inputs()):
        feed["token_type_ids"] = np.zeros_like(ids)
    hidden = sess.run(None, feed)[0]
    m = mask[..., None].astype(np.float32)
    pooled = (hidden * m).sum(1) / np.clip(m.sum(1), 1e-9, None)  # mean pooling
    norms = np.linalg.norm(pooled, axis=1, keepdims=True)
    return (pooled / np.clip(norms, 1e-9, None)).astype(np.float32)


def text_for(record: dict) -> str:
    """What actually gets embedded.

    Name and description first because they are the densest summary of a record, then
    the body. Only the first MAX_TOKENS tokens survive truncation, so for a very long
    record this represents its opening rather than the whole thing — good enough to
    spot two records about the same subject, which is what this is for.
    """
    return "\n".join(filter(None, (
        record.get("name", "").replace("-", " "),
        record.get("description", ""),
        (record.get("content") or "")[:2000],
    )))
