"""Build the demo data folder: four synthetic product searches, a watchlist and alerts.

Everything goes through the app's own repo functions (snapshots, ads, overrides, analyses,
watch items, alerts) and its own scoring, so every screen renders exactly as with real data.
Nothing here touches Meta or Claude: ads, copy and images are invented, brands are made up
and every link points at a ``.example`` domain.

``generate_demo`` is deterministic for a seed and ``now``, and idempotent: a folder already
built for the same seed (and not older than a week) is left as it is.
"""

import hashlib
import json
import math
import random
import re
import shutil
import sqlite3
from contextlib import closing
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Literal
from zoneinfo import ZoneInfo

from sqlalchemy import Engine
from sqlmodel import Session, select

from adscout.analysis.models import Angle, AngleAnalysis, Emotion, Hook, Offer, OfferKind, clean
from adscout.analysis.prompt import PROMPT_VERSION, input_hash
from adscout.db.repo import (
    add_alert,
    create_snapshot,
    create_watch_item,
    finish_snapshot,
    make_engine,
    save_analysis,
    set_override,
    snapshots_for_watch,
    update_watch_item,
)
from adscout.db.tables import Alert, Exclusion, WatchItem
from adscout.demo import DEMO_MARKER
from adscout.demo.images import Art, Layout, Palette, jpeg_bytes, phash, render
from adscout.demo.images import Product as ArtProduct
from adscout.locks import SERVER_LOCK, SERVER_PORT
from adscout.models import Ad, MediaItem, SearchQuery
from adscout.services.analysis import analysis_input
from adscout.services.media import BLOB_DIR
from adscout.services.report import build_report
from adscout.sources import SearchResult
from adscout.watch.rules import TREND_STATUSES, alert_for, summarize

GENERATOR_VERSION = 2
STALE_AFTER = timedelta(days=7)
DEMO_MODEL = "Claude Sonnet 5.5 (sample)"
DEMO_BACKEND = "demo"
SOURCE = "meta_web"
COUNTRY = "US"
WATCH_DAYS = 21
CAPPED_DAY = 9  # days before now: the scaling item's run that stopped at the ad limit
BURST_DAY = 3  # the scaling item's launch day (its one "up" alert)
DROP_DAY = 6  # the fading item's pause day (its one "down" alert)
ALERT_MARGIN = 0.36  # the burst and the drop move the count by at least this share

# Real brands seen in this project's data: demo names must never contain these.
DENYLIST = ("corecare", "corecorex", "axzenia", "alpha's vow", "therablink", "yoli", "soovera")

Role = Literal["seller", "off_topic", "noise", "template", "excluded"]


# --- Content specs --------------------------------------------------------------------------


@dataclass(frozen=True)
class AngleSpec:
    name: str
    description: str
    emotion: Emotion
    bodies: tuple[str, ...]  # the copy that carries the angle
    headlines: tuple[str, ...]  # short image overlays


@dataclass(frozen=True)
class OfferSpec:
    kind: OfferKind
    summary: str  # as the analysis words it
    line: str  # as the ad says it
    badge: str | None = None


@dataclass(frozen=True)
class Brand:
    name: str
    palette: Palette
    role: Role = "seller"
    creatives: int = 3
    ages: tuple[int, int] = (5, 120)  # creative start ages, days before the snapshot
    ads: tuple[int, int] = (1, 3)  # ads per creative
    video_share: float = 0.25
    # The angles (indexes into the product's) this brand's copy uses; empty means any.
    angle_pool: tuple[int, ...] = ()
    # Explicit copy for non-sellers: (body, title, url path).
    copy: tuple[tuple[str, str, str], ...] = ()

    @property
    def slug(self) -> str:
        return re.sub(r"[^a-z0-9]+", "", self.name.lower().replace("&", "and"))

    @property
    def domain(self) -> str:
        return f"{self.slug}.example"


@dataclass(frozen=True)
class ProductSpec:
    keyword: str
    art: ArtProduct
    item: str  # the product's display name, e.g. "Posture Brace"
    path: str  # the landing page path segment
    angles: tuple[AngleSpec, ...]
    hooks: tuple[str, ...]
    offers: tuple[OfferSpec, ...]
    titles: tuple[str, ...]  # "{brand}" and "{item}" are filled in
    quotes: tuple[tuple[str, str], ...]  # (quote, reviewer) for review creatives
    brands: tuple[Brand, ...]
    seasonality: Literal["evergreen", "seasonal", "unclear"]
    seasonality_note: str
    verdict: str
    watch: Literal["scaling", "fading"] | None = None
    hours_ago: float = 2.0  # the latest snapshot's start, before ``now``
    layouts: tuple[Layout, ...] = ("studio", "lifestyle", "review", "offer", "ugc")


CTAS = (
    "👉 Tap Shop Now to get yours.",
    "Tap below to claim yours.",
    "Shop now while stock lasts.",
    "👇 Get yours before they're gone.",
    "Order today, ships in 24 hours.",
)


def _p(top: str, bottom: str, accent: str, product: str) -> Palette:
    def rgb(h: str) -> tuple[int, int, int]:
        return int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)

    return Palette(rgb(top), rgb(bottom), rgb(accent), rgb(product))


POSTURE_PALETTES = (
    _p("e9eef4", "c3d1e2", "e8603f", "2e3e5c"),
    _p("f3ede4", "dccbb5", "2f6d5f", "3b3a3f"),
    _p("e6efe9", "b9d3c4", "f2a33a", "26443a"),
    _p("f5f0f6", "d6c9de", "6c4ab6", "40364f"),
    _p("eef2f2", "c7d6d8", "ff7a59", "1f4e5a"),
    _p("fbf3e9", "f0d2b0", "c4462f", "5a4a3c"),
    _p("e9edf7", "aebbe0", "ffb627", "28306b"),
    _p("f0f0ee", "cfd0cb", "1e88e5", "2b2b2b"),
)
EYE_PALETTES = (
    _p("f8e2d6", "d6aaaa", "ff8c3c", "785c8c"),
    _p("ece6f6", "b9aedb", "ff9a62", "4f4787"),
    _p("f6efe6", "e2c8ad", "e5673b", "8c6f5e"),
    _p("e8eef6", "aebfd9", "ffb35c", "3d5a80"),
    _p("f5e9ef", "dab4c8", "f07c52", "9c5a7d"),
    _p("eef1ec", "c4cfbf", "e88b3a", "5e6b58"),
)
LED_PALETTES = (
    _p("1e1828", "462846", "ff5a6e", "dcdce6"),
    _p("151b26", "283a52", "5ab4ff", "e2e6ee"),
    _p("2a1a1f", "5a2a36", "ff7a8a", "f1e4e6"),
    _p("1b1b1f", "3a3340", "ffb36b", "e8e2dc"),
    _p("221a2e", "41305e", "c38bff", "e6e0f2"),
    _p("2b2224", "6b3b3f", "ff6b5a", "f4e8e2"),
    _p("f2e7e9", "d7b5bd", "e0405a", "fafafa"),
    _p("eceaf2", "c3bfd6", "7a5af5", "ffffff"),
)
PHONE_PALETTES = (
    _p("d6e8e2", "78aaa0", "ffb428", "3c4048"),
    _p("e4e9f0", "8fa3bf", "ff6a3d", "2c3038"),
    _p("efe9df", "c9b79a", "2a9d8f", "34363c"),
    _p("e7ecef", "a9b8c2", "e63946", "1d2228"),
    _p("f1ece6", "d8c3a5", "3a86ff", "2f3136"),
    _p("e0ece4", "9cc5a8", "f4a261", "264653"),
)


def _brands(
    names: list[str],
    palettes: tuple[Palette, ...],
    specs: list[tuple[int, tuple[int, int], tuple[int, int]]],
    pools: dict[str, tuple[int, ...]] | None = None,
) -> tuple[Brand, ...]:
    return tuple(
        Brand(
            name,
            palettes[i % len(palettes)],
            creatives=n,
            ages=ages,
            ads=ads,
            angle_pool=(pools or {}).get(name, (pools or {}).get("*", ())),
        )
        for i, (name, (n, ages, ads)) in enumerate(zip(names, specs, strict=True))
    )


POSTURE = ProductSpec(
    keyword="posture brace",
    art="posture",
    item="Posture Brace",
    path="posture-brace",
    watch="scaling",
    hours_ago=2.0,
    layouts=("studio", "lifestyle", "split", "review", "offer", "ugc"),
    angles=(
        AngleSpec(
            "Desk-job back pain relief",
            "Relief for upper-back and neck ache from long days at a desk or laptop, "
            "framed around the 3 pm slump.",
            "pain_relief",
            (
                "Eight hours hunched over a laptop adds up. The {item} eases your "
                "shoulders back so your upper back stops doing all the work.",
                "That knot between your shoulder blades? It's your posture, not your chair. "
                "Wear it 20 minutes a day and feel the difference by Friday.",
                "Built for desk workers: soft padded straps, adjustable tension, and support "
                "right where the ache starts.",
            ),
            ("No more 3 pm back ache", "Built for desk days", "Your upper back, unlocked"),
        ),
        AngleSpec(
            "Stand taller, look confident",
            "Better posture sold as presence and confidence, usually with a day 1 vs "
            "day 14 before-and-after.",
            "confidence",
            (
                "Good posture is the cheapest confidence upgrade there is. Shoulders back, "
                "chin up, and suddenly you walk into every room differently.",
                "Most of us lose height to a desk-chair slump. The {item} trains "
                "your shoulders back until it feels natural.",
            ),
            ("Stand taller in 14 days", "Day 1 vs day 14", "Walk in like you own it"),
        ),
        AngleSpec(
            "Invisible under clothes",
            "A thin, breathable brace that hides beneath a shirt at work without anyone noticing.",
            "convenience",
            (
                "Ultra-thin, breathable mesh that disappears under a T-shirt or a blazer. "
                "Nobody at work will know. They'll just notice you look sharper.",
                "No bulky straps, no velcro squeak. Wear it beneath your shirt in 10 "
                "seconds and forget it's there.",
            ),
            ("Invisible under any shirt", "Nobody will know", "10 seconds to put on"),
        ),
        AngleSpec(
            "Physio-designed support",
            "Physio and chiropractor framing that positions the brace as a health tool, "
            "not a gadget.",
            "health",
            (
                "Designed with physiotherapists to support natural spinal alignment. "
                "Gentle tension, no pinching, safe for daily wear.",
                "Your physio would tell you the same thing: retrain the muscles, don't just "
                "prop them up. That's exactly how the {item} works.",
            ),
            ("Designed with physios", "Alignment, not force"),
        ),
        AngleSpec(
            "Thousands of 5-star reviews",
            "Review counts, star ratings and customer quotes used as proof that it works.",
            "social_proof",
            (
                "Rated 4.8 stars by over 31,000 customers. Here's what they keep telling us: "
                '"I didn\'t realise how much I slouched until I stopped."',
                'Our customers\' favourite thing? "I forgot I was wearing it." Join '
                "thousands who fixed their posture this year.",
            ),
            ("31,000+ five-star reviews", "Loved by desk workers"),
        ),
        AngleSpec(
            "Cheaper than a physio visit",
            "Frames the brace as a one-off cost against repeat appointments or a new office chair.",
            "value",
            (
                "One physio session costs more than this brace. It works every single day, "
                "for less than the price of a lunch out.",
            ),
            ("Less than one physio visit",),
        ),
    ),
    hooks=(
        "If you work at a desk, read this 👇",
        "My physio told me to stop slouching. This is what actually fixed it.",
        "POV: you finally stopped hunching over your laptop",
        "Nobody at work knows I'm wearing this.",
        "Shoulders back in 3 seconds. Seriously.",
        "Over 31,000 people stood up straighter this year.",
        "Your neck pain isn't from your pillow.",
        "Day 1 vs day 14 👀",
    ),
    offers=(
        OfferSpec("discount", "50% off, today only", "🔥 50% off today only.", "50% OFF"),
        OfferSpec(
            "bundle",
            "Buy 2, get 1 free: one for home, one for the office",
            "Buy 2, get 1 free: one for home, one for the office.",
            "3 FOR 2",
        ),
        OfferSpec("free_shipping", "Free US shipping on every order", "Free US shipping, always."),
        OfferSpec(
            "guarantee",
            "60-day stand-taller money-back guarantee",
            "Try it for 60 days. Stand taller or your money back.",
        ),
        OfferSpec(
            "urgency",
            "Restocked: the last batch sold out in 48 hours",
            "Back in stock. The last batch sold out in 48 hours.",
        ),
        OfferSpec(
            "free_gift",
            "Free posture e-guide with every brace",
            "Free posture e-guide with every order.",
        ),
    ),
    titles=(
        "{brand} {item} | Stand Taller Today",
        "The {item} Desk Workers Swear By",
        "{item}: 50% Off Ends Tonight",
        "Better Posture by Week Two",
        "{brand}™ {item}",
    ),
    quotes=(
        (
            "I didn't realise how much I slouched until I stopped. My neck thanks me.",
            "Dana R., verified buyer",
        ),
        (
            "I wear it under my work shirt every day. Nobody has noticed, but they've noticed me.",
            "Marcus T., verified buyer",
        ),
        ("Three weeks in and the ache between my shoulders is gone.", "Priya S., verified buyer"),
    ),
    brands=(
        *_brands(
            [
                "Alignly",
                "Postura Lab",
                "Stancewell",
                "Spineway Co",
                "Truvela",
                "Ostra Wellness",
                "Levelbody",
                "Northpoint Posture",
                "Halden & Co",
                "Mivora",
            ],
            POSTURE_PALETTES,
            [
                (11, (5, 215), (1, 3)),
                (7, (12, 170), (1, 3)),
                (5, (6, 130), (1, 3)),
                (4, (25, 110), (1, 3)),
                (4, (1, 19), (2, 3)),
                (3, (35, 150), (1, 2)),
                (3, (2, 16), (2, 3)),
                (3, (18, 95), (1, 2)),
                (2, (40, 80), (1, 2)),
                (2, (5, 14), (2, 3)),
            ],
            {"Alignly": (0, 1, 2)},
        ),
        Brand(
            "Seatcraft Ergonomics",
            POSTURE_PALETTES[7],
            role="off_topic",
            ads=(1, 2),
            copy=(
                (
                    "Tired of posture braces that dig into your shoulders? Fix the chair, not "
                    "your back.\n\nThe Aero task chair supports your spine all day, so you never "
                    "need a posture brace again. 30-night home trial.",
                    "Ditch the Posture Brace. Get a Better Chair.",
                    "aero-task-chair",
                ),
                (
                    "A posture brace is a band-aid. Our lumbar-adaptive chair is the fix.\n\n"
                    "Free delivery and assembly this month.",
                    "Why a Posture Brace Won't Fix Your Back",
                    "lumbar-chair",
                ),
            ),
            ages=(20, 70),
        ),
        Brand(
            "Brightline Chiro Studio",
            POSTURE_PALETTES[4],
            role="excluded",
            ads=(1, 2),
            copy=(
                (
                    "Before you buy another posture brace, book a free spinal check.\n\n"
                    "Our chiropractors build a plan for your back, not a one-size strap. "
                    "New patients: first visit free.",
                    "Free Spinal Check Before Your Next Posture Brace",
                    "new-patients",
                ),
            ),
            ages=(30, 60),
        ),
        Brand(
            "ShopNova Deals",
            POSTURE_PALETTES[6],
            role="template",
            ads=(1, 2),
            copy=(
                ("{{product.brand}}", "{{product.name}}", "catalog"),
                ("{{product.description}}", "{{product.name}}", "catalog-2"),
            ),
            ages=(10, 40),
        ),
        Brand(
            "Pilates Collective",
            POSTURE_PALETTES[2],
            role="noise",
            ads=(1, 2),
            copy=(
                (
                    "Fix your posture with our 28-day at-home pilates challenge. 15 minutes a "
                    "day, no equipment, real results.",
                    "28-Day Posture Pilates Challenge",
                    "28-day-challenge",
                ),
            ),
            ages=(15, 50),
        ),
    ),
    seasonality="evergreen",
    seasonality_note="No seasonal cues in the copy, and the leading advertiser has launched new "
    "creatives every month for over seven months.",
    verdict=(
        "Six angles are in play across the market, though the leading advertiser runs only "
        "three of them, led by desk-job pain relief and the stand-taller confidence story. "
        "The longest-running creatives are spread across "
        "several angles rather than stacked on one hook, which points to a product that "
        "sells on more than a single message. The main risk is wording: the physio framing "
        "invites a closer look at health claims."
    ),
)


LED = ProductSpec(
    keyword="led face mask",
    art="led",
    item="LED Face Mask",
    path="led-face-mask",
    hours_ago=5.0,
    layouts=("studio", "lifestyle", "split", "review", "offer", "ugc"),
    angles=(
        AngleSpec(
            "Spa results at home",
            "Professional LED facials for a fraction of the clinic price, done on the sofa.",
            "value",
            (
                "Clinic LED facials cost $150 a session. This is the same red and near-infrared "
                "light, for less than one appointment, any night you like.",
                "Skip the waiting list at the skin clinic. Same wavelengths, your living room, "
                "ten minutes.",
            ),
            ("The facial, at home", "Clinic results, sofa price"),
        ),
        AngleSpec(
            "Smoother skin, fewer fine lines",
            "Collagen and fine-line claims with 4 to 8 week timelines and close-up "
            "before-and-afters.",
            "appearance",
            (
                "Red light at 630 nm supports your skin's own collagen. Most people see "
                "smoother, plumper skin in 4 to 8 weeks.",
                "Fine lines around the eyes were my thing. Week 8, no filter, and I've stopped "
                "reaching for concealer.",
            ),
            ("Week 1 vs week 8", "Smoother in 8 weeks", "No filter. Just light."),
        ),
        AngleSpec(
            "Calm, clear skin",
            "Blue light for breakouts and redness, aimed at younger buyers with acne-prone skin.",
            "confidence",
            (
                "Blue light mode targets breakout-causing bacteria without drying your skin "
                "out. Calmer, clearer skin, no harsh actives.",
            ),
            ("Blue light for breakouts", "Calm skin, no actives"),
        ),
        AngleSpec(
            "Ten-minute nightly ritual",
            "A wind-down self-care routine: put it on, press play, relax.",
            "convenience",
            (
                "Put it on, press play on your show, done. Ten minutes, cordless, and it "
                "switches itself off.",
                "The easiest step in my skincare routine. I wear it while I answer emails.",
            ),
            ("10 minutes. Then glow.", "Your new night ritual"),
        ),
        AngleSpec(
            "Everyone's talking about it",
            "Waitlists, sell-outs and creator reviews used as proof the mask is worth it.",
            "social_proof",
            (
                "Sold out three times this year. Over 12,000 five-star reviews and counting.",
                "Why is everyone wearing a glowing red mask? Because it works, and 12,000 "
                "reviews agree.",
            ),
            ("12,000 five-star reviews", "Sold out 3 times"),
        ),
        AngleSpec(
            "The gift she'll actually use",
            "Gift framing for partners and mothers, sometimes tied to holidays.",
            "gift",
            ("The gift she'd never buy herself. Arrives in a keepsake box, ready to give.",),
            ("The gift she'll love",),
        ),
    ),
    hooks=(
        "I stopped paying $150 a facial. Here's why.",
        "Week 1 vs week 8 (no filter)",
        "The clinic treatment, now at home.",
        "This is your sign to try red light therapy.",
        "10 minutes. One episode. Glowing skin.",
        "Why is everyone wearing a glowing red mask?",
        "My skin at 42 👇",
        "She thought it was a Halloween mask. Then she saw my skin.",
    ),
    offers=(
        OfferSpec(
            "discount",
            "40% off plus a free eye serum",
            "40% off + a free eye serum today.",
            "40% OFF",
        ),
        OfferSpec(
            "guarantee",
            "90-day visible-results guarantee",
            "See results in 90 days or get a full refund.",
        ),
        OfferSpec(
            "bundle",
            "Mask and neck attachment bundle, save $60",
            "Bundle the neck attachment and save $60.",
        ),
        OfferSpec(
            "urgency", "Flash sale ends at midnight", "Flash sale ends at midnight.", "FLASH SALE"
        ),
        OfferSpec("free_shipping", "Free express shipping", "Free express shipping."),
        OfferSpec(
            "other", "Pay in 4 interest-free instalments", "Pay in 4 interest-free instalments."
        ),
    ),
    titles=(
        "{brand} {item} | Red & Blue Light",
        "The {item} That Sold Out 3 Times",
        "{item}: Clinic Results at Home",
        "{brand} Light Therapy {item}",
    ),
    quotes=(
        ("Week 8 and my fine lines are visibly softer. I'm obsessed.", "Rachel M., verified buyer"),
        (
            "I wear it while I watch one episode. My skin has never looked calmer.",
            "Ana L., verified buyer",
        ),
    ),
    brands=(
        Brand(
            "Lumelle",
            LED_PALETTES[0],
            creatives=7,
            ages=(6, 100),
            ads=(2, 4),
            angle_pool=(0, 1, 4),
        ),
        *_brands(
            [
                "Quillumen",
                "Photonne",
                "Skinlume Studio",
                "Veloura Beauty",
                "Glimmerlab",
                "Auvelle",
                "Nightbloom Skin",
                "Redwave Ritual",
                "Opaline Co",
                "Brille Lab",
                "Solenne Skin",
                "Mirabelle Glow",
                "Kaida Beauty",
                "Lunaro",
                "Vivenne",
                "Prismora",
                "Elodie Skin Co",
                "Ambera",
                "Cielo Glow",
                "Novelle Labs",
                "Rosalume",
                "Pureflare",
                "Tidewell Beauty",
                "Selvane",
                "Irisette",
            ],
            LED_PALETTES[1:] + LED_PALETTES[:1],
            [
                (5, (4, 120), (1, 3)),
                (4, (10, 95), (1, 3)),
                (4, (2, 70), (1, 3)),
                (3, (8, 110), (1, 3)),
                (3, (1, 45), (1, 3)),
                (3, (20, 90), (1, 2)),
                (3, (3, 60), (1, 3)),
                (3, (12, 80), (1, 2)),
                (2, (5, 40), (1, 3)),
                (2, (30, 100), (1, 2)),
                (2, (1, 25), (2, 3)),
                (2, (14, 65), (1, 2)),
                (2, (6, 50), (1, 3)),
                (2, (2, 30), (1, 2)),
                (2, (40, 120), (1, 2)),
                (2, (9, 35), (1, 2)),
                (2, (1, 20), (1, 3)),
                (2, (18, 75), (1, 2)),
                (1, (3, 30), (2, 3)),
                (1, (25, 60), (1, 2)),
                (1, (1, 10), (1, 3)),
                (1, (7, 45), (1, 2)),
                (1, (2, 15), (1, 2)),
                (1, (10, 55), (1, 2)),
                (1, (1, 8), (1, 2)),
            ],
        ),
        Brand(
            "Halo & Hue",
            LED_PALETTES[6],
            role="off_topic",
            ads=(1, 2),
            copy=(
                (
                    "Using an LED face mask? Pair it with our peptide serum for twice the "
                    "glow.\n\nOur Copper Peptide Serum is made to layer under any LED face "
                    "mask. 20% off your first bottle.",
                    "The Serum Your LED Face Mask Is Missing",
                    "copper-peptide-serum",
                ),
                (
                    "Your LED face mask works harder with the right prep. Meet the serum "
                    "made for light therapy.",
                    "Prep Serum for LED Face Mask Users",
                    "light-prep-serum",
                ),
            ),
            ages=(10, 60),
        ),
        Brand(
            "Daylumen Clinic",
            LED_PALETTES[3],
            role="off_topic",
            ads=(1, 1),
            copy=(
                (
                    "Home LED face masks can't match a clinical dose. Book an in-clinic light "
                    "therapy course: 6 sessions, 25% off this month.",
                    "Better Than an LED Face Mask: Clinical Light Therapy",
                    "light-therapy-course",
                ),
            ),
            ages=(20, 40),
        ),
        Brand(
            "Beauty Finds Daily",
            LED_PALETTES[7],
            role="template",
            ads=(1, 2),
            copy=(
                ("{{product.brand}}", "{{product.name}}", "catalog"),
                ("{{product.description}}", "{{product.name}}", "catalog-2"),
            ),
            ages=(5, 30),
        ),
        Brand(
            "Glowmetric",
            LED_PALETTES[4],
            role="noise",
            ads=(1, 2),
            copy=(
                (
                    "The LED makeup mirror with daylight, warm and cool light settings. See "
                    "your face the way everyone else does.",
                    "Glowmetric LED Vanity Mirror",
                    "led-vanity-mirror",
                ),
            ),
            ages=(20, 90),
        ),
    ),
    seasonality="evergreen",
    seasonality_note="Gift framing appears in a few creatives, but most ads run year-round on "
    "skin-results and value angles.",
    verdict=(
        "Six angles are in play across the market and the leading advertiser runs three, "
        "with spa-results-at-home value and fine-line before-and-afters carrying most of "
        "the long-running creatives. More than twenty advertisers "
        "run near-identical masks and copy, so the angles are proven but no one owns them. "
        "The main risk is competition: winning here means a sharper offer or a new angle, "
        "not a new product."
    ),
)


EYE = ProductSpec(
    keyword="heated eye mask",
    art="eye",
    item="Heated Eye Mask",
    path="heated-eye-mask",
    hours_ago=27.0,
    angles=(
        AngleSpec(
            "Tension headache relief",
            "Warmth for tension headaches and sinus pressure after long days.",
            "pain_relief",
            (
                "Gentle, even heat melts the tension around your eyes and temples. Most "
                "people feel it ease in under 10 minutes.",
                "Sinus pressure, tension headaches, tired eyes: the warmth reaches all of it.",
            ),
            ("Melt the tension away", "Headache? Heat helps."),
        ),
        AngleSpec(
            "Fall asleep faster",
            "A bedtime routine framed around switching off: warmth, darkness and a "
            "15-minute timer.",
            "health",
            (
                "Warm, weighted and completely dark. Set the 15-minute timer and you'll be "
                "asleep before it clicks off.",
                "I used to scroll until 1 am. Now I put this on and I'm out in 10 minutes.",
            ),
            ("Asleep in 10 minutes", "Warm. Dark. Out."),
        ),
        AngleSpec(
            "Screen-tired, dry eyes",
            "Relief for dry, strained eyes after screens, aimed at office workers.",
            "health",
            (
                "Eight hours of screens leaves your eyes dry and heavy. Ten minutes of "
                "moist warmth and they feel brand new.",
            ),
            ("Screens all day?", "Dry eyes, sorted"),
        ),
        AngleSpec(
            "The gift they'll actually use",
            "Holiday gifting for partners, mums and coworkers, with arrive-by dates.",
            "gift",
            (
                "The cosy gift everyone on your list actually wants. Gift-boxed and ready to wrap.",
                "Stuck on a gift for the tired mum in your life? This is it.",
            ),
            ("The gift they'll use daily", "Gift-boxed and ready"),
        ),
        AngleSpec(
            "Cordless and travel-ready",
            "USB-C rechargeable and cordless, pitched for flights and travel.",
            "convenience",
            (
                "Cordless, USB-C rechargeable and slim enough for your carry-on. Long-haul "
                "flights just got easier.",
            ),
            ("Cordless. Carry-on ready.",),
        ),
    ),
    hooks=(
        "Screens all day? Your eyes need this.",
        "I fall asleep in 10 minutes now.",
        "The gift every tired mum asked for 🎁",
        "Warm. Weighted. Lights out.",
        "Tension headache? Try this before reaching for a pill.",
        "Why didn't anyone tell me about this sooner?",
    ),
    offers=(
        OfferSpec("discount", "Holiday sale: 35% off", "Holiday sale: 35% off.", "35% OFF"),
        OfferSpec(
            "bundle",
            "Buy 2, save 20%: one to keep, one to gift",
            "Buy 2, save 20%: one to keep, one to gift.",
        ),
        OfferSpec(
            "urgency",
            "Order by the 18th to arrive before the holidays",
            "Order by the 18th to arrive before the holidays.",
        ),
        OfferSpec("guarantee", "30-night sleep trial", "Try it for 30 nights, risk-free."),
    ),
    titles=(
        "{brand} {item} | Warm Compress for Eyes",
        "The {item} for Better Sleep",
        "{item}: Holiday Sale",
    ),
    quotes=(
        ("I'm asleep before the timer even clicks off.", "Hannah K., verified buyer"),
        ("Bought one for me and two as gifts. Everyone loves it.", "Joel P., verified buyer"),
    ),
    brands=(
        Brand("Driftwell", EYE_PALETTES[0], creatives=10, ages=(2, 54), ads=(2, 3)),
        Brand("Hushmoor", EYE_PALETTES[1], creatives=5, ages=(1, 30), ads=(1, 3)),
        Brand(
            "Silkmoon Sleep",
            EYE_PALETTES[2],
            role="noise",
            ads=(1, 2),
            copy=(
                (
                    "100% mulberry silk sleep mask. Blocks every bit of light, kind to your "
                    "lashes and your skin.",
                    "Silkmoon Mulberry Silk Sleep Mask",
                    "silk-sleep-mask",
                ),
                (
                    "The softest sleep mask you'll ever own. Now in 6 new colours.",
                    "Silk Sleep Masks in New Colours",
                    "silk-sleep-mask-colours",
                ),
            ),
            ages=(20, 80),
        ),
        Brand(
            "Chillpatch",
            EYE_PALETTES[3],
            role="noise",
            ads=(1, 2),
            copy=(
                (
                    "Puffy morning eyes? Our cooling gel eye mask goes from freezer to face in "
                    "seconds.",
                    "Chillpatch Cooling Gel Eye Mask",
                    "cooling-gel-eye-mask",
                ),
            ),
            ages=(10, 60),
        ),
        Brand(
            "Nestled Home",
            EYE_PALETTES[5],
            role="noise",
            ads=(1, 2),
            copy=(
                (
                    "Our heated throw blanket has 6 heat settings and an auto-off timer. The "
                    "cosiest thing in your living room this winter.",
                    "Nestled Heated Throw Blanket",
                    "heated-throw",
                ),
            ),
            ages=(5, 40),
        ),
        Brand(
            "Dozewell Tea",
            EYE_PALETTES[4],
            role="off_topic",
            ads=(1, 2),
            copy=(
                (
                    "Skip the heated eye mask and gadgets. Our chamomile and lavender blend "
                    "helps you wind down the natural way.",
                    "Better Than a Heated Eye Mask: Bedtime Tea",
                    "bedtime-blend",
                ),
            ),
            ages=(8, 30),
        ),
    ),
    seasonality="seasonal",
    seasonality_note="Gift and holiday framing in the copy, and every creative is under two months "
    "old: demand may be tied to cold-weather gifting.",
    verdict=(
        "Five angles are already in play, split between sleep, headache relief and holiday "
        "gifting. Only two advertisers are selling it and every creative is under two "
        "months old, so nothing has proven it can run for long. The main risk is "
        "seasonality: the gifting angle may fade after the holidays."
    ),
)


PHONE = ProductSpec(
    keyword="magnetic phone mount",
    art="phone",
    item="Magnetic Phone Mount",
    path="magnetic-phone-mount",
    watch="fading",
    hours_ago=2.2,
    angles=(
        AngleSpec(
            "One-hand snap, no clamps",
            "Snap the phone on and off with one hand; no clamps or cradles to fiddle with.",
            "convenience",
            (
                "No clamps, no squeezing, no fumbling at the lights. Your phone snaps on "
                "with one hand and stays put.",
                "Get in, snap, drive. It takes less than a second.",
            ),
            ("Snap on. Drive off.", "One hand. Zero fuss."),
        ),
        AngleSpec(
            "Holds on the bumpiest roads",
            "Strength demos: potholes, gravel roads and hard braking with the phone still "
            "attached.",
            "confidence",
            (
                "N52 magnets hold through potholes, gravel and hard braking. We tested it so "
                "you don't have to.",
            ),
            ("Tested on gravel at 60 mph", "It won't let go"),
        ),
        AngleSpec(
            "Stop holding your phone",
            "Hands-free driving framed around safety and avoiding fines.",
            "other",
            (
                "Holding your phone while driving is risky and in many states it's a fine. "
                "Keep your eyes up and your hands on the wheel.",
            ),
            ("Eyes up, hands free",),
        ),
        AngleSpec(
            "Under $20",
            "Price-led ads: multi-packs and clearance pricing.",
            "value",
            ("Why pay $40 for a phone mount? Same strength, same build, a fraction of the price.",),
            ("2 for $19.99",),
        ),
    ),
    hooks=(
        "Stop balancing your phone on your lap.",
        "It snaps on. It stays on.",
        "Tested on a gravel road at 60 mph.",
        "The $15 upgrade every car needs.",
    ),
    offers=(
        OfferSpec("bundle", "2-pack for $19.99", "2-pack for $19.99.", "2 FOR $19"),
        OfferSpec("discount", "70% off clearance", "Clearance: 70% off.", "70% OFF"),
        OfferSpec("free_shipping", "Free shipping today", "Free shipping today only."),
    ),
    titles=(
        "{brand} {item}",
        "{item} | Snap & Go",
        "{item}: 2 for $19.99",
    ),
    quotes=(("Holds my phone over every pothole in town.", "Chris D., verified buyer"),),
    brands=(
        *_brands(
            [
                "Gripstead",
                "Dashlock",
                "Zentrapod",
                "Roadmate Gear",
                "Mountwise",
                "Clutchline",
                "Veerline",
                "Holdfast Auto",
                "Pivotly",
                "Steerly",
                "Trekmount",
                "Axleby",
            ],
            PHONE_PALETTES,
            [
                (4, (21, 52), (3, 5)),
                (3, (21, 60), (3, 5)),
                (4, (25, 75), (5, 7)),
                (3, (21, 65), (2, 4)),
                (3, (24, 55), (2, 4)),
                (2, (20, 70), (3, 5)),
                (2, (21, 50), (2, 4)),
                (2, (30, 80), (2, 4)),
                (2, (22, 45), (2, 3)),
                (2, (21, 60), (2, 4)),
                (1, (22, 70), (3, 5)),
                (1, (12, 40), (2, 4)),
            ],
            {"Gripstead": (0, 3), "*": (0, 1, 3, 2)},
        ),
        Brand(
            "Autoshine Detailing",
            PHONE_PALETTES[3],
            role="off_topic",
            ads=(1, 2),
            copy=(
                (
                    "Free magnetic phone mount with every Complete Detailing Kit this week. "
                    "Wax, polish, microfibre and more.",
                    "Detailing Kit + Free Magnetic Phone Mount",
                    "complete-detailing-kit",
                ),
            ),
            ages=(15, 40),
        ),
        Brand(
            "Gadget Outlet Deals",
            PHONE_PALETTES[1],
            role="template",
            ads=(1, 2),
            copy=(
                ("{{product.brand}}", "{{product.name}}", "catalog"),
                ("{{product.description}}", "{{product.name}}", "catalog-2"),
            ),
            ages=(10, 50),
        ),
    ),
    seasonality="evergreen",
    seasonality_note="No seasonal cues; ads are short-lived and new creatives have slowed over the "
    "last month.",
    verdict=(
        "Four angles are in play, but the leading advertiser runs only two, and most "
        "creatives repeat the same one-hand-snap promise at a lower price. No advertiser "
        "has kept a creative running "
        "for long or launched new ones steadily, and several have paused. The main risk is "
        "a race to the bottom on price in a crowded commodity niche."
    ),
)

PRODUCTS: tuple[ProductSpec, ...] = (POSTURE, LED, EYE, PHONE)


# --- Plans ----------------------------------------------------------------------------------


@dataclass
class CreativePlan:
    brand: Brand
    page_id: str
    role: Role
    angles: list[int]
    hook: int | None
    offer: int | None
    body: str
    title: str
    link_url: str
    video: bool
    media: MediaItem


@dataclass
class AdPlan:
    ad_id: str
    creative: CreativePlan
    start: datetime
    end: datetime | None = None  # when it stopped running (absent from later snapshots)
    collation_id: str | None = None
    collation_count: int = 1
    body: str | None = None  # a copy variant on the same image

    def to_ad(self) -> Ad:
        c = self.creative
        return Ad(
            source=SOURCE,
            ad_archive_id=self.ad_id,
            page_id=c.page_id,
            page_name=c.brand.name,
            start_date=self.start,
            end_date=None,
            is_active=True,
            body=self.body if self.body is not None else c.body,
            title=c.title,
            link_url=c.link_url,
            link_caption=c.brand.domain,
            cta_text="Learn more" if c.role in ("excluded", "noise") else "Shop now",
            display_format="DPA" if c.role == "template" else ("VIDEO" if c.video else "IMAGE"),
            media=[c.media],
            collation_id=self.collation_id,
            collation_count=self.collation_count,
            platforms=["FACEBOOK", "INSTAGRAM"]
            if c.page_id[-1] in "02468"
            else ["FACEBOOK", "INSTAGRAM", "MESSENGER"],
        )


class _MediaStore:
    """Writes each drawn creative once as a content-addressed JPEG blob, with its phash.

    Within one product, a creative whose image lands within ``MIN_DISTANCE`` bits of an
    earlier one is redrawn (another composition, then another layout), so only the intended
    variants merge by image hash.
    """

    MIN_DISTANCE = 8
    ATTEMPTS = 12

    def __init__(self, media_dir: Path) -> None:
        self._blobs = media_dir / BLOB_DIR
        self._blobs.mkdir(parents=True, exist_ok=True)
        self._hashes: list[int] = []
        self.renders = 0

    def next_product(self) -> None:
        """Images of different products never share a report: compare within one only."""
        self._hashes = []

    def save(
        self, art: Art, *, video: bool, ref: str, layouts: tuple[Layout, ...] = ()
    ) -> MediaItem:
        # Fallback layouts in a fixed rotation starting after the requested one.
        start = layouts.index(art.layout) + 1 if art.layout in layouts else 0
        others = [lay for lay in layouts[start:] + layouts[:start] if lay != art.layout]
        for attempt in range(self.ATTEMPTS):
            layout = (
                art.layout if attempt < 2 or not others else others[(attempt - 2) % len(others)]
            )
            candidate = replace(art, layout=layout, variant=art.variant + attempt * 101)
            img = render(candidate)
            self.renders += 1
            ph = phash(img)
            value = int(ph, 16)
            if all((value ^ h).bit_count() >= self.MIN_DISTANCE for h in self._hashes):
                break
        self._hashes.append(value)
        data = jpeg_bytes(img)
        name = f"{hashlib.sha256(data).hexdigest()[:32]}.jpg"
        (self._blobs / name).write_bytes(data)
        local = f"{BLOB_DIR}/{name}"
        if video:
            return MediaItem(
                type="video",
                url=f"https://media.example/v/{ref}.mp4",
                poster_path=local,
                local_path=None,
                phash=ph,
            )
        return MediaItem(
            type="image", url=f"https://media.example/i/{ref}.jpg", local_path=local, phash=ph
        )


class _Ids:
    def __init__(self, rng: random.Random) -> None:
        self._rng = rng
        self._seen: set[str] = set()

    def make(self, prefix: str, digits: int) -> str:
        while True:
            value = prefix + "".join(
                str(self._rng.randrange(10)) for _ in range(digits - len(prefix))
            )
            if value not in self._seen:
                self._seen.add(value)
                return value


def _spread(rng: random.Random, n: int, lo: int, hi: int) -> list[int]:
    """``n`` ages spread evenly over [lo, hi] with a little jitter."""
    if n == 1:
        return [rng.randint(lo, hi)]
    step = (hi - lo) / (n - 1)
    return [
        max(lo, min(hi, round(lo + i * step + rng.uniform(-step / 3, step / 3)))) for i in range(n)
    ]


def _fill(text: str, spec: ProductSpec, brand: Brand) -> str:
    return text.replace("{item}", spec.item).replace("{brand}", brand.name)


class _Planner:
    def __init__(
        self, spec: ProductSpec, seed: int, t0: datetime, media: _MediaStore, ids: _Ids
    ) -> None:
        self.spec = spec
        self.rng = random.Random(f"{seed}:{spec.keyword}")
        self.t0 = t0  # the latest snapshot's time: ages count back from here
        self.media = media
        self.ids = ids
        self.plans: list[AdPlan] = []
        self.page_ids: dict[str, str] = {}

    def page_id(self, brand: Brand) -> str:
        if brand.name not in self.page_ids:
            self.page_ids[brand.name] = self.ids.make("10", 15)
        return self.page_ids[brand.name]

    def _start(self, age: int) -> datetime:
        return self.t0 - timedelta(days=age, hours=self.rng.uniform(1, 20))

    def seller_creative(self, brand: Brand, age: int, index: int) -> CreativePlan:
        spec, rng = self.spec, self.rng
        pool = brand.angle_pool or tuple(range(len(spec.angles)))
        primary = rng.choices(pool, weights=[6, 5, 4, 3, 3, 2][: len(pool)])[0]
        angles = [primary]
        if rng.random() < 0.3:
            second = rng.choice(pool)
            if second != primary:
                angles.append(second)
        angle = spec.angles[primary]
        hook = rng.randrange(len(spec.hooks)) if rng.random() < 0.85 else None
        offer = rng.randrange(len(spec.offers)) if rng.random() < 0.8 else None
        parts = []
        if hook is not None:
            parts.append(spec.hooks[hook])
        parts.append(_fill(rng.choice(angle.bodies), spec, brand))
        tail = []
        if offer is not None:
            tail.append(spec.offers[offer].line)
        tail.append(rng.choice(CTAS))
        parts.append(" ".join(tail))
        body = "\n\n".join(parts)
        title = _fill(rng.choice(spec.titles), spec, brand)
        video = rng.random() < brand.video_share
        rotation = spec.layouts[(index + len(brand.name)) % len(spec.layouts)]
        layout: Layout = "ugc" if video and index % 2 else rotation
        quote, reviewer = rng.choice(spec.quotes)
        badge = spec.offers[offer].badge if offer is not None else None
        art = Art(
            product=spec.art,
            layout=layout,
            brand=brand.name,
            palette=brand.palette,
            headline=rng.choice(angle.headlines),
            badge=badge if layout in ("studio", "offer") else None,
            quote=quote,
            reviewer=reviewer,
            variant=index,
        )
        ref = f"{brand.slug}-{index}-{age}"
        return CreativePlan(
            brand=brand,
            page_id=self.page_id(brand),
            role="seller",
            angles=angles,
            hook=hook,
            offer=offer,
            body=body,
            title=title,
            link_url=f"https://{brand.domain}/products/{spec.path}",
            video=video,
            media=self.media.save(art, video=video, ref=ref, layouts=spec.layouts),
        )

    def other_creative(self, brand: Brand, copy: tuple[str, str, str], index: int) -> CreativePlan:
        body, title, path = copy
        layout: Layout = (
            "studio" if brand.role == "template" else self.rng.choice(("studio", "offer", "ugc"))
        )
        art = Art(
            product="box",
            layout=layout,
            brand=brand.name,
            palette=brand.palette,
            headline=title if brand.role != "template" else brand.name,
            badge=None,
            variant=index,
        )
        return CreativePlan(
            brand=brand,
            page_id=self.page_id(brand),
            role=brand.role,
            angles=[],
            hook=None,
            offer=None,
            body=body,
            title=title,
            link_url=f"https://{brand.domain}/products/{path}",
            video=False,
            media=self.media.save(
                art, video=False, ref=f"{brand.slug}-{path}", layouts=("studio", "offer", "ugc")
            ),
        )

    def add_ads(
        self, creative: CreativePlan, age: int, count: int, *, variant_copy: bool = True
    ) -> None:
        rng = self.rng
        first = self._start(age)
        collated = rng.random() < 0.35
        collation = self.ids.make("9", 15) if collated else None
        for i in range(count):
            start = (
                first
                if i == 0
                else min(
                    self.t0 - timedelta(hours=1),
                    first + timedelta(days=rng.randint(0, 10), hours=rng.uniform(0, 12)),
                )
            )
            self.plans.append(
                AdPlan(
                    ad_id=self.ids.make("1", 16),
                    creative=creative,
                    start=start,
                    collation_id=collation,
                    collation_count=rng.randint(1, 4) if collated else 1,
                )
            )
        # Now and then the same image runs with different copy: it merges by image hash.
        if variant_copy and creative.role == "seller" and rng.random() < 0.15:
            hook = self.spec.hooks[rng.randrange(len(self.spec.hooks))]
            body = "\n\n".join([hook, *creative.body.split("\n\n")[1:]])
            if body != creative.body:
                self.plans.append(
                    AdPlan(
                        ad_id=self.ids.make("1", 16),
                        creative=creative,
                        start=first + timedelta(days=rng.randint(1, 6)) if age > 7 else first,
                        body=body,
                    )
                )

    def build(self) -> None:
        index = 0
        for brand in self.spec.brands:
            if brand.role == "seller":
                for age in _spread(self.rng, brand.creatives, *brand.ages):
                    index += 1
                    creative = self.seller_creative(brand, age, index)
                    self.add_ads(creative, age, self.rng.randint(*brand.ads))
            else:
                for copy in brand.copy:
                    index += 1
                    creative = self.other_creative(brand, copy, index)
                    self.add_ads(
                        creative, self.rng.randint(*brand.ages), self.rng.randint(*brand.ads)
                    )

    def launch(self, brand: Brand, age: int, ads_needed: int) -> None:
        """New creatives launched ``age`` days ago, at least ``ads_needed`` ads in all."""
        added = 0
        index = 500
        while added < ads_needed:
            index += 1
            creative = self.seller_creative(brand, age, index)
            n = min(4, max(2, ads_needed - added))
            before = len(self.plans)
            self.add_ads(creative, age, n, variant_copy=False)
            for plan in self.plans[before:]:
                plan.start = self.t0 - timedelta(days=age, hours=self.rng.uniform(1, 6))
            added += n


def _alive(plans: list[AdPlan], t: datetime) -> list[AdPlan]:
    return [p for p in plans if p.start <= t and (p.end is None or p.end > t)]


def _relevant_count(plans: list[AdPlan], t: datetime) -> int:
    return sum(1 for p in _alive(plans, t) if p.creative.role == "seller")


# --- Writing --------------------------------------------------------------------------------


def _snapshot(
    session: Session,
    spec: ProductSpec,
    plans: list[AdPlan],
    t: datetime,
    *,
    watch_item_id: int | None,
    cap: int | None = None,
    rng: random.Random,
) -> int:
    alive = sorted(_alive(plans, t), key=lambda p: (p.start, p.ad_id), reverse=True)
    status: Literal["ok", "capped"] = "ok"
    message = None
    if cap is not None and len(alive) > cap:
        alive = alive[:cap]
        status = "capped"
        message = f"Stopped at the limit of {cap} ads."
    query = SearchQuery(keyword=spec.keyword, country=COUNTRY)
    snap = create_snapshot(session, query, SOURCE, watch_item_id=watch_item_id)
    assert snap.id is not None
    finish_snapshot(
        session,
        snap.id,
        SearchResult(ads=[p.to_ad() for p in alive], status=status, message=message),
    )
    snap.started_at = t
    snap.finished_at = t + timedelta(seconds=rng.randint(70, 240))
    session.add(snap)
    session.commit()
    return snap.id


def _strip_emoji(text: str) -> str:
    return "".join(ch for ch in text if ord(ch) < 0x2000).strip()


def _analyse(
    session: Session, snapshot_id: int, spec: ProductSpec, by_id: dict[str, CreativePlan]
) -> None:
    snap, items = analysis_input(session, snapshot_id)
    if not items:
        return
    leads = [(i.id, by_id[i.id]) for i in items if i.id in by_id]
    selling = [(i, c) for i, c in leads if c.role == "seller"]
    angles = [
        Angle(
            name=a.name,
            description=a.description,
            emotion=a.emotion,
            ad_ids=[i for i, c in selling if n in c.angles],
        )
        for n, a in enumerate(spec.angles)
    ]
    hooks = [
        Hook(text=_strip_emoji(h), ad_ids=[i for i, c in selling if c.hook == n])
        for n, h in enumerate(spec.hooks)
    ]
    hooks.sort(key=lambda h: -len(h.ad_ids))
    offers = [
        Offer(kind=o.kind, text=o.summary, ad_ids=[i for i, c in selling if c.offer == n])
        for n, o in enumerate(spec.offers)
    ]
    offers.sort(key=lambda o: -len(o.ad_ids))
    result = AngleAnalysis(
        angles=angles,
        hooks=hooks,
        offers=offers,
        not_product_ad_ids=[i for i, c in leads if c.role == "off_topic"],
        seasonality=spec.seasonality,
        seasonality_note=spec.seasonality_note,
        verdict=spec.verdict,
    )
    cleaned = clean(result, {i.id for i in items})
    row = save_analysis(
        session,
        snapshot_id=snapshot_id,
        prompt_version=PROMPT_VERSION,
        model=DEMO_MODEL,
        backend=DEMO_BACKEND,
        input_hash=input_hash(items),
        ad_count=len(items),
        result=cleaned.model_dump(mode="json"),
        usage=None,
    )
    assert snap.finished_at is not None
    row.created_at = snap.finished_at + timedelta(minutes=2)
    session.add(row)
    session.commit()


def _run_time(now: datetime, spec: ProductSpec, days_ago: int) -> datetime:
    return (now - timedelta(days=days_ago, hours=spec.hours_ago)).replace(microsecond=0)


def _shape_watch(planner: _Planner, now: datetime) -> None:
    """Script the scaling or fading story so exactly one day crosses the 30% alert line."""
    spec = planner.spec
    plans = planner.plans
    # Not seeded on purpose: the authored verdicts and docs/media depend on this shape.
    rng = random.Random(f"shape:{spec.keyword}")
    day = {d: _run_time(now, spec, d) for d in range(WATCH_DAYS)}
    if spec.watch == "scaling":
        before = _relevant_count(plans, day[BURST_DAY + 1])
        natural = _relevant_count(plans, day[BURST_DAY]) - before
        needed = math.ceil(ALERT_MARGIN * before) - natural
        if needed > 0:
            age = round((planner.t0 - day[BURST_DAY]).total_seconds() / 86400) + 0
            planner.launch(spec.brands[0], max(age, BURST_DAY), needed)
    elif spec.watch == "fading":
        # Gradual pauses most days, then one advertiser pauses everything on the drop day.
        sellers = [
            p for p in plans if p.creative.role == "seller" and p.start <= day[WATCH_DAYS - 1]
        ]
        rng.shuffle(sellers)
        pauser = spec.brands[2].name
        gradual = [p for p in sellers if p.creative.brand.name != pauser]
        for d in range(WATCH_DAYS - 2, -1, -1):
            if d == DROP_DAY:
                continue
            for _ in range(rng.choice((1, 1, 2, 2, 3))):
                if len(gradual) > 20:
                    gradual.pop().end = day[d] - timedelta(hours=rng.uniform(1, 12))
        before = _relevant_count(plans, day[DROP_DAY + 1])
        cut = day[DROP_DAY] - timedelta(hours=3)
        dropped = [
            p for p in plans if p.creative.brand.name == pauser and (p.end is None or p.end > cut)
        ]
        for p in dropped:
            p.end = cut
        extra = [p for p in gradual if p.end is None]
        while (
            before - _relevant_count(plans, day[DROP_DAY]) < math.ceil(ALERT_MARGIN * before)
            and extra
        ):
            extra.pop().end = cut


def _watch_item(session: Session, spec: ProductSpec, now: datetime) -> WatchItem:
    item = create_watch_item(session, SearchQuery(keyword=spec.keyword, country=COUNTRY))
    item.created_at = _run_time(now, spec, WATCH_DAYS) - timedelta(minutes=12)
    session.add(item)
    session.commit()
    session.refresh(item)
    return item


def _alerts(session: Session, item: WatchItem, now: datetime) -> None:
    assert item.id is not None
    tz = ZoneInfo("UTC")
    prev = None
    for snap in snapshots_for_watch(session, item.id):
        if snap.id is None or snap.status not in TREND_STATUSES:
            continue
        summary = summarize(build_report(session, snap.id, now, tz))
        draft = alert_for(prev, summary, item.query)
        if draft is not None:
            alert = add_alert(
                session,
                watch_item_id=item.id,
                snapshot_id=snap.id,
                kind=draft.kind,
                message=draft.message,
                change_pct=draft.change_pct,
            )
            assert snap.finished_at is not None
            alert.created_at = snap.finished_at + timedelta(seconds=30)
            session.add(alert)
            session.commit()
        prev = summary


def _build(data_dir: Path, seed: int, now: datetime) -> None:
    media = _MediaStore(data_dir / "media")
    ids = _Ids(random.Random(f"{seed}:ids"))
    planners: list[_Planner] = []
    for spec in PRODUCTS:
        media.next_product()
        planner = _Planner(spec, seed, _run_time(now, spec, 0), media, ids)
        planner.build()
        if spec.watch is not None:
            _shape_watch(planner, now)
        planners.append(planner)

    engine = make_engine(data_dir / "adscout.db")
    try:
        _write(engine, planners, seed, now)
    finally:
        engine.dispose()


def _write(engine: Engine, planners: list[_Planner], seed: int, now: datetime) -> None:
    rng = random.Random(f"{seed}:write")
    with Session(engine) as session:
        items: dict[str, WatchItem] = {}
        for planner in planners:
            if planner.spec.watch is not None:
                items[planner.spec.keyword] = _watch_item(session, planner.spec, now)
        # The latest snapshot of each product first: ids 1-4 are the four reports.
        latest: dict[str, int] = {}
        for planner in planners:
            spec = planner.spec
            item = items.get(spec.keyword)
            latest[spec.keyword] = _snapshot(
                session,
                spec,
                planner.plans,
                _run_time(now, spec, 0),
                watch_item_id=None if item is None else item.id,
                rng=rng,
            )
        history: dict[str, list[int]] = {}
        for planner in planners:
            spec = planner.spec
            item = items.get(spec.keyword)
            if item is None:
                continue
            ids = []
            for d in range(WATCH_DAYS - 1, 0, -1):
                cap = None
                if spec.watch == "scaling" and d == CAPPED_DAY:
                    count = len(_alive(planner.plans, _run_time(now, spec, d)))
                    cap = max(10, (count - 1) // 10 * 10)
                ids.append(
                    _snapshot(
                        session,
                        spec,
                        planner.plans,
                        _run_time(now, spec, d),
                        watch_item_id=item.id,
                        cap=cap,
                        rng=rng,
                    )
                )
            history[spec.keyword] = ids

        # The user's own exclusion: a clinic advertising against the product.
        for planner in planners:
            for brand in planner.spec.brands:
                if brand.role == "excluded":
                    set_override(
                        session,
                        planner.spec.keyword,
                        "advertiser",
                        planner.page_id(brand),
                        "exclude",
                    )
        for row in session.exec(select(Exclusion)):
            row.created_at = (now - timedelta(days=4)).replace(microsecond=0)
            session.add(row)
        session.commit()

        for planner in planners:
            by_id = {p.ad_id: p.creative for p in planner.plans}
            keyword = planner.spec.keyword
            for sid in [latest[keyword], *history.get(keyword, [])]:
                _analyse(session, sid, planner.spec, by_id)

        for item in items.values():
            assert item.id is not None
            snaps = snapshots_for_watch(session, item.id)
            update_watch_item(session, item.id, last_run_at=snaps[-1].started_at, last_status="ok")
            _alerts(session, item, now)
        # Every alert but the newest has been read.
        alerts = list(session.exec(select(Alert)))
        alerts.sort(key=lambda a: (a.created_at, a.id or 0))
        for alert in alerts[:-1]:
            alert.read = True
            session.add(alert)
        session.commit()


# --- Entry point ----------------------------------------------------------------------------

_KEEP = frozenset({SERVER_LOCK, SERVER_PORT})


def _marker(data_dir: Path) -> dict[str, object] | None:
    try:
        raw = json.loads((data_dir / DEMO_MARKER).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return raw if isinstance(raw, dict) else None


def is_current(data_dir: Path, seed: int, now: datetime) -> bool:
    """Whether ``data_dir`` already holds this seed's demo, built within the last week."""
    marker = _marker(data_dir)
    if marker is None or marker.get("seed") != seed or marker.get("version") != GENERATOR_VERSION:
        return False
    if not (data_dir / "adscout.db").is_file():
        return False
    try:
        built = datetime.fromisoformat(str(marker.get("now")))
    except ValueError:
        return False
    return built.tzinfo is not None and timedelta(0) <= now - built < STALE_AFTER


class NotADemoFolder(Exception):
    """The folder holds something other than demo data, so it is left alone."""


def reset_demo(data_dir: Path) -> None:
    """Remove the demo data, keeping only the server lock and port files.

    Refuses (``NotADemoFolder``) unless the folder carries the demo marker or is empty.
    """
    if not data_dir.is_dir():
        return
    if not (data_dir / DEMO_MARKER).exists() and any(
        child.name not in _KEEP for child in data_dir.iterdir()
    ):
        raise NotADemoFolder(
            f"{data_dir} isn't a demo folder (no {DEMO_MARKER}), so AdScout won't wipe it. "
            "Move or delete it yourself to rebuild the demo there."
        )
    for child in data_dir.iterdir():
        if child.name in _KEEP:
            continue
        if child.is_dir():
            shutil.rmtree(child)
        else:
            child.unlink()


def generate_demo(data_dir: Path, seed: int = 7, now: datetime | None = None) -> None:
    """Build the demo folder, unless it already holds this seed's (recent) demo."""
    now = (now or datetime.now(UTC)).astimezone(UTC).replace(microsecond=0)
    if is_current(data_dir, seed, now):
        return
    reset_demo(data_dir)
    data_dir.mkdir(parents=True, exist_ok=True)
    _build(data_dir, seed, now)
    marker = {"seed": seed, "version": GENERATOR_VERSION, "now": now.isoformat()}
    (data_dir / DEMO_MARKER).write_text(json.dumps(marker), encoding="utf-8")


def demo_brand_names() -> list[str]:
    """Every invented advertiser name (for the denylist check)."""
    return [b.name for spec in PRODUCTS for b in spec.brands]


def denylist_hits(db_path: Path) -> list[str]:
    """Every text value in the demo database that names a real brand (empty when clean).

    Scans every text column of every table (ads, analyses, watch items, alerts, settings),
    plus the invented brand names, so published screenshots can never show a real brand.
    """
    hits = [name for name in demo_brand_names() if _denylisted(name)]
    # Read-only: never create or change the database being checked.
    with closing(sqlite3.connect(f"{db_path.resolve().as_uri()}?mode=ro", uri=True)) as db:
        tables = [
            row[0]
            for row in db.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%'"
            )
        ]
        for table in tables:
            for row in db.execute(f'SELECT * FROM "{table}"'):  # table names come from the schema
                for value in row:
                    if isinstance(value, bytes):
                        value = value.decode("utf-8", errors="ignore")
                    if isinstance(value, str) and _denylisted(value):
                        hits.append(f"{table}: {value[:120]}")
    return hits


def _denylisted(text: str) -> bool:
    lowered = text.lower()
    return any(bad in lowered for bad in DENYLIST)
