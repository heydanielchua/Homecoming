/**
 * Listing Check.
 *
 * Paste a link from PropertyGuru or 99.co, add the asking price and which
 * floor, and the page reports everything the government knows about that
 * flat. One listing at a time.
 *
 * The whole flow is: resolve the block from the link text, fetch that block's
 * precomputed file, apply the typed price, floor and facing, and show it. The
 * only things kept between visits are the typed inputs — the report itself is
 * rebuilt from today's files every time a saved unit is opened, so a flat
 * saved in January shows June's figures in June.
 */

import * as React from 'react';
import { Tabs } from '@base-ui/react/tabs';
import { Toggle } from '@base-ui/react/toggle';
import { ToggleGroup } from '@base-ui/react/toggle-group';
import { Toast } from '@base-ui/react/toast';
import { Tooltip } from '@base-ui/react/tooltip';

import { ageInDays, fetchBlock, fetchManifest, fetchMeta, fetchShard, fetchTown } from './lib/data';
import { month, titleCase } from './lib/format';
import {
  NO_DETAILS,
  SQFT_PER_SQM,
  extractDetails,
  extractListing,
  isBareLink,
  type ListingDetails,
} from './lib/listing';
import { resolveAddress, resolveLink, type Resolution } from './lib/resolve';
import { describeUnit, loadSaved, remove, storeSaved, upsert } from './lib/saved';
import { readHandoff } from './lib/handoff';
import { readShared, replaceHash, shareLink } from './lib/share';
import { flatTypeForSize, pricePerSqft } from './lib/unit';
import { areaSqft, areaSqm } from './lib/units';
import type {
  BlockReport,
  Facing,
  Manifest,
  Meta,
  SavedUnit,
  TownReport,
} from './lib/types';
import { CompareTable, type LoadedUnit } from './components/CompareTable';
import { EMPTY_INPUTS, UnitForm, type Inputs } from './components/UnitForm';
import { Report } from './components/Report';

/** Past this, a monthly job has not run and the page should say so. */
const STALE_DAYS = 45;

function Masthead({ manifest }: { manifest: Manifest | null }) {
  const age = ageInDays(manifest?.built_at);
  return (
    <header>
      <div className="masthead">
        <h1>Listing Check</h1>
        <p>
          Paste a link, see what the government knows about that flat.
        </p>
      </div>
      {manifest ? (
        <p className="muted">
          Built {age === 0 ? 'today' : age === 1 ? 'yesterday' : `${age} days ago`} from
          sales up to {manifest.as_of.month}.{' '}
          {manifest.counts.reports?.toLocaleString('en-SG')} blocks.
          {age !== null && age > STALE_DAYS ? (
            <>
              {' '}
              <strong>
                That is over {STALE_DAYS} days old — run the monthly job.
              </strong>
            </>
          ) : null}
        </p>
      ) : null}
    </header>
  );
}

/**
 * A number that reads as a figure and edits as a field.
 *
 * Grouped while you are not in it — $1,100,000 is information, 1100000 is a
 * form value — and plain digits the moment it has focus, because separators
 * fight the caret and every keystroke would have to re-place it.
 */
function FigureInput({
  value,
  onChange,
  placeholder,
  group,
  width,
  label,
}: {
  value: string;
  onChange: (next: string) => void;
  placeholder: string;
  group: boolean;
  width: string;
  /**
   * What this figure is, for a screen reader.
   *
   * The visible label sits above as part of the figure's heading, which a
   * sighted reader takes in at a glance and a screen reader does not: the
   * control itself announced as "edit text, blank" and nothing more.
   */
  label: string;
}) {
  const [editing, setEditing] = React.useState(false);
  const shown =
    !editing && group && value ? Number(value).toLocaleString() : value;
  return (
    <input
      className="input"
      inputMode="numeric"
      aria-label={label}
      style={{ width }}
      placeholder={placeholder}
      value={shown}
      onFocus={() => setEditing(true)}
      onBlur={() => setEditing(false)}
      onChange={(e) => onChange(e.target.value.replace(/[^\d.]/g, ''))}
    />
  );
}

/**
 * Which of a block's flat types to show when nothing has said which.
 *
 * A portal link carries an address and an opaque id. It does not carry the
 * flat type, so for a block that has sold more than one the page has to
 * choose, and it was choosing whichever came first in the file. For 107
 * Potong Pasir Avenue 1 that is the 3-room, and the listing somebody had just
 * pasted was a 4-room: a $592,500 median against a $755,000 one, under the
 * right address, with nothing on the page saying which type it was about.
 *
 * The type with the most sales behind it is a better guess — it is the one
 * whose figures mean the most and the one a block is mostly made of — but it
 * is still a guess, which is why it is now shown and can be changed.
 */
export function defaultFlatType(
  types: Record<string, { money: { n_sales: { v: number | null } } }>,
): string {
  let best = '';
  let most = -1;
  for (const [type, section] of Object.entries(types)) {
    const sales = section.money?.n_sales?.v ?? 0;
    if (sales > most) {
      most = sales;
      best = type;
    }
  }
  return best;
}

export function App() {
  const [meta, setMeta] = React.useState<Meta | null>(null);
  const [manifest, setManifest] = React.useState<Manifest | null>(null);
  const [loadError, setLoadError] = React.useState<string | null>(null);

  const [inputs, setInputs] = React.useState<Inputs>(EMPTY_INPUTS);
  // Opened by hand as well as by a failed link: the block-and-street fields
  // were only reachable by pasting something that did not resolve, which
  // makes the second way in a punishment for getting the first one wrong.
  const [addressOpen, setAddressOpen] = React.useState(false);
  const [block, setBlock] = React.useState('');
  const [street, setStreet] = React.useState('');

  const [resolution, setResolution] = React.useState<Resolution | null>(null);
  const [report, setReport] = React.useState<BlockReport | null>(null);
  const [town, setTown] = React.useState<TownReport | null>(null);
  const [busy, setBusy] = React.useState(false);

  const [saved, setSaved] = React.useState<SavedUnit[]>(() => loadSaved());
  const [loaded, setLoaded] = React.useState<LoadedUnit[]>([]);
  const [tab, setTab] = React.useState('report');

  // The shared data, once.
  React.useEffect(() => {
    let live = true;
    void (async () => {
      try {
        const [m, mf] = await Promise.all([fetchMeta(), fetchManifest()]);
        if (!live) return;
        if (!m) {
          setLoadError(
            'The data directory is missing. Run `nhf site --out dist/data` and ' +
              'deploy its output next to this page.',
          );
          return;
        }
        setMeta(m);
        setManifest(mf);
      } catch (error) {
        if (live) setLoadError(String(error));
      }
    })();
    return () => {
      live = false;
    };
  }, []);

  /**
   * The block whose report is on screen, so opening a different one can tell
   * that it is a different one. A ref rather than state because `openSlug`
   * must not be rebuilt every time the report changes.
   */
  const loadedSlug = React.useRef<string | null>(null);

  /**
   * Figures read off a pasted listing, waiting for the block to resolve.
   *
   * The paste arrives before the report does, and the flat type it names only
   * means something once the block's own types are known — a listing saying
   * "4 Room" is worth nothing against a block that has only sold 3-roomers.
   */
  const pastedListing = React.useRef<{
    price: number | null;
    flatType: string | null;
    details: ListingDetails;
  } | null>(null);

  /**
   * The listing's own details table, as it wrote it.
   *
   * Kept separate from everything the report holds, because the two
   * genuinely disagree: HDB's completion year is the block's and the
   * listing's is the project's, HDB's floor area comes from the last
   * comparable sale and the listing's is this flat's. Showing ours where
   * somebody is reading theirs is what makes the page look out of step with
   * the tab it was copied from.
   */
  const [details, setDetails] = React.useState<ListingDetails>(NO_DETAILS);

  /**
   * Whether the asking price in the header was read off a pasted listing.
   *
   * Only the note under the figure changes, but the note is the whole point:
   * a price that came off the listing must not sit under "type what the
   * listing asks", which tells the reader to supply something already there
   * and leaves them unsure whether what they are looking at is the seller's
   * number or a leftover of their own.
   */
  const [askingFromListing, setAskingFromListing] = React.useState(false);

  /**
   * Whether the flat type on screen came from the listing or from us.
   *
   * It decides a line of copy and the line matters: every figure below is for
   * one flat type, and a block that has sold three of them has three different
   * answers. If the page picked the type, it has to say so and offer the
   * others, because the reader is the only one here who can see the listing.
   */
  const [typeFromListing, setTypeFromListing] = React.useState(false);

  const openSlug = React.useCallback(
    async (slug: string) => {
      const payload = await fetchBlock(slug);
      if (!payload) {
        setReport(null);
        setResolution({
          status: 'unresolved',
          reason:
            'That block resolved, but it has no report — nothing has sold in ' +
            'it in the last two years, so there is nothing to compare an ' +
            'asking price with.',
        });
        return null;
      }
      // Moving to another flat clears what was typed about the last one. A
      // size, a price and a floor belong to one unit; carried across they sit
      // under the new block's name looking like its own, and the verdict gets
      // worked out against a price nobody is asking for it. A shared link and
      // a saved unit set their values straight after this, so they are not
      // affected.
      const switching = loadedSlug.current !== null && loadedSlug.current !== slug;
      loadedSlug.current = slug;

      setReport(payload);
      setTown(await fetchTown(payload.town_slug));
      const pasted = pastedListing.current;
      pastedListing.current = null;
      if (pasted?.price != null) setAskingFromListing(true);
      else if (switching) setAskingFromListing(false);
      if (pasted) setDetails(pasted.details);
      else if (switching) setDetails(NO_DETAILS);
      const toldType = Boolean(pasted?.flatType && payload.flat_types[pasted.flatType]);
      if (toldType) setTypeFromListing(true);
      else if (switching || !pasted) setTypeFromListing(false);

      setInputs((current) => ({
        ...current,
        ...(switching
          ? { size: '', asking: '', storey: '', facing: null }
          : null),
        // A price read off the listing describes the flat being opened, so it
        // survives the clearing above rather than being wiped by it.
        ...(pasted?.price != null ? { asking: String(pasted.price) } : null),
        flatType:
          // What the listing called itself, but only if this block has sold
          // one: a type with no sales here has nothing to compare against.
          pasted?.flatType && payload.flat_types[pasted.flatType]
            ? pasted.flatType
            : !switching && current.flatType && payload.flat_types[current.flatType]
              ? current.flatType
              : defaultFlatType(payload.flat_types),
      }));
      return payload;
    },
    [],
  );

  /**
   * A listing handed over by the bookmarklet, in the fragment.
   *
   * Runs before the shared-link reader below, because a hand-off carries no
   * block slug — the bookmarklet is reading a portal page and has no idea
   * what this site calls the block — so the link has to be resolved first,
   * exactly as if it had been pasted and checked.
   */
  React.useEffect(() => {
    if (!meta) return;
    const handed = readHandoff(window.location.hash);
    if (!handed) return;
    void (async () => {
      setBusy(true);
      try {
        const found = extractListing(handed);
        const table = extractDetails(handed);
        if (found.url) setInputs((current) => ({ ...current, link: found.url! }));
        pastedListing.current = {
          price: found.price,
          flatType: found.flatType,
          details: table,
        };
        setDetails(table);
        if (found.price !== null) setAskingFromListing(true);
        if (!found.url) {
          setResolution({
            status: 'unresolved',
            reason:
              'That came from a listing page, but there was no link in it to ' +
              'say which block. Paste the listing link here instead.',
          });
          return;
        }
        const result = await resolveLink(found.url, fetchShard);
        setResolution(result);
        if (result.status === 'resolved' && result.slug) {
          await openSlug(result.slug);
        }
      } finally {
        setBusy(false);
      }
    })();
  }, [meta, openSlug]);

  // A shared link carries a unit's details in the fragment.
  React.useEffect(() => {
    if (!meta) return;
    if (readHandoff(window.location.hash)) return;
    const shared = readShared(window.location.hash);
    if (!shared) return;
    void (async () => {
      setBusy(true);
      const payload = await openSlug(shared.slug);
      if (payload) {
        setResolution({ status: 'resolved', slug: shared.slug });
        setAskingFromListing(false);
        setInputs({
          link: shared.url,
          size: shared.size ? String(shared.size) : '',
          asking: shared.asking ? String(shared.asking) : '',
          storey: shared.storey ? String(shared.storey) : '',
          facing: shared.facing,
          flatType:
            shared.flatType && payload.flat_types[shared.flatType]
              ? shared.flatType
              : defaultFlatType(payload.flat_types),
        });
      }
      setBusy(false);
    })();
  }, [meta, openSlug]);

  async function check() {
    setBusy(true);
    setReport(null);
    try {
      const result = await resolveLink(inputs.link, fetchShard);
      setResolution(result);
      if (result.status === 'resolved' && result.slug) {
        await openSlug(result.slug);
      }
    } finally {
      setBusy(false);
    }
  }

  async function checkAddress() {
    setBusy(true);
    try {
      const result = await resolveAddress(block, street, fetchShard);
      setResolution(result);
      if (result.status === 'resolved' && result.slug) {
        await openSlug(result.slug);
      }
    } finally {
      setBusy(false);
    }
  }

  async function pickCandidate(slug: string) {
    setBusy(true);
    try {
      setResolution({ status: 'resolved', slug });
      await openSlug(slug);
    } finally {
      setBusy(false);
    }
  }

  const asking = inputs.asking ? Number(inputs.asking) : null;
  const storey = inputs.storey ? Number(inputs.storey) : null;
  const size = inputs.size ? Number(inputs.size) : null;

  /**
   * The flat type, worked out from the size against this block's own sales
   * rather than picked from a menu. A size that matches nothing — typed in
   * square feet, or a stray zero — leaves the earlier answer standing rather
   * than silently moving the whole Money section to another type.
   */
  const { flatType } = React.useMemo(() => {
    if (!report) return { flatType: inputs.flatType, matchedFromSize: false };
    const sizes = Object.entries(report.flat_types)
      .map(([name, s]) => ({
        flatType: name,
        medianSqm: s.money?.median_sqm?.v ?? 0,
      }))
      .filter((s) => s.medianSqm > 0);
    const matched = flatTypeForSize(size, sizes);
    if (matched) return { flatType: matched, matchedFromSize: true };
    // Nothing matched, so whatever is used below is a fallback and must not
    // be described as though the size had picked it.
    if (inputs.flatType && report.flat_types[inputs.flatType]) {
      return { flatType: inputs.flatType, matchedFromSize: false };
    }
    return {
      flatType: defaultFlatType(report.flat_types),
      matchedFromSize: false,
    };
  }, [report, size, inputs.flatType]);

  /**
   * What this block's own sales say about a flat of this type.
   *
   * These are the figures the header shows the moment a link resolves, which
   * is the whole point of pasting one: a size and a price without typing
   * anything. They are HDB's, not the listing's — the portals put neither in
   * a URL and this page never fetches the listing — so the header says whose
   * they are rather than passing them off as this unit's.
   */
  const money = report && flatType ? report.flat_types[flatType]?.money : null;
  const typeKeys = report ? Object.keys(report.flat_types) : [];
  const medianSqm = money?.median_sqm?.v ?? null;
  /**
   * The last flat of this type to change hands in this block.
   *
   * The header used to carry the median of two years of sales, which is the
   * right number for working out what a flat is worth and the wrong one to
   * put in front of somebody: a median is an average of a market, and what
   * they want to know is what the last one actually went for. Here that is
   * $670,000 in August against a $720,000 median — the median is the steadier
   * figure and the transaction is the real one, and they are not the same
   * thing.
   *
   * The sales arrive newest first, but the newest is taken by date rather
   * than by position: nothing in the file promises that order, and a header
   * quietly showing the oldest sale would look exactly like one showing the
   * newest.
   */
  const lastTxn = React.useMemo(() => {
    const sales = money?.sales ?? [];
    let newest: (typeof sales)[number] | null = null;
    for (const sale of sales) {
      if (!sale?.month || sale.price == null) continue;
      if (!newest || sale.month > newest.month) newest = sale;
    }
    return newest;
  }, [money]);

  // A size typed into a shared link wins; otherwise the block's own median.
  const shownSqm = size ?? medianSqm;
  // The asking price if there is one, because that is the flat being judged;
  // the last transaction when there is not, so the row is never simply blank
  // and never quotes a figure the card no longer shows.
  const shownPsf = pricePerSqft(asking ?? lastTxn?.price ?? null, shownSqm);

  /**
   * The listing's details table, as rows ready to draw.
   *
   * `ours` is filled only where HDB's answer to the same question differs,
   * so a row carries the disagreement rather than the page quietly picking
   * a side. The labels are the portals' own.
   */
  const detailRows = React.useMemo(() => {
    const rows: { label: string; value: string; ours?: string }[] = [];
    const add = (label: string, value: string | number | null, ours?: string) => {
      if (value === null || value === '') return;
      rows.push({ label, value: String(value), ours });
    };

    add('Property type', details.propertyType);
    add('Model', details.model);
    if (details.floorAreaSqft !== null) {
      const sqm = Math.round(details.floorAreaSqft / SQFT_PER_SQM);
      add(
        'Floor area',
        `${details.floorAreaSqft.toLocaleString()} sqft · ${sqm} sqm`,
        // HDB's figure comes from the last comparable sale in the block, so
        // a flat that is a few square metres off its neighbours is worth
        // saying out loud rather than rounding away.
        shownSqm !== null && Math.abs(sqm - Math.round(shownSqm)) >= 2
          ? `${Math.round(shownSqm)} sqm`
          : undefined,
      );
    }
    add('Price/sqft', details.priceSqft);
    add('Floor Level', details.floorLevel);
    add('No. of bedrooms', details.bedrooms);
    add('No. of bathrooms', details.bathrooms);
    add('Furnishing', details.furnishing);
    add(
      'Built year',
      details.builtYear,
      details.builtYear !== null &&
        report?.year_completed &&
        report.year_completed !== details.builtYear
        ? String(report.year_completed)
        : undefined,
    );
    add('Tenure', details.tenure);
    add('Last updated', details.lastUpdated);
    return rows;
  }, [details, report, shownSqm]);

  const hasDetails = detailRows.length > 0;

  // Keep the carried value in step, so a share link and a saved unit record
  // the type the page is actually showing.
  React.useEffect(() => {
    if (flatType && flatType !== inputs.flatType) {
      setInputs((current) => ({ ...current, flatType }));
    }
  }, [flatType, inputs.flatType]);

  const draft = React.useMemo(() => {
    if (!report) return null;
    return {
      url: inputs.link,
      slug: report.slug,
      blockKey: report.block_key,
      block: report.block,
      street: report.street,
      town: report.town,
      size,
      flatType,
      asking,
      storey,
      facing: inputs.facing as Facing | null,
      note: '',
    };
  }, [report, inputs, asking, storey]);

  // Keep the address bar in step, so a reload or a shared link reopens this
  // exact unit. Replace rather than push: a keystroke is not a page.
  React.useEffect(() => {
    if (draft) replaceHash(draft);
  }, [draft]);

  // Rebuild every saved unit's report from today's files whenever the list
  // changes. This is where "only the inputs are saved" earns its keep.
  React.useEffect(() => {
    if (!meta) return;
    let live = true;
    void (async () => {
      const out: LoadedUnit[] = [];
      for (const unit of saved) {
        const payload = await fetchBlock(unit.slug);
        if (!payload) continue;
        out.push({
          unit,
          report: payload,
          town: await fetchTown(payload.town_slug),
        });
      }
      if (live) setLoaded(out);
    })();
    return () => {
      live = false;
    };
  }, [saved, meta]);

  const toast = Toast.useToastManager();

  function save() {
    if (!draft) return;
    const next = upsert(saved, draft);
    setSaved(next);
    if (storeSaved(next)) {
      toast.add({
        title: 'Saved to this browser',
        description:
          'Only what you typed is kept. The report is rebuilt from the latest ' +
          'data each time you open it.',
      });
    } else {
      toast.add({
        title: 'Could not save',
        description:
          'This browser is not allowing site storage, so the list cannot be ' +
          'kept. Use the share link instead.',
      });
    }
  }

  function removeUnit(id: string) {
    const next = remove(saved, id);
    setSaved(next);
    storeSaved(next);
  }

  async function copyShareLink() {
    if (!draft) return;
    const link = shareLink(draft);
    try {
      await navigator.clipboard.writeText(link);
      toast.add({
        title: 'Link copied',
        description: 'It carries what you typed, not the report.',
      });
    } catch {
      toast.add({ title: 'Copy it from the address bar', description: link });
    }
  }

  /**
   * A listing pasted anywhere on the page.
   *
   * It sits here rather than on the link box because the link box is not
   * where people aim. The instruction is "copy the listing and paste it in",
   * and the answer to "in where?" should be anywhere at all — a paste that
   * lands an inch off target and silently does nothing is the same failure
   * as having no feature.
   *
   * A bare link is the common case and goes through untouched. Anything
   * longer is searched for the figures the URL cannot carry.
   */
  const onPasteAnywhere = React.useCallback((event: React.ClipboardEvent) => {
    const text = event.clipboardData.getData('text');
    if (!text || isBareLink(text)) return;
    const found = extractListing(text);
    if (found.price === null && found.url === null) return;
    event.preventDefault();
    // Held until the block resolves, because the flat type can only be
    // matched against the types this block has actually sold.
    const table = extractDetails(text);
    pastedListing.current = {
      price: found.price,
      flatType: found.flatType,
      details: table,
    };
    setDetails(table);
    if (found.price !== null) setAskingFromListing(true);
    setInputs((current) => ({
      ...current,
      link: found.url ?? current.link,
      ...(found.price !== null ? { asking: String(found.price) } : null),
    }));
  }, []);

  if (loadError) {
    return (
      <main className="page">
        <Masthead manifest={null} />
        <div className="card">
          <p className="error">{loadError}</p>
        </div>
      </main>
    );
  }

  if (!meta) {
    return (
      <main className="page">
        <Masthead manifest={null} />
        <p className="empty">Loading the data…</p>
      </main>
    );
  }


  return (
    <main className="page" onPaste={onPasteAnywhere}>
      <Masthead manifest={manifest} />

      <UnitForm
        inputs={inputs}
        onChange={setInputs}
        onSubmit={check}
        busy={busy}
        needsAddress={
          addressOpen || (resolution !== null && resolution.status !== 'resolved')
        }
        onUseAddress={() => setAddressOpen(true)}
        block={block}
        street={street}
        onAddressChange={(b, s) => {
          setBlock(b);
          setStreet(s);
        }}
        onAddressSubmit={checkAddress}
      />

      {resolution && resolution.status !== 'resolved' ? (
        <div className="card card--tight">
          <p>{resolution.reason}</p>
          {resolution.candidates && resolution.candidates.length > 1 ? (
            <div className="row" style={{ marginTop: 10 }}>
              {resolution.candidates.map((candidate) => (
                <button
                  key={candidate.slug}
                  type="button"
                  className="button"
                  onClick={() => void pickCandidate(candidate.slug)}
                  disabled={!candidate.report}
                >
                  {titleCase(candidate.street)}
                  {candidate.report ? '' : ' (no sales)'}
                </button>
              ))}
            </div>
          ) : null}
        </div>
      ) : null}

      {report ? (
        <>
          <div className="card card--tight spread">
            <div>
              <h2>
                {report.block} {titleCase(report.street)}
              </h2>
              <p className="secondary">
                {titleCase(report.town)}
                {report.year_completed ? ` · built ${report.year_completed}` : ''}
                {report.dwelling_units
                  ? ` · ${report.dwelling_units} flats`
                  : ''}
              </p>

              {/* The flat's own figures, which belong to the listing rather
                  than to the block. Typed, because neither portal puts a
                  price or a floor area in the URL and the page never fetches
                  the listing itself. */}
              {/* What this block's own sales say about a flat of this
                  type, filled in the moment a link resolves. The size and
                  the recent prices are HDB's figures, not the listing's —
                  neither portal puts either in a URL and the page never
                  fetches the listing — so they are labelled as what they
                  are. The asking price is the one thing only the seller
                  knows, so it is the one thing typed. */}
              <div className="unit-figures">
                {/* First, because every figure after it is for this type and
                    only this type. A block that has sold a 3-room and a
                    4-room has two different answers to every question on this
                    page, and a portal link says nothing about which one you
                    are holding. */}
                {typeKeys.length > 0 ? (
                  <div className="unit-figure">
                    <span className="unit-figure-label" id="flat-type-label">
                      Flat type
                    </span>
                    {typeKeys.length === 1 ? (
                      <span className="unit-figure-value">
                        {titleCase(flatType)}
                      </span>
                    ) : (
                      <ToggleGroup
                        className="type-pills"
                        aria-labelledby="flat-type-label"
                        value={[flatType]}
                        multiple={false}
                        onValueChange={(picked) => {
                          const next = picked[picked.length - 1];
                          if (!next || next === flatType) return;
                          setTypeFromListing(false);
                          setInputs((current) => ({
                            ...current,
                            flatType: next,
                            // The size belonged to the old type's sales, not
                            // to this one, and a 4-room's area under a 3-room
                            // heading is how this went wrong to begin with.
                            size: '',
                          }));
                        }}
                      >
                        {typeKeys.map((type) => (
                          <Toggle key={type} value={type}>
                            {titleCase(type)}
                          </Toggle>
                        ))}
                      </ToggleGroup>
                    )}
                    <span className="unit-figure-note">
                      {typeKeys.length === 1
                        ? 'the only type sold in this block'
                        : typeFromListing
                          ? 'read off the listing you pasted'
                          : 'our pick — check it against the listing'}
                    </span>
                  </div>
                ) : null}

                <div className="unit-figure unit-figure--derived">
                  <span className="unit-figure-label">Floor area</span>
                  <span className="unit-figure-value">
                    {areaSqft(shownSqm) ?? '—'}
                  </span>
                  <span className="unit-figure-note">
                    {shownSqm === null
                      ? 'no sales to measure'
                      : `${areaSqm(shownSqm)} · typical ${flatType.toLowerCase()} here`}
                  </span>
                </div>

                <div className="unit-figure unit-figure--derived">
                  <span className="unit-figure-label">Last transaction price</span>
                  <span className="unit-figure-value">
                    {lastTxn ? `$${lastTxn.price.toLocaleString()}` : '—'}
                  </span>
                  <span className="unit-figure-note">
                    {/* The date, and the floor it was on. One sale is not a
                        market, and a price without the storey it sold at is
                        the most misleading number on the page: in this block
                        the same flat type runs $670,000 to $745,000 depending
                        largely on how high up it is. */}
                    {!lastTxn
                      ? 'nothing sold here lately'
                      : `${month(lastTxn.month)}${
                          lastTxn.storey ? ` · floor ${lastTxn.storey.toLowerCase()}` : ''
                        }`}
                  </span>
                </div>

                <label className="unit-figure">
                  <span className="unit-figure-label">Asking</span>
                  <span className="unit-figure-input">
                    <span className="unit-figure-unit">$</span>
                    <FigureInput
                      value={inputs.asking}
                      label="Asking price in dollars"
                      placeholder="—"
                      group
                      width="10ch"
                      onChange={(asking) => {
                        setAskingFromListing(false);
                        setInputs({ ...inputs, asking: asking.replace(/[^\d]/g, '') });
                      }}
                    />
                  </span>
                  <span className="unit-figure-note">
                    {askingFromListing && asking !== null
                      ? 'read off the listing you pasted'
                      : 'paste the listing, or type what it asks'}
                  </span>
                </label>

                <div className="unit-figure unit-figure--derived">
                  <span className="unit-figure-label">Price/sqft</span>
                  <span className="unit-figure-value">
                    {shownPsf !== null
                      ? `$${Math.round(shownPsf).toLocaleString()}`
                      : '—'}
                  </span>
                  <span className="unit-figure-note">
                    {shownPsf === null
                      ? 'needs a size and a price'
                      : asking !== null
                        ? 'from the asking price'
                        : 'from the last transaction'}
                  </span>
                </div>
              </div>

              {/* Said once, where the gap is, and only while it is a gap.
                  Neither portal puts the asking price in the URL — the whole
                  of a 99.co link is an address and an opaque id — and the
                  listing itself is never fetched, so a link alone leaves this
                  one figure blank. The old copy admitted that in a hint under
                  the link box, which is above where anyone is looking by the
                  time they notice the blank, and left them to work out what
                  to do about it. This says what to press. */}
              {/* The listing's own details table, in the listing's own
                  words. Every label here is the one 99.co or PropertyGuru
                  prints, so that reading this card against the tab it came
                  from is a matter of matching rows, not translating. Where
                  the listing and HDB disagree — and on the completion year
                  they often do, HDB recording the block and the portal the
                  project — both are shown and neither is corrected. */}
              {hasDetails ? (
                <dl className="listing-details">
                  {detailRows.map((row) => (
                    <div className="listing-detail" key={row.label}>
                      <dt>{row.label}</dt>
                      <dd>
                        {row.value}
                        {row.ours ? (
                          <span className="listing-detail-ours">
                            HDB says {row.ours}
                          </span>
                        ) : null}
                      </dd>
                    </div>
                  ))}
                </dl>
              ) : null}

              {asking === null ? (
                <p className="ask-paste">
                  <strong>The asking price is not in the link.</strong> No
                  portal puts it there. Go back to the listing, select the
                  whole page — <kbd>⌘A</kbd> or <kbd>Ctrl</kbd>+<kbd>A</kbd>,
                  then <kbd>⌘C</kbd> or <kbd>Ctrl</kbd>+<kbd>C</kbd> — and
                  paste it anywhere on this page. The price gets read off it.
                </p>
              ) : null}
            </div>
            <div className="row">
              <button type="button" className="button" onClick={save}>
                Save this unit
              </button>
              <button
                type="button"
                className="button button--quiet"
                onClick={() => void copyShareLink()}
              >
                Copy share link
              </button>
            </div>
          </div>

          <Tabs.Root
            className="tabs"
            value={tab}
            onValueChange={(value) => setTab(value as string)}
          >
            <Tabs.List className="tablist">
              <Tabs.Tab className="tab" value="report">
                This flat
              </Tabs.Tab>
              <Tabs.Tab className="tab" value="compare">
                Saved ({saved.length})
              </Tabs.Tab>
              <Tabs.Indicator className="tab-indicator" />
            </Tabs.List>

            <Tabs.Panel value="report">
              <Report
                report={report}
                town={town}
                meta={meta}
                flatType={flatType}
                asking={asking}
                storey={storey}
                facing={inputs.facing}
                inputs={inputs}
          onInputs={setInputs}
              />
            </Tabs.Panel>

            <Tabs.Panel value="compare">
              <div className="card">
                <CompareTable units={loaded} meta={meta} onRemove={removeUnit} />
              </div>
            </Tabs.Panel>
          </Tabs.Root>
        </>
      ) : saved.length > 0 ? (
        <div className="card">
          <h2>Saved units</h2>
          <p className="secondary" style={{ margin: '6px 0 12px' }}>
            In this browser only — that is the cost of having no login. A share
            link carries a unit to another device.
          </p>
          <ul className="saved-list">
            {saved.map((unit) => (
              <li className="saved-item" key={unit.id}>
                <div className="saved-item-main">
                  <div className="saved-item-title">{describeUnit(unit)}</div>
                  <div className="muted">
                    {unit.asking
                      ? `${unit.asking.toLocaleString('en-SG', {
                          style: 'currency',
                          currency: 'SGD',
                          maximumFractionDigits: 0,
                        })} asked`
                      : 'no asking price'}
                    {unit.facing ? ` · facing ${unit.facing}` : ''}
                  </div>
                </div>
                <div className="row">
                  <button
                    type="button"
                    className="button button--quiet"
                    onClick={() => {
                      setAskingFromListing(false);
                      setInputs({
                        link: unit.url,
                        size: unit.size ? String(unit.size) : '',
                        asking: unit.asking ? String(unit.asking) : '',
                        storey: unit.storey ? String(unit.storey) : '',
                        facing: unit.facing,
                        flatType: unit.flatType,
                      });
                      void pickCandidate(unit.slug);
                    }}
                  >
                    Open
                  </button>
                  <button
                    type="button"
                    className="button button--quiet button--danger"
                    onClick={() => removeUnit(unit.id)}
                  >
                    Remove
                  </button>
                </div>
              </li>
            ))}
          </ul>
          {loaded.length > 1 ? (
            <div className="table-scroll" style={{ marginTop: 16 }}>
              <CompareTable units={loaded} meta={meta} onRemove={removeUnit} />
            </div>
          ) : null}
        </div>
      ) : null}

      <footer className="card card--tight">
        <details>
          <summary className="secondary">Where this comes from</summary>
          <p className="muted" style={{ marginTop: 8 }}>
            {manifest?.sources}
          </p>
          {manifest?.datasets.length ? (
            <div className="table-scroll">
              <table>
                <thead>
                  <tr>
                    <th scope="col">Dataset</th>
                    <th scope="col">Status</th>
                    <th scope="col" className="num">Rows</th>
                    <th scope="col">Loaded</th>
                  </tr>
                </thead>
                <tbody>
                  {manifest.datasets.map((entry) => (
                    <tr key={entry.dataset}>
                      <th scope="row">{entry.label ?? entry.dataset}</th>
                      <td>{entry.status}</td>
                      <td className="num">
                        {entry.rows?.toLocaleString('en-SG') ?? '—'}
                      </td>
                      <td>{entry.at?.slice(0, 10) ?? '—'}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          ) : null}
          <p className="muted" style={{ marginTop: 8 }}>
            Nothing here is scraped. A listing page is never fetched — only the
            address in the link is read. Saved units stay in this browser.
          </p>
        </details>
      </footer>
    </main>
  );
}

/** The providers Base UI needs for tooltips and toasts. */
export function Root() {
  return (
    <Tooltip.Provider delay={200}>
      <Toast.Provider>
        <App />
        <Toast.Portal>
          <Toast.Viewport className="toast-viewport">
            <ToastList />
          </Toast.Viewport>
        </Toast.Portal>
      </Toast.Provider>
    </Tooltip.Provider>
  );
}

function ToastList() {
  const { toasts } = Toast.useToastManager();
  return toasts.map((item) => (
    <Toast.Root key={item.id} toast={item} className="toast">
      <Toast.Content>
        <Toast.Title className="toast-title" />
        <Toast.Description className="toast-description" />
      </Toast.Content>
    </Toast.Root>
  ));
}
