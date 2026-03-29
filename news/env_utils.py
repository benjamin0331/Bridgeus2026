import os
from pathlib import Path


def load_dotenv_file(path: str | Path = ".env", override: bool = False) -> Path | None:
    env_path = Path(path)
    if not env_path.is_absolute():
        env_path = Path(__file__).resolve().parent / env_path

    if not env_path.exists():
        return None

    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue

        if line.startswith("export "):
            line = line[7:].strip()

        if "=" not in line:
            continue

        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip()

        if not key:
            continue

        if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
            value = value[1:-1]

        if override or key not in os.environ:
            os.environ[key] = value

    return env_path
