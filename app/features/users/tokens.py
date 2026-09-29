import hashlib
import secrets

def generate_refresh_token() -> str:
    """"The raw secret handed to the client. Never stored as-is."""
    return secrets.token_urlsafe(32)

def hash_token(raw_token: str) -> str:
    """One-way hash for DB storage/lookup. Same input always produces the same output."""
    return hashlib.sha256(raw_token.encode()).hexdigest()