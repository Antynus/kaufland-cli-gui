# slip-invoice-checker

Unofficial CLI that collects your shopping receipts in one place and checks them. It pulls the full purchase history from the Kaufland and Lidl Plus apps (item names, quantities, prices, discounts) and keeps a ledger of Croatian fiscal slips from any other shop, each linked to the Tax Administration's receipt checker.

Formerly `kaufland-cli`. The `kaufland` command still works as an alias for `slip kaufland`.

## Install

```sh
uvx --from git+https://github.com/antynus/slip-invoice-checker slip --help
```

Or clone and run `uv run slip --help`.

## Stores

`slip stores` prints this table.

| Store | Key | How receipts get in |
|---|---|---|
| Kaufland | `kaufland` | App API: full history with line items |
| Lidl Plus | `lidl` | App API: full history with line items |
| SPAR, Plodine, Studenac, IKEA, Decathlon, Shell, INA, Multiplus Card, Offertisima | `spar`, `plodine`, `studenac`, `ikea`, `decathlon`, `shell`, `ina`, `multiplus`, `offertisima` | Fiscal slip from the receipt's QR code |
| Anything else in Croatia | `other` | Fiscal slip from the receipt's QR code |

None of the fiscal-only stores has a known receipts API yet. See [Adding a store](#adding-a-store).

## Loyalty app receipts

```sh
slip kaufland login --email you@example.com   # once; password is prompted
slip kaufland list -n 10                      # one row per receipt
slip kaufland items -n 10 --output csv        # one row per purchased item
slip kaufland receipts --all                  # raw API payloads, full history

slip lidl login                               # once; paste a refresh token (see below)
slip lidl items --all --output csv

slip <store> status
slip <store> logout
```

`list` and `items` share the same columns for every store, starting with `chain`, so CSV exports from different stores can be concatenated.

### Getting a Lidl Plus refresh token

Lidl's login page is protected by reCAPTCHA, so `slip` does not sign in with your password. Get a refresh token once with the community tool [lidl-plus](https://github.com/Andre0512/lidl-plus). It opens a real browser where you complete the login:

```sh
pip install "lidl-plus[auth]"
lidl-plus auth
```

Then run `slip lidl login` and paste the token, or set `LIDL_REFRESH_TOKEN`. From then on `slip` renews the token itself.

## Fiscal slips (any Croatian shop)

Every fiscalised receipt in Croatia has a QR code that links to `porezna.gov.hr/rn` with the receipt's JIR (or ZKI), issue time and total. Scan it with your phone's camera, copy the link and add it:

```sh
slip add spar 'https://porezna.gov.hr/rn?jir=...&datv=20251120_1815&izn=23,99'
slip add ina --zki 0123...cdef --date 2025-11-21 --time 07:42 --amount 55,10   # typed off the paper
slip slips --output csv          # everything saved, newest first
slip slips --store plodine
slip verify 0a1b2c3d --open      # opens the Tax Administration check in your browser
slip remove 0a1b2c3d
```

Amounts in the QR code are read the same way the official page reads them: `23,99` or `23.99` are euros, and `2399` with no separator is cents. Slips issued before 2023 are marked HRK.

The official checker asks for a reCAPTCHA on every lookup, so `slip verify` gives you the link instead of checking automatically. A slip that is registered shows as confirmed on that page.

## Config

| Variable | Default | Purpose |
|---|---|---|
| `SLIP_DATA_DIR` | `$XDG_CONFIG_HOME/slip-checker` | Tokens (one folder per store) and `slips.json` |
| `SLIP_TIMEOUT_SECONDS` | `30` | HTTP timeout (`KAUFLAND_TIMEOUT_SECONDS` also works) |
| `KAUFLAND_COUNTRY` | `PL` | Two-letter country of your Kaufland account (set `HR` for Croatia) |
| `KAUFLAND_UI_LOCALES` | derived from country | Kaufland login page locale |
| `KAUFLAND_DATA_DIR` | `$SLIP_DATA_DIR/kaufland` | Folder holding Kaufland `tokens.json` |
| `KAUFLAND_EMAIL` / `KAUFLAND_PASSWORD` | unset | Non-interactive Kaufland login |
| `LIDL_COUNTRY` | `HR` | Two-letter country of your Lidl Plus account |
| `LIDL_LANGUAGE` | country in lower case | `Accept-Language` sent to Lidl |
| `LIDL_REFRESH_TOKEN` | unset | Non-interactive Lidl login |
| `LIDL_APP_VERSION` | `999.99.9` | App version header, in case Lidl starts rejecting it |

Kaufland tokens saved by `kaufland-cli` in `~/.config/kaufland-cli/` move to the new folder the first time they are needed.

## Adding a store

Stores live in `src/slip_checker/stores.py`. To pull a store's history from its app, write a provider in `src/slip_checker/providers/` that subclasses `ApiProvider` (login, status, logout, fetch, summaries, line_items), build rows with `summary_row`/`item_row`, and pass an instance to the store's `Store(...)` entry. The CLI picks it up automatically. `providers/lidl.py` is a compact example.

The usual way to find an app's endpoints is to capture its traffic with HTTP Toolkit or mitmproxy while you open the receipts screen in the app.

## Notes

- Kaufland: OAuth2 with PKCE against cidaas (`account.kaufland.com`) using the mobile client id, run headlessly. Receipts come from `p.crm-dynamics.schwarz/api/v2/customers/<sub>/transactions`. Amounts arrive in minor units, and `items` and `list` convert them while `receipts` does not.
- Lidl Plus: tokens from `accounts.lidl.com/connect/token`, receipts from `tickets.lidlplus.com/api/v2/<country>/tickets`. `items` and `receipts` fetch each receipt's details (one extra request each), while `list` does not.
- Tokens and slips are stored with mode `0600`. Passwords and tokens are only sent to the store's own login servers.
- These are app-facing APIs that can change without notice. Go easy on `--all`.

## Tests

```sh
uv run python -m unittest discover -s tests
```
