# Meta Ad Library web fixtures

Recorded live on 2026-10-17 (AEDT) with `scripts/record_fixtures.py`, a fresh **logged-out**
Playwright Chromium profile, headful, 3 scrolls per search with 3-6 s random waits.
No login wall, CAPTCHA or checkpoint was hit, so `blocks/` is empty.

```
uv run python scripts/record_fixtures.py "posture corrector" --country US --scrolls 3
uv run python scripts/record_fixtures.py "heated eye mask"   --country US --scrolls 3
uv run python scripts/record_fixtures.py "zzqxwv nothing"    --country US --scrolls 3
uv run python scripts/record_fixtures.py --login   # sign in once if Meta starts asking
```

## Folders

| folder | page.html (page 1, embedded) | graphql_*.json (scroll pages) | unique ads | innerText `Library ID:` |
|---|---|---|---|---|
| `posture-corrector-US/` | 30 ads, `count: 1699` | 4 files, 39 ads | 69 | 56 |
| `heated-eye-mask-US/` | 30 ads, `count: 442` | 6 files, 60 ads | 90 | 86 |
| `zzqxwv-nothing-US/` | 0 ads, `count: 0` | none | 0 | 0 ("No ads match your search criteria") |

No overlap between page-1 (HTML) ids and the GraphQL ids. Each folder has:

- `page.html` - `page.content()` taken **before** scrolling. `<style>` blocks and inline scripts
  over 2 KB that contain neither `ad_archive_id` nor `search_results_connection` were removed.
- `graphql_N.json` - body of each `/api/graphql/` response containing `ad_archive_id` or
  `search_results_connection`, in arrival order.
- `inner_text.txt` - `document.body.innerText` taken **after** the scrolls.
- `meta.json` - search URL, final URL, scroll counts, per-file sizes / ad counts, the sanitised
  GraphQL request (`fb_api_req_friendly_name`, `doc_id`, `variables`) and the facebook.com
  document/XHR URLs seen (query strings stripped).

Sizes: page.html 0.8-1.8 MB (mostly rendered DOM markup), graphql files 55-80 KB. Nothing > 2 MB.

## Where the ads come from

**Page 1 is embedded in the HTML, not XHR.** The first 30 ads arrive in a
`<script type="application/json" data-sjs>` block:

```
require[0]                       = ["ScheduledServerJS", "handle", null, [ {...} ]]
require[0][3][0].__bbox.require[0] = ["RelayPrefetchedStreamCache@<hash>", "next", [],
                                      ["adp_AdLibraryFoundationRootQueryRelayPreloader_<id>", {"__bbox": {...}}]]
  ...[3][1].__bbox = {complete, result, sequence_number}
  ...[3][1].__bbox.result.data.ad_library_main.search_results_connection
      = {count: <total results>, edges: [...], page_info: {end_cursor, has_next_page}}
```

**Later pages are XHR:** `POST https://www.facebook.com/api/graphql/`,
`fb_api_req_friendly_name=AdLibrarySearchPaginationQuery`, `doc_id=24922295957467452`,
`variables.first=10`, `variables.cursor=<previous end_cursor>`. The first XHR cursor equals the
embedded `page_info.end_cursor`. Each response body is **one plain JSON document** (no
`for (;;);` prefix, not newline-delimited):

```
data.ad_library_main.search_results_connection = {edges: [...], page_info: {end_cursor, has_next_page}}
```

(no `count` in XHR pages). One scroll can trigger more than one page (heated eye mask: 6 XHRs
for 3 scrolls).

Empty search: the embedded connection is `{"count":0,"edges":[],"page_info":{"end_cursor":"","has_next_page":false}}`
and no pagination XHR is made.

## Ad node shape

`edges[].node = {__typename, collated_results: [ad, ...]}` (usually 1 ad per edge, sometimes more).

Ad (`collated_results[]`) fields, as seen across 159 ads:

| field | type / notes |
|---|---|
| `ad_archive_id` | str |
| `collation_id` | str **or null** (16/159 null) |
| `collation_count` | int **or null** (24/159 null) - treat null as 1 |
| `page_id`, `page_name` | str (also repeated in `snapshot`) |
| `is_active` | bool |
| `start_date` | int unix **seconds**, always 07:00 UTC = midnight US Pacific |
| `end_date` | int unix seconds, **set even for active ads** (= last delivery day, e.g. yesterday) |
| `publisher_platform` | list of `FACEBOOK`, `INSTAGRAM`, `AUDIENCE_NETWORK`, `MESSENGER`, `WHATSAPP`, `THREADS` |
| others | `page_is_deleted, categories, gated_type, impressions_with_index, currency, spend(null), reach_estimate(null), targeted_or_reached_countries, hide_data_status, ad_id(null), ...` |

`snapshot` fields: `body{text}` (**body itself can be null**), `title`, `link_url`, `caption`
(display domain, e.g. `axzenia.com` - maps to `Ad.link_caption`), `link_description`, `cta_text`,
`cta_type`, `display_format` (`VIDEO`, `IMAGE`, `DCO`, `DPA`, `CAROUSEL`, `MULTI_IMAGES`),
`images[{original_image_url, resized_image_url, watermarked_resized_image_url, image_crops}]`,
`videos[{video_hd_url, video_sd_url, video_preview_image_url, watermarked_video_hd_url, watermarked_video_sd_url}]`,
`cards[{body, title, caption, link_url, link_description, cta_text, cta_type, original_image_url,
resized_image_url, video_hd_url, video_sd_url, video_preview_image_url, ...}]`, `page_name`,
`page_profile_uri`, `page_profile_picture_url`, `page_like_count`, `page_categories`, `byline`,
`branded_content`, `extra_links`, `extra_texts`, `extra_images`, `extra_videos`, ...

`DCO`/`DPA` ads have empty top-level `images`/`videos`; media lives in `cards[]`, and
`body.text`/`title` are templates such as `{{product.name}}` / `{{product.brand}}`.

## innerText shape (fallback parser)

```
Active
Library ID: 2275498076149743
Started running on 31 Oct 2024
Platforms
2 ads use this creative and text      (only when collated)
Open Drop-down
See summary details | See ad details
Axzenia                               <- advertiser
Sponsored
<body lines...>
```

- The date is **`D Mon YYYY`** (`31 Oct 2024`), not `Mon D, YYYY`, even with `locale=en-US`
  (likely chosen from the AU IP). Parsers should accept both.
- The status line (`Active`) comes **before** `Library ID:`, i.e. at the tail of the previous
  chunk when splitting on `Library ID:`.
- The page header always contains a `Log in` button, so "log in" alone is not a block signal.
- The innerText count is lower than the JSON count (cards not yet rendered when captured).

## Observations for the block detector

The first ever navigation (posture corrector, fresh profile) went through a
`https://www.facebook.com/__rd_verify_<token>` document redirect that resolved by itself with
no user action. Not a block, but worth tolerating.

## Sanitisation

Before writing, `sanitise()` replaces the values of `fb_dtsg, lsd, jazoest, __user, av,
actorID, USER_ID, ACCOUNT_ID, NAME, SHORT_NAME, viewerID, token, async_get_token, dtsg, __hsi,
__spin_r, __spin_t, __rev, hsi, client_revision, server_revision, sessionID` and the session-cookie keys (`c_` + `user`, `xs`, `datr`, `fr`, `sb`)
with `REDACTED` (plain JSON, escaped JSON and `key=value` forms). Request headers
and cookies are never written; non-library response URLs keep only scheme/host/path and the
`__rd_verify_` token is redacted. The profile was logged out, so there is no viewer name/id.
Advertiser data (public ads) is kept as-is.
