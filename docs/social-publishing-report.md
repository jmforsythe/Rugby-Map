# Automating the weekly report carousel: Instagram and TikTok

*Status, 4 Oct 2026: Instagram Phase 1 is implemented. Slides are 4:5 with a
3:4 grid-safe layout. `deploy.yml` (`publish_social`) hosts the current week under
`/social/`. `social-weekly.yml` runs `rugby.social.publish_instagram` with the
completeness, asset, token and already-posted guards. The Tuesday
`update-fixtures.yml` run chains scrape → deploy → post, and
`refresh-instagram-token.yml` refreshes the token (needs `SOCIAL_ADMIN_PAT`).
TikTok is still manual. The rest of this document is the original proposal.*

## TL;DR

- **Instagram can be fully automated** with Meta's official API. It costs nothing
  and needs no App Review, because the app only ever posts to your own account.
  - **One required change:** the API rejects 3:4 images. The Instagram slides must
    switch to **4:5 (1080×1350)**.
- **TikTok can't be posted publicly and unattended from your own app.**
  - TikTok only lets audited apps post publicly, and its audit rejects "a utility
    tool to help upload contents to the account(s) you or your team manages".
  - Realistic options:
    - your own app **uploads the slides as a draft** to your TikTok inbox, and you
      tap "post" on your phone (semi-automatic, free);
    - a **third-party scheduler** whose app *is* audited, such as Buffer or
      Upload-Post (fully automatic, free-to-about-$24/mo);
    - stay manual, with the workflow handing you the files and caption.
- **Pipeline:**
  1. The weekly scrape runs.
  2. The site deploy renders the slides into the site (`/social/...` on
     `rugbyunionmap.uk`), so they're publicly reachable.
  3. A new `social-weekly.yml` workflow is dispatched at the end of that deploy
     and publishes them.
- **Timing matters more than tooling.**
  - At Monday's scrape, about 10% of the weekend's fixtures still have no result:
    99 of 991 for the weekend of 26 Sep, against about 1% once a week is fully
    reported.
  - A Monday-morning post would rank an incomplete week. Add a **Tuesday scrape**
    and publish from that, behind a completeness check.

**Recommended path:**
1. **Phase 1:** Instagram via the Graph API. TikTok as an inbox draft, or as a
   ready-to-post bundle (JPEGs and caption) in the workflow run.
2. **Phase 2,** if you want TikTok hands-off: Buffer's API, falling back to
   Upload-Post.

---

## 1. Where things stand

| Piece | Today |
|---|---|
| Scrape | `update-fixtures.yml`, Mondays 06:23 UTC. It commits `fixture_data` and then runs `gh workflow run deploy.yml`. |
| Site | `deploy.yml` builds `dist/` and deploys GitHub Pages at `https://rugbyunionmap.uk`. It already installs Playwright and Chromium, which renders the slides. |
| Slides | `python -m rugby.analysis.instagram_weekly` writes 5 JPEGs per format under `output/instagram/upload/`, and a caption and asset list in `output/instagram/queue/*.json`. |
| Fonts and crests | Text measurement fetches Oswald and Barlow once, into `data/caches/fonts`. Crests are downloaded from `images.englandrugby.com` and cached in `data/caches`. In CI both should be cached with `actions/cache` (see §6). |

## 2. Hard platform constraints

### Instagram (Meta Graph API: "Instagram API with Instagram Login")

- **Account:** must be a Business or Creator account. No Facebook Page is needed
  on the Instagram Login path.
- **Access:** Standard Access, no App Review, provided the account is added to the
  app. App Review (Advanced Access) is only for accounts you don't own.
- **Scopes:** `instagram_business_basic` and `instagram_business_content_publish`.
  The older scope names were deprecated in January 2025.
- **Carousel flow:**
  1. One `POST /{ig-id}/media` per slide (`image_url`, `is_carousel_item=true`).
  2. `POST /{ig-id}/media` with `media_type=CAROUSEL`, `children` and `caption`.
  3. Poll `status_code` until it reads `FINISHED`.
  4. `POST /{ig-id}/media_publish`. The host is `graph.instagram.com`.
- **Images:**
  - JPEG only, at most 8 MB, at most 1440 px wide.
  - The aspect ratio must be between **4:5 and 1.91:1**.
  - 3:4 is narrower than 4:5, so the current 1440×1920 slides will be rejected
    (error 36003). Instagram's app now shows 3:4 on the grid, but the API docs
    haven't changed, so assume 4:5.
  - The other slides are cropped to match the first.
- **Limits:**
  - 10 items per carousel.
  - 100 API posts per rolling 24 hours.
  - Captions up to 2,200 characters, with at most 30 hashtags.
- **Hosting:** `image_url` must be publicly fetchable, over https, when Meta
  fetches it. GitHub Pages on your own domain is the safest choice.
- **Tokens:**
  - Long-lived tokens last **60 days**, and are refreshed with
    `GET graph.instagram.com/refresh_access_token`. A token must be at least
    24 hours old to refresh.
  - CI has to refresh the token on a schedule and write the new one back to the
    repo secret.
  - Never-expiring system-user tokens exist only on the heavier Facebook Login /
    Business Manager path, which isn't worth it here.
- **Scheduling:** the API has no confirmed native scheduling, so the GitHub
  Actions trigger does the scheduling.

### TikTok (Content Posting API)

- **Photo posts:**
  - `POST /v2/post/publish/content/init/` with `media_type=PHOTO` and
    `source=PULL_FROM_URL`. URL pulling is the only source TikTok allows for photos.
  - Up to 35 images, JPEG or WebP, at most 1080p. The 1080×1920 slides fit.
- **URLs:**
  - https only, and **no redirects**.
  - The domain or URL prefix must be **verified**: a DNS TXT record for a domain,
    or a signature file for a prefix.
  - `rugbyunionmap.uk` can be domain-verified with a TXT record. GitHub release
    asset URLs redirect, so they won't work.
- **Two modes:**
  - `DIRECT_POST` (scope `video.publish`) publishes straight away.
  - `MEDIA_UPLOAD` (scope `video.upload`) puts a draft in your TikTok inbox. You
    finish it in the app: music, privacy, post.
- **Unaudited apps** can only post **private (SELF_ONLY)** content, from at most
  5 users a day, who must have private accounts.
  - The audit checks compliance with TikTok's content-sharing guidelines.
  - Those guidelines explicitly disallow "a utility tool to help upload contents
    to the account(s) you or your team manages" and apps for "private use".
  - Direct Post also requires an interactive consent screen: a privacy picker
    with no default, a music consent notice and a preview. A cron job can't
    provide that.
  - **Conclusion:** public, unattended posting from your own app isn't possible.
- **Inbox drafts** are the plausible route for your own app. You still need the
  `video.upload` scope approved and the domain verified. The docs are unclear on
  whether an unaudited app can create drafts that you then post publicly. Test it
  in the app's sandbox first.
- **Tokens:**
  - Access tokens last 24 hours; refresh tokens last 365 days.
  - The **refresh token rotates**, so store the new one after every refresh.
- **Limits:** 6 requests per minute per token, and at most 5 pending shares
  per 24 hours.

## 3. Options

| Option | Instagram | TikTok | Cost | Effort | Main risk |
|---|---|---|---|---|---|
| **A. DIY Instagram + TikTok bundle** | Auto (Graph API) | Manual: the workflow uploads the JPEGs and caption as an artifact and opens or updates an issue | Free | Low | Forgetting to post TikTok |
| **B. DIY Instagram + TikTok draft** | Auto | Semi-auto: draft lands in your inbox, one tap to post | Free | Medium (TikTok app, scope approval, domain verification, rotating refresh token) | `video.upload` approval and behaviour uncertain for an unaudited app |
| **C. Buffer API for both** | Auto | Auto (Buffer's TikTok app is audited) | Free tier (API in public beta, 3,000 requests per 30 days) or paid | Low to medium | API is beta; check the TikTok photo field support before committing |
| **D. Upload-Post for both** | Auto | Auto (direct or draft) | About $16–24/mo (TikTok needs a paid plan) | Low | Third-party dependency and cost |
| E. Ayrshare / Postiz Cloud / Publer | Auto | Auto | $29–149+/mo | Low | Cost is high for one weekly post |

Not suitable:
- **Hootsuite:** its API can't post to Instagram Business accounts.
- **Zapier:** handles Instagram carousels but has no TikTok posting.
- **Self-hosted Postiz:** it needs your own TikTok app, so the same audit problem
  applies.
- **Marketplace GitHub Actions:** there's no maintained one for Instagram
  carousels. A small Python step is the norm.

**Recommendation:** start with **A**, and upgrade TikTok to **B** if the draft
flow proves workable. Move to **C** if you want TikTok posted with no tap at all.
That keeps Instagram on the free official API, with nothing in between that could
break or start charging.

## 4. Proposed GitHub Actions design

```
update-fixtures.yml  (Mon 06:23 UTC, + new Tue 06:23 UTC run)
   └─ commits fixture_data, then:
      gh workflow run deploy.yml -f publish_social=true      (Tuesday run only)
         deploy.yml
           ├─ builds dist/, incl. dist/social/weekly/<saturday>/{instagram,tiktok}/*.jpg
           │  + dist/social/weekly/<saturday>/post.json (captions, asset URLs)
           ├─ deploys Pages  →  https://rugbyunionmap.uk/social/weekly/<saturday>/...
           └─ if inputs.publish_social: gh workflow run social-weekly.yml -f week=<saturday>
                social-weekly.yml
                  ├─ guard: already posted?  completeness ≥ threshold?
                  ├─ Instagram: create containers → poll → media_publish
                  ├─ TikTok: draft upload (B) / Buffer (C) / artifact + issue (A)
                  └─ record the week as posted
```

### Why this shape

- **Explicit `workflow_dispatch`, not `workflow_run`.**
  - `deploy.yml` also runs on every push to `main`, so a `workflow_run` trigger
    would need filtering to avoid posting after unrelated deploys.
  - A dispatch made with `GITHUB_TOKEN` is the documented exception that *does*
    start a new run, so the chain is reliable.
  - It's the same pattern `update-fixtures.yml` already uses to start the deploy.
- **Render inside the deploy.**
  - The Pages artifact replaces the whole site on every deploy. Images written by
    a separate workflow would vanish on the next deploy unless they're part of
    the build.
  - The deploy already has Chromium.
  - Hosting on your own domain means no redirects, and lets TikTok verify the
    domain with one TXT record.
- **Post on Tuesday, not Monday.**
  - With about 10% of results missing on Monday, the top-10s can still change.
  - Either add `cron: "23 6 * * 2"` to `update-fixtures.yml` and publish only
    from that run, or keep one scrape and delay publishing.
  - The completeness check below covers bad weeks either way.

### Guards in `social-weekly.yml`

1. **Already posted.**
   - Keep a repo variable `SOCIAL_LAST_POSTED_WEEK`, set with `gh variable set`,
     or a small committed `data/social/posted.json`.
   - Skip the run if the week has already been posted.
   - This stops a re-run or manual dispatch double-posting. Instagram has no
     "delete" for API mistakes beyond doing it by hand.
2. **Complete enough.** Publish only if at least about 95% of the week's fixtures
   have a score or a walkover. Otherwise skip, and write the reason to the job
   summary.
3. **Images live.** HEAD each asset URL and require a 200 with no redirect before
   calling the APIs. The Pages deploy can lag slightly behind the workflow ending.
4. **Dry run.** A `workflow_dispatch` input `dry_run: true` renders and validates
   everything but skips the publish calls, for testing.

### Sketch

```yaml
# .github/workflows/social-weekly.yml (sketch)
name: Publish weekly report to social
on:
  workflow_dispatch:
    inputs:
      week: { description: "Saturday (YYYY-MM-DD)", required: true, type: string }
      dry_run: { type: boolean, default: false }
permissions:
  contents: read
concurrency: { group: social-weekly, cancel-in-progress: false }
jobs:
  publish:
    runs-on: ubuntu-latest
    environment: social          # secrets scoped here; optional manual-approval gate
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with: { python-version: "3.12", cache: pip }
      - run: pip install -r requirements.txt
      - name: Guards (posted already? completeness? assets live?)
        run: python -m rugby.social.preflight --week "${{ inputs.week }}"
      - name: Instagram carousel
        if: ${{ !inputs.dry_run }}
        env:
          IG_USER_ID: ${{ secrets.IG_USER_ID }}
          IG_ACCESS_TOKEN: ${{ secrets.IG_ACCESS_TOKEN }}
        run: python -m rugby.social.publish_instagram --week "${{ inputs.week }}"
      - name: TikTok (draft / bundle)
        if: ${{ !inputs.dry_run }}
        run: python -m rugby.social.publish_tiktok --week "${{ inputs.week }}" --mode draft
      - name: Record posted week
        if: ${{ !inputs.dry_run }}
        env: { GH_TOKEN: ${{ secrets.SOCIAL_ADMIN_PAT }} }
        run: gh variable set SOCIAL_LAST_POSTED_WEEK --body "${{ inputs.week }}"
```

Plus a small **token-refresh workflow**:
- Run it weekly or monthly on a cron.
- It calls Instagram's `refresh_access_token` and writes the result back with
  `gh secret set IG_ACCESS_TOKEN`. Do the same for the TikTok refresh token if
  you use option B.
- Writing secrets needs a **fine-grained PAT** with "Secrets: read and write" on
  this repo only, stored as `SOCIAL_ADMIN_PAT`. `GITHUB_TOKEN` can't write
  secrets.
- Have it fail loudly so an expiring token is noticed long before day 60.

### Secrets and setup checklist

- [ ] Instagram account switched to Business or Creator.
- [ ] Meta developer app (type Business) with the Instagram product, using
      Instagram Login, and your account added.
- [ ] Generate a token, exchange it for a 60-day token, and store
      `IG_ACCESS_TOKEN` and `IG_USER_ID` (in the `social` environment).
- [ ] `SOCIAL_ADMIN_PAT` (fine-grained, this repo, Secrets and Variables: write).
- [ ] TikTok (option B): developer app, Content Posting API with `video.upload`,
      TXT record verifying `rugbyunionmap.uk`, OAuth once locally, store
      `TIKTOK_CLIENT_KEY`, `TIKTOK_CLIENT_SECRET` and `TIKTOK_REFRESH_TOKEN`.
      Or, for option C, a Buffer account with Instagram and TikTok connected,
      and `BUFFER_TOKEN`.

## 5. Code changes needed in this repo

1. **Instagram format: 3:4 → 4:5.** In `rugby/analysis/instagram_weekly.py`,
   `FORMATS["instagram"]` becomes 1080×1350, with JPEGs at 1080×1350 (or
   1440×1800). Ten rows still fit; each row gets about 9 px shorter.
2. **Render in the deploy.**
   - Add a `--out dist/social/weekly` mode that writes the JPEGs and a `post.json`
     holding the captions and absolute asset URLs.
   - Call it from `deploy.yml`'s assemble job for the latest week.
   - Keep earlier weeks out of `dist`, or prune them, to avoid growing the site.
3. **`rugby/social/`** (new):
   - `preflight.py`: posted-already, completeness and asset checks.
   - `publish_instagram.py`: about 60 lines with `requests`. Create the
     containers, poll `status_code`, publish, then log the permalink to
     `$GITHUB_STEP_SUMMARY`.
   - `publish_tiktok.py`: draft upload, or the Buffer call.
   - All with unit tests that mock HTTP.
4. **`update-fixtures.yml`:** add the Tuesday cron, and pass
   `-f publish_social=true` to the deploy only on that run.
   **`deploy.yml`:** add a `publish_social` input and a final step that dispatches
   `social-weekly.yml` after a successful Pages deploy.
5. **CI caches:** add `actions/cache` for `data/caches/fonts` and the crest cache,
   so renders don't refetch about 50 crests and two fonts every week.

## 6. Risks and mitigations

| Risk | Mitigation |
|---|---|
| Incomplete or late results produce a wrong top 10 | Tuesday publish and the completeness check. The caption already links to the live stats page for the week. |
| Double posting on re-runs | Posted-week marker and the `concurrency` group. |
| Instagram token silently expires (60 days) | Scheduled refresh workflow that fails loudly. Check token expiry in preflight and warn under 14 days. |
| Meta can't fetch an image (error 9004 / 2207052) | Asset HEAD check; retry the container creation once after a delay. |
| Platform API or policy changes (both change often) | Keep the platform code small and isolated in `rugby/social/`. The dry-run mode lets you re-validate quickly. |
| Junk data (e.g. the 12 Sep 2026 dates in old seasons, extreme merit scores like 338–14) tops a list | Preflight sanity check: flag any score above about 150 or a margin above about 140, and hold the post for a manual run instead. |
| Club crests are clubs' marks | Already used on the site and in the existing posts. Consider a line in the bio or caption crediting englandrugby.com as the data source (the slides already say this). |

## 7. Suggested order of work

1. Switch Instagram to 4:5 and render into `dist/social` in the deploy. Check the
   URLs are live.
2. Set up the Meta app and token. Run `social-weekly.yml` in `dry_run`, then for
   real once.
3. Add the Tuesday scrape, the completeness check and the posted-week marker. Turn
   on the automatic dispatch.
4. Add the token-refresh workflow.
5. TikTok:
   - start with option A (artifact and issue);
   - try option B in TikTok's sandbox;
   - if drafts can't be posted publicly, decide between staying manual and paying
     for option C or D.

## Sources

- Meta, Instagram content publishing (carousel flow, limits, cropping):
  https://developers.facebook.com/docs/instagram-platform/content-publishing/
- Meta, Instagram API with Instagram Login (no Facebook Page needed):
  https://developers.facebook.com/docs/instagram-platform/instagram-api-with-instagram-login
- Meta, Instagram API with Instagram Login, business login (scope names and the
  January 2025 deprecation):
  https://developers.facebook.com/docs/instagram-platform/instagram-api-with-instagram-login/business-login
- Meta, Instagram Platform overview (Standard vs Advanced Access):
  https://developers.facebook.com/docs/instagram-platform/overview
- Meta, IG user media reference (JPEG, 8 MB, 4:5–1.91:1 aspect range):
  https://developers.facebook.com/documentation/instagram-platform/instagram-graph-api/reference/ig-user/media
- Meta, refresh access token (60-day lifetime, 24-hour minimum age):
  https://developers.facebook.com/docs/instagram-platform/reference/refresh_access_token
- Meta, system users (never-expiring tokens on the Business Manager path):
  https://developers.facebook.com/docs/business-management-apis/system-users/install-apps-and-generate-tokens/
- Error codes 36003 and 2207052 (third-party references): https://zernio.com/instagram/errors,
  https://bundle.social/instagram-api/errors
- TikTok, photo post API: https://developers.tiktok.com/doc/content-posting-api-reference-photo-post
- TikTok, media transfer and URL verification:
  https://developers.tiktok.com/doc/content-posting-api-media-transfer-guide
- TikTok, Content Posting API get started (creator info query):
  https://developers.tiktok.com/doc/content-posting-api-get-started
- TikTok, uploading content as drafts:
  https://developers.tiktok.com/doc/content-posting-api-get-started-upload-content
- TikTok, content-sharing guidelines (audit criteria, Direct Post UX rules):
  https://developers.tiktok.com/doc/content-sharing-guidelines
- TikTok, token management (24-hour access, 365-day rotating refresh tokens):
  https://developers.tiktok.com/doc/oauth-user-access-token-management
- TikTok, sandbox: https://developers.tiktok.com/blog/introducing-sandbox
- Buffer API: https://support.buffer.com/article/859-does-buffer-have-an-api and
  https://developers.buffer.com/guides/hosting-media.html
- Buffer, Instagram support: https://support.buffer.com/article/554-using-instagram-with-buffer
- Upload-Post, TikTok: https://www.upload-post.com/platforms/tiktok/
- Ayrshare, TikTok API: https://www.ayrshare.com/docs/apis/post/social-networks/tiktok
- Ayrshare pricing: https://www.ayrshare.com/pricing/
- Postiz public API: https://docs.postiz.com/public-api
- Postiz, TikTok provider: https://docs.postiz.com/providers/tiktok
- Postiz pricing: https://postiz.com/pricing
- Publer docs: https://publer.com/docs
- Metricool pricing: https://www.metricool.com/pricing/
- Zapier, Instagram for Business: https://help.zapier.com/hc/en-us/articles/8496101110541
- Hootsuite developer FAQ: https://developer.hootsuite.com/docs/faq

**Not verified:**
- whether the Instagram API now accepts 3:4 (the docs say no);
- whether Instagram supports `scheduled_publish_time`;
- whether TikTok drafts from an unaudited app can be posted publicly;
- Buffer's exact field-level support for TikTok photo posts.
