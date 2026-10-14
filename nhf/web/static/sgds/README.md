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

To update: bump the package, copy the three files over, and check
`app.css` still resolves every `--sgds-` token it names.

Upstream: https://designsystem.tech.gov.sg
