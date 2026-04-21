import os
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CHROMA_DIR = PROJECT_ROOT / "chroma_data"


def resolve_chroma_dir(chroma_dir: str | None = None) -> Path:
    raw_path = chroma_dir or os.getenv("CHROMA_PERSIST_DIR")
    if raw_path:
        return Path(raw_path).expanduser().resolve()
    return DEFAULT_CHROMA_DIR


def ensure_chroma_dir_writable(chroma_dir: str | Path | None = None) -> str:
    path = resolve_chroma_dir(str(chroma_dir) if chroma_dir is not None else None)
    path.mkdir(parents=True, exist_ok=True)

    probe_path = path / ".bridgeus_chroma_write_probe"
    try:
        with open(probe_path, "wb") as probe_file:
            probe_file.write(b"")
    except PermissionError as exc:
        raise PermissionError(
            "Chroma persist directory is not writable: "
            f"{path}. Fix the directory owner/permissions or set "
            "CHROMA_PERSIST_DIR to a writable path."
        ) from exc
    finally:
        try:
            probe_path.unlink()
        except FileNotFoundError:
            pass

    return str(path)
