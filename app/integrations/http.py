"""Bounded HTTP access to the fixed City of Calgary public-data endpoint."""

import urllib.error
import urllib.request


class FetchError(Exception):
    """A redacted HTTP failure, with a safe retry hint."""

    def __init__(self, message: str, *, temporary: bool = False) -> None:
        super().__init__(message)
        self.temporary = temporary


class NoRedirect(urllib.request.HTTPRedirectHandler):
    """Reject redirects rather than sending a fixed-source request elsewhere."""

    def redirect_request(
        self,
        req: urllib.request.Request,
        fp: object,
        code: int,
        msg: str,
        headers: object,
        newurl: str,
    ) -> None:
        """Return no replacement request for any redirect response."""
        return None


def fetch_calgary(path: str) -> bytes:
    """Read a size-limited response using the system's configured HTTPS transport."""
    if not path.startswith("/resource/vdjc-pybd.json?"):
        raise ValueError("Unsupported data endpoint")
    request = urllib.request.Request(
        "https://data.calgary.ca" + path,
        headers={
            "User-Agent": "WebixLeadDesk/0.2",
            "Accept": "application/json",
            "Accept-Encoding": "identity",
        },
    )
    try:
        with urllib.request.build_opener(NoRedirect()).open(request, timeout=15) as response:
            data: bytes = response.read(2_000_001)
            if len(data) > 2_000_000:
                raise FetchError("City data response exceeds the size limit")
            return data
    except urllib.error.HTTPError as error:
        raise FetchError(
            "City data request failed", temporary=error.code in {429, 500, 502, 503, 504}
        ) from error
    except (OSError, urllib.error.URLError) as error:
        raise FetchError("City data connection failed", temporary=True) from error
