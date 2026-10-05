"""Publish the weekly report carousel to Instagram via the Graph API.

Reads the week's ``post.json`` (caption + JPEG URLs) from the live site, where
``deploy.yml`` renders it with ``publish_social=true``, then:

1. checks enough of the week's results are in (rankings settle at ~97-99%);
2. checks every slide is publicly fetchable (Meta cURLs the URLs itself);
3. checks the token belongs to ``IG_USER_ID``;
4. skips if the week is already on the account (its stats link is in a caption);
5. creates one container per slide, a carousel container, waits for it, publishes.

``--dry-run`` stops after step 4. Uses Instagram API with Instagram Login
(``graph.instagram.com``); reads ``IG_ACCESS_TOKEN`` and ``IG_USER_ID`` from the
environment.

Usage::

    python -m rugby.social.publish_instagram --week 2026-09-26 --dry-run
    python -m rugby.social.publish_instagram            # most recent Saturday
"""

from __future__ import annotations

import argparse
import logging
import os
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date
from pathlib import Path

import requests

from core.config import setup_logging
from rugby.analysis.instagram_weekly import most_recent_saturday
from rugby.seo import BASE_URL, absolute_url
from rugby.weekly_report import saturday_of_week, week_reported_counts

logger = logging.getLogger(__name__)

GRAPH_HOST = "https://graph.instagram.com"
GRAPH_VERSION = "v25.0"
MIN_COMPLETENESS = 0.95
MAX_CAROUSEL_ITEMS = 10
HTTP_TIMEOUT = 30
# Pages can take a minute or two to serve a fresh deploy.
ASSET_RETRIES = 10
ASSET_RETRY_DELAY = 30
SITE_USER_AGENT = "rugby-mapping/1.0 (+https://rugbyunionmap.uk; weekly-instagram-publish)"
STATUS_POLL_ATTEMPTS = 30
STATUS_POLL_DELAY = 5


class PublishError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class WeeklyPost:
    week: str
    caption: str
    assets: list[str]


def _summary(line: str) -> None:
    """Append to the GitHub Actions job summary (and the log)."""
    logger.info(line)
    path = os.environ.get("GITHUB_STEP_SUMMARY")
    if path:
        with open(path, "a", encoding="utf-8") as f:
            f.write(line + "\n")


def stats_link_marker(week: str) -> str:
    """Substring every weekly caption contains, used to detect an existing post."""
    return f"/stats/?week={week}"


def site_session() -> requests.Session:
    """HTTP session for rugbyunionmap.uk (no Instagram API credentials)."""
    session = requests.Session()
    session.headers["User-Agent"] = SITE_USER_AGENT
    session.headers["Accept"] = "application/json, image/jpeg, */*"
    return session


def fetch_post(
    week: str,
    session: requests.Session,
    *,
    retries: int = ASSET_RETRIES,
    delay: float = ASSET_RETRY_DELAY,
    sleep: Callable[[float], None] = time.sleep,
) -> WeeklyPost:
    url = absolute_url(f"/social/weekly/{week}/post.json")
    for attempt in range(1, retries + 1):
        resp = session.get(url, timeout=HTTP_TIMEOUT, allow_redirects=False)
        if resp.status_code == 200:
            break
        if attempt < retries:
            logger.info("%s returned HTTP %s; retrying in %ss", url, resp.status_code, delay)
            sleep(delay)
    else:
        raise PublishError(
            f"{url} returned HTTP {resp.status_code}; deploy with publish_social first"
        )
    data = resp.json()
    if data.get("week") != week:
        raise PublishError(f"{url} is for week {data.get('week')!r}, not {week}")
    assets = data.get("assets") or []
    if not assets or len(assets) > MAX_CAROUSEL_ITEMS:
        raise PublishError(f"Expected 1-{MAX_CAROUSEL_ITEMS} assets, got {len(assets)}")
    if not all(isinstance(a, str) and a.startswith(f"{BASE_URL}/social/") for a in assets):
        raise PublishError("Asset URLs must be under the site's /social/ path")
    return WeeklyPost(week=week, caption=data["caption"], assets=assets)


def check_assets(
    assets: list[str],
    session: requests.Session,
    *,
    retries: int = ASSET_RETRIES,
    delay: float = ASSET_RETRY_DELAY,
    sleep: Callable[[float], None] = time.sleep,
) -> None:
    """Every asset must be a 200 JPEG with no redirect, as Meta fetches it directly."""
    pending = list(assets)
    for attempt in range(1, retries + 1):
        failures: list[str] = []
        for url in pending:
            resp = session.head(url, timeout=HTTP_TIMEOUT, allow_redirects=False)
            content_type = resp.headers.get("Content-Type", "")
            if resp.status_code != 200 or not content_type.startswith("image/jpeg"):
                failures.append(f"{url} (HTTP {resp.status_code}, {content_type or 'no type'})")
        if not failures:
            return
        pending = [f.split(" ", 1)[0] for f in failures]
        if attempt < retries:
            logger.info("%d asset(s) not live yet; retrying in %ss", len(failures), delay)
            sleep(delay)
    raise PublishError("Assets not publicly fetchable: " + "; ".join(failures))


class InstagramClient:
    def __init__(
        self,
        user_id: str,
        token: str,
        session: requests.Session | None = None,
        *,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.user_id = user_id
        self.session = session or requests.Session()
        # Bearer header rather than a query parameter, so the token never
        # appears in URLs that end up in exception messages or logs.
        self.session.headers["Authorization"] = f"Bearer {token}"
        self.sleep = sleep

    def _call(self, method: str, path: str, **kwargs) -> dict:
        url = f"{GRAPH_HOST}/{GRAPH_VERSION}/{path}"
        resp = self.session.request(method, url, timeout=HTTP_TIMEOUT, **kwargs)
        try:
            body = resp.json()
        except ValueError:
            body = {}
        if resp.status_code != 200 or "error" in body:
            error = body.get("error", {})
            raise PublishError(
                f"{method} /{path} failed (HTTP {resp.status_code}): "
                f"{error.get('message', resp.text[:200])} "
                f"[code {error.get('code')}, subcode {error.get('error_subcode')}]"
            )
        return body

    def me(self) -> dict:
        return self._call("GET", "me", params={"fields": "id,user_id,username,account_type"})

    def recent_captions(self, limit: int = 25) -> list[str]:
        body = self._call(
            "GET", f"{self.user_id}/media", params={"fields": "caption", "limit": limit}
        )
        return [item.get("caption") or "" for item in body.get("data", [])]

    def create_image_container(self, image_url: str) -> str:
        body = self._call(
            "POST",
            f"{self.user_id}/media",
            data={"image_url": image_url, "is_carousel_item": "true"},
        )
        return body["id"]

    def create_carousel(self, children: list[str], caption: str) -> str:
        body = self._call(
            "POST",
            f"{self.user_id}/media",
            data={"media_type": "CAROUSEL", "children": ",".join(children), "caption": caption},
        )
        return body["id"]

    def wait_until_finished(self, container_id: str) -> None:
        for _ in range(STATUS_POLL_ATTEMPTS):
            status = self._call("GET", container_id, params={"fields": "status_code"})
            code = status.get("status_code")
            if code == "FINISHED":
                return
            if code in {"ERROR", "EXPIRED"}:
                raise PublishError(f"Container {container_id} status {code}")
            self.sleep(STATUS_POLL_DELAY)
        raise PublishError(f"Container {container_id} not ready after polling")

    def publish(self, container_id: str) -> str:
        body = self._call(
            "POST", f"{self.user_id}/media_publish", data={"creation_id": container_id}
        )
        return body["id"]

    def permalink(self, media_id: str) -> str:
        return self._call("GET", media_id, params={"fields": "permalink"}).get("permalink", "")


def publish_week(
    week: str,
    client: InstagramClient,
    session: requests.Session,
    *,
    dry_run: bool,
    force: bool = False,
    min_completeness: float = MIN_COMPLETENESS,
    fixture_data_dir: Path | None = None,
    asset_retries: int = ASSET_RETRIES,
) -> str | None:
    """Run the checks and (unless ``dry_run``) publish; returns the permalink if posted."""
    reported, due = week_reported_counts(date.fromisoformat(week), fixture_data_dir)
    ratio = reported / due if due else 0.0
    _summary(f"- Results reported for week {week}: {reported}/{due} ({ratio:.1%})")
    if ratio < min_completeness and not force:
        _summary(
            f"- **Skipped:** below the {min_completeness:.0%} threshold; rankings may still change"
        )
        return None

    post = fetch_post(week, session)
    check_assets(post.assets, session, retries=asset_retries)
    _summary(f"- {len(post.assets)} slides live on {BASE_URL}")

    me = client.me()
    # /me returns both an app-scoped ``id`` and the professional account ``user_id``;
    # either works as the publishing path, so accept whichever was stored.
    if str(client.user_id) not in {str(me.get("id")), str(me.get("user_id"))}:
        raise PublishError(f"Token belongs to @{me.get('username')}, not IG_USER_ID")
    _summary(f"- Token OK for @{me.get('username')} ({me.get('account_type')})")

    marker = stats_link_marker(week)
    if any(marker in caption for caption in client.recent_captions()):
        _summary(f"- **Skipped:** week {week} is already posted")
        return None

    if dry_run:
        _summary("- Dry run: all checks passed, nothing published")
        return None

    children = [client.create_image_container(url) for url in post.assets]
    for child in children:
        client.wait_until_finished(child)
    carousel = client.create_carousel(children, post.caption)
    client.wait_until_finished(carousel)
    media_id = client.publish(carousel)
    link = client.permalink(media_id)
    _summary(f"- **Published:** {link or media_id}")
    return link or media_id


def _require_env(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise SystemExit(f"{name} is not set")
    return value


def main() -> None:
    parser = argparse.ArgumentParser(description="Publish the weekly report carousel to Instagram")
    parser.add_argument(
        "--week",
        type=date.fromisoformat,
        default=None,
        help="Any date in the week (snapped to its Saturday); default: most recent Saturday",
    )
    parser.add_argument("--dry-run", action="store_true", help="Run every check but don't publish")
    parser.add_argument(
        "--force", action="store_true", help="Publish even if the week's results look incomplete"
    )
    args = parser.parse_args()

    setup_logging()
    saturday = saturday_of_week(args.week) if args.week else most_recent_saturday(date.today())
    week = saturday.isoformat()
    _summary(f"### Instagram weekly post: weekend of {week}")

    site = site_session()
    client = InstagramClient(_require_env("IG_USER_ID"), _require_env("IG_ACCESS_TOKEN"))
    try:
        publish_week(week, client, site, dry_run=args.dry_run, force=args.force)
    except PublishError as exc:
        _summary(f"- **Failed:** {exc}")
        raise SystemExit(1) from exc


if __name__ == "__main__":
    main()
