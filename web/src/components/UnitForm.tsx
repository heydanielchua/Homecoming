/**
 * The four things we type, and the one optional fifth.
 *
 * Everything else on the page is a lookup. These are here because they cannot
 * be looked up: the link carries the block and street, the asking price is
 * known only to the seller, the listing's storey is not published anywhere,
 * and HDB does not publish which way a unit faces.
 *
 * Leaving the floor out is allowed and is not the same as a guess — the view
 * and sun rows then read "unknown", which is what the PRD asks for. The form
 * says so under the field rather than making the floor required.
 */

import { Field } from '@base-ui/react/field';
import { Select } from '@base-ui/react/select';
import { Toggle } from '@base-ui/react/toggle';
import { ToggleGroup } from '@base-ui/react/toggle-group';

import type { Facing } from '../lib/types';
import { FACINGS } from '../lib/types';

export interface Inputs {
  link: string;
  asking: string;
  storey: string;
  facing: Facing | null;
  flatType: string;
  destinationPostcode: string;
}

export const EMPTY_INPUTS: Inputs = {
  link: '',
  asking: '',
  storey: '',
  facing: null,
  flatType: '',
  destinationPostcode: '',
};

interface Props {
  inputs: Inputs;
  onChange: (next: Inputs) => void;
  onSubmit: () => void;
  flatTypes: string[];
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
  flatTypes,
  busy,
  needsAddress,
  onUseAddress,
  block,
  street,
  onAddressChange,
  onAddressSubmit,
}: Props) {
  const set = <K extends keyof Inputs>(key: K, value: Inputs[K]) =>
    onChange({ ...inputs, [key]: value });

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
            type="url"
            inputMode="url"
            placeholder="https://www.99.co/singapore/sale/property/…"
            value={inputs.link}
            onChange={(event) => set('link', event.target.value)}
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

      <div className="row--fields">
        <Field.Root className="field">
          <Field.Label className="label">Asking price</Field.Label>
          <Field.Control
            className="input"
            inputMode="numeric"
            placeholder="620000"
            value={inputs.asking}
            onChange={(event) =>
              set('asking', event.target.value.replace(/[^\d]/g, ''))
            }
          />
          <Field.Description className="hint">
            Only the seller knows it.
          </Field.Description>
        </Field.Root>

        <Field.Root className="field">
          <Field.Label className="label">Floor</Field.Label>
          <Field.Control
            className="input"
            inputMode="numeric"
            placeholder="12"
            value={inputs.storey}
            onChange={(event) =>
              set('storey', event.target.value.replace(/[^\d]/g, ''))
            }
          />
          <Field.Description className="hint">
            Leave it out and the view and sun rows read unknown.
          </Field.Description>
        </Field.Root>

        <div className="field">
          <span className="label" id="facing-label">
            Facing
          </span>
          <ToggleGroup
            aria-labelledby="facing-label"
            value={inputs.facing ? [inputs.facing] : []}
            onValueChange={(value) =>
              set('facing', (value[0] as Facing | undefined) ?? null)
            }
            className="compass"
          >
            {FACINGS.map((point) => (
              <Toggle
                key={point}
                value={point}
                className="compass-point"
                aria-label={point}
              >
                {point}
              </Toggle>
            ))}
          </ToggleGroup>
          <span className="hint">HDB does not publish unit orientation.</span>
        </div>
      </div>

      <div className="row--fields">
        {flatTypes.length > 0 ? (
          <div className="field">
            <Select.Root
              items={flatTypes.map((t) => ({ label: t, value: t }))}
              value={inputs.flatType || null}
              onValueChange={(value) => set('flatType', (value as string) ?? '')}
            >
              <Select.Label className="label">Flat type</Select.Label>
              <Select.Trigger className="input" style={{ textAlign: 'left' }}>
                <Select.Value placeholder="Pick one" />
              </Select.Trigger>
              <Select.Portal>
                <Select.Positioner sideOffset={4}>
                  <Select.Popup className="popup">
                    <Select.List>
                      {flatTypes.map((type) => (
                        <Select.Item
                          key={type}
                          value={type}
                          className="button button--quiet"
                          style={{ width: '100%', justifyContent: 'flex-start' }}
                        >
                          <Select.ItemText>{type}</Select.ItemText>
                        </Select.Item>
                      ))}
                    </Select.List>
                  </Select.Popup>
                </Select.Positioner>
              </Select.Portal>
            </Select.Root>
            <span className="hint">The types that have sold in this block.</span>
          </div>
        ) : null}

        <Field.Root className="field">
          <Field.Label className="label">Destination postcode</Field.Label>
          <Field.Control
            className="input"
            inputMode="numeric"
            placeholder="optional"
            value={inputs.destinationPostcode}
            onChange={(event) =>
              set(
                'destinationPostcode',
                event.target.value.replace(/[^\d]/g, '').slice(0, 6),
              )
            }
          />
          <Field.Description className="hint">
            Optional. Leave it blank and the drive rows stay hidden.
          </Field.Description>
        </Field.Root>
      </div>
    </form>
  );
}
