"""
Config loader for Public.com API.
Reads from .env (repo root) or environment variables. No OpenClaw required.
"""
import os

# Repo root = directory containing this file's parent (e.g. .../claw-skill-public-dot-com)
_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
_REPO_ROOT = os.path.dirname(_SCRIPT_DIR)

def _load_env():
    """Load .env from repo root so it works from any cwd."""
    try:
        from dotenv import load_dotenv
        env_path = os.path.join(_REPO_ROOT, ".env")
        load_dotenv(env_path)
        load_dotenv()  # also cwd, so export FOO= still wins
    except ImportError:
        pass

_load_env()


def get_api_secret():
    """Get PUBLIC_COM_SECRET from environment (after .env load). Returns None if not set."""
    return os.getenv("PUBLIC_COM_SECRET")


def get_account_id():
    """Get PUBLIC_COM_ACCOUNT_ID from environment (after .env load). Returns None if not set."""
    return os.getenv("PUBLIC_COM_ACCOUNT_ID")
