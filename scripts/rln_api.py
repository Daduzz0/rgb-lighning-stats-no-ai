"""Small client for the original RGB Lightning Node REST API. No AIR-GB imports."""
import json
import os
import urllib.error
import urllib.parse
import urllib.request


class ApiError(RuntimeError):
    pass


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


class Rln:
    def __init__(self, url, token_env=None):
        parts = urllib.parse.urlsplit(url or "")
        if parts.scheme not in ("http", "https") or not parts.hostname or parts.username or parts.password or parts.query or parts.fragment:
            raise ValueError("Configurare un URL REST diretto del nodo, senza credenziali nell'URL")
        if parts.path.strip("/"):
            raise ValueError("Serve la radice REST del daemon RLN, non /api del servizio AIR-GB")
        self.url = url.rstrip("/")
        self.headers = {"Content-Type": "application/json"}
        if token_env:
            token = os.environ.get(token_env)
            if not token:
                raise ValueError(f"Variabile token mancante: {token_env}")
            self.headers["Authorization"] = "Bearer " + token
        self.opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())

    def request(self, endpoint, body=None, timeout=15):
        request = urllib.request.Request(
            self.url + "/" + endpoint,
            data=None if body is None else json.dumps(body).encode(),
            headers=self.headers,
            method="GET" if body is None else "POST",
        )
        try:
            with self.opener.open(request, timeout=timeout) as response:
                result = json.load(response)
        except urllib.error.HTTPError as error:
            # Error bodies can contain sensitive arguments: don't echo them.
            try:
                name = json.loads(error.read()).get("name", "HTTP_ERROR")
            except (ValueError, AttributeError):
                name = "HTTP_ERROR"
            raise ApiError(f"{endpoint}: HTTP {error.code} ({name})") from None
        except (OSError, ValueError) as error:
            raise ApiError(f"{endpoint}: {type(error).__name__}; nessun retry automatico") from None
        if not isinstance(result, dict):
            raise ApiError(f"{endpoint}: risposta JSON non riconosciuta")
        return result


def regtest(value):
    return isinstance(value, str) and value.lower() == "regtest"
