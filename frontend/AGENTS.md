# frontend/

Static web UI served by FastAPI. No build step.

## Owns

- `index.html` — single-page layout, Tailwind via CDN, `<template>` blocks for
  the account form and run panel, mounts `/static/app.js` as ES module.
- `app.js` — vanilla JS: account form CRUD, run creation via `POST /api/sync`,
  WebSocket subscriber, progress bar + log pane per account, localStorage
  persistence of form values.

## Work Guidance

- The contract with the backend is the WebSocket payload shape
  (`{type, account_id, data}`). The `handleEvent` switch in `app.js` is the
  only place that should grow when adding new event types.
- Styling is Tailwind via CDN with the custom palette defined in the
  `<script>tailwind.config` block of `index.html`. Reuse existing tokens
  (`bg-panel`, `border-line`, `text-white/70`, etc.) — do not introduce
  raw hex colors.
- Form state persists in `localStorage` under key `imapsync-ui:accounts`.
  Passwords are intentionally NOT persisted (never written to storage).
- The account-form template is in `index.html` (`#tpl-account`). All new
  form fields go there AND in `app.js#readAccounts`.
- The run-panel template is `index.html#tpl-run`. Each account pane is built
  by `app.js#buildAccountProgressPane`. Progress bars are pure CSS
  (`width %` on `.bar`); the script only sets `style.width`.

## Verification

- Open `http://localhost:8000/`. The page should render the dark theme
  and show one empty account form.
- Fill two accounts with garbage hosts; click Start sync. Both panes should
  show `error` exit codes within ~5 s, with the per-folder log expanded
  and `view log` collapsible.
- `localStorage` should retain form values after a reload; passwords
  should be empty.