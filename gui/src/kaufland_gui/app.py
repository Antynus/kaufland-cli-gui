"""Flet user interface for kaufland-cli. Runs on Windows, macOS, Linux and Android."""

from __future__ import annotations

import asyncio
from collections.abc import Callable

import flet as ft

from kaufland_cli import auth, receipts
from kaufland_cli.config import KauflandError, token_path

from . import data

SEED = "#7C3AED"
WIDE = 840  # px; above this the side rail replaces the bottom bar
DESKTOP = {ft.PagePlatform.WINDOWS, ft.PagePlatform.MACOS, ft.PagePlatform.LINUX}

NAV = [
    ("Overview", ft.Icons.SPACE_DASHBOARD_OUTLINED, ft.Icons.SPACE_DASHBOARD),
    ("Receipts", ft.Icons.RECEIPT_LONG_OUTLINED, ft.Icons.RECEIPT_LONG),
    ("Products", ft.Icons.SHOPPING_BASKET_OUTLINED, ft.Icons.SHOPPING_BASKET),
    ("Settings", ft.Icons.SETTINGS_OUTLINED, ft.Icons.SETTINGS),
]
LIMITS = [("20", 20), ("50", 50), ("100", 100), ("250", 250), ("Full history", 0)]


def muted(text: str, size: int = 13, **kw) -> ft.Text:
    return ft.Text(text, size=size, color=ft.Colors.ON_SURFACE_VARIANT, **kw)


def surface(content: ft.Control, *, padding: int = 20, **kw) -> ft.Container:
    return ft.Container(
        content=content,
        padding=padding,
        border_radius=20,
        bgcolor=ft.Colors.SURFACE_CONTAINER_LOW,
        border=ft.Border.all(1, ft.Colors.OUTLINE_VARIANT),
        **kw,
    )


def theme(dark: bool = False) -> ft.Theme:
    return ft.Theme(
        color_scheme_seed=SEED,
        color_scheme=ft.ColorScheme(primary=SEED) if not dark else ft.ColorScheme(primary="#C4B5FD"),
        use_material3=True,
        visual_density=ft.VisualDensity.COMFORTABLE,
    )


class App:
    def __init__(self, page: ft.Page):
        self.page = page
        self.settings = data.Settings.load()
        self.receipts: list[dict] = data.load_cache()
        self.demo = False
        self.busy = False
        self.tab = 0
        self.wide: bool | None = None
        self.receipt_query = ""
        self.product_query = ""
        self.file_picker = ft.FilePicker()
        self.clipboard = ft.Clipboard()

        page.title = "Kaufland Receipts"
        page.theme = theme()
        page.dark_theme = theme(dark=True)
        page.theme_mode = {"light": ft.ThemeMode.LIGHT, "dark": ft.ThemeMode.DARK}.get(
            self.settings.theme, ft.ThemeMode.SYSTEM
        )
        page.padding = 0
        page.spacing = 0
        page.on_resize = self._on_resize
        if page.platform in DESKTOP and getattr(page, "window", None) is not None:
            page.window.min_width, page.window.min_height = 380, 600
            page.window.width, page.window.height = 1180, 800

    # ---------- lifecycle ----------

    def start(self) -> None:
        if token_path().exists():
            self.show_shell()
            if not self.receipts:
                self.page.run_task(self.refresh)
        else:
            self.show_login()

    def _on_resize(self, _e) -> None:
        if self.wide is not None and self.is_wide() != self.wide:
            self.show_shell()

    def is_wide(self) -> bool:
        return (self.page.width or 0) >= WIDE

    def toast(self, message: str, *, error: bool = False) -> None:
        self.page.show_dialog(
            ft.SnackBar(
                ft.Text(message, color=ft.Colors.ON_ERROR_CONTAINER if error else None),
                bgcolor=ft.Colors.ERROR_CONTAINER if error else None,
                behavior=ft.SnackBarBehavior.FLOATING,
                show_close_icon=True,
                duration=ft.Duration(seconds=6 if error else 3),
            )
        )

    # ---------- login ----------

    def show_login(self) -> None:
        self.wide = None
        email = ft.TextField(
            label="Email",
            prefix_icon=ft.Icons.ALTERNATE_EMAIL,
            keyboard_type=ft.KeyboardType.EMAIL,
            autofill_hints=[ft.AutofillHint.EMAIL],
            autofocus=True,
            border_radius=14,
        )
        password = ft.TextField(
            label="Password",
            prefix_icon=ft.Icons.LOCK_OUTLINE,
            password=True,
            can_reveal_password=True,
            autofill_hints=[ft.AutofillHint.PASSWORD],
            border_radius=14,
        )
        country = self.country_dropdown()
        progress = ft.ProgressBar(visible=False, border_radius=4)
        button = ft.FilledButton(
            "Sign in",
            icon=ft.Icons.LOGIN,
            height=50,
            expand=True,
            style=ft.ButtonStyle(shape=ft.RoundedRectangleBorder(radius=14)),
        )

        async def submit(_e=None):
            if not email.value or not password.value:
                self.toast("Enter your Kaufland Card email and password.", error=True)
                return
            self.settings.country = country.value or "PL"
            self.settings.save()
            for c in (email, password, country, button):
                c.disabled = True
            progress.visible = True
            self.page.update()
            loop = asyncio.get_running_loop()

            def otp() -> str:
                return asyncio.run_coroutine_threadsafe(self.ask_otp(), loop).result(timeout=600)

            try:
                info = await asyncio.to_thread(auth.login, email.value.strip(), password.value, otp=otp)
            except KauflandError as exc:
                self.toast(str(exc), error=True)
            except Exception as exc:  # noqa: BLE001 - surface anything unexpected instead of crashing the UI
                self.toast(f"Unexpected error: {exc}", error=True)
            else:
                self.demo = False
                self.toast(f"Signed in as {info.get('email') or info.get('sub')}")
                self.show_shell()
                await self.refresh()
                return
            for c in (email, password, country, button):
                c.disabled = False
            progress.visible = False
            self.page.update()

        def demo(_e):
            self.demo = True
            self.receipts = data.demo_receipts()
            self.show_shell()
            self.toast("Demo mode: synthetic receipts, nothing is sent anywhere.")

        button.on_click = submit
        password.on_submit = submit
        email.on_submit = lambda _e: password.focus()

        card = ft.Container(
            width=440,
            padding=32,
            border_radius=28,
            bgcolor=ft.Colors.SURFACE,
            shadow=ft.BoxShadow(
                blur_radius=40, spread_radius=-8, color=ft.Colors.with_opacity(0.25, "#000000")
            ),
            content=ft.Column(
                tight=True,
                spacing=16,
                horizontal_alignment=ft.CrossAxisAlignment.STRETCH,
                controls=[
                    ft.Row([self.logo(56)]),
                    ft.Text("Welcome back", size=28, weight=ft.FontWeight.W_700),
                    muted(
                        "Sign in with your Kaufland Card account to browse and export your digital receipts."
                    ),
                    ft.Container(height=4),
                    country,
                    email,
                    password,
                    progress,
                    ft.Row([button]),
                    ft.Row(
                        [ft.TextButton("Explore with demo data", icon=ft.Icons.AUTO_AWESOME, on_click=demo)],
                        alignment=ft.MainAxisAlignment.CENTER,
                    ),
                    ft.Row(
                        [
                            ft.Icon(ft.Icons.SHIELD_OUTLINED, size=16, color=ft.Colors.ON_SURFACE_VARIANT),
                            muted("Your password goes only to account.kaufland.com.", size=12, expand=True),
                        ],
                        spacing=8,
                    ),
                ],
            ),
        )
        self.page.controls.clear()
        self.page.add(
            ft.Container(
                expand=True,
                gradient=ft.LinearGradient(
                    begin=ft.Alignment.TOP_LEFT,
                    end=ft.Alignment.BOTTOM_RIGHT,
                    colors=["#4C1D95", "#7C3AED", "#DB2777"],
                ),
                alignment=ft.Alignment.CENTER,
                padding=16,
                content=ft.SafeArea(
                    ft.Column(
                        [card],
                        scroll=ft.ScrollMode.AUTO,
                        horizontal_alignment=ft.CrossAxisAlignment.CENTER,
                        alignment=ft.MainAxisAlignment.CENTER,
                    )
                ),
            )
        )
        self.page.update()

    async def ask_otp(self) -> str:
        future: asyncio.Future[str] = asyncio.get_running_loop().create_future()
        field = ft.TextField(label="One-time code", keyboard_type=ft.KeyboardType.NUMBER, autofocus=True)

        def done(_e=None):
            if not future.done():
                future.set_result(field.value or "")
            self.page.pop_dialog()

        field.on_submit = done
        self.page.show_dialog(
            ft.AlertDialog(
                modal=True,
                icon=ft.Icon(ft.Icons.MARK_EMAIL_UNREAD_OUTLINED),
                title=ft.Text("Check your inbox"),
                content=ft.Column([muted("Kaufland sent you a verification code."), field], tight=True),
                actions=[ft.FilledButton("Verify", on_click=done)],
            )
        )
        return await future

    # ---------- shell ----------

    def show_shell(self) -> None:
        self.wide = self.is_wide()
        self.body = ft.Container(expand=True)
        self.render_tab()

        def change(e):
            self.tab = int(e.control.selected_index)
            self.render_tab()
            self.page.update()

        destinations = NAV
        if self.wide:
            nav = ft.NavigationRail(
                selected_index=self.tab,
                label_type=ft.NavigationRailLabelType.ALL,
                min_width=96,
                group_alignment=-0.85,
                leading=ft.Container(self.logo(44), padding=ft.Padding.only(top=20, bottom=12)),
                destinations=[
                    ft.NavigationRailDestination(icon=i, selected_icon=s, label=t) for t, i, s in destinations
                ],
                on_change=change,
                bgcolor=ft.Colors.SURFACE_CONTAINER_LOW,
            )
            layout = ft.Row([nav, ft.VerticalDivider(width=1), self.body], expand=True, spacing=0)
            self.page.navigation_bar = None
        else:
            layout = self.body
            self.page.navigation_bar = ft.NavigationBar(
                selected_index=self.tab,
                destinations=[
                    ft.NavigationBarDestination(icon=i, selected_icon=s, label=t) for t, i, s in destinations
                ],
                on_change=change,
            )
        self.page.controls.clear()
        self.page.add(ft.SafeArea(layout, expand=True, avoid_intrusions_bottom=False))
        self.page.update()

    def render_tab(self) -> None:
        builders: list[Callable[[], ft.Control]] = [
            self.view_overview,
            self.view_receipts,
            self.view_products,
            self.view_settings,
        ]
        self.body.content = builders[self.tab]()

    def rerender(self) -> None:
        if self.wide is None:
            return
        self.render_tab()
        self.page.update()

    def header(self, title: str, subtitle: str, *, actions: list[ft.Control] | None = None) -> ft.Control:
        refresh = ft.IconButton(
            ft.Icons.SYNC,
            tooltip="Fetch receipts",
            on_click=self.refresh,
            disabled=self.busy,
            style=ft.ButtonStyle(bgcolor=ft.Colors.SECONDARY_CONTAINER),
        )
        trailing = [ft.ProgressRing(width=22, height=22, stroke_width=3)] if self.busy else [refresh]
        return ft.Row(
            [
                ft.Column(
                    [ft.Text(title, size=30, weight=ft.FontWeight.W_700), muted(subtitle)],
                    spacing=2,
                    expand=True,
                ),
                *(actions or []),
                *trailing,
            ],
            vertical_alignment=ft.CrossAxisAlignment.CENTER,
        )

    def page_frame(self, *controls: ft.Control, scroll: bool = True) -> ft.Control:
        pad = 32 if self.wide else 16
        column = ft.Column(
            list(controls),
            spacing=20,
            horizontal_alignment=ft.CrossAxisAlignment.STRETCH,
            scroll=ft.ScrollMode.AUTO if scroll else None,
            expand=True,
        )
        return ft.Container(
            ft.Row(
                [ft.Container(column, expand=True, max_width=1100)],
                alignment=ft.MainAxisAlignment.CENTER,
                vertical_alignment=ft.CrossAxisAlignment.START,
                expand=True,
            ),
            padding=ft.Padding.only(left=pad, right=pad, top=pad, bottom=0 if scroll else pad),
            expand=True,
        )

    def logo(self, size: int) -> ft.Control:
        return ft.Container(
            width=size,
            height=size,
            border_radius=size * 0.3,
            gradient=ft.LinearGradient(
                colors=["#7C3AED", "#DB2777"], begin=ft.Alignment.TOP_LEFT, end=ft.Alignment.BOTTOM_RIGHT
            ),
            alignment=ft.Alignment.CENTER,
            content=ft.Icon(ft.Icons.RECEIPT_LONG, color=ft.Colors.WHITE, size=size * 0.55),
        )

    def empty(self, text: str) -> ft.Control:
        return surface(
            ft.Column(
                [
                    ft.Icon(ft.Icons.INBOX_OUTLINED, size=48, color=ft.Colors.ON_SURFACE_VARIANT),
                    ft.Text("Nothing here yet", size=18, weight=ft.FontWeight.W_600),
                    muted(text, text_align=ft.TextAlign.CENTER),
                    ft.FilledTonalButton(
                        "Fetch receipts", icon=ft.Icons.SYNC, on_click=self.refresh, disabled=self.busy
                    ),
                ],
                horizontal_alignment=ft.CrossAxisAlignment.CENTER,
                spacing=10,
            ),
            padding=40,
        )

    # ---------- data ----------

    async def refresh(self, _e=None) -> None:
        if self.busy:
            return
        self.busy = True
        self.rerender()
        try:
            if self.demo:
                await asyncio.sleep(0.6)
                fetched = data.demo_receipts()
            else:
                limit = self.settings.limit
                fetched = await asyncio.to_thread(receipts.fetch, limit or 20, fetch_all=limit == 0)
                await asyncio.to_thread(data.save_cache, fetched)
            self.receipts = fetched
            self.toast(f"Loaded {len(fetched)} receipts")
        except KauflandError as exc:
            self.toast(str(exc), error=True)
        except Exception as exc:  # noqa: BLE001
            self.toast(f"Unexpected error: {exc}", error=True)
        finally:
            self.busy = False
            self.rerender()

    # ---------- overview ----------

    def view_overview(self) -> ft.Control:
        if not self.receipts:
            return self.page_frame(
                self.header("Overview", "Your shopping at a glance"),
                self.empty("Fetch your receipts to see spending insights."),
            )
        o = data.overview(self.receipts)
        cur = o.currency
        stats = [
            (
                "Total spent",
                data.fmt_money(o.total, cur),
                ft.Icons.PAYMENTS_OUTLINED,
                ft.Colors.PRIMARY_CONTAINER,
                ft.Colors.ON_PRIMARY_CONTAINER,
            ),
            (
                "You saved",
                data.fmt_money(o.saving, cur),
                ft.Icons.SAVINGS_OUTLINED,
                ft.Colors.TERTIARY_CONTAINER,
                ft.Colors.ON_TERTIARY_CONTAINER,
            ),
            (
                "Receipts",
                str(o.count),
                ft.Icons.RECEIPT_LONG_OUTLINED,
                ft.Colors.SECONDARY_CONTAINER,
                ft.Colors.ON_SECONDARY_CONTAINER,
            ),
            (
                "Average basket",
                data.fmt_money(o.average, cur),
                ft.Icons.SHOPPING_CART_OUTLINED,
                ft.Colors.SURFACE_CONTAINER_HIGHEST,
                ft.Colors.ON_SURFACE,
            ),
        ]
        tiles = ft.ResponsiveRow(
            [
                ft.Container(
                    col={"xs": 6, "md": 3},
                    padding=18,
                    border_radius=20,
                    bgcolor=bg,
                    content=ft.Column(
                        [
                            ft.Icon(icon, color=fg, size=22),
                            ft.Text(
                                value,
                                size=22,
                                weight=ft.FontWeight.W_700,
                                color=fg,
                                no_wrap=True,
                                overflow=ft.TextOverflow.ELLIPSIS,
                            ),
                            ft.Text(label, size=13, color=fg, opacity=0.8),
                        ],
                        spacing=6,
                    ),
                )
                for label, value, icon, bg, fg in stats
            ],
            spacing=12,
            run_spacing=12,
        )

        top = max((v for _, v in o.months), default=0) or 1
        bars = ft.Row(
            [
                ft.Column(
                    [
                        muted(f"{v:,.0f}".replace(",", " "), size=11),
                        ft.Container(
                            height=max(6, 150 * v / top),
                            width=36,
                            border_radius=ft.BorderRadius.only(
                                top_left=10, top_right=10, bottom_left=4, bottom_right=4
                            ),
                            gradient=ft.LinearGradient(
                                colors=["#DB2777", "#7C3AED"],
                                begin=ft.Alignment.TOP_CENTER,
                                end=ft.Alignment.BOTTOM_CENTER,
                            ),
                            tooltip=data.fmt_money(v, cur),
                        ),
                        muted(m, size=12),
                    ],
                    horizontal_alignment=ft.CrossAxisAlignment.CENTER,
                    alignment=ft.MainAxisAlignment.END,
                    spacing=6,
                    expand=True,
                )
                for m, v in o.months
            ],
            height=210,
            vertical_alignment=ft.CrossAxisAlignment.END,
        )
        chart = surface(
            ft.Column([ft.Text("Monthly spending", size=16, weight=ft.FontWeight.W_600), bars], spacing=16),
            col={"xs": 12, "md": 7},
        )

        store_top = max((s[1] for s in o.stores), default=0) or 1
        stores = surface(
            ft.Column(
                [ft.Text("Top stores", size=16, weight=ft.FontWeight.W_600)]
                + [
                    ft.Column(
                        [
                            ft.Row(
                                [
                                    ft.Text(
                                        name, expand=True, max_lines=1, overflow=ft.TextOverflow.ELLIPSIS
                                    ),
                                    muted(f"{n}×  ·  {data.fmt_money(total, cur)}", size=12),
                                ]
                            ),
                            ft.ProgressBar(value=total / store_top, bar_height=8, border_radius=8),
                        ],
                        spacing=6,
                    )
                    for name, total, n in o.stores
                ],
                spacing=14,
            ),
            col={"xs": 12, "md": 5},
        )
        recent = surface(
            ft.Column(
                [
                    ft.Row(
                        [
                            ft.Text("Recent receipts", size=16, weight=ft.FontWeight.W_600, expand=True),
                            ft.TextButton("See all", on_click=lambda _e: self.goto(1)),
                        ]
                    ),
                    *[self.receipt_tile(r) for r in self.receipts[:4]],
                ],
                spacing=4,
            ),
            padding=12,
        )
        subtitle = f"{o.count} receipts · {o.items} items" + (" · demo data" if self.demo else "")
        return self.page_frame(
            self.header("Overview", subtitle),
            tiles,
            ft.ResponsiveRow([chart, stores], spacing=12, run_spacing=12),
            recent,
            ft.Container(height=12),
        )

    def goto(self, index: int) -> None:
        self.tab = index
        if self.wide:
            self.show_shell()
        else:
            self.page.navigation_bar.selected_index = index
            self.rerender()

    # ---------- receipts ----------

    def receipt_tile(self, receipt: dict) -> ft.Control:
        summary = receipts.summaries([receipt])[0]
        cur = summary["currency"]
        subtitle = f"{data.fmt_date(summary['timestamp'])} · {summary['items']} items"
        trailing = [ft.Text(data.fmt_money(summary["total"], cur), weight=ft.FontWeight.W_600)]
        if summary["saving"]:
            trailing.append(
                ft.Text(f"−{data.fmt_money(summary['saving'], cur)}", size=12, color=ft.Colors.TERTIARY)
            )
        return ft.ListTile(
            leading=ft.CircleAvatar(
                content=ft.Icon(ft.Icons.STOREFRONT, size=20),
                bgcolor=ft.Colors.PRIMARY_CONTAINER,
                color=ft.Colors.ON_PRIMARY_CONTAINER,
            ),
            title=ft.Text(
                summary["store"] or "Kaufland",
                weight=ft.FontWeight.W_500,
                max_lines=1,
                overflow=ft.TextOverflow.ELLIPSIS,
            ),
            subtitle=muted(subtitle, size=12),
            trailing=ft.Column(
                trailing,
                spacing=0,
                alignment=ft.MainAxisAlignment.CENTER,
                horizontal_alignment=ft.CrossAxisAlignment.END,
            ),
            on_click=lambda _e, r=receipt: self.open_receipt(r),
            shape=ft.RoundedRectangleBorder(radius=14),
        )

    def view_receipts(self) -> ft.Control:
        if not self.receipts:
            return self.page_frame(
                self.header("Receipts", "Every Kaufland Card purchase"), self.empty("No receipts loaded yet.")
            )

        def haystack(r: dict) -> str:
            store = r.get("store") if isinstance(r.get("store"), dict) else {}
            names = [p.get("name") for p in r.get("positions") or [] if isinstance(p, dict)]
            return " ".join(
                str(x)
                for x in (store.get("name"), store.get("city"), data.fmt_date(r.get("timestamp")), *names)
            ).lower()

        index = [(haystack(r), r) for r in self.receipts]
        listing = ft.ListView(spacing=2, expand=True, padding=ft.Padding.only(bottom=16))
        title = self.header("Receipts", "")

        def fill() -> None:
            q = self.receipt_query.lower()
            rows = [r for text, r in index if q in text]
            listing.controls = [self.receipt_tile(r) for r in rows] or [muted("No matches.")]
            title.controls[0].controls[1].value = f"{len(rows)} of {len(self.receipts)} receipts"

        fill()
        return self.page_frame(
            title,
            self.search_field("Search store, date or product", "receipt_query", fill),
            listing,
            scroll=False,
        )

    def search_field(self, hint: str, attr: str, fill: Callable[[], None]) -> ft.Control:
        def on_change(e):
            setattr(self, attr, e.control.value or "")
            fill()
            self.body.update()

        return ft.TextField(
            value=getattr(self, attr),
            hint_text=hint,
            prefix_icon=ft.Icons.SEARCH,
            border_radius=28,
            filled=True,
            border_width=0,
            dense=True,
            on_submit=on_change,
            on_change=on_change,
        )

    def open_receipt(self, receipt: dict) -> None:
        summary = receipts.summaries([receipt])[0]
        cur = summary["currency"]
        items = receipts.line_items([receipt])
        lines = [
            ft.Row(
                [
                    ft.Column(
                        [
                            ft.Text(
                                i["name"] or "—",
                                weight=ft.FontWeight.W_500,
                                color=ft.Colors.ERROR if i["refund"] else None,
                            ),
                            muted(
                                f"{i['quantity']} {i['unit'] or ''} × {data.fmt_money(i['unit_price'], cur)}"
                                + ("  · refund" if i["refund"] else ""),
                                size=12,
                            ),
                        ],
                        spacing=0,
                        expand=True,
                    ),
                    ft.Text(data.fmt_money(i["total"], cur), weight=ft.FontWeight.W_600),
                ]
            )
            for i in items
        ]
        totals = ft.Container(
            padding=16,
            border_radius=16,
            bgcolor=ft.Colors.PRIMARY_CONTAINER,
            content=ft.Column(
                [
                    ft.Row(
                        [
                            ft.Text(
                                "Total",
                                weight=ft.FontWeight.W_600,
                                expand=True,
                                color=ft.Colors.ON_PRIMARY_CONTAINER,
                            ),
                            ft.Text(
                                data.fmt_money(summary["total"], cur),
                                size=20,
                                weight=ft.FontWeight.W_700,
                                color=ft.Colors.ON_PRIMARY_CONTAINER,
                            ),
                        ]
                    ),
                    ft.Row(
                        [
                            ft.Text("Saved", expand=True, color=ft.Colors.ON_PRIMARY_CONTAINER),
                            ft.Text(
                                data.fmt_money(summary["saving"], cur), color=ft.Colors.ON_PRIMARY_CONTAINER
                            ),
                        ]
                    ),
                    ft.Row(
                        [
                            ft.Text("Bonus points", expand=True, color=ft.Colors.ON_PRIMARY_CONTAINER),
                            ft.Text(str(summary["bonus_points"] or 0), color=ft.Colors.ON_PRIMARY_CONTAINER),
                        ]
                    ),
                ],
                spacing=4,
            ),
        )
        body = ft.Column(
            [
                ft.Text(summary["store"] or "Kaufland", size=22, weight=ft.FontWeight.W_700),
                muted(" · ".join(x for x in (summary["city"], data.fmt_date(summary["timestamp"])) if x)),
                ft.Divider(),
                *lines,
                ft.Divider(),
                totals,
            ],
            spacing=10,
            scroll=ft.ScrollMode.AUTO,
            tight=True,
        )
        if self.wide:
            self.page.show_dialog(
                ft.AlertDialog(
                    content=ft.Container(body, width=520),
                    actions=[ft.TextButton("Close", on_click=lambda _e: self.page.pop_dialog())],
                    scrollable=True,
                )
            )
        else:
            self.page.show_dialog(
                ft.BottomSheet(
                    ft.Container(body, padding=ft.Padding.only(left=20, right=20, bottom=28)),
                    show_drag_handle=True,
                    scrollable=True,
                    use_safe_area=True,
                )
            )

    # ---------- products ----------

    def view_products(self) -> ft.Control:
        if not self.receipts:
            return self.page_frame(
                self.header("Products", "What you buy most"),
                self.empty("Products appear once receipts are loaded."),
            )
        all_products = data.products(self.receipts)
        listing = ft.ListView(spacing=2, expand=True, padding=ft.Padding.only(bottom=16))
        title = self.header("Products", "")

        def tile(p: data.Product) -> ft.Control:
            return ft.ListTile(
                leading=ft.CircleAvatar(
                    content=ft.Text(p.name[:1].upper(), weight=ft.FontWeight.W_700),
                    bgcolor=ft.Colors.SECONDARY_CONTAINER,
                    color=ft.Colors.ON_SECONDARY_CONTAINER,
                ),
                title=ft.Text(
                    p.name, max_lines=1, overflow=ft.TextOverflow.ELLIPSIS, weight=ft.FontWeight.W_500
                ),
                subtitle=muted(
                    f"Bought {p.count}× · last {data.fmt_money(p.last_price, p.currency)}"
                    + (f" · {data.fmt_date(p.last_seen, with_time=False)}" if self.wide else ""),
                    size=12,
                ),
                trailing=ft.Text(data.fmt_money(p.spent, p.currency), weight=ft.FontWeight.W_600),
                shape=ft.RoundedRectangleBorder(radius=14),
            )

        def fill() -> None:
            q = self.product_query.lower()
            rows = [p for p in all_products if q in p.name.lower()]
            listing.controls = [tile(p) for p in rows[:400]] or [muted("No matches.")]
            title.controls[0].controls[1].value = f"{len(rows)} products, sorted by money spent"

        fill()
        return self.page_frame(
            title,
            self.search_field("Search products", "product_query", fill),
            listing,
            scroll=False,
        )

    # ---------- settings ----------

    def country_dropdown(self) -> ft.Dropdown:
        return ft.Dropdown(
            label="Country",
            value=self.settings.country,
            leading_icon=ft.Icons.PUBLIC,
            expand=True,
            options=[
                ft.DropdownOption(key=code, text=f"{name} ({code})") for code, name in data.COUNTRIES.items()
            ],
        )

    def view_settings(self) -> ft.Control:
        s = self.settings

        if self.demo:
            account = [
                ft.ListTile(
                    leading=ft.Icon(ft.Icons.AUTO_AWESOME),
                    title=ft.Text("Demo mode"),
                    subtitle=muted("Synthetic data. Sign out to use a real account."),
                )
            ]
        else:
            try:
                st = auth.status()
                account = [
                    ft.ListTile(
                        leading=ft.Icon(ft.Icons.PERSON_OUTLINE),
                        title=ft.Text("Signed in"),
                        subtitle=muted(f"User ID {st['user_id']}"),
                    ),
                    ft.ListTile(
                        leading=ft.Icon(ft.Icons.KEY_OUTLINED),
                        title=ft.Text("Session"),
                        subtitle=muted(
                            "Refreshes automatically"
                            if st["has_refresh_token"]
                            else "Sign in again when it expires"
                        ),
                    ),
                    ft.ListTile(
                        leading=ft.Icon(ft.Icons.FOLDER_OUTLINED),
                        title=ft.Text("Data folder"),
                        subtitle=muted(st["data_dir"], selectable=True),
                    ),
                ]
            except KauflandError as exc:
                account = [muted(str(exc))]

        country = self.country_dropdown()

        def set_country(e):
            s.country = e.control.value
            s.save()
            self.toast(f"Country set to {s.country}. Fetch again to reload.")

        country.on_select = set_country

        limit = ft.Dropdown(
            label="Receipts per fetch",
            value=str(s.limit),
            leading_icon=ft.Icons.FORMAT_LIST_NUMBERED,
            expand=True,
            options=[ft.DropdownOption(key=str(v), text=t) for t, v in LIMITS],
        )

        def set_limit(e):
            s.limit = int(e.control.value)
            s.save()

        limit.on_select = set_limit

        def set_theme(e):
            s.theme = next(iter(e.control.selected))
            s.save()
            self.page.theme_mode = {"light": ft.ThemeMode.LIGHT, "dark": ft.ThemeMode.DARK}.get(
                s.theme, ft.ThemeMode.SYSTEM
            )
            self.page.update()

        theme_picker = ft.SegmentedButton(
            selected=[s.theme],
            segments=[
                ft.Segment("system", label=ft.Text("Auto"), icon=ft.Icon(ft.Icons.BRIGHTNESS_AUTO)),
                ft.Segment("light", label=ft.Text("Light"), icon=ft.Icon(ft.Icons.LIGHT_MODE)),
                ft.Segment("dark", label=ft.Text("Dark"), icon=ft.Icon(ft.Icons.DARK_MODE)),
            ],
            on_change=set_theme,
        )

        exports = ft.Row(
            [
                ft.FilledTonalButton(
                    "Items CSV",
                    icon=ft.Icons.TABLE_VIEW,
                    on_click=lambda _e: self.page.run_task(self.export, "items-csv"),
                ),
                ft.FilledTonalButton(
                    "Receipts CSV",
                    icon=ft.Icons.LIST_ALT,
                    on_click=lambda _e: self.page.run_task(self.export, "list-csv"),
                ),
                ft.FilledTonalButton(
                    "Raw JSON",
                    icon=ft.Icons.DATA_OBJECT,
                    on_click=lambda _e: self.page.run_task(self.export, "receipts-json"),
                ),
            ],
            wrap=True,
            spacing=8,
            run_spacing=8,
        )

        def section(title: str, *controls: ft.Control) -> ft.Control:
            return surface(
                ft.Column([ft.Text(title, size=16, weight=ft.FontWeight.W_600), *controls], spacing=14)
            )

        return self.page_frame(
            self.header("Settings", "Account, data and appearance"),
            section(
                "Account",
                *account,
                ft.Row([ft.OutlinedButton("Sign out", icon=ft.Icons.LOGOUT, on_click=self.sign_out)]),
            ),
            section(
                "Fetching",
                ft.ResponsiveRow(
                    [
                        ft.Container(country, col={"xs": 12, "md": 6}),
                        ft.Container(limit, col={"xs": 12, "md": 6}),
                    ],
                    run_spacing=12,
                ),
                muted("Full history can take a while. The API is unofficial, so go easy on it.", size=12),
            ),
            section("Export", muted(f"{len(self.receipts)} receipts loaded."), exports),
            section("Appearance", ft.Row([theme_picker], scroll=ft.ScrollMode.AUTO)),
            ft.Row(
                [muted("Unofficial app · not affiliated with Kaufland", size=12)],
                alignment=ft.MainAxisAlignment.CENTER,
            ),
            ft.Container(height=12),
        )

    async def export(self, kind: str) -> None:
        if not self.receipts:
            self.toast("Fetch receipts first.", error=True)
            return
        text = data.export_text(self.receipts, kind)
        ext = "json" if kind.endswith("json") else "csv"
        name = {
            "items-csv": "kaufland-items",
            "list-csv": "kaufland-receipts",
            "receipts-json": "kaufland-raw",
        }[kind]
        payload = text.encode("utf-8")
        try:
            path = await self.file_picker.save_file(
                dialog_title="Export", file_name=f"{name}.{ext}", allowed_extensions=[ext], src_bytes=payload
            )
            if path and self.page.platform in DESKTOP:
                with open(path, "wb") as fh:
                    fh.write(payload)
            if path:
                self.toast(f"Saved {name}.{ext}")
        except Exception as exc:  # noqa: BLE001
            await self.clipboard.set(text)
            self.toast(f"Could not save a file ({exc}); copied to clipboard instead.")

    async def sign_out(self, _e=None) -> None:
        if not self.demo:
            await asyncio.to_thread(auth.logout)
            await asyncio.to_thread(data.clear_cache)
        self.demo = False
        self.receipts = []
        self.tab = 0
        self.page.navigation_bar = None
        self.show_login()


def main(page: ft.Page) -> None:
    App(page).start()
