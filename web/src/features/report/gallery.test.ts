import { describe, expect, it } from "vitest";

import { makeAd } from "./fixtures";
import {
  advertiserKey,
  copyPreview,
  excludeCalls,
  filterGroups,
  groupCreatives,
  groupReasons,
  imageSources,
  includeCalls,
  landingHref,
  toggleSubject,
} from "./gallery";

describe("gallery helpers", () => {
  it("uses the backend's advertiser key, never its own rule", () => {
    expect(
      advertiserKey(makeAd({ page_id: "", page_name: " Nameless ", advertiser_key: "Nameless" })),
    ).toBe("Nameless");
    expect(advertiserKey(makeAd({ page_id: "1", advertiser_key: "other" }))).toBe("other");
    expect(advertiserKey(makeAd({ page_id: "", page_name: "", advertiser_key: null }))).toBe("");
  });

  it("previews copy as one line cut at a word, at most 140 characters", () => {
    expect(copyPreview("Say goodbye to back pain!\n\nOur brace:\n• Fixes posture")).toEqual({
      short: "Say goodbye to back pain! Our brace: • Fixes posture",
      truncated: false,
    });
    const long = `${"posture ".repeat(30)}end`;
    const { short, truncated } = copyPreview(long);
    expect(truncated).toBe(true);
    expect(short.length).toBeLessThanOrEqual(141);
    expect(short).toMatch(/posture…$/);
  });

  it("counts collated ads behind a creative", () => {
    const [group] = groupCreatives([
      makeAd({ ad_archive_id: "1", creative_key: "k", collation_count: 3 }),
      makeAd({ ad_archive_id: "2", creative_key: "k", collation_count: 1 }),
    ]);
    expect(group?.variants).toBe(1);
    expect(group?.adCount).toBe(4);
  });

  it("reads video tiles from the poster, never the video file", () => {
    expect(
      imageSources({
        type: "video",
        url: "https://cdn.test/v.mp4",
        preview_url: "https://cdn.test/p.jpg",
        local_path: "1/9_1.mp4",
        poster_path: "1/9_0.jpg",
      }),
    ).toEqual(["/media/1/9_0.jpg", "https://cdn.test/p.jpg"]);
    expect(
      imageSources({ type: "video", url: "https://cdn.test/v.mp4", local_path: "1/9_1.mp4" }),
    ).toEqual([]);
  });

  it("includes an advertiser's ads with one clear call", () => {
    const ads = [
      makeAd({ ad_archive_id: "1", relevant: false, reason: "advertiser_override" }),
      makeAd({ ad_archive_id: "2", relevant: false, reason: "advertiser_override" }),
      makeAd({ ad_archive_id: "3", relevant: false, reason: "template" }),
    ];
    expect(includeCalls(ads)).toEqual([
      { kind: "clear", scope: "advertiser", target_id: "514393445092458" },
      { kind: "set", scope: "ad", target_id: "3", mode: "include" },
    ]);
  });

  it("names a toggle's target by advertiser", () => {
    const one = [makeAd({ ad_archive_id: "1" }), makeAd({ ad_archive_id: "2" })];
    expect(toggleSubject(one, excludeCalls(one))).toBe("2 ads from Northbrace");
    const mixed = [
      ...one,
      makeAd({ ad_archive_id: "3", page_id: "9", advertiser_key: "9", page_name: "Upright" }),
    ];
    expect(toggleSubject(mixed, excludeCalls(mixed))).toBe("3 ads from 2 advertisers");
    const withAdvertiser = [
      makeAd({ ad_archive_id: "1", reason: "advertiser_override" }),
      makeAd({ ad_archive_id: "2", reason: "advertiser_override" }),
    ];
    expect(toggleSubject(withAdvertiser, includeCalls(withAdvertiser))).toBe(
      "all ads from Northbrace",
    );
  });

  it("leads a filtered tile with an ad that matches the filter", () => {
    const video = {
      type: "video" as const,
      url: "https://cdn.test/v.mp4",
      poster_path: "1/1_0.jpg",
    };
    const groups = groupCreatives([
      makeAd({ ad_archive_id: "1", creative_key: "k", days_running: 300, media: [video] }),
      makeAd({
        ad_archive_id: "2",
        creative_key: "k",
        days_running: 100,
        page_id: "998",
        advertiser_key: "998",
        page_name: "Upright Co",
      }),
    ]);
    expect(groups[0]?.lead.page_name).toBe("Northbrace");

    const [byAdvertiser] = filterGroups(groups, "998", "all");
    expect(byAdvertiser?.lead.page_name).toBe("Upright Co");
    expect(byAdvertiser?.daysRunning).toBe(100);
    expect(byAdvertiser?.ads).toHaveLength(2);

    const [images] = filterGroups(groups, null, "image");
    expect(images?.lead.media[0]?.type).toBe("image");
    expect(filterGroups(groups, "514393445092458", "image")).toEqual([]);
  });

  it("lists each reason in a group once, the lead's first", () => {
    const [group] = groupCreatives([
      makeAd({ ad_archive_id: "1", creative_key: "k", reason: "template", days_running: 9 }),
      makeAd({ ad_archive_id: "2", creative_key: "k", reason: "ad_override", days_running: 90 }),
      makeAd({ ad_archive_id: "3", creative_key: "k", reason: "template", days_running: 5 }),
    ]);
    expect(groupReasons(group!)).toEqual(["ad_override", "template"]);
  });

  it("links only plain hostnames", () => {
    expect(landingHref("shop.example.com")).toBe("https://shop.example.com/");
    expect(landingHref("javascript:alert(1)")).toBeNull();
    expect(landingHref("evil.com/@x")).toBeNull();
    expect(landingHref(null)).toBeNull();
  });
});
