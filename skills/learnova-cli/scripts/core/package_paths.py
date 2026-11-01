"""Shared pure rules for excluding private state from distributable packages."""
from pathlib import PurePosixPath

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
