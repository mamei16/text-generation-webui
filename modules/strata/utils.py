import os

def _timeout_env(name: str, default: float) -> float | None:
    try:
        v = float(os.environ.get(name, default))
    except ValueError:
        v = default
    return None if v <= 0 else v