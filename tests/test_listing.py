"""Resolving a pasted portal link to one HDB block.

The URLs here are the shapes the two portals actually publish, and the
addresses in them are real: 669C JURONG WEST ST 64, 266B on both PUNGGOL WAY
and COMPASSVALE BOW, 12 ST GEORGE'S RD. A test that resolved made-up
addresses would pass against a parser that had stopped matching anything.
"""

from __future__ import annotations

import pytest

from nhf.listing import (
    AMBIGUOUS,
    JUNK_TOKENS,
    RESOLVED,
    UNRESOLVED,
    BlockIndex,
    aliases_for,
    parse_link,
    resolve_address,
    resolve_link,
    street_variants,
)

# Every non-numeric word in the 632 street names HDB publishes for its
# blocks, as of the property information dataset. Street names are public
# and change about never, so this is a fixture rather than a snapshot to
# keep in step; `nhf site` reports an alias collision if it ever drifts.
REAL_STREET_WORDS = set("""
    ADMIRALTY AH AIRPORT ALJUNIED ALKAFF AN ANCHORVALE ANG ARANG AVE AYER
    BAHAGIA BAHRU BAIN BALAM BANDA BANGKIT BARTLEY BATOK BATU BEACH BEDOK
    BENDEMEER BEO BERSEH BIDADARI BISHAN BLANGAH BOON BOW BRIDGE BRIGHT BT
    BUANGKOK BUFFALO CAMBRIDGE CANBERRA CANTONMENT CASHEW CASSIA CHAI
    CHAMPIONS CHANDER CHANGI CHAY CHEE CHENG CHIAT CHIN CHING CHOA CHU CHUAN
    CIRCLE CIRCUIT CL CLARENCE CLEMENTI COAST COMPASSVALE CORPORATION COURSE
    CRAWFORD CRES CROSS CTRL CWEALTH DAKOTA DAMAI DAWSON DELTA DEPOT DORSET
    DOVER DR DUA DUSUN EAST EDGEDALE EDGEFIELD ELIAS EMPRESS ENG EUNOS
    EVERTON FAJAR FARRER FATT FERNVALE FIELD FRENCH GANGSA GDN GDNS GEORGES
    GEYLANG GHIM GLOUCESTER GREEN GUAN HAIG HALT HAVELOCK HENDERSON HILL HO
    HOLLAND HONG HOOD HOON HOUGANG HOY HTS INDUS JELAPANG JELEBU JELLICOE
    JLN JOO JURONG KALLANG KANG KAYU KEAT KELANTAN KEMPAS KENG KENT KG KIM
    KING KIO KITCHENER KLANG KLINIK KRETA KUANG KUKOH LANE LAY LEBAR LENGKOK
    LENGKONG LEW LIAK LIAN LIM LIMAU LING LINK LOH LOMPANG LOOP LOR LOWER
    MACPHERSON MAMOR MARGARET MARINE MARSILING MAUDE MCNAIR MEI MEMBINA
    MERAH MING MINYAK MKT MO MOH MONTREAL MOULMEIN MOUNT NEW NORTHSHORE NTH
    OLD OWEN PAGAR PANDAN PANJANG PARADE PASIR PAYA PAYOH PENDING PETIR PINE
    PING PIPIT PK PL PLAINS PLANTATION PLAZA POH PONG POTONG PUNGGOL PURMEI
    QUEEN QUEENS QUEENSWAY RACE RAJAH RD REDHILL RESERVOIR RING RIS RISE
    RIVERVALE ROWELL RUMAH SAGO SATU SAUJANA SEGAR SELEGIE SEMBAWANG SENG
    SENGKANG SENJA SERAI SERANGOON SHENG SHUNFU SIAK SILAT SIMEI SIMS SIN
    SMITH SOO SPOTTISWOODE SQ ST STH STIRLING STRATHMORE SULTAN SUMANG SWEE
    TAH TAMAN TAMPINES TANGLIN TAO TEBAN TECK TELOK TENAGA TENGAH TENTERAM
    TER TESSENSOHN TG THOMSON TIAN TIGA TINGGI TIONG TOA TOH TOWNER UBI UPP
    VEERASAMY VERNON VIEW VILLAGE VISTA WALK WATERLOO WATT WAY WELLINGTON
    WEST WHAMPOA WHYE WOODLANDS WOODLEIGH YAN YI YISHUN YONG YORK YUAN YUNG
    ZION
""".split())


# A small index standing in for the 10,817 blocks HDB publishes. Every entry
# is a real block on a real street, including the two that share 266B.
BLOCKS = [
    ("669C|JURONG WEST ST 64", "669C", "JURONG WEST ST 64"),
    ("266B|PUNGGOL WAY", "266B", "PUNGGOL WAY"),
    ("266B|COMPASSVALE BOW", "266B", "COMPASSVALE BOW"),
    ("101|ANG MO KIO AVE 3", "101", "ANG MO KIO AVE 3"),
    ("220|BT BATOK EAST AVE 3", "220", "BT BATOK EAST AVE 3"),
    ("12|ST GEORGE'S RD", "12", "ST GEORGE'S RD"),
    ("1|LOR 7 TOA PAYOH", "1", "LOR 7 TOA PAYOH"),
    ("406|TAMPINES ST 41", "406", "TAMPINES ST 41"),
    ("18|C'WEALTH AVE WEST", "18", "C'WEALTH AVE WEST"),
    ("5|BEDOK STH AVE 2", "5", "BEDOK STH AVE 2"),
    ("201|TG PAGAR PLAZA", "201", "TG PAGAR PLAZA"),
    ("519|PASIR RIS ST 13", "519", "PASIR RIS ST 13"),
]


@pytest.fixture()
def index() -> BlockIndex:
    idx = BlockIndex()
    for key, block, street in BLOCKS:
        idx.add(key, block, street)
    return idx


class TestStreetSpelling:
    """The portals write streets out; HDB abbreviates them."""

    def test_both_spellings_of_a_street_are_indexed(self):
        variants = street_variants("JURONG WEST ST 64")
        assert "jurong-west-st-64" in variants        # HDB's own
        assert "jurong-west-street-64" in variants    # the portals'

    def test_a_mixture_of_spellings_is_indexed_too(self):
        """A portal is as likely to half-expand as to expand: the published
        slug for 220 really does read `bukit-batok-east-avenue-3`, but
        `bt-batok-east-avenue-3` has to resolve as well."""
        variants = street_variants("BT BATOK EAST AVE 3")
        for slug in ("bt-batok-east-ave-3", "bukit-batok-east-avenue-3",
                     "bt-batok-east-avenue-3", "bukit-batok-east-ave-3"):
            assert slug in variants

    def test_apostrophes_are_dropped_not_split(self):
        """`C'WEALTH` is one token to expand, and a portal writes
        `commonwealth`; splitting on the apostrophe would lose both."""
        variants = street_variants("C'WEALTH AVE WEST")
        assert "commonwealth-avenue-west" in variants
        assert "cwealth-ave-west" in variants

    def test_st_is_indexed_as_both_street_and_saint(self):
        """ST is STREET in 166 of HDB's street names and SAINT in two, and
        the position does not settle it — UPP CROSS ST ends with the STREET
        sense. Both spellings are written out; the unused one is never asked
        for."""
        variants = street_variants("ST GEORGE'S RD")
        assert "saint-georges-road" in variants
        assert "st-georges-rd" in variants

    def test_ris_is_never_expanded(self):
        """RIS appears in 15 street names and every one is PASIR RIS. Reading
        it as an abbreviation of RISE would mint a street nobody lives on."""
        variants = street_variants("PASIR RIS ST 13")
        assert "pasir-ris-st-13" in variants
        assert not any("rise" in v for v in variants)

    def test_an_abbreviation_only_adds_aliases(self):
        """The HDB spelling is always present, whatever the expansion did."""
        for _key, block, street in BLOCKS:
            slugs = aliases_for(block, street)
            hdb_form = (
                block.lower() + "-"
                + "-".join(street.replace("'", "").lower().split())
            )
            assert hdb_form in slugs, street


class TestRealPortalLinks:
    def test_a_99co_link_resolves(self, index):
        r = resolve_link(
            "https://www.99.co/singapore/sale/property/"
            "669c-jurong-west-street-64-hdb-eSeF", index)
        assert r.status == RESOLVED
        assert r.block_key == "669C|JURONG WEST ST 64"
        assert r.portal == "99.co"

    def test_a_propertyguru_link_resolves(self, index):
        r = resolve_link(
            "https://www.propertyguru.com.sg/listing/"
            "hdb-for-sale-266b-punggol-way-25099211", index)
        assert r.status == RESOLVED
        assert r.block_key == "266B|PUNGGOL WAY"
        assert r.portal == "PropertyGuru"

    def test_the_street_decides_between_two_blocks_with_one_number(self, index):
        """266B is both COMPASSVALE BOW and PUNGGOL WAY, so the street is
        load-bearing and each link has to reach its own block."""
        punggol = resolve_link(
            "https://www.propertyguru.com.sg/listing/"
            "hdb-for-sale-266b-punggol-way-25099211", index)
        compassvale = resolve_link(
            "https://www.propertyguru.com.sg/listing/"
            "hdb-for-sale-266b-compassvale-bow-25099211", index)
        assert punggol.block_key == "266B|PUNGGOL WAY"
        assert compassvale.block_key == "266B|COMPASSVALE BOW"

    def test_a_trailing_hash_does_not_stop_a_match(self, index):
        """99.co appends a short mixed-case id that is not a number, so it
        cannot be stripped by shape — the tail is dropped a token at a time
        until the street matches."""
        r = resolve_link(
            "https://www.99.co/singapore/sale/property/"
            "101-ang-mo-kio-avenue-3-hdb-xK9q", index)
        assert r.block_key == "101|ANG MO KIO AVE 3"

    def test_a_street_number_is_not_mistaken_for_a_listing_id(self, index):
        """JURONG WEST ST 64 ends in a number and TAMPINES ST 41 does too.
        Only five digits or more is an id."""
        assert resolve_link(
            "https://www.propertyguru.com.sg/listing/"
            "hdb-for-sale-406-tampines-street-41-25099211", index
        ).block_key == "406|TAMPINES ST 41"

    def test_a_number_inside_the_street_name_survives(self, index):
        """LOR 7 TOA PAYOH puts a number in the middle, and block 1 in
        front of it."""
        r = resolve_link(
            "https://www.99.co/singapore/rent/property/"
            "1-lorong-7-toa-payoh-hdb-q9", index)
        assert r.block_key == "1|LOR 7 TOA PAYOH"

    def test_query_strings_are_ignored(self, index):
        r = resolve_link(
            "https://www.99.co/singapore/sale/property/"
            "669c-jurong-west-street-64-hdb-eSeF?utm_source=whatsapp#photos",
            index)
        assert r.block_key == "669C|JURONG WEST ST 64"

    def test_a_link_without_a_scheme_still_resolves(self, index):
        """Pasted from a phone, a link often arrives without the https://."""
        r = resolve_link(
            "propertyguru.com.sg/listing/hdb-for-sale-266b-punggol-way-1", index)
        assert r.block_key == "266B|PUNGGOL WAY"


class TestItNeverGuesses:
    def test_a_bare_block_number_asks_which_street(self, index):
        """The PRD's own example: 266B on its own is a question."""
        r = resolve_link(
            "https://www.propertyguru.com.sg/listing/"
            "hdb-for-sale-266b-25099211", index)
        assert r.status == AMBIGUOUS
        assert set(r.candidates) == {"266B|PUNGGOL WAY", "266B|COMPASSVALE BOW"}
        assert "PUNGGOL WAY" in r.reason and "COMPASSVALE BOW" in r.reason

    def test_an_unknown_block_is_refused_not_approximated(self, index):
        r = resolve_link(
            "https://www.propertyguru.com.sg/listing/"
            "hdb-for-sale-999z-nowhere-street-1", index)
        assert r.status == UNRESOLVED
        assert r.block_key is None
        assert "no HDB block matches" in r.reason

    def test_a_trailing_number_is_not_read_as_the_block(self, index):
        """`...-999z-nowhere-street-1` must not report on block 1 — which is a
        real block on LOR 7 TOA PAYOH — just because 1 is in the path."""
        r = resolve_link(
            "https://www.propertyguru.com.sg/listing/"
            "hdb-for-sale-999z-nowhere-street-1", index)
        assert "LOR 7 TOA PAYOH" not in (r.reason or "")

    def test_a_link_from_another_site_says_so(self, index):
        r = resolve_link("https://example.com/some/listing/page", index)
        assert r.status == UNRESOLVED
        assert "not a PropertyGuru or 99.co link" in r.reason

    def test_an_empty_link_is_not_an_error(self, index):
        r = resolve_link("", index)
        assert r.status == UNRESOLVED
        assert r.reason

    def test_a_colliding_alias_resolves_to_nothing(self):
        """If two blocks ever claimed one spelling, the lookup must refuse it
        rather than return whichever was indexed first."""
        idx = BlockIndex()
        idx.add("1|SOME RD", "1", "SOME RD")
        idx.add("1|SOME ROAD", "1", "SOME ROAD")      # same slug, two keys
        assert idx.collisions
        assert idx.lookup("1-some-road") is None


class TestTypedFallback:
    """When the link cannot be read, the site asks for the block and street."""

    def test_the_portal_spelling_is_accepted(self, index):
        r = resolve_address("669C", "jurong west street 64", index)
        assert r.block_key == "669C|JURONG WEST ST 64"

    def test_hdbs_own_spelling_is_accepted(self, index):
        r = resolve_address("669c", "JURONG WEST ST 64", index)
        assert r.block_key == "669C|JURONG WEST ST 64"

    def test_an_unknown_address_is_refused(self, index):
        r = resolve_address("999", "nowhere rd", index)
        assert r.status == UNRESOLVED
        assert "no block 999" in r.reason


class TestPathNoise:
    def test_no_junk_token_is_a_real_street_word(self):
        """The one way this file could silently break resolution: dropping
        `west` or `park` as noise would unname a few hundred blocks."""
        collisions = {t.upper() for t in JUNK_TOKENS} & REAL_STREET_WORDS
        assert not collisions, f"these are real street words: {sorted(collisions)}"

    def test_the_block_is_found_wherever_the_path_puts_it(self, index):
        parse = parse_link(
            "https://www.99.co/singapore/sale/property/"
            "669c-jurong-west-street-64-hdb-eSeF")
        assert parse.tokens[0] == "669C"
        assert 0 in parse.block_candidates
        assert "SINGAPORE" not in parse.tokens      # dropped as noise
