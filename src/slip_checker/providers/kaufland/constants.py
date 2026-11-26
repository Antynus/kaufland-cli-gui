from __future__ import annotations

import os
from pathlib import Path

from ...config import SlipError, config_home, store_dir

CIDAAS_BASE = "https://account.kaufland.com"
API_BASE = "https://p.crm-dynamics.schwarz"
USERINFO_URL = f"{CIDAAS_BASE}/users-srv/userinfo"
AUTHZ_PATH = "/authz-srv/authz"
TOKEN_PATH = "/token-srv/token"
LOGIN_PATH = "/pages-app/login"
LOGIN_ROUTE = "routes/$pages/login"

CIDAAS_CLIENT_ID = "fb1b425b-ab2f-4140-aef9-20263b6cfa49"
LOYALTY_CLIENT_ID = "88207bfc-780b-400d-92ee-893ae72dab40"
REDIRECT_URI = "com.kaufland.kaufland://oauth/callback"
CALLBACK_SCHEME = "com.kaufland.kaufland://"
OAUTH_VERSION = "1.5.22"
APP_VERSION = "6.17.1"

BROWSER_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36"
)

MAX_PAGE_SIZE = 50


def country() -> str:
    value = os.getenv("KAUFLAND_COUNTRY", "PL").upper()
    if len(value) != 2 or not value.isalpha():
        raise SlipError("KAUFLAND_COUNTRY must be a two-letter country code")
    return value


def ui_locales() -> str:
    code = country()
    return os.getenv("KAUFLAND_UI_LOCALES", f"{code.lower()}-{code}")


def data_dir() -> Path:
    # KAUFLAND_DATA_DIR keeps its old meaning: the folder that holds tokens.json.
    override = os.getenv("KAUFLAND_DATA_DIR")
    if override:
        return Path(override).expanduser()
    return store_dir("kaufland")


def legacy_token_path() -> Path:
    return config_home() / "kaufland-cli" / "tokens.json"


def token_path() -> Path:
    path = data_dir() / "tokens.json"
    legacy = legacy_token_path()
    if not path.exists() and not os.getenv("KAUFLAND_DATA_DIR") and legacy.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        legacy.replace(path)
    return path
