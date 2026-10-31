"""Shared pure rules for excluding private state from distributable packages."""
from pathlib import PurePosixPath


def is_hidden_path(relative: str) -> bool:
    return any(part.startswith('.') for part in PurePosixPath(relative).parts)
