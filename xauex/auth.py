"""
OAuth2 Authentication for cTrader Open API

Run once before starting the bot for the first time:
    python auth.py

Handles:
1. Opens browser to cTrader authorisation URL
2. Starts a temporary HTTP server on http://localhost:8050
3. Catches the redirect callback with the code
4. Exchanges the code for access + refresh tokens
5. Writes tokens and expiry timestamp to .env
"""

import asyncio
import logging
import os
import tempfile
import time
import urllib.parse
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Optional

import aiohttp
from dotenv import dotenv_values, load_dotenv

logger = logging.getLogger(__name__)

_AUTH_URL = "https://connect.spotware.com/apps/auth"
_TOKEN_URL = "https://connect.spotware.com/apps/token"
_REDIRECT_URI = "http://localhost:8050/callback"
_SCOPE = "trading"
_ENV_FILE = Path(".env")

# Shared holder for the auth code captured by the HTTP callback handler
_auth_code: Optional[str] = None


def _atomic_update_env(path: str, updates: dict[str, str]) -> None:
    current = dotenv_values(path)
    current.update(updates)
    dirpath = os.path.dirname(path) or "."
    fd, tmp_path = tempfile.mkstemp(dir=dirpath, suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as f:
            for key, value in current.items():
                if value is None:
                    value = ""
                f.write(f"{key}={value}\n")
        os.replace(tmp_path, path)
    except Exception:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


class _CallbackHandler(BaseHTTPRequestHandler):
    """Minimal HTTP handler that captures the OAuth2 code from the redirect."""

    def do_GET(self) -> None:
        global _auth_code
        parsed = urllib.parse.urlparse(self.path)
        params = urllib.parse.parse_qs(parsed.query)
        code = params.get("code", [None])[0]

        if code:
            _auth_code = code
            body = b"<html><body><h1>Authorisation successful! You may close this tab.</h1></body></html>"
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.end_headers()
            self.wfile.write(body)
        else:
            self.send_response(400)
            self.end_headers()
            self.wfile.write(b"<html><body><h1>Missing code parameter.</h1></body></html>")

    def log_message(self, *args):
        pass  # suppress default access log


async def authenticate() -> bool:
    """
    Run OAuth2 authorisation code flow.
    Returns True on success and writes tokens to .env.
    """
    global _auth_code
    _auth_code = None

    load_dotenv()
    client_id = os.getenv("CTRADER_CLIENT_ID", "")
    client_secret = os.getenv("CTRADER_CLIENT_SECRET", "")

    if not client_id or not client_secret:
        logger.error("CTRADER_CLIENT_ID and CTRADER_CLIENT_SECRET must be set in .env")
        return False

    # Step 1: Build authorisation URL and open browser
    auth_params = urllib.parse.urlencode({
        "client_id": client_id,
        "redirect_uri": _REDIRECT_URI,
        "response_type": "code",
        "scope": _SCOPE,
    })
    auth_full_url = f"{_AUTH_URL}?{auth_params}"
    logger.info("Opening browser: %s", auth_full_url)
    webbrowser.open(auth_full_url)

    # Step 2: Start local callback server
    server = HTTPServer(("127.0.0.1", 8050), _CallbackHandler)
    server.timeout = 1.0  # non-blocking poll

    print("Waiting for OAuth2 callback on http://localhost:8050/callback ...")
    timeout_seconds = 120
    elapsed = 0.0

    loop = asyncio.get_event_loop()
    while _auth_code is None and elapsed < timeout_seconds:
        await loop.run_in_executor(None, server.handle_request)
        elapsed += 1.0

    server.server_close()

    if _auth_code is None:
        logger.error("Timed out waiting for OAuth2 callback.")
        return False

    # Step 3: Exchange code for tokens
    logger.info("Exchanging code for tokens...")
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(
                _TOKEN_URL,
                data={
                    "grant_type": "authorization_code",
                    "code": _auth_code,
                    "redirect_uri": _REDIRECT_URI,
                    "client_id": client_id,
                    "client_secret": client_secret,
                },
            ) as resp:
                if resp.status != 200:
                    text = await resp.text()
                    logger.error("Token exchange failed (%d): %s", resp.status, text)
                    return False
                data = await resp.json()
    except Exception as exc:
        logger.error("Token exchange request failed: %s", exc)
        return False

    access_token = data.get("access_token")
    refresh_token = data.get("refresh_token")
    expires_in = int(data.get("expires_in", 3600))
    expiry_ts = int(time.time()) + expires_in

    if not access_token or not refresh_token:
        logger.error("Token response missing fields: %s", data)
        return False

    # Step 4: Write tokens to .env
    env_path = str(_ENV_FILE)
    _atomic_update_env(
        env_path,
        {
            "CTRADER_ACCESS_TOKEN": access_token,
            "CTRADER_REFRESH_TOKEN": refresh_token,
            "CTRADER_TOKEN_EXPIRY": str(expiry_ts),
        },
    )

    logger.info("Tokens saved to %s. Expiry in %d seconds.", env_path, expires_in)
    return True


async def refresh_token(config) -> None:
    """
    Refresh the access token using the stored refresh token.
    Updates .env with new tokens on success.
    Raises RuntimeError on failure.
    """
    client_id = config.ctrader_client_id
    client_secret = config.ctrader_client_secret
    refresh_tok = config.ctrader_refresh_token

    if not refresh_tok:
        raise RuntimeError("No refresh token available. Run auth.py first.")

    async with aiohttp.ClientSession() as session:
        async with session.post(
            _TOKEN_URL,
            data={
                "grant_type": "refresh_token",
                "refresh_token": refresh_tok,
                "client_id": client_id,
                "client_secret": client_secret,
            },
        ) as resp:
            if resp.status != 200:
                text = await resp.text()
                raise RuntimeError(f"Token refresh failed ({resp.status}): {text}")
            data = await resp.json()

    access_token = data.get("access_token")
    new_refresh = data.get("refresh_token", refresh_tok)
    expires_in = int(data.get("expires_in", 3600))
    expiry_ts = int(time.time()) + expires_in

    env_path = str(_ENV_FILE)
    _atomic_update_env(
        env_path,
        {
            "CTRADER_ACCESS_TOKEN": access_token,
            "CTRADER_REFRESH_TOKEN": new_refresh,
            "CTRADER_TOKEN_EXPIRY": str(expiry_ts),
        },
    )
    logger.info("[TOKEN] Refreshed. New expiry in %d seconds.", expires_in)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    success = asyncio.run(authenticate())
    if success:
        print("✓ Authentication successful. Tokens saved to .env")
        exit(0)
    else:
        print("✗ Authentication failed.")
        exit(1)
