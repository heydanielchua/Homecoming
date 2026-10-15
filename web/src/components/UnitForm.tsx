/**
 * One field: the link.
 *
 * Everything on this page is a lookup off the block, and the block is in the
 * address in the listing's URL. So that is all this asks for. The page never
 * fetches the listing — scraping the portals is against their terms and is
 * not needed, because the address is in the path.
 *
 * The details that cannot be looked up — the asking price, the floor, the way
 * the unit faces — used to sit under this field, so the first thing the page
 * did was hand you six empty boxes before it had shown you anything. They now
 * live in the sections whose answers they change: the price with the verdict
 * it is judged against, the floor and the facing with the view and the sun
 * they decide. An input next to its own answer explains itself; the same
 * input in a form at the top is a quiz.
 *
 * The block and street fields stay here, because they are not details about a
 * unit — they are the other way of saying which block, for a link that did
 * not resolve.
 */

import { Field } from '@base-ui/react/field';

import type { Facing } from '../lib/types';

export interface Inputs {
  link: string;
  /** Floor area in square metres, as HDB publishes it. */
  size: string;
  asking: string;
  storey: string;
  facing: Facing | null;
  /**
   * Worked out from the size against this block's own sales rather than
   * typed. Carried here so a shared link keeps it when no size came with it.
   */
  flatType: string;
}

export const EMPTY_INPUTS: Inputs = {
  link: '',
  size: '',
  asking: '',
  storey: '',
  facing: null,
  flatType: '',
};

interface Props {
  inputs: Inputs;
  onChange: (next: Inputs) => void;
  onSubmit: () => void;
  busy: boolean;
  /** Set when the link could not be resolved, so the fallback fields show. */
  needsAddress: boolean;
  onUseAddress: () => void;
  block: string;
  street: string;
  onAddressChange: (block: string, street: string) => void;
  onAddressSubmit: () => void;
}

export function UnitForm({
  inputs,
  onChange,
  onSubmit,
  busy,
  needsAddress,
  onUseAddress,
  block,
  street,
  onAddressChange,
  onAddressSubmit,
}: Props) {
  return (
    <form
      className="card stack"
      onSubmit={(event) => {
        event.preventDefault();
        onSubmit();
      }}
    >
      <div className="row--link">
        <Field.Root className="field">
          <Field.Label className="label">Listing link</Field.Label>
          <Field.Control
            className="input"
            // Deliberately not type="url": the parser accepts a link with no
            // scheme, which is what you get copying an address bar on a
            // phone, and native validation would refuse it before the parser
            // ever saw it.
            type="text"
            inputMode="url"
            autoComplete="url"
            spellCheck={false}
            placeholder="https://www.99.co/singapore/sale/property/…"
            value={inputs.link}
            onChange={(event) => onChange({ ...inputs, link: event.target.value })}
          />
          <Field.Description className="hint">
            From PropertyGuru or 99.co. The page is never fetched — only the
            address in the link is read.
          </Field.Description>
        </Field.Root>

        <button
          className="button button--primary"
          type="submit"
          disabled={busy || !inputs.link.trim()}
        >
          {busy ? 'Looking…' : 'Check it'}
        </button>

        {needsAddress ? null : (
          <button
            type="button"
            className="button button--quiet row--link-alt"
            onClick={onUseAddress}
          >
            or by block and street
          </button>
        )}
      </div>

      {needsAddress ? (
        <div className="row--address" role="group" aria-label="Block and street">
          <Field.Root className="field">
            <Field.Label className="label">Block</Field.Label>
            <Field.Control
              className="input"
              placeholder="669C"
              value={block}
              onChange={(event) => onAddressChange(event.target.value, street)}
            />
          </Field.Root>
          <Field.Root className="field field--wide">
            <Field.Label className="label">Street</Field.Label>
            <Field.Control
              className="input"
              placeholder="Jurong West Street 64"
              value={street}
              onChange={(event) => onAddressChange(block, event.target.value)}
            />
            <Field.Description className="hint">
              Either spelling works — HDB writes <em>St</em> and the portals
              write <em>Street</em>.
            </Field.Description>
          </Field.Root>
          <button
            className="button"
            type="button"
            onClick={onAddressSubmit}
            disabled={busy || !block.trim() || !street.trim()}
          >
            Find the block
          </button>
        </div>
      ) : null}
    </form>
  );
}
