"""Shared pure rules for excluding private state from distributable packages."""
from pathlib import Path, PurePosixPath
import sys

CREDENTIAL_NAMES = frozenset({
    'auth.json', 'credentials.json', 'credentials', 'secrets.json', 'secrets.yaml', 'secrets.yml',
    'tokens.json', 'token.json', 'service_account.json', 'id_rsa', 'id_ed25519', 'id_ecdsa', 'id_dsa',
})
CREDENTIAL_SUFFIXES = ('.pem', '.key', '.p12', '.pfx')


def is_hidden_path(relative: str) -> bool:
    return any(part.startswith('.') for part in PurePosixPath(relative).parts)


def is_private_path(relative: str) -> bool:
    path = PurePosixPath(relative)
    name = path.name.casefold()
    return (is_hidden_path(relative) or '__pycache__' in {part.casefold() for part in path.parts}
            or name in CREDENTIAL_NAMES or name.endswith(CREDENTIAL_SUFFIXES))


def has_unsafe_symlink_ancestor(path: Path) -> bool:
    """Reject symlink traversal, except macOS's fixed /var and /tmp aliases.

    macOS reports temporary directories below /var/folders, while /var itself
    points to /private/var. That system alias must not make ordinary temporary
    installations and packages unsafe; arbitrary parent symlinks stay rejected.
    """
    path = Path(path).absolute()
    for part in (path, *path.parents):
        if not part.is_symlink():
            continue
        if (sys.platform == 'darwin' and str(part) in ('/var', '/tmp')
                and part.resolve() == Path('/private') / part.name):
            continue
        return True
    return False
