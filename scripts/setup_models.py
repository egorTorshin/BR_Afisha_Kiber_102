"""Download pinned official Qwen GGUF files and verify their LFS SHA-256."""

import hashlib
import json
import os
from pathlib import Path
from urllib.parse import quote
from urllib.request import Request, urlopen


MODELS = (
    ("Qwen/Qwen2.5-1.5B-Instruct-GGUF", "qwen2.5-1.5b-instruct-q4_k_m.gguf",
     "91cad51170dc346986eccefdc2dd33a9da36ead9",
     "6a1a2eb6d15622bf3c96857206351ba97e1af16c30d7a74ee38970e434e9407e"),
    ("Qwen/Qwen2.5-3B-Instruct-GGUF", "qwen2.5-3b-instruct-q4_k_m.gguf",
     "7dabda4d13d513e3e842b20f0d435c732f172cbe",
     "626b4a6678b86442240e33df819e00132d3ba7dddfe1cdc4fbb18e0a9615c62d"),
)


def fetch_json(url: str) -> object:
    with urlopen(Request(url, headers={"User-Agent": "afisha-model-setup"}), timeout=30) as reply:
        return json.load(reply)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(4 * 1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def main() -> None:
    directory = Path(os.getenv("MODELS_DIR", "models")).expanduser().resolve()
    directory.mkdir(parents=True, exist_ok=True)
    lock_path = directory / "models.lock.json"
    lock = json.loads(lock_path.read_text(encoding="utf-8")) if lock_path.exists() else {}
    for repo, filename, revision, expected in MODELS:
        if (lock.get(repo, {}).get("revision") not in (None, revision) or
                lock.get(repo, {}).get("sha256") not in (None, expected)):
            raise RuntimeError(f"Model lock disagrees with pinned revision: {repo}")
        target = directory / filename
        if target.exists() and sha256(target) == expected:
            print(f"Verified existing {target}")
        else:
            tree = fetch_json(f"https://huggingface.co/api/models/{repo}/tree/{revision}")
            entry = next((item for item in tree if item["path"].lower() == filename), None)
            if entry is None or entry.get("lfs", {}).get("oid") != expected:
                raise RuntimeError(f"Official LFS checksum disagrees with pin: {repo}/{filename}")
            partial = directory / (filename + ".part")
            if target.exists():
                target.unlink()
            url = f"https://huggingface.co/{repo}/resolve/{quote(revision)}/{filename}?download=true"
            print(f"Downloading {repo}/{filename} to {target}", flush=True)
            offset = partial.stat().st_size if partial.exists() else 0
            headers = {"User-Agent": "afisha-model-setup"}
            if offset:
                headers["Range"] = f"bytes={offset}-"
            with urlopen(Request(url, headers=headers), timeout=90) as reply:
                append = offset > 0 and reply.status == 206
                with partial.open("ab" if append else "wb") as output:
                    for chunk in iter(lambda: reply.read(4 * 1024 * 1024), b""):
                        output.write(chunk)
            if sha256(partial) != expected:
                partial.unlink(missing_ok=True)
                raise RuntimeError(f"SHA-256 mismatch for {filename}")
            partial.replace(target)
        lock[repo] = {"revision": revision, "file": filename, "sha256": expected}
        lock_path.write_text(json.dumps(lock, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print("Both models are verified; subsequent starts do not download weights.")


if __name__ == "__main__":
    main()
