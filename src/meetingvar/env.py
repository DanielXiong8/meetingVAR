"""Load only the current working directory's explicit local .env file."""
from pathlib import Path


def load_local_env(path: Path | None = None) -> None:
    path = Path.cwd() / ".env" if path is None else path
    if not path.is_file():
        return
    try:
        from dotenv import load_dotenv
    except ImportError as exc:
        raise RuntimeError("读取 .env 需要 python-dotenv，请运行 python -m pip install -e .") from exc
    # Existing process settings always take precedence. Never search parents.
    load_dotenv(dotenv_path=path, override=False, encoding="utf-8-sig", interpolate=False)
