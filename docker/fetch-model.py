"""Fetch the embedding model into the image at BUILD time, pinned and hash-checked.

The image is a finished distributable, so it carries the model: the semantic layer works
on first start, offline, and a first start can never fail on a download the user does
not understand. This is the build-time equivalent of `ai-memory-manage embed --setup`, which
cannot be used here -- manage.py opens the encrypted store (and asks for a passphrase)
before it looks at its subcommand.

Pinned twice: by repository commit, so the URL always means the same files, and by
SHA-256, so a changed or corrupted file fails the build instead of shipping. The hashes
are the LFS object ids HuggingFace reports for that commit (X-Linked-ETag), checked
against a copy downloaded independently on 2026-08-30.

Model: sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2, Apache-2.0
(redistribution allowed; the licence notice is written next to the files).

Standard library only -- it runs in a stage that has nothing else installed.
"""
import hashlib
import sys
import urllib.request
from pathlib import Path

REPO = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"
REVISION = "e8f8c211226b894fcb81acc59f3b34ba3efd5f42"
FILES = {
    # local name: (path in the repo, sha256)
    "model.onnx": ("onnx/model.onnx",
                   "10f7a088420252b26caf819236ca2c9d2987afd0fc06fec7553b542a5655a05a"),
    "tokenizer.json": ("tokenizer.json",
                       "2c3387be76557bd40970cec13153b3bbf80407865484b209e655e5e4729076b8"),
}

NOTICE = f"""\
paraphrase-multilingual-MiniLM-L12-v2
Source:   https://huggingface.co/{REPO}
Revision: {REVISION}
Licence:  Apache License 2.0 -- https://www.apache.org/licenses/LICENSE-2.0
Authors:  Nils Reimers and Iryna Gurevych (sentence-transformers / UKP Lab).
Only the ONNX export and the tokenizer are included, unmodified.
"""


def fetch(url: str, target: Path, sha256: str) -> None:
    h = hashlib.sha256()
    tmp = target.with_suffix(target.suffix + ".part")
    with urllib.request.urlopen(url, timeout=60) as resp, open(tmp, "wb") as f:
        while chunk := resp.read(1 << 20):
            h.update(chunk)
            f.write(chunk)
    if h.hexdigest() != sha256:
        tmp.unlink()
        raise SystemExit(f"{target.name}: sha256 {h.hexdigest()} != expected {sha256}")
    tmp.rename(target)
    print(f"{target.name}: {target.stat().st_size / 1e6:.1f} MB, sha256 ok")


def main() -> int:
    dest = Path(sys.argv[1])
    dest.mkdir(parents=True, exist_ok=True)
    for local, (remote, sha256) in FILES.items():
        fetch(f"https://huggingface.co/{REPO}/resolve/{REVISION}/{remote}",
              dest / local, sha256)
    (dest / "NOTICE").write_text(NOTICE, encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
