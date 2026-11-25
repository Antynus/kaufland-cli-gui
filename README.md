# kaufland-cli

Unofficial CLI for Kaufland Card digital receipts, plus a receipts app for PC and Android that also covers Lidl and any Croatian fiscal receipt.

The CLI exports your purchase history as JSON or CSV: item names, quantities, prices and discounts, with no screenshots of the app.

<p>
  <img src="docs/screenshots/desktop-overview.png" width="640" alt="Desktop app, overview dashboard">
  <img src="docs/screenshots/android-overview.png" width="200" alt="Android app, overview dashboard">
</p>

## App for PC and Android

There is also a graphical app in [`gui/`](gui/) built with [Flet](https://flet.dev) (Python on Flutter). It collects receipts from several sources into one place:

| Source | How it connects | What you get |
|---|---|---|
| **Kaufland** | email and password (this package) | every item, price and discount |
| **Lidl Plus** | Lidl's own sign-in page: inside the app on Android, a Chrome/Edge window on PC | every item, price and discount |
| **Any Croatian shop** (Konzum, Studenac, Plodine, SPAR, Müller, Offertissima, IKEA, Decathlon, Shell…) | scan the fiscal QR code with the camera, pick a photo, or paste the QR link | shop, date, time and total |
| **Anything else** | a [store plug-in](gui/PLUGINS.md): one Python file per shop or API | whatever the plug-in returns |

On top of that:

- a dashboard with spending by month, by category and by shop
- shops are grouped by name and given a category (groceries, fuel, home, sport…) that you can change
- a searchable receipt list, and a product view of everything you've bought across Kaufland and Lidl
- CSV and JSON export, and light, dark or system theme
- a side rail on wide windows and a bottom bar on phones, from one codebase
- demo data, so you can try the app without an account

| | | |
|---|---|---|
| <img src="docs/screenshots/desktop-stores.png" width="420"> | <img src="docs/screenshots/desktop-scan.png" width="420"> | <img src="docs/screenshots/android-stores.png" width="180"> |

Fiscal QR codes don't include the shop name or the items, so you pick the shop once and the app suggests it next time. Decathlon and IKEA accounts aren't built in: both sign-in pages are behind Cloudflare bot protection, and their purchase APIs aren't public. [PLUGINS.md](gui/PLUGINS.md) explains how to add them as plug-ins if you record their app traffic.

### Download

The [Build GUI apps](.github/workflows/gui.yml) workflow builds the Android APK and the Windows, Linux and macOS apps. Find them under the run's **Artifacts** in the Actions tab. When you push a `v*` tag, they are also attached to a GitHub release.

The PC builds are portable zips (`kaufland-receipts-windows-portable.zip`, `-linux-`, `-macos-`). They need no installer: unzip anywhere, including a USB stick, and run the `.exe` inside on Windows, the executable on Linux, or the `.app` on macOS. The portable app keeps your sign-ins and receipts in the system's app data folder, not next to the exe.

To install the APK, copy it to your phone, open it, and allow installs from that source. The APK is debug-signed. Android shows a warning because it does not come from the Play Store.

### Run from source

```sh
cd gui
uv run --with "flet[all]==1.1.3" flet run src/main.py
```

### Build locally

`flet build` downloads Flutter, plus the JDK and Android SDK for the APK, on first run.

```sh
cd gui
cp -r ../src/kaufland_cli src/
uv run --with "flet[all]==1.1.3" flet build apk      # or: windows, macos, linux
```

Output lands in `gui/build/<target>/`. The desktop apps need to be built on that OS.

The packaged app keeps its tokens and settings in its own app data folder, not in `~/.config/kaufland-cli`, so you sign in once inside the app.

## Install

```sh
uvx --from git+https://github.com/shinzo13/kaufland-cli kaufland --help
```

Or clone and run `uv run kaufland --help`.

## Use

```sh
kaufland login --email you@example.com   # once; password is prompted
kaufland list -n 10                      # one row per receipt
kaufland items -n 10 --output csv        # one row per purchased item
kaufland receipts --all                  # raw API payloads, full history
kaufland status
kaufland logout
```

## Config

| Variable | Default | Purpose |
|---|---|---|
| `KAUFLAND_COUNTRY` | `PL` | Two-letter country of your Kaufland account |
| `KAUFLAND_UI_LOCALES` | derived from country | Login page locale |
| `KAUFLAND_DATA_DIR` | `$XDG_CONFIG_HOME/kaufland-cli` | Token storage |
| `KAUFLAND_EMAIL` / `KAUFLAND_PASSWORD` | unset | Non-interactive login |
| `KAUFLAND_TIMEOUT_SECONDS` | `30` | HTTP timeout |

## Notes

- Auth is OAuth2 with PKCE against cidaas (`account.kaufland.com`) using the mobile client id. Receipts come from `p.crm-dynamics.schwarz/api/v2/customers/<sub>/transactions`.
- Login runs without a browser. The client accepts only the `com.kaufland.kaufland://` redirect, which no browser can follow, so credentials are posted straight to the cidaas login route.
- Tokens are stored in `tokens.json` with mode `0600` and refresh themselves, so `login` is needed only once. Your password is sent only to `account.kaufland.com`.
- Amounts arrive in minor units. `items` and `list` convert them, `receipts` does not.
- This is an app-facing API that can change without notice. Go easy on `--all`. Only receipts where the card was scanned at checkout exist in the history.
