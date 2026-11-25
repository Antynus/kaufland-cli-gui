# Store plug-ins

The app has three ways to get receipts:

1. **Built-in accounts:** Kaufland (email and password) and Lidl Plus (browser sign-in).
2. **Fiscal QR codes:** any Croatian receipt, from any shop. The shop name you type is remembered, and the shop gets a category.
3. **Plug-ins:** a Python file that teaches the app another shop's online account or API.

## Installing a plug-in

Stores › Plug-ins › **Install plug-in**, then choose the `.py` file. It is copied to the app's `plugins` folder (the path is shown on the same screen) and loaded straight away. If it fails to load, the error is shown there and the app keeps working.

A plug-in runs with the same access as the app, including your saved sign-ins, so only install files you trust.

## Writing one

A plug-in is one file that defines `STORE`, an instance of a `Store` subclass:

```python
from kaufland_gui.stores.base import Store, StoreError, amount


class MyShop(Store):
    id = "myshop"  # unique, lowercase
    name = "My Shop"
    description = "Digital receipts from My Shop"
    color = "#0EA5E9"
    auth = "token"  # "password", "browser", "token" or "none"

    def connected(self): ...
    def set_token(self, token): ...  # auth == "token"
    def fetch(self, limit, *, cached, progress=None):
        return [...]  # normalized receipts


STORE = MyShop()
```

What to implement for each sign-in method:

| `auth` | The app shows | You implement |
|---|---|---|
| `password` | an email/password form, plus a one-time-code dialog if `otp()` is called | `login(email, password, *, otp=None)`, `connected()`, `logout()` |
| `browser` | the shop's sign-in page: in-app on Android, a Chrome/Edge window on PC | `login_url()`, `callback_prefix()`, `complete_login(callback_url)` |
| `token` | a field to paste an API key or token | `set_token(token)`, `connected()`, `logout()` |
| `none` | nothing; the store is always on | only `fetch` |

`fetch(limit, cached=..., progress=...)` returns receipts newest first. `limit` is the number to fetch, and `0` means everything. `cached` is the last result, so you can skip downloading receipts you already have. Call `progress("text")` to show status in the header.

Each receipt is a plain dict; the full format is documented at the top of [`stores/base.py`](src/kaufland_gui/stores/base.py). The minimum is:

```python
{
    "uid": "myshop:123",
    "source": "myshop",
    "id": "123",
    "merchant": "My Shop",
    "timestamp": "2026-11-20T17:03:00+01:00",
    "currency": "EUR",
    "total": 23.45,
    "items": [],
}
```

Use `self.read_state(name)`, `self.write_state(name, value, private=True)` and `self.delete_state(name)` for tokens and settings: each store gets its own folder, and `private=True` makes the file readable only by you. Raise `StoreError("message")` for anything the user should see.

[`examples/json_api_store.py`](examples/json_api_store.py) is a complete plug-in for an HTTP API that returns JSON, ready to adapt.

## Finding a shop's API

Most shop apps talk to a private API. To see it, record the app's traffic while you browse your purchase history, for example with [HTTP Toolkit](https://httptoolkit.com) on Android. Look for the request that returns your receipts and note its URL, its headers (especially `Authorization`) and how the token is obtained. These APIs are unofficial and can change without notice, and using them may be against the shop's terms.

## Why Decathlon and IKEA aren't built in

Both sign-in pages sit behind Cloudflare bot protection, and their purchase-history APIs aren't documented anywhere public. A built-in integration couldn't be tested without an account. Their Croatian receipts carry the fiscal QR code, so scanning covers them today. If you record the app traffic as described above, a plug-in is the way to add them.
