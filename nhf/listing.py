"""Turning a pasted portal link into one HDB block.

Everything in the report hangs off this, so it is built to fail honestly
rather than to always answer. The page is never fetched — scraping the
portals is against their terms and is not needed, because both of them put
the address in the URL path:

    99.co/singapore/sale/property/669c-jurong-west-street-64-hdb-eSeF
    propertyguru.com.sg/listing/hdb-for-sale-266b-punggol-way-25099211

Two things make this harder than reading the slug.

**Portal and HDB spell streets differently.** The portals write
`JURONG WEST STREET 64`; HDB's own datasets write `JURONG WEST ST 64`. The
same goes for AVENUE/AVE, DRIVE/DR, CRESCENT/CRES, CENTRAL/CTRL, BUKIT/BT,
JALAN/JLN and a dozen more.

**Block numbers are not unique.** `266B` is both `266B|COMPASSVALE BOW` and
`266B|PUNGGOL WAY`, so the street is load-bearing and a block number on its
own is a question, not an answer.

The approach is an alias index rather than a parser clever enough to be
trusted. Every block in the HDB data is written out under every spelling a
portal might plausibly use, the pasted link is reduced to the same shape,
and resolution is an exact dictionary lookup. That inverts where the
uncertainty sits: a guess at expanding an abbreviation cannot produce a
wrong block, it can only produce an alias no block claims, and an alias no
block claims is a miss rather than a lie.

It also means the browser does not need any of this logic beyond the
tokenising, because the index ships with the site as data.
"""

from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass, field

from .norm import block_key, clean

# HDB's abbreviations, mapped to every long form worth indexing. A token with
# more than one expansion gets all of them: the alias index is a dictionary,
# so a wrong expansion costs one unused key and never a wrong answer.
#
# `ST` is the reason this is a list and not a string. It is STREET in 166 of
# the 168 street names that contain it and SAINT in the other two —
# `ST GEORGE'S RD` and `ST GEORGE'S LANE` — and the position does not settle
# it either, since `UPP CROSS ST` ends with the STREET sense. Both forms are
# written out and the one nobody links to is never looked up.
STREET_EXPANSIONS: dict[str, tuple[str, ...]] = {
    "ST": ("STREET", "SAINT"),
    "RD": ("ROAD",),
    "AVE": ("AVENUE",),
    "DR": ("DRIVE",),
    "CRES": ("CRESCENT",),
    "CTRL": ("CENTRAL",),
    "BT": ("BUKIT",),
    "JLN": ("JALAN",),
    "GDN": ("GARDEN",),
    "GDNS": ("GARDENS",),
    "NTH": ("NORTH",),
    "STH": ("SOUTH",),
    "UPP": ("UPPER",),
    "LOR": ("LORONG",),
    "CL": ("CLOSE",),
    "PL": ("PLACE",),
    "TER": ("TERRACE",),
    "PK": ("PARK",),
    "HTS": ("HEIGHTS",),
    "MKT": ("MARKET",),
    "SQ": ("SQUARE",),
    "KG": ("KAMPONG", "KAMPUNG"),
    "TG": ("TANJONG",),
    "CWEALTH": ("COMMONWEALTH",),
}

# Deliberately absent, because they look like abbreviations and are not:
#
#   RIS     appears in 15 street names and every one of them is PASIR RIS
#   INDUS   is Indus Road, not an industrial estate
#   SIMEI, SIMS, SENJA, SEGAR, PETIR ... are simply short names
#
# Expanding any of those would mint an alias for a street that does not
# exist, which is harmless here, but RIS -> RISE would also shadow the real
# `PASIR RIS` spelling if the expansion ever replaced rather than added.
NOT_ABBREVIATIONS = frozenset({"RIS", "INDUS"})

# Path and slug noise. None of these is a word in any HDB street name, which
# is checked by a test against the real street list rather than asserted.
JUNK_TOKENS = frozenset({
    "www", "com", "sg", "en", "http", "https",
    "singapore", "property", "properties", "listing", "listings", "project",
    "sale", "sales", "rent", "rental", "buy", "hdb", "for", "resale",
    "flat", "flats", "apartment", "condo", "blk", "block", "d",
})

# A block is up to four digits and an optional letter: 1, 101, 669C, 266B.
BLOCK_TOKEN = re.compile(r"^\d{1,4}[A-Z]?$")

# A listing id. Both portals use a long number; 99.co also appends a short
# mixed-case hash. Street names carry numbers too — JURONG WEST ST 64,
# TAMPINES ST 86 — but never five digits of one, so the length is what
# separates a street number from an id.
LISTING_ID = re.compile(r"^\d{5,}$")

RESOLVED, AMBIGUOUS, UNRESOLVED = "resolved", "ambiguous", "unresolved"

PORTALS = {
    "99.co": "99.co",
    "propertyguru.com.sg": "PropertyGuru",
}


def _tokenise(text: str) -> list[str]:
    """Split anything into upper-case word tokens.

    Apostrophes are dropped rather than turned into separators, so HDB's
    `ST GEORGE'S RD` and a portal's `st-georges-road` tokenise alike, and
    `C'WEALTH` survives as one token to be expanded.
    """
    squashed = text.replace("'", "").replace("’", "")
    return [t for t in re.split(r"[^A-Za-z0-9]+", squashed.upper()) if t]


def street_tokens(street: str) -> list[str]:
    """HDB's street name as tokens, ready to expand."""
    return _tokenise(clean(street))


def _expansions(token: str) -> tuple[str, ...]:
    if token in NOT_ABBREVIATIONS:
        return ()
    return STREET_EXPANSIONS.get(token, ())


def street_variants(street: str) -> list[str]:
    """Every spelling of one street worth indexing, as hyphenated slugs.

    The abbreviated form HDB publishes, the fully expanded form a portal
    writes, and the mixtures in between — a portal is as likely to write
    `bukit-batok-east-avenue-3` as `bt-batok-east-avenue-3`, and both are
    cheap to hold.

    The product is bounded: no HDB street name carries more than three
    abbreviations, so this returns at most a handful of slugs per street.
    """
    tokens = street_tokens(street)
    if not tokens:
        return []

    # One list of alternatives per position, original spelling first.
    choices: list[tuple[str, ...]] = []
    for token in tokens:
        choices.append((token,) + _expansions(token))

    variants: list[list[str]] = [[]]
    for options in choices:
        variants = [prefix + [option] for prefix in variants for option in options]

    seen: dict[str, None] = {}
    for variant in variants:
        seen.setdefault("-".join(variant).lower(), None)
    return list(seen)


def block_variants(block: str) -> list[str]:
    """Spellings of a block number. `669C` and `669c` only differ in case."""
    text = clean(block).replace(" ", "")
    return [text.lower()] if text else []


def aliases_for(block: str, street: str) -> list[str]:
    """Every `block-street` slug that should resolve to this block."""
    out: dict[str, None] = {}
    for b in block_variants(block):
        for s in street_variants(street):
            out.setdefault(f"{b}-{s}", None)
    return list(out)


@dataclass
class BlockIndex:
    """Alias -> block key, plus what to say when a block number is all we got.

    `by_block` is what turns the 266B problem into a question with options
    instead of a coin toss, and `collisions` is how a new dataset introducing
    a genuinely ambiguous alias becomes visible at build time rather than a
    wrong report later.
    """

    by_alias: dict[str, str] = field(default_factory=dict)
    by_block: dict[str, list[str]] = field(default_factory=dict)
    collisions: dict[str, list[str]] = field(default_factory=dict)

    def add(self, key: str, block: str, street: str) -> None:
        for alias in aliases_for(block, street):
            existing = self.by_alias.get(alias)
            if existing is None:
                self.by_alias[alias] = key
            elif existing != key:
                self.collisions.setdefault(alias, [existing]).append(key)
        for b in block_variants(block):
            keys = self.by_block.setdefault(b, [])
            if key not in keys:
                keys.append(key)

    def lookup(self, alias: str) -> str | None:
        if alias in self.collisions:
            return None
        return self.by_alias.get(alias)


def build_index(conn: sqlite3.Connection) -> BlockIndex:
    """Index every block HDB publishes, and every block that has transacted.

    Both, because the two sets differ: property information covers 10,817
    blocks and the resale files mention addresses that are not in it, and a
    link to one of those should resolve and then report thin data rather than
    fail to resolve at all.
    """
    index = BlockIndex()
    for row in conn.execute(
        "SELECT block_key, block, street_name FROM block ORDER BY block_key"
    ):
        index.add(row["block_key"], row["block"], row["street_name"])
    for row in conn.execute(
        "SELECT DISTINCT block_key, block, street_name FROM resale_txn"
        " WHERE block_key NOT IN (SELECT block_key FROM block) ORDER BY block_key"
    ):
        index.add(row["block_key"], row["block"], row["street_name"])
    return index


@dataclass
class LinkParse:
    """What could be read out of the link text, before any lookup."""

    url: str
    portal: str | None
    tokens: list[str]
    block_candidates: list[int]          # positions in `tokens`

    @property
    def recognised_portal(self) -> bool:
        return self.portal in PORTALS.values()


def parse_link(url: str) -> LinkParse:
    """Reduce a pasted link to tokens, and note where a block could be.

    The whole path is tokenised rather than one segment picked out, because
    the two portals disagree about which segment holds the address and a
    third would disagree again. Noise words are dropped by name, and what is
    left is a short list of words with the block number somewhere in it.
    """
    text = (url or "").strip()
    host = ""
    m = re.match(r"^(?:[a-z][a-z0-9+.-]*:)?//([^/?#]+)", text, re.I)
    if m:
        host = m.group(1).lower()
    elif re.match(r"^[\w.-]+\.(?:com|co|sg|net)", text, re.I):
        host = re.split(r"[/?#]", text, maxsplit=1)[0].lower()

    portal = None
    for domain, name in PORTALS.items():
        if host == domain or host.endswith("." + domain):
            portal = name
            break

    # Query strings and fragments carry tracking, not addresses.
    path = re.split(r"[?#]", text, maxsplit=1)[0]
    if host:
        path = path[path.index(host) + len(host):] if host in path else path

    tokens = [t for t in _tokenise(path) if t.lower() not in JUNK_TOKENS]
    # A trailing listing id is noise too, but only at the end: a five-digit
    # number in the middle of a path is some portal's internal id and the
    # block has not been seen yet.
    while tokens and LISTING_ID.match(tokens[-1]):
        tokens.pop()

    positions = [i for i, t in enumerate(tokens) if BLOCK_TOKEN.match(t)]
    return LinkParse(url=text, portal=portal, tokens=tokens, block_candidates=positions)


@dataclass
class Resolution:
    """The answer, and when there isn't one, what to ask for.

    `status` is `resolved`, `ambiguous` or `unresolved`. The site shows
    `reason` verbatim and then asks for the block and street, which is the
    PRD's rule: it never guesses.
    """

    status: str
    block_key: str | None = None
    block: str | None = None
    street_name: str | None = None
    portal: str | None = None
    matched_alias: str | None = None
    candidates: list[str] = field(default_factory=list)
    reason: str | None = None

    @property
    def ok(self) -> bool:
        return self.status == RESOLVED


def _split_key(key: str) -> tuple[str, str]:
    block, _, street = key.partition("|")
    return block, street


def resolve_tokens(
    tokens: list[str], positions: list[int], index: BlockIndex
) -> tuple[list[str], str | None]:
    """Every block this token list could mean, longest street match first.

    Two things vary and both are tried. The block could be any token that
    looks like one, because a slug is not guaranteed to put the address
    first. And the tokens after it could run past the end of the street into
    a listing id or a portal's trailing hash, so tails are dropped one at a
    time — longest first, since a longer exact match is the more specific
    reading of the same link.
    """
    found: list[str] = []
    matched: str | None = None
    for position in positions:
        block = tokens[position].lower()
        tail = [t.lower() for t in tokens[position + 1:]]
        for end in range(len(tail), 0, -1):
            alias = f"{block}-{'-'.join(tail[:end])}"
            key = index.lookup(alias)
            if key and key not in found:
                found.append(key)
                if matched is None:
                    matched = alias
                break                 # this block token is settled
    return found, matched


def resolve_link(url: str, index: BlockIndex) -> Resolution:
    """A pasted link, resolved to one block or refused with a reason."""
    parse = parse_link(url)
    portal = parse.portal

    if not parse.tokens:
        return Resolution(
            UNRESOLVED, portal=portal,
            reason="that link has no address in it — nothing in the path "
                   "looks like a block and a street",
        )

    keys, alias = resolve_tokens(parse.tokens, parse.block_candidates, index)

    if len(keys) == 1:
        block, street = _split_key(keys[0])
        return Resolution(
            RESOLVED, block_key=keys[0], block=block, street_name=street,
            portal=portal, matched_alias=alias,
        )

    if len(keys) > 1:
        return Resolution(
            AMBIGUOUS, portal=portal, candidates=keys,
            reason=(
                "that link reads as more than one block: "
                + ", ".join(f"{b} {s}" for b, s in map(_split_key, keys))
                + ". Which one is it?"
            ),
        )

    # Nothing matched. A bare block number is the common case and deserves a
    # better answer than "no", because 266B really is two different blocks.
    #
    # Only the first number that looks like a block is considered. A listing
    # slug puts the block before its street, so every later one is a street
    # number or part of an id — and without that rule
    # `...-999z-nowhere-street-1` reports on block 1, which is a real block on
    # 28 different streets, instead of admitting the link did not resolve.
    for position in parse.block_candidates[:1]:
        block = parse.tokens[position].lower()
        on_block = index.by_block.get(block) or []
        if on_block:
            streets = sorted({s for _, s in map(_split_key, on_block)})
            listed = ", ".join(streets[:8])
            if len(streets) > 8:
                listed += f", and {len(streets) - 8} other streets"
            return Resolution(
                AMBIGUOUS, portal=portal, candidates=on_block,
                reason=(
                    f"block {block.upper()} exists, but the street in that link "
                    f"does not match any of its spellings. HDB has a block "
                    f"{block.upper()} on: {listed}. Which street is it?"
                    if len(streets) > 1 else
                    f"block {block.upper()} exists — on {listed} — but the "
                    f"street in that link does not match how HDB spells it. "
                    f"Is that the right block?"
                ),
            )

    hint = (
        ""
        if parse.recognised_portal
        else " That is not a PropertyGuru or 99.co link, and other sites do "
             "not put the address in the path."
    )
    return Resolution(
        UNRESOLVED, portal=portal,
        reason=(
            "no HDB block matches that link." + hint
            + " Type the block and street instead."
        ),
    )


def resolve_address(block: str, street: str, index: BlockIndex) -> Resolution:
    """The typed fallback: a block and a street, in any spelling."""
    tokens = [t for t in _tokenise(f"{block} {street}") if t.lower() not in JUNK_TOKENS]
    positions = [i for i, t in enumerate(tokens) if BLOCK_TOKEN.match(t)]
    keys, alias = resolve_tokens(tokens, positions, index)

    if len(keys) == 1:
        blk, st = _split_key(keys[0])
        return Resolution(RESOLVED, block_key=keys[0], block=blk, street_name=st,
                          matched_alias=alias)
    if len(keys) > 1:
        return Resolution(
            AMBIGUOUS, candidates=keys,
            reason="that block and street read as more than one block: "
                   + ", ".join(f"{b} {s}" for b, s in map(_split_key, keys)),
        )

    direct = clean(block), clean(street)
    exact = index.by_alias.get(
        f"{direct[0].replace(' ', '').lower()}-{'-'.join(_tokenise(direct[1])).lower()}"
    )
    if exact:
        blk, st = _split_key(exact)
        return Resolution(RESOLVED, block_key=exact, block=blk, street_name=st)

    return Resolution(
        UNRESOLVED,
        reason=f"HDB has no block {clean(block)} on {clean(street)}. "
               "Check the spelling against the block's own address.",
    )


def key_for(block: str, street: str) -> str:
    """The block key the rest of the app uses, for a resolved pair."""
    return block_key(block, street)
