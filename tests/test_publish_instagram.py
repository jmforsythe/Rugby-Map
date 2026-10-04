"""Tests for rugby.social.publish_instagram (HTTP fully mocked)."""

import json
from pathlib import Path

import pytest

from rugby.seo import BASE_URL
from rugby.social import publish_instagram as pi

WEEK = "2026-09-26"
ASSETS = [f"{BASE_URL}/social/weekly/{WEEK}/instagram/0{i}.jpg" for i in range(3)]
CAPTION = f"Weekly round-up … rugbyunionmap.uk/stats/?week={WEEK}"


class FakeResponse:
    def __init__(self, status: int = 200, body: dict | None = None, headers: dict | None = None):
        self.status_code = status
        self._body = body if body is not None else {}
        self.headers = headers or {}
        self.text = json.dumps(self._body)

    def json(self):
        return self._body


class FakeSiteSession:
    """The public site: post.json plus HEAD for each JPEG."""

    def __init__(self, post: dict | None = None, head_status: int = 200):
        self.post = post or {"week": WEEK, "caption": CAPTION, "assets": ASSETS}
        self.head_status = head_status
        self.heads: list[str] = []

    def get(self, url, **_kwargs):
        assert url == f"{BASE_URL}/social/weekly/{WEEK}/post.json"
        return FakeResponse(body=self.post)

    def head(self, url, **_kwargs):
        self.heads.append(url)
        return FakeResponse(self.head_status, headers={"Content-Type": "image/jpeg"})


class FakeGraphSession:
    def __init__(self, captions: list[str] | None = None, me_user_id: str = "17841"):
        self.headers: dict[str, str] = {}
        self.captions = captions or []
        self.me_user_id = me_user_id
        self.calls: list[tuple[str, str, dict]] = []
        self._next_id = 0

    def request(self, method, url, **kwargs):
        path = url.split(f"/{pi.GRAPH_VERSION}/", 1)[1]
        self.calls.append((method, path, kwargs))
        if path == "me":
            return FakeResponse(
                body={"id": "app-scoped", "user_id": self.me_user_id, "username": "site"}
            )
        if method == "GET" and path.endswith("/media"):
            return FakeResponse(body={"data": [{"caption": c} for c in self.captions]})
        if method == "POST":
            self._next_id += 1
            return FakeResponse(body={"id": f"c{self._next_id}"})
        if kwargs.get("params", {}).get("fields") == "status_code":
            return FakeResponse(body={"status_code": "FINISHED"})
        return FakeResponse(body={"permalink": "https://www.instagram.com/p/abc/"})


@pytest.fixture
def complete_week(monkeypatch):
    monkeypatch.setattr(pi, "week_reported_counts", lambda *_a, **_k: (98, 100))


def _client(graph: FakeGraphSession, user_id: str = "17841") -> pi.InstagramClient:
    return pi.InstagramClient(user_id, "secret-token", graph, sleep=lambda _s: None)


def test_publishes_carousel_in_order(complete_week):
    graph = FakeGraphSession()
    link = pi.publish_week(WEEK, _client(graph), FakeSiteSession(), dry_run=False)

    assert link == "https://www.instagram.com/p/abc/"
    posts = [(path, kw.get("data")) for method, path, kw in graph.calls if method == "POST"]
    assert [d["image_url"] for _p, d in posts[:3]] == ASSETS
    assert all(d["is_carousel_item"] == "true" for _p, d in posts[:3])
    assert posts[3][1] == {"media_type": "CAROUSEL", "children": "c1,c2,c3", "caption": CAPTION}
    assert posts[4] == ("17841/media_publish", {"creation_id": "c4"})
    assert graph.headers["Authorization"] == "Bearer secret-token"
    assert not any("secret-token" in path for _m, path, _kw in graph.calls)


def test_dry_run_publishes_nothing(complete_week):
    graph = FakeGraphSession()
    assert pi.publish_week(WEEK, _client(graph), FakeSiteSession(), dry_run=True) is None
    assert not [c for c in graph.calls if c[0] == "POST"]


def test_skips_when_already_posted(complete_week):
    graph = FakeGraphSession(captions=["older post", CAPTION])
    assert pi.publish_week(WEEK, _client(graph), FakeSiteSession(), dry_run=False) is None
    assert not [c for c in graph.calls if c[0] == "POST"]


def test_skips_incomplete_week_unless_forced(monkeypatch):
    monkeypatch.setattr(pi, "week_reported_counts", lambda *_a, **_k: (90, 100))
    graph = FakeGraphSession()
    site = FakeSiteSession()
    assert pi.publish_week(WEEK, _client(graph), site, dry_run=False) is None
    assert graph.calls == [] and site.heads == []

    assert pi.publish_week(WEEK, _client(graph), site, dry_run=False, force=True)


def test_accepts_app_scoped_id(complete_week):
    graph = FakeGraphSession()
    assert (
        pi.publish_week(WEEK, _client(graph, "app-scoped"), FakeSiteSession(), dry_run=True) is None
    )


def test_rejects_token_for_another_account(complete_week):
    graph = FakeGraphSession(me_user_id="999")
    with pytest.raises(pi.PublishError, match="not IG_USER_ID"):
        pi.publish_week(WEEK, _client(graph), FakeSiteSession(), dry_run=True)


def test_missing_assets_fail_after_retries(complete_week):
    site = FakeSiteSession(head_status=404)
    with pytest.raises(pi.PublishError, match="not publicly fetchable"):
        pi.check_assets(ASSETS, site, retries=2, delay=0, sleep=lambda _s: None)
    assert len(site.heads) == 2 * len(ASSETS)


def test_rejects_assets_off_site(complete_week):
    site = FakeSiteSession(
        post={"week": WEEK, "caption": CAPTION, "assets": ["https://evil.example/x.jpg"]}
    )
    with pytest.raises(pi.PublishError, match="/social/"):
        pi.fetch_post(WEEK, site)


def test_summary_written(tmp_path: Path, monkeypatch, complete_week):
    summary = tmp_path / "summary.md"
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(summary))
    pi.publish_week(WEEK, _client(FakeGraphSession()), FakeSiteSession(), dry_run=True)
    text = summary.read_text(encoding="utf-8")
    assert "98/100" in text and "Dry run" in text and "secret-token" not in text
