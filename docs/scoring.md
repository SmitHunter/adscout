# How the Winner Score works

The Winner Score is a number from 0 to 100 built from four factors. Every threshold and
weight lives in one file, [`src/adscout/scoring/thresholds.py`](../src/adscout/scoring/thresholds.py),
and the factor functions are in [`src/adscout/scoring/factors.py`](../src/adscout/scoring/factors.py).
This page explains each step in the order AdScout runs it.

1. [Relevance: which ads count](#relevance-which-ads-count)
2. [Creatives: grouping duplicate ads](#creatives-grouping-duplicate-ads)
3. [The four factors](#the-four-factors)
4. [The ramp: from value to points](#the-ramp-from-value-to-points)
5. [The total, and why it can be provisional](#the-total-and-why-it-can-be-provisional)
6. [The strongest advertiser and the market](#the-strongest-advertiser-and-the-market)
7. [Saturation](#saturation)

## Relevance: which ads count

A keyword search on the Ad Library returns plenty of ads that only mention the word in
passing. AdScout decides, for every ad, whether it is relevant, and records the reason. The
first rule that applies wins:

| Order | Rule | Result |
| --- | --- | --- |
| 1 | You included or excluded this ad | Your choice (`ad_override`) |
| 2 | You included or excluded this advertiser | Your choice (`advertiser_override`) |
| 3 | Claude marked the ad as not selling the product | Excluded (`claude_off_topic`) |
| 4 | Every word of the keyword appears in the ad | Relevant (`keyword`) |
| 5 | The copy is an unfilled template such as `{{product.name}}` | Excluded (`template`) |
| 6 | Anything else | Excluded (`no_keyword`) |

The keyword match ([`relevance.py`](../src/adscout/scoring/relevance.py)) looks in the ad's
body, title, link caption and link URL. It ignores case and accents, treats simple plurals
as the same word ("correctors" matches "corrector"), and skips small words such as "for",
"the" and "with". Every remaining word must appear.

For an advertiser search (one advertiser's page), rule 4 is replaced: every ad is relevant
unless an override or Claude says otherwise.

Overrides are stored per search (the keyword, or the advertiser's page id), so they apply to
every snapshot of that search, including future daily checks. Set them from the report's
gallery and advertiser table, or with `uv run adscout exclude`.

Claude's off-topic flags come from the angle analysis. They take effect at scoring time,
are listed in the report's Excluded tab, and always lose to your own overrides.

## Creatives: grouping duplicate ads

Advertisers run the same creative many times, under different ad ids, in different
countries and placements. Counting ads would reward copying, so AdScout counts distinct
creatives ([`creatives.py`](../src/adscout/scoring/creatives.py)):

1. **Meta's collation.** Ads Meta groups under "N ads use this creative" share a collation
   id. Those ads are one creative.
2. **Copy and media.** Ads without a collation id are grouped by a hash of their normalised
   body text and the path of their first media file. An ad with neither text nor media is
   its own creative.
3. **Perceptual hash.** When media is downloaded, AdScout computes a perceptual hash (pHash)
   of each image or video poster. Two creatives whose images differ by 6 bits or fewer out of
   64 are merged, which catches the same image re-uploaded or re-encoded.

A creative's start date is the earliest start date among its ads.

## The four factors

Each factor has a fail mark, a pass mark and, for three of them, a full-marks mark.

| Factor | Measures | Fail below | Pass from | Full marks at | Weight |
| --- | --- | --- | --- | --- | --- |
| Longevity | Age of the oldest active ad, in days | 30 | 90 | 180 | 30 |
| | Share of creatives running 90 days or more | 5% | 25% | 50% | |
| Continuity | Months of the last 6 with a new creative | 3 | 5 | (pass) | 20 |
| Creative volume | Distinct creatives | 3 | 10 | 20 | 25 |
| Angle diversity | Angles backed by the ads | 2 | 3 | 5 | 25 |

**Longevity.** Advertisers stop paying for ads that don't sell, so an ad still running after
months is evidence. The factor's points are half the oldest active ad's age and half the
share of creatives that started 90 days or more ago, each through the ramp below. The second
half stops one old ad from carrying a product on its own. The pass/warn/fail status shows
the oldest ad's age.

**Continuity.** A winner gets new creatives month after month; a fad launches everything
at once and stops. AdScout counts the calendar months, in your `TIMEZONE`, out of the
current month and the five before it, in which at least one creative started. This is also
the score's evergreen check. Claude's seasonality label (evergreen, seasonal or unclear) is
shown beside the score but doesn't change it.

**Creative volume.** The number of distinct creatives among the relevant ads.

**Angle diversity.** The number of selling angles from Claude's analysis that the ads
actually support. For the market, an angle counts when at least 2 relevant creatives use it,
so a single loosely fitting ad can't add an angle; the report marks the rest "Not scored".
For the strongest advertiser, an angle counts when at least one of its creatives uses it.
Angles are re-checked against the relevant ads every time the score is built, so excluding
ads after an analysis updates the count without another Claude call.

## The ramp: from value to points

Each value becomes a fraction of the factor's points:

| Value | Fraction of points |
| --- | --- |
| Below the fail mark | 0 |
| Fail mark up to the pass mark | 30%, rising in a straight line to 70% |
| Pass mark up to full marks | 70%, rising in a straight line to 100% |
| Full marks or more | 100% |

When a factor has no full-marks mark (Continuity), the line runs from 30% at the fail mark
to 100% at the pass mark. Continuity therefore earns 0 for 2 months, 30% for 3, 65% for 4
and 100% for 5 or 6.

The status chip follows the marks: **fail** below the fail mark, **pass** at or above the
pass mark, **warn** in between.

A worked example: 18 distinct creatives sits between the pass mark (10) and full marks
(20), so Creative volume earns 70% + 30% × 8/10 = 94% of its 25 points, or 23.5.

## The total, and why it can be provisional

The total is the points earned divided by the points available, scaled to 100 and rounded:

```
total = round(100 × Σ(weight × fraction) / Σ(weight of factors that have a value))
```

Angle diversity needs Claude. Until a snapshot has been analysed, that factor is pending,
the total is built from the other three factors (75 points, rescaled to 100), and the score
is marked **provisional**. Once Claude has read the ads, all 100 points count and the label
goes away.

Analyses are cached per snapshot (keyed by the prompt version, the model and the exact
input). A daily check of a watched search reuses the latest analysis of the same search
when at least 90% of its relevant creatives were in that analysis; the reused angles are
mapped onto the new snapshot's creatives. Reuse always measures against the analysis
Claude actually produced, never against an earlier reuse, so small daily changes can't
carry stale angles forward indefinitely.

## The strongest advertiser and the market

AdScout computes the score twice:

- **Winner Score (the headline).** The advertiser with the most distinct relevant creatives
  (ties go to the most ads), scored on its own ads with the same four factors. One
  advertiser scaling a product hard is the strongest signal there is, and pooling it with a
  dozen one-ad copycats would hide it.
- **Market demand.** All relevant ads from every advertiser, pooled into one score.

The report shows both, with tabs to switch the factor rulers between the leading advertiser
and the market. When no ad has an identifiable advertiser, the headline falls back to the
market score.

## Saturation

The number of distinct advertisers with relevant ads is shown as a label, not scored:

| Advertisers | Label | Meaning |
| --- | --- | --- |
| 0–2 | Early | Demand not yet proven |
| 3–15 | Healthy | Proven, with room |
| 16 or more | Crowded | Proven demand, hard to stand out |

Early and crowded markets add a warning under the score.
