"""Login for the two of us. No public sign-up.

Accounts come from the NHF_USERS environment variable, as
`name:password-hash` pairs separated by commas. `nhf hash-password` prints a
hash. With NHF_USERS unset the app serves the loopback address only, so a
development run cannot accidentally be left open on a cloud instance.
"""

from __future__ import annotations

import os
from functools import wraps

from flask import current_app, redirect, request, session, url_for
from werkzeug.security import check_password_hash, generate_password_hash

SESSION_KEY = "nhf_user"
LOOPBACK = {"127.0.0.1", "::1", "localhost"}


def configured_users() -> dict[str, str]:
    raw = os.environ.get("NHF_USERS", "").strip()
    users: dict[str, str] = {}
    for part in raw.split(","):
        if not part.strip():
            continue
        name, _, digest = part.partition(":")
        if name.strip() and digest.strip():
            users[name.strip().lower()] = digest.strip()
    return users


def hash_password(password: str) -> str:
    return generate_password_hash(password)


def check_login(name: str, password: str) -> bool:
    digest = configured_users().get((name or "").strip().lower())
    return bool(digest) and check_password_hash(digest, password or "")


def auth_disabled() -> bool:
    return not configured_users()


def current_user() -> str | None:
    if auth_disabled():
        return "local"
    return session.get(SESSION_KEY)


def login_required(view):
    @wraps(view)
    def wrapper(*args, **kwargs):
        if auth_disabled():
            # Unconfigured means development: loopback only, never the world.
            if request.remote_addr not in LOOPBACK:
                current_app.logger.warning(
                    "refused %s: NHF_USERS is not set, so only loopback is served",
                    request.remote_addr,
                )
                return ("This app has no accounts configured, so it only answers on "
                        "localhost. Set NHF_USERS to use it over the network.", 403)
            return view(*args, **kwargs)
        if not session.get(SESSION_KEY):
            return redirect(url_for("login", next=request.path))
        return view(*args, **kwargs)

    return wrapper
