# Singapore Government Design System — design tokens

Vendored from `@govtechsg/sgds-web-component@3.29.0` (MIT), files
`themes/root.css`, `themes/day.css` and `themes/night.css`, unmodified.

Only the tokens are taken. The components in that package are Lit custom
elements, which would mean shipping a JavaScript runtime and rewriting every
template; the tokens are plain CSS custom properties, so `app.css` consumes
them and the markup is untouched.

They are vendored rather than loaded from a CDN because the app is meant to
work in a lift lobby with no signal — a stylesheet fetched over the network
is a stylesheet that is missing exactly then. The static export copies
`static/` wholesale, so these ship with the published copy too.

To update: bump the package and copy `themes/day.css`, `themes/night.css`
and everything they `@import`, transitively — `root.css` pulls in
`responsive.css`, which is easy to miss by copying a list of names. The
tests assert that no `@import` under `static/` dangles and that every
`--sgds-` token `app.css` names resolves.

Upstream: https://designsystem.tech.gov.sg

## Version skew

The components on `master` reference semantic type tokens
(`--sgds-font-size-subtitle-md`, `--sgds-line-height-xs`) that the released
3.29.0 themes do not define — those ship the numeric scale only
(`--sgds-font-size-14`, `--sgds-line-height-20`). Where `app.css` follows a
component's spec it uses the numeric names, because `var()` falls back
silently and a token that resolves to nothing means the design system is not
actually in effect for that property. A test fails on any `--sgds-` token
`app.css` names that the vendored files do not define, so a future bump will
say when the semantic names become available.
