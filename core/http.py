"""HTTP client helpers: sessions, request wrappers, and anti-bot handling."""

import functools
import logging
import os
import sys
import threading
import time

import requests

logger = logging.getLogger(__name__)


@functools.cache
def _resolve_curl_path() -> str:
    """Find a curl binary that isn't Windows' bundled System32\\curl.exe.

    On Windows, ``subprocess.run(["curl", ...])`` resolves ``curl`` via
    ``CreateProcess``'s search order, which checks ``System32`` before the
    user's ``PATH`` entries. That bundled curl build (distinct from e.g. Git
    for Windows' mingw64 curl) reliably gets a Cloudflare challenge (202)
    on every request to englandrugby.com, even though a request with
    identical headers/args from a curl on ``PATH`` succeeds — so prefer any
    non-System32 curl found on ``PATH`` before falling back to the bare name.
    """
    if sys.platform != "win32":
        return "curl"

    default_root = r"C:\Windows"
    system_root = os.environ.get("SystemRoot", default_root)  # noqa: SIM112
    skip_dirs = {
        os.path.normcase(os.path.join(system_root, "System32")),
        os.path.normcase(os.path.join(system_root, "SysWOW64")),
    }
    for directory in os.environ.get("PATH", "").split(os.pathsep):
        if not directory or os.path.normcase(directory) in skip_dirs:
            continue
        candidate = os.path.join(directory, "curl.exe")
        if os.path.isfile(candidate):
            return candidate

    return "curl"


class AntiBotDetectedError(Exception):
    """Exception raised when anti-bot detection is triggered."""

    log_text: str | None

    def __init__(self, message: str, *, log_text: str | None = None) -> None:
        super().__init__(message)
        self.log_text = log_text


class AntiBotBackoffBudget:
    """Caps cumulative anti-bot sleep so a long scrape cannot stall for hours.

    Not thread-safe: give each worker its own budget rather than sharing one
    across the thread pools in e.g. ``rugby.addresses``.
    """

    def __init__(self, max_seconds: float = 3600.0) -> None:
        self.max_seconds = max_seconds
        self.used_seconds = 0.0

    @property
    def remaining(self) -> float:
        return max(0.0, self.max_seconds - self.used_seconds)

    @property
    def exhausted(self) -> bool:
        return self.remaining <= 0

    def sleep(self, requested: float) -> float:
        """Sleep up to *requested* seconds; raise when the budget is exhausted."""
        if self.exhausted:
            raise AntiBotDetectedError(
                f"anti-bot backoff budget exhausted ({self.max_seconds:.0f}s)"
            )
        actual = min(requested, self.remaining)
        if actual > 0:
            time.sleep(actual)
            self.used_seconds += actual
        if actual < requested:
            raise AntiBotDetectedError(
                f"anti-bot backoff budget exhausted after {self.used_seconds:.0f}s"
            )
        return actual


_thread_local = threading.local()
_print_lock = threading.Lock()


def get_session() -> requests.Session:
    """Get thread-local session (requests.Session is not thread-safe)."""
    sess = getattr(_thread_local, "session", None)
    if sess is None:
        sess = requests.Session()
        _thread_local.session = sess
    return sess


def get_headers(referer: str | None = None) -> dict[str, str]:
    """Get standard headers with optional referer for RFU website requests."""
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8",
        "Accept-Language": "en-GB,en-US;q=0.9,en;q=0.8",
        "Accept-Encoding": "gzip, deflate, br",
        "Connection": "keep-alive",
        "Upgrade-Insecure-Requests": "1",
        "Sec-Fetch-Dest": "document",
        "Sec-Fetch-Mode": "navigate",
        "Sec-Fetch-Site": "same-origin" if referer else "none",
        "Sec-Fetch-User": "?1",
        "Cache-Control": "max-age=0",
    }
    if referer:
        headers["Referer"] = referer
    return headers


def _curl_fallback(url: str, referer: str | None, timeout: int) -> requests.Response:
    """Use curl as a fallback when requests gets a Cloudflare 202 challenge."""
    import subprocess

    cmd = [
        _resolve_curl_path(),
        "-s",
        "-w",
        "\n%{http_code}",
        "-H",
        f"User-Agent: {get_headers()['User-Agent']}",
        "-H",
        "Accept: text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "--max-time",
        str(timeout),
    ]
    if referer:
        cmd += ["-H", f"Referer: {referer}"]
    cmd.append(url)

    result = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout + 10)
    lines = result.stdout.rsplit("\n", 1)
    body = lines[0] if len(lines) > 1 else result.stdout
    status = int(lines[-1]) if len(lines) > 1 and lines[-1].strip().isdigit() else 0

    resp = requests.Response()
    resp.status_code = status
    resp._content = body.encode("utf-8")
    resp.encoding = "utf-8"
    return resp


def _anti_bot_sleep(seconds: float, budget: AntiBotBackoffBudget | None) -> None:
    if budget is not None:
        budget.sleep(seconds)
    else:
        time.sleep(seconds)


def make_request(
    url: str,
    referer: str | None = None,
    max_retries: int = 3,
    timeout: int = 30,
    delay_seconds: float = 2.0,
    antibot_budget: AntiBotBackoffBudget | None = None,
    antibot_backoff_base: float = 5.0,
    antibot_backoff_cap: float = 600.0,
) -> requests.Response:
    """Make an HTTP GET request with retry logic and exponential backoff.

    Falls back to curl when the requests library receives a Cloudflare 202
    challenge (TLS fingerprint mismatch).

    Anti-bot responses (202/403) retry with exponential backoff, doubling from
    *antibot_backoff_base* up to *antibot_backoff_cap* per wait. Pass an
    *antibot_budget* to bound the cumulative wait across many calls.
    """
    for attempt in range(max_retries):
        try:
            if delay_seconds > 0:
                time.sleep(delay_seconds + attempt * 2)

            response = get_session().get(url, headers=get_headers(referer), timeout=timeout)
            if response.status_code == 202:
                response = _curl_fallback(url, referer, timeout)
            if response.status_code in (202, 403):
                raise AntiBotDetectedError(f"{response.status_code} code")
            response.raise_for_status()
            return response

        except AntiBotDetectedError as exc:
            if attempt == max_retries - 1:
                raise
            backoff = min(antibot_backoff_base * (2**attempt), antibot_backoff_cap)
            logger.warning(
                "Anti-bot detection (%s); backing off %.0fs before retry %d/%d",
                exc,
                backoff,
                attempt + 2,
                max_retries,
            )
            _anti_bot_sleep(backoff, antibot_budget)

        except requests.exceptions.RequestException:
            if attempt == max_retries - 1:
                raise
            time.sleep(5 * (attempt + 1))

    raise RuntimeError(f"Failed to fetch {url} after {max_retries} attempts")


def print_block(text: str) -> None:
    """Log multi-line text without interleaving across threads."""
    with _print_lock:
        logger.info(text)
