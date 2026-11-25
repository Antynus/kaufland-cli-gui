"""Flet user interface. Runs on Windows, macOS, Linux and Android."""

from __future__ import annotations

import asyncio
import threading
import urllib.parse
import urllib.request
from collections.abc import Callable
from datetime import datetime
from pathlib import Path

import flet as ft

from kaufland_cli.config import data_dir

from . import browser_login, categories, data, qr
from .stores import Registry, Store, StoreError, clear_cache, load_cache, plugins_dir, save_cache

SEED = "#7C3AED"
WIDE = 840  # px; above this the side rail replaces the bottom bar
DESKTOP = {ft.PagePlatform.WINDOWS, ft.PagePlatform.MACOS, ft.PagePlatform.LINUX}
MOBILE = {ft.PagePlatform.ANDROID, ft.PagePlatform.IOS}
PLUGIN_DOCS = "https://github.com/Antynus/kaufland-cli-gui/blob/main/gui/PLUGINS.md"

NAV = [
    ("Overview", ft.Icons.SPACE_DASHBOARD_OUTLINED, ft.Icons.SPACE_DASHBOARD),
    ("Receipts", ft.Icons.RECEIPT_LONG_OUTLINED, ft.Icons.RECEIPT_LONG),
    ("Products", ft.Icons.SHOPPING_BASKET_OUTLINED, ft.Icons.SHOPPING_BASKET),
    ("Stores", ft.Icons.STOREFRONT_OUTLINED, ft.Icons.STOREFRONT),
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


def title(text: str) -> ft.Text:
    return ft.Text(text, size=16, weight=ft.FontWeight.W_600)


def badge(text: str, color: str) -> ft.Control:
    return ft.Container(
        ft.Text(text, size=11, weight=ft.FontWeight.W_600, color=color),
        padding=ft.Padding.symmetric(horizontal=8, vertical=2),
        border_radius=10,
        bgcolor=ft.Colors.with_opacity(0.12, color),
    )


def avatar(name: str, color: str, size: int = 40) -> ft.Control:
    return ft.Container(
        ft.Text(
            (name or "?")[:1].upper(), weight=ft.FontWeight.W_700, color=ft.Colors.WHITE, size=size * 0.42
        ),
        width=size,
        height=size,
        border_radius=size / 2,
        bgcolor=color,
        alignment=ft.Alignment.CENTER,
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
        self.registry = Registry()
        self.receipts: list[dict] = []
        self.demo = False
        self.busy = False
        self.status = ""
        self.tab = 0
        self.wide: bool | None = None
        self.receipt_query = ""
        self.product_query = ""
        self.merchant_filter: str | None = None
        self.file_picker = ft.FilePicker()
        self.clipboard = ft.Clipboard()
        self.launcher = ft.UrlLauncher()

        page.title = "Receipts"
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
            page.window.width, page.window.height = 1180, 820

    # ================= lifecycle =================

    @property
    def online_stores(self) -> list[Store]:
        return [s for s in self.registry.stores.values() if not s.local]

    def load_receipts(self) -> None:
        if self.demo:
            return
        rows: list[dict] = []
        for store in self.registry.stores.values():
            if store.local:
                rows += store.fetch(0, cached=[])
            elif store.connected():
                rows += load_cache(store.id)
        rows.sort(key=data.sort_key, reverse=True)
        self.receipts = rows

    def start(self) -> None:
        self.load_receipts()
        self.show_shell()
        stale = [s.id for s in self.online_stores if s.connected() and not load_cache(s.id)]
        if stale:
            self.page.run_task(self.refresh, stale)

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
                duration=ft.Duration(seconds=7 if error else 3),
            )
        )

    def close_dialog(self, _e=None) -> None:
        self.page.pop_dialog()

    def color_for(self, merchant: str) -> str:
        return categories.color(self.settings.category(merchant))

    # ================= shell =================

    def show_shell(self) -> None:
        self.wide = self.is_wide()
        self.body = ft.Container(expand=True)
        self.render_tab()

        def change(e):
            self.tab = int(e.control.selected_index)
            self.render_tab()
            self.page.update()

        if self.wide:
            nav = ft.NavigationRail(
                selected_index=self.tab,
                label_type=ft.NavigationRailLabelType.ALL,
                min_width=96,
                group_alignment=-0.85,
                leading=ft.Container(self.logo(44), padding=ft.Padding.only(top=20, bottom=12)),
                destinations=[
                    ft.NavigationRailDestination(icon=i, selected_icon=s, label=t) for t, i, s in NAV
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
                    ft.NavigationBarDestination(icon=i, selected_icon=s, label=t) for t, i, s in NAV
                ],
                on_change=change,
            )
        self.page.floating_action_button = ft.FloatingActionButton(
            icon=ft.Icons.QR_CODE_SCANNER,
            content="Add receipt" if self.wide else None,
            tooltip="Add a receipt",
            on_click=lambda _e: self.add_menu(),
        )
        self.page.controls.clear()
        self.page.add(ft.SafeArea(layout, expand=True, avoid_intrusions_bottom=False))
        self.page.update()

    def render_tab(self) -> None:
        builders: list[Callable[[], ft.Control]] = [
            self.view_overview,
            self.view_receipts,
            self.view_products,
            self.view_stores,
            self.view_settings,
        ]
        self.body.content = builders[self.tab]()
        if self.page.floating_action_button:
            self.page.floating_action_button.visible = self.tab in (0, 1)

    def rerender(self) -> None:
        if self.wide is None:
            return
        self.render_tab()
        self.page.update()

    def goto(self, index: int) -> None:
        self.tab = index
        if self.wide:
            self.show_shell()
        else:
            self.page.navigation_bar.selected_index = index
            self.rerender()

    def header(self, text: str, subtitle: str, *, actions: list[ft.Control] | None = None) -> ft.Row:
        refresh = ft.IconButton(
            ft.Icons.SYNC,
            tooltip="Fetch receipts from connected stores",
            on_click=lambda _e: self.page.run_task(self.refresh),
            disabled=self.busy,
            style=ft.ButtonStyle(bgcolor=ft.Colors.SECONDARY_CONTAINER),
        )
        if self.busy:
            trailing = [
                muted(self.status, size=12, max_lines=1, overflow=ft.TextOverflow.ELLIPSIS)
                if self.wide
                else None,
                ft.ProgressRing(width=22, height=22, stroke_width=3),
            ]
        else:
            trailing = [refresh]
        return ft.Row(
            [
                ft.Column(
                    [ft.Text(text, size=30, weight=ft.FontWeight.W_700), muted(subtitle)],
                    spacing=2,
                    expand=True,
                ),
                *(actions or []),
                *[c for c in trailing if c],
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

    # ================= data =================

    async def refresh(self, store_ids: list[str] | None = None) -> None:
        if self.busy:
            return
        if self.demo:
            self.toast("Demo mode: nothing to fetch. Leave demo mode in Settings to use your stores.")
            return
        targets = [
            s for s in self.online_stores if s.connected() and (store_ids is None or s.id in store_ids)
        ]
        if not targets:
            self.load_receipts()
            self.rerender()
            self.toast("Connect a store first, or add receipts by scanning their QR code.")
            self.goto(3)
            return
        self.busy = True
        loop = asyncio.get_running_loop()

        def progress(text: str) -> None:
            def apply():
                self.status = text
                self.rerender()

            loop.call_soon_threadsafe(apply)

        fetched, failed = 0, []
        for store in targets:
            self.status = f"{store.name}: connecting"
            self.rerender()
            try:
                rows = await asyncio.to_thread(
                    store.fetch, self.settings.limit, cached=load_cache(store.id), progress=progress
                )
                await asyncio.to_thread(save_cache, store.id, rows)
                fetched += len(rows)
            except StoreError as exc:
                failed.append(f"{store.name}: {exc}")
            except Exception as exc:  # noqa: BLE001 - one broken store must not stop the others
                failed.append(f"{store.name}: unexpected error ({exc})")
        self.busy = False
        self.status = ""
        self.load_receipts()
        self.rerender()
        if failed:
            self.toast("  •  ".join(failed), error=True)
        else:
            self.toast(f"Updated {fetched} receipts from {', '.join(s.name for s in targets)}")

    def visible_receipts(self) -> list[dict]:
        if not self.merchant_filter:
            return self.receipts
        return [r for r in self.receipts if r.get("merchant") == self.merchant_filter]

    # ================= overview =================

    def welcome(self) -> ft.Control:
        buttons = [
            ft.FilledButton(f"Connect {s.name}", icon=ft.Icons.LINK, on_click=lambda _e, s=s: self.connect(s))
            for s in self.online_stores
        ]
        return surface(
            ft.Column(
                [
                    self.logo(56),
                    ft.Text(
                        "All your receipts in one place",
                        size=22,
                        weight=ft.FontWeight.W_700,
                        text_align=ft.TextAlign.CENTER,
                    ),
                    muted(
                        "Connect your Kaufland and Lidl accounts, or scan the QR code on any Croatian fiscal "
                        "receipt: Konzum, Studenac, Plodine, SPAR, Müller, IKEA, Decathlon, Shell "
                        "and the rest.",
                        text_align=ft.TextAlign.CENTER,
                    ),
                    ft.Row(
                        [
                            *buttons,
                            ft.FilledTonalButton(
                                "Scan a receipt",
                                icon=ft.Icons.QR_CODE_SCANNER,
                                on_click=lambda _e: self.add_menu(),
                            ),
                        ],
                        wrap=True,
                        alignment=ft.MainAxisAlignment.CENTER,
                        spacing=8,
                        run_spacing=8,
                    ),
                    ft.TextButton(
                        "Explore with demo data",
                        icon=ft.Icons.AUTO_AWESOME,
                        on_click=lambda _e: self.set_demo(True),
                    ),
                ],
                horizontal_alignment=ft.CrossAxisAlignment.CENTER,
                spacing=14,
            ),
            padding=32,
        )

    def filter_bar(self) -> ft.Control | None:
        if not self.merchant_filter:
            return None

        def clear(_e):
            self.merchant_filter = None
            self.rerender()

        return ft.Row(
            [
                ft.Chip(
                    label=ft.Text(f"Shop: {self.merchant_filter}"),
                    leading=ft.Icon(ft.Icons.FILTER_ALT, size=16),
                    on_delete=clear,
                    delete_icon_tooltip="Show all shops",
                )
            ]
        )

    def view_overview(self) -> ft.Control:
        if not self.receipts:
            return self.page_frame(self.header("Overview", "Your shopping at a glance"), self.welcome())
        rows = self.visible_receipts()
        o = data.overview(rows, self.settings)
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
        chart = surface(ft.Column([title("Monthly spending"), bars], spacing=16), col={"xs": 12, "md": 7})

        cat_total = sum(v for _, v in o.categories) or 1
        category_rows = surface(
            ft.Column(
                [
                    title("By category"),
                    ft.Row(
                        [
                            ft.Container(
                                expand=max(1, round(1000 * v / cat_total)),
                                height=14,
                                bgcolor=categories.color(c),
                                tooltip=categories.label(c),
                            )
                            for c, v in o.categories
                        ],
                        spacing=2,
                    ),
                    *[
                        ft.Row(
                            [
                                ft.Container(
                                    width=10, height=10, border_radius=5, bgcolor=categories.color(c)
                                ),
                                ft.Text(categories.label(c), expand=True),
                                muted(f"{100 * v / cat_total:.0f}%", size=12),
                                ft.Text(data.fmt_money(v, cur), weight=ft.FontWeight.W_500),
                            ],
                            spacing=10,
                        )
                        for c, v in o.categories[:6]
                    ],
                ],
                spacing=12,
            ),
            col={"xs": 12, "md": 5},
        )

        shop_top = max((s[1] for s in o.merchants), default=0) or 1
        shops = surface(
            ft.Column(
                [
                    ft.Row(
                        [
                            title("Top shops"),
                            ft.Container(expand=True),
                            ft.TextButton("All shops", on_click=lambda _e: self.goto(3)),
                        ]
                    )
                ]
                + [
                    ft.Container(
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
                                ft.ProgressBar(
                                    value=total / shop_top,
                                    bar_height=8,
                                    border_radius=8,
                                    color=self.color_for(name),
                                ),
                            ],
                            spacing=6,
                        ),
                        on_click=lambda _e, name=name: self.filter_merchant(name),
                        border_radius=8,
                        padding=4,
                    )
                    for name, total, n in o.merchants[:5]
                ],
                spacing=10,
            ),
            col={"xs": 12, "md": 7},
        )
        recent = surface(
            ft.Column(
                [
                    ft.Row(
                        [
                            title("Recent receipts"),
                            ft.Container(expand=True),
                            ft.TextButton("See all", on_click=lambda _e: self.goto(1)),
                        ]
                    ),
                    *[self.receipt_tile(r) for r in rows[:5]],
                ],
                spacing=4,
            ),
            padding=12,
        )
        subtitle = f"{o.count} receipts · {len(o.merchants)} shops" + (" · demo data" if self.demo else "")
        return self.page_frame(
            self.header("Overview", subtitle),
            *[c for c in (self.filter_bar(),) if c],
            tiles,
            ft.ResponsiveRow([chart, category_rows], spacing=12, run_spacing=12),
            ft.ResponsiveRow(
                [shops, ft.Container(self.sources_card(), col={"xs": 12, "md": 5})],
                spacing=12,
                run_spacing=12,
            ),
            recent,
            ft.Container(height=72),
        )

    def sources_card(self) -> ft.Control:
        counts: dict[str, int] = {}
        for r in self.receipts:
            counts[r.get("source", "")] = counts.get(r.get("source", ""), 0) + 1
        rows = []
        for store in self.registry.stores.values():
            n = counts.get(store.id, 0)
            state = "local" if store.local else ("connected" if store.connected() else "not connected")
            rows.append(
                ft.Row(
                    [
                        ft.Container(width=10, height=10, border_radius=5, bgcolor=store.color),
                        ft.Text(store.name, expand=True),
                        muted(f"{n} receipts · {state}", size=12),
                    ],
                    spacing=10,
                )
            )
        return surface(
            ft.Column(
                [
                    ft.Row(
                        [
                            title("Sources"),
                            ft.Container(expand=True),
                            ft.TextButton("Manage", on_click=lambda _e: self.goto(3)),
                        ]
                    ),
                    *rows,
                ],
                spacing=12,
            )
        )

    def filter_merchant(self, merchant: str) -> None:
        self.merchant_filter = merchant
        self.goto(1)

    # ================= receipts =================

    def receipt_tile(self, receipt: dict) -> ft.Control:
        cur = receipt.get("currency")
        merchant = receipt.get("merchant") or "Unknown shop"
        n_items = len(receipt.get("items") or [])
        parts = [data.fmt_date(receipt.get("timestamp"))]
        if n_items:
            parts.append(f"{n_items} items")
        if receipt.get("fiscal"):
            parts.append("QR")
        trailing = [ft.Text(data.fmt_money(receipt.get("total") or 0, cur), weight=ft.FontWeight.W_600)]
        if receipt.get("saving"):
            trailing.append(
                ft.Text(f"−{data.fmt_money(receipt['saving'], cur)}", size=12, color=ft.Colors.TERTIARY)
            )
        branch = receipt.get("branch")
        name = branch if branch and merchant.lower() in branch.lower() else merchant
        return ft.ListTile(
            leading=avatar(merchant, self.color_for(merchant)),
            title=ft.Text(name, weight=ft.FontWeight.W_500, max_lines=1, overflow=ft.TextOverflow.ELLIPSIS),
            subtitle=muted(" · ".join(parts), size=12),
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
            return self.page_frame(self.header("Receipts", "From every connected store"), self.welcome())
        rows_all = self.visible_receipts()

        def haystack(r: dict) -> str:
            names = [i.get("name") or "" for i in r.get("items") or []]
            return " ".join(
                str(x or "")
                for x in (
                    r.get("merchant"),
                    r.get("branch"),
                    r.get("city"),
                    r.get("note"),
                    data.fmt_date(r.get("timestamp")),
                    *names,
                )
            ).lower()

        index = [(haystack(r), r) for r in rows_all]
        listing = ft.ListView(spacing=2, expand=True, padding=ft.Padding.only(bottom=80))
        head = self.header("Receipts", "")

        def fill() -> None:
            q = self.receipt_query.lower()
            rows = [r for text, r in index if q in text]
            listing.controls = [self.receipt_tile(r) for r in rows[:500]] or [muted("No matches.")]
            head.controls[0].controls[1].value = f"{len(rows)} of {len(self.receipts)} receipts"

        fill()
        return self.page_frame(
            head,
            *[c for c in (self.filter_bar(),) if c],
            self.search_field("Search shop, date or product", "receipt_query", fill),
            listing,
            scroll=False,
        )

    def show_sheet(self, body: ft.Control, actions: list[ft.Control]) -> None:
        if self.wide:
            self.page.show_dialog(
                ft.AlertDialog(content=ft.Container(body, width=540), actions=actions, scrollable=True)
            )
        else:
            self.page.show_dialog(
                ft.BottomSheet(
                    ft.Container(
                        ft.Column(
                            [body, ft.Row(actions, alignment=ft.MainAxisAlignment.END, wrap=True)],
                            tight=True,
                            spacing=12,
                        ),
                        padding=ft.Padding.only(left=20, right=20, bottom=28),
                    ),
                    show_drag_handle=True,
                    scrollable=True,
                    use_safe_area=True,
                )
            )

    def open_receipt(self, receipt: dict) -> None:
        cur = receipt.get("currency")
        merchant = receipt.get("merchant") or "Unknown shop"
        category = self.settings.category(merchant)
        lines = [
            ft.Row(
                [
                    ft.Column(
                        [
                            ft.Text(
                                i.get("name") or "—",
                                weight=ft.FontWeight.W_500,
                                color=ft.Colors.ERROR if i.get("refund") else None,
                            ),
                            muted(
                                f"{data.fmt_qty(i.get('quantity'))} {i.get('unit') or ''} × "
                                f"{data.fmt_money(i.get('unit_price') or 0, cur)}"
                                + ("  · refund" if i.get("refund") else ""),
                                size=12,
                            ),
                        ],
                        spacing=0,
                        expand=True,
                    ),
                    ft.Text(data.fmt_money(i.get("total") or 0, cur), weight=ft.FontWeight.W_600),
                ]
            )
            for i in receipt.get("items") or []
        ]
        if not lines:
            lines = [
                muted(
                    "No item details for this receipt."
                    + (" Fiscal QR codes only carry the date and total." if receipt.get("fiscal") else "")
                )
            ]

        def total_row(label: str, value: str, big: bool = False) -> ft.Row:
            return ft.Row(
                [
                    ft.Text(
                        label,
                        expand=True,
                        color=ft.Colors.ON_PRIMARY_CONTAINER,
                        weight=ft.FontWeight.W_600 if big else None,
                    ),
                    ft.Text(
                        value,
                        color=ft.Colors.ON_PRIMARY_CONTAINER,
                        size=20 if big else None,
                        weight=ft.FontWeight.W_700 if big else None,
                    ),
                ]
            )

        totals = [total_row("Total", data.fmt_money(receipt.get("total") or 0, cur), big=True)]
        if receipt.get("saving"):
            totals.append(total_row("Saved", data.fmt_money(receipt["saving"], cur)))
        if receipt.get("points"):
            totals.append(total_row("Bonus points", str(receipt["points"])))
        store = self.registry.get(receipt.get("source", ""))
        details = [
            ft.Row(
                [
                    ft.Text(
                        receipt.get("branch") or merchant, size=22, weight=ft.FontWeight.W_700, expand=True
                    ),
                    badge(categories.label(category), categories.color(category)),
                ]
            ),
            muted(
                " · ".join(
                    x
                    for x in (
                        receipt.get("city"),
                        data.fmt_date(receipt.get("timestamp")),
                        store.name if store else None,
                    )
                    if x
                )
            ),
        ]
        if receipt.get("note"):
            details.append(ft.Text(receipt["note"], italic=True))
        if receipt.get("fiscal"):
            fiscal = receipt["fiscal"]
            details.append(
                muted(
                    ("JIR " + fiscal["jir"]) if fiscal.get("jir") else ("ZKI " + fiscal.get("zki", "")),
                    size=11,
                    selectable=True,
                )
            )
        body = ft.Column(
            [
                *details,
                ft.Divider(),
                *lines,
                ft.Divider(),
                ft.Container(
                    ft.Column(totals, spacing=4),
                    padding=16,
                    border_radius=16,
                    bgcolor=ft.Colors.PRIMARY_CONTAINER,
                ),
            ],
            spacing=10,
            tight=True,
        )
        actions: list[ft.Control] = []
        if receipt.get("source") == "scanned" and not receipt.get("demo"):
            if (receipt.get("fiscal") or {}).get("url"):
                actions.append(
                    ft.TextButton(
                        "Check at Porezna",
                        icon=ft.Icons.VERIFIED_OUTLINED,
                        on_click=lambda _e: self.page.run_task(
                            self.launcher.launch_url, receipt["fiscal"]["url"]
                        ),
                    )
                )
            actions.append(
                ft.TextButton(
                    "Edit",
                    icon=ft.Icons.EDIT_OUTLINED,
                    on_click=lambda _e: (self.close_dialog(), self.receipt_form(existing=receipt)),
                )
            )
            actions.append(
                ft.TextButton(
                    "Delete", icon=ft.Icons.DELETE_OUTLINE, on_click=lambda _e: self.delete_receipt(receipt)
                )
            )
        actions.append(ft.TextButton("Close", on_click=self.close_dialog))
        self.show_sheet(body, actions)

    def delete_receipt(self, receipt: dict) -> None:
        self.registry.scanned.delete(receipt["uid"])
        self.close_dialog()
        self.load_receipts()
        self.rerender()
        self.toast("Receipt deleted")

    # ================= adding receipts =================

    def camera_supported(self) -> bool:
        if self.page.platform not in MOBILE or self.page.web:
            return False
        try:
            import flet_camera  # noqa: F401
        except ImportError:
            return False
        return True

    def add_menu(self) -> None:
        options = []
        if self.camera_supported():
            options.append(
                (
                    ft.Icons.PHOTO_CAMERA_OUTLINED,
                    "Scan the QR code",
                    "Point the camera at the receipt",
                    lambda: self.page.run_task(self.scan_with_camera),
                )
            )
        options += [
            (
                ft.Icons.IMAGE_OUTLINED,
                "From a photo",
                "Pick a photo or screenshot of the receipt",
                lambda: self.page.run_task(self.scan_from_file),
            ),
            (
                ft.Icons.LINK,
                "Paste the QR link",
                "The porezna.gov.hr/rn?… link from any QR scanner app",
                self.paste_qr_dialog,
            ),
            (
                ft.Icons.EDIT_NOTE,
                "Type it in",
                "Shop, total and date, for receipts without a QR code",
                lambda: self.receipt_form(),
            ),
        ]

        def pick(fn):
            def handler(_e):
                self.close_dialog()
                fn()

            return handler

        body = ft.Column(
            [
                ft.Text("Add a receipt", size=22, weight=ft.FontWeight.W_700),
                muted("Works with every Croatian fiscal receipt, from any shop."),
                *[
                    ft.ListTile(
                        leading=ft.Icon(icon),
                        title=ft.Text(label),
                        subtitle=muted(hint, size=12),
                        on_click=pick(fn),
                        shape=ft.RoundedRectangleBorder(radius=14),
                    )
                    for icon, label, hint, fn in options
                ],
            ],
            tight=True,
            spacing=6,
        )
        self.show_sheet(body, [ft.TextButton("Cancel", on_click=self.close_dialog)])

    def handle_qr_text(self, text: str) -> None:
        try:
            fiscal = qr.parse(text)
        except qr.QrError as exc:
            self.toast(str(exc), error=True)
            return
        existing = self.registry.scanned.find_fiscal(fiscal.key)
        if existing:
            self.toast(f"Already saved: {existing['merchant']}, {data.fmt_date(existing['timestamp'])}")
            self.open_receipt(existing)
            return
        self.receipt_form(fiscal=fiscal)

    async def scan_from_file(self) -> None:
        files = await self.file_picker.pick_files(
            dialog_title="Photo of the receipt", file_type=ft.FilePickerFileType.IMAGE, with_data=True
        )
        if not files:
            return
        f = files[0]
        payload = f.bytes
        if payload is None and f.path:
            payload = await asyncio.to_thread(Path(f.path).read_bytes)
        if not payload:
            self.toast("Could not read that file.", error=True)
            return
        await self.decode_and_handle(payload)

    async def decode_and_handle(self, payload: bytes) -> bool:
        try:
            texts = await asyncio.to_thread(qr.decode_image, payload)
        except qr.QrError as exc:
            self.toast(str(exc), error=True)
            return False
        fiscal = [t for t in texts if "jir=" in t.lower() or "zki=" in t.lower()]
        if not fiscal:
            self.toast(
                "No receipt QR code found in that image. Try a sharper, closer photo."
                if not texts
                else "Found a code, but it is not a fiscal receipt QR code.",
                error=True,
            )
            return False
        self.handle_qr_text(fiscal[0])
        return True

    async def scan_with_camera(self) -> None:
        import flet_camera as fc

        camera = fc.Camera(expand=True, preview_enabled=True)
        status = muted("Starting the camera…")
        capture = ft.FilledButton("Capture", icon=ft.Icons.CAMERA, disabled=True)

        async def take(_e):
            capture.disabled = True
            status.value = "Reading the QR code…"
            self.page.update()
            try:
                image = await camera.take_picture()
            except Exception as exc:  # noqa: BLE001
                status.value = f"Camera error: {exc}"
                capture.disabled = False
                self.page.update()
                return
            texts = []
            try:
                texts = await asyncio.to_thread(qr.decode_image, image)
            except qr.QrError as exc:
                status.value = str(exc)
            match = next((t for t in texts if "jir=" in t.lower() or "zki=" in t.lower()), None)
            if match:
                self.close_dialog()
                self.handle_qr_text(match)
                return
            status.value = (
                status.value
                if not texts and status.value.startswith("No QR")
                else (
                    "No receipt QR code found. Hold the phone steady, fill the frame with the code "
                    "and try again."
                )
            )
            capture.disabled = False
            self.page.update()

        capture.on_click = take
        self.page.show_dialog(
            ft.AlertDialog(
                title=ft.Text("Scan the receipt QR code"),
                content=ft.Container(
                    ft.Column(
                        [
                            ft.Container(
                                camera, height=360, border_radius=16, clip_behavior=ft.ClipBehavior.ANTI_ALIAS
                            ),
                            status,
                        ],
                        tight=True,
                        spacing=10,
                    ),
                    width=360,
                ),
                actions=[ft.TextButton("Cancel", on_click=self.close_dialog), capture],
            )
        )
        await asyncio.sleep(0.4)
        try:
            cams = await camera.get_available_cameras()
            back = next(
                (c for c in cams if c.lens_direction == fc.CameraLensDirection.BACK),
                cams[0] if cams else None,
            )
            if back is None:
                status.value = "No camera found. Use “From a photo” instead."
            else:
                await camera.initialize(back, fc.ResolutionPreset.HIGH, enable_audio=False)
                status.value = "Fill the frame with the QR code, then tap Capture."
                capture.disabled = False
        except Exception as exc:  # noqa: BLE001
            status.value = f"Could not start the camera ({exc}). Use “From a photo” instead."
        self.page.update()

    def paste_qr_dialog(self) -> None:
        field = ft.TextField(
            label="QR link",
            hint_text="https://porezna.gov.hr/rn?jir=…&datv=…&izn=…",
            multiline=True,
            min_lines=2,
            autofocus=True,
        )

        async def paste(_e):
            field.value = await self.clipboard.get() or ""
            self.page.update()

        def submit(_e):
            self.close_dialog()
            self.handle_qr_text(field.value or "")

        self.page.show_dialog(
            ft.AlertDialog(
                title=ft.Text("Paste the receipt QR link"),
                content=ft.Container(
                    ft.Column(
                        [muted("Scan the receipt with any QR app and copy the link it shows."), field],
                        tight=True,
                        horizontal_alignment=ft.CrossAxisAlignment.STRETCH,
                        spacing=12,
                    ),
                    width=460,
                ),
                actions=[
                    ft.TextButton("Paste", icon=ft.Icons.CONTENT_PASTE, on_click=paste),
                    ft.TextButton("Cancel", on_click=self.close_dialog),
                    ft.FilledButton("Continue", on_click=submit),
                ],
            )
        )

    def known_merchants(self) -> list[str]:
        counts: dict[str, int] = {}
        for r in self.registry.scanned.receipts():
            counts[r["merchant"]] = counts.get(r["merchant"], 0) + 1
        common = [
            "Konzum",
            "Studenac",
            "Plodine",
            "SPAR",
            "Tommy",
            "Müller",
            "dm",
            "Offertissima",
            "IKEA",
            "Decathlon",
            "Shell",
            "INA",
        ]
        ranked = sorted(counts, key=lambda m: -counts[m])
        return ranked + [m for m in common if m not in counts]

    def receipt_form(self, *, fiscal: qr.FiscalReceipt | None = None, existing: dict | None = None) -> None:
        when = (
            data.parse_time(existing["timestamp"])
            if existing
            else (fiscal.issued if fiscal else datetime.now())
        )
        shop = ft.TextField(
            label="Shop",
            value=existing["merchant"] if existing else "",
            autofocus=True,
            prefix_icon=ft.Icons.STOREFRONT_OUTLINED,
            capitalization=ft.TextCapitalization.WORDS,
        )
        total = ft.TextField(
            label="Total",
            value=f"{(existing or {}).get('total') or (fiscal.total if fiscal else ''):}",
            suffix=ft.Text("€"),
            keyboard_type=ft.KeyboardType.NUMBER,
            read_only=bool(fiscal or (existing or {}).get("fiscal")),
            expand=True,
        )
        stamp = ft.TextField(
            label="Date and time",
            value=when.strftime("%Y-%m-%d %H:%M") if when else "",
            hint_text="YYYY-MM-DD HH:MM",
            read_only=bool(fiscal or (existing or {}).get("fiscal")),
            expand=True,
        )
        city = ft.TextField(label="City (optional)", value=(existing or {}).get("city") or "")
        note = ft.TextField(label="Note (optional)", value=(existing or {}).get("note") or "", multiline=True)
        category_text = muted("", size=12)

        def update_category(_e=None):
            name = (shop.value or "").strip()
            category_text.value = (
                f"Category: {categories.label(self.settings.category(name))}" if name else ""
            )
            self.page.update()

        shop.on_change = update_category

        def choose(name: str):
            def handler(_e):
                shop.value = name
                update_category()

            return handler

        suggestions = ft.Row(
            [ft.Chip(label=ft.Text(m), on_click=choose(m)) for m in self.known_merchants()[:12]],
            wrap=True,
            spacing=6,
            run_spacing=6,
        )

        def save(_e):
            try:
                value = float((total.value or "").replace(",", ".").replace("€", "").strip())
                stamp_value = datetime.strptime((stamp.value or "").strip(), "%Y-%m-%d %H:%M")
            except ValueError:
                self.toast("Check the total (e.g. 12,49) and the date (YYYY-MM-DD HH:MM).", error=True)
                return
            try:
                if existing:
                    self.registry.scanned.update(
                        existing["uid"],
                        merchant=shop.value.strip(),
                        total=round(value, 2),
                        timestamp=stamp_value.isoformat(),
                        city=city.value.strip() or None,
                        note=note.value.strip(),
                    )
                else:
                    self.registry.scanned.add(
                        merchant=shop.value or "",
                        total=value,
                        timestamp=stamp_value.isoformat(),
                        city=city.value or "",
                        note=note.value or "",
                        fiscal=fiscal,
                    )
            except StoreError as exc:
                self.toast(str(exc), error=True)
                return
            self.close_dialog()
            if self.demo:
                self.set_demo(False)
            self.load_receipts()
            self.rerender()
            self.toast("Receipt saved" if not existing else "Receipt updated")

        intro = []
        if fiscal:
            intro.append(
                ft.Container(
                    ft.Row(
                        [
                            ft.Icon(ft.Icons.VERIFIED, color=ft.Colors.PRIMARY),
                            ft.Text(
                                f"Fiscal receipt · {data.fmt_money(fiscal.total, 'EUR')} · "
                                f"{fiscal.issued.strftime('%d %b %Y, %H:%M')}",
                                expand=True,
                            ),
                        ]
                    ),
                    padding=12,
                    border_radius=12,
                    bgcolor=ft.Colors.PRIMARY_CONTAINER,
                )
            )
            intro.append(
                muted(
                    "The QR code doesn't include the shop name. Pick or type it once; "
                    "it is suggested next time.",
                    size=12,
                )
            )
        self.page.show_dialog(
            ft.AlertDialog(
                title=ft.Text("Edit receipt" if existing else "New receipt"),
                content=ft.Container(
                    ft.Column(
                        [*intro, shop, suggestions, category_text, ft.Row([total, stamp]), city, note],
                        tight=True,
                        horizontal_alignment=ft.CrossAxisAlignment.STRETCH,
                        spacing=12,
                        scroll=ft.ScrollMode.AUTO,
                    ),
                    width=480,
                ),
                actions=[
                    ft.TextButton("Cancel", on_click=self.close_dialog),
                    ft.FilledButton("Save", icon=ft.Icons.CHECK, on_click=save),
                ],
                scrollable=True,
            )
        )

    # ================= products =================

    def view_products(self) -> ft.Control:
        rows = self.visible_receipts()
        all_products = data.products(rows)
        if not all_products:
            return self.page_frame(
                self.header("Products", "What you buy most"),
                surface(
                    muted(
                        "Item lists come from connected stores (Kaufland, Lidl). Scanned QR receipts only "
                        "carry the total, so they don't show up here."
                    ),
                    padding=24,
                ),
                self.welcome() if not self.receipts else ft.Container(),
            )
        listing = ft.ListView(spacing=2, expand=True, padding=ft.Padding.only(bottom=16))
        head = self.header("Products", "")

        def tile(p: data.Product) -> ft.Control:
            shops = ", ".join(sorted(m for m in p.merchants if m))
            return ft.ListTile(
                leading=avatar(p.name, ft.Colors.SECONDARY),
                title=ft.Text(
                    p.name, max_lines=1, overflow=ft.TextOverflow.ELLIPSIS, weight=ft.FontWeight.W_500
                ),
                subtitle=muted(
                    f"{p.count}× · last {data.fmt_money(p.last_price, p.currency)}"
                    + (f" · {shops}" if shops else "")
                    + (f" · {data.fmt_date(p.last_seen, with_time=False)}" if self.wide else ""),
                    size=12,
                    max_lines=1,
                    overflow=ft.TextOverflow.ELLIPSIS,
                ),
                trailing=ft.Text(data.fmt_money(p.spent, p.currency), weight=ft.FontWeight.W_600),
                shape=ft.RoundedRectangleBorder(radius=14),
            )

        def fill() -> None:
            q = self.product_query.lower()
            found = [p for p in all_products if q in p.name.lower()]
            listing.controls = [tile(p) for p in found[:400]] or [muted("No matches.")]
            head.controls[0].controls[1].value = f"{len(found)} products, sorted by money spent"

        fill()
        return self.page_frame(
            head,
            *[c for c in (self.filter_bar(),) if c],
            self.search_field("Search products", "product_query", fill),
            listing,
            scroll=False,
        )

    # ================= stores =================

    def view_stores(self) -> ft.Control:
        accounts = [self.store_card(s) for s in self.online_stores]

        o = data.overview(self.receipts, self.settings)
        shop_rows = [
            ft.ListTile(
                leading=avatar(name, self.color_for(name), 36),
                title=ft.Text(name, max_lines=1, overflow=ft.TextOverflow.ELLIPSIS),
                subtitle=muted(f"{n} receipts · {data.fmt_money(total, o.currency)}", size=12),
                trailing=ft.TextButton(
                    categories.label(self.settings.category(name)),
                    icon=ft.Icons.LABEL_OUTLINE,
                    on_click=lambda _e, name=name: self.pick_category(name),
                ),
                on_click=lambda _e, name=name: self.filter_merchant(name),
                shape=ft.RoundedRectangleBorder(radius=14),
                dense=True,
            )
            for name, total, n in o.merchants
        ] or [muted("Shops appear here once you have receipts.")]

        plugin_problems = [
            ft.Text(f"{p.path.name}: {p.error}", color=ft.Colors.ERROR, size=12)
            for p in self.registry.problems
        ]
        plugins = surface(
            ft.Column(
                [
                    title("Plug-ins"),
                    muted(
                        "Add another shop's online account with a plug-in: a single Python file that tells "
                        "the app how to sign in and download receipts. Plug-ins run with full access to "
                        "this app's "
                        "data, so only install ones you trust.",
                        size=12,
                    ),
                    *plugin_problems,
                    ft.Row(
                        [
                            ft.FilledTonalButton(
                                "Install from link",
                                icon=ft.Icons.LINK,
                                on_click=lambda _e: self.plugin_link_dialog(),
                            ),
                            ft.TextButton(
                                "From a file",
                                icon=ft.Icons.EXTENSION_OUTLINED,
                                on_click=lambda _e: self.page.run_task(self.install_plugin),
                            ),
                            ft.TextButton(
                                "How to write one",
                                icon=ft.Icons.MENU_BOOK_OUTLINED,
                                on_click=lambda _e: self.page.run_task(self.launcher.launch_url, PLUGIN_DOCS),
                            ),
                            ft.TextButton("Reload", icon=ft.Icons.REFRESH, on_click=self.reload_plugins),
                        ],
                        wrap=True,
                    ),
                    muted(f"Folder: {plugins_dir()}", size=11, selectable=True),
                ],
                spacing=12,
            )
        )
        return self.page_frame(
            self.header("Stores", "Accounts, shops and categories"),
            title("Online accounts"),
            ft.ResponsiveRow(
                [ft.Container(c, col={"xs": 12, "md": 6}) for c in accounts], spacing=12, run_spacing=12
            ),
            surface(
                ft.Column(
                    [
                        ft.Row(
                            [
                                ft.Icon(ft.Icons.QR_CODE_2, color=self.registry.scanned.color),
                                title("Any other shop"),
                            ]
                        ),
                        muted(
                            "Konzum, Studenac, Plodine, SPAR, Müller, Offertissima, IKEA, Decathlon, Shell… "
                            "Scan the QR code printed on the receipt; the shop is remembered and "
                            "categorised.",
                            size=12,
                        ),
                        ft.Row(
                            [
                                ft.FilledTonalButton(
                                    "Add a receipt",
                                    icon=ft.Icons.QR_CODE_SCANNER,
                                    on_click=lambda _e: self.add_menu(),
                                )
                            ]
                        ),
                    ],
                    spacing=10,
                )
            ),
            surface(
                ft.Column(
                    [
                        title("Shops & categories"),
                        muted(
                            "Tap a shop to see its receipts, or its label to change the category.", size=12
                        ),
                        *shop_rows,
                    ],
                    spacing=4,
                ),
                padding=12,
            ),
            plugins,
            ft.Container(height=12),
        )

    def store_card(self, store: Store) -> ft.Control:
        connected = store.connected()
        count = sum(1 for r in self.receipts if r.get("source") == store.id)
        actions: list[ft.Control] = []
        if connected:
            actions += [
                ft.FilledTonalButton(
                    "Fetch",
                    icon=ft.Icons.SYNC,
                    disabled=self.busy,
                    on_click=lambda _e: self.page.run_task(self.refresh, [store.id]),
                ),
                ft.TextButton("Disconnect", icon=ft.Icons.LOGOUT, on_click=lambda _e: self.disconnect(store)),
            ]
            if hasattr(store, "transfer_code"):
                actions.append(
                    ft.TextButton(
                        "Copy sign-in for another device",
                        icon=ft.Icons.DEVICES,
                        on_click=lambda _e: self.page.run_task(self.copy_transfer, store),
                    )
                )
        else:
            actions.append(
                ft.FilledButton("Connect", icon=ft.Icons.LINK, on_click=lambda _e: self.connect(store))
            )
        if store.plugin_path:
            actions.append(
                ft.TextButton(
                    "Remove plug-in",
                    icon=ft.Icons.DELETE_OUTLINE,
                    on_click=lambda _e: self.remove_plugin(store),
                )
            )
        status = (
            f"Connected{(' as ' + store.account_label()) if store.account_label() else ''} · {count} receipts"
            if connected
            else "Not connected"
        )
        return surface(
            ft.Column(
                [
                    ft.Row(
                        [
                            avatar(store.name, store.color, 44),
                            ft.Column(
                                [
                                    ft.Text(store.name, size=18, weight=ft.FontWeight.W_600),
                                    muted(store.description, size=12),
                                ],
                                spacing=0,
                                expand=True,
                            ),
                            badge("Plug-in", ft.Colors.TERTIARY) if store.plugin_path else ft.Container(),
                        ],
                        spacing=14,
                    ),
                    ft.Row(
                        [
                            ft.Icon(
                                ft.Icons.CHECK_CIRCLE if connected else ft.Icons.RADIO_BUTTON_UNCHECKED,
                                size=16,
                                color=ft.Colors.PRIMARY if connected else ft.Colors.OUTLINE,
                            ),
                            muted(status, size=12, expand=True),
                        ],
                        spacing=6,
                    ),
                    ft.Row(actions, wrap=True, spacing=6, run_spacing=6),
                ],
                spacing=12,
            ),
            padding=18,
        )

    def pick_category(self, merchant: str) -> None:
        current = self.settings.category(merchant)

        def choose(cat: str):
            def handler(_e):
                key = categories.normalise(merchant)
                if cat == categories.default_category(merchant):
                    self.settings.categories.pop(key, None)
                else:
                    self.settings.categories[key] = cat
                self.settings.save()
                self.close_dialog()
                self.rerender()

            return handler

        self.page.show_dialog(
            ft.AlertDialog(
                title=ft.Text(f"Category for {merchant}"),
                content=ft.Column(
                    [
                        ft.ListTile(
                            leading=ft.Container(width=14, height=14, border_radius=7, bgcolor=color),
                            title=ft.Text(label),
                            trailing=ft.Icon(ft.Icons.CHECK) if key == current else None,
                            on_click=choose(key),
                            dense=True,
                        )
                        for key, (label, color) in categories.CATEGORIES.items()
                    ],
                    tight=True,
                    scroll=ft.ScrollMode.AUTO,
                ),
                actions=[ft.TextButton("Cancel", on_click=self.close_dialog)],
            )
        )

    def disconnect(self, store: Store) -> None:
        store.logout()
        clear_cache(store.id)
        self.load_receipts()
        self.rerender()
        self.toast(f"{store.name} disconnected")

    async def copy_transfer(self, store: Store) -> None:
        try:
            code = store.transfer_code()
        except StoreError as exc:
            self.toast(str(exc), error=True)
            return
        await self.clipboard.set(code)
        self.toast(
            f"Copied. On the other device: Stores › {store.name} › Connect › “Paste a link or code”. "
            "Treat it like a password."
        )

    async def install_plugin(self) -> None:
        files = await self.file_picker.pick_files(
            dialog_title="Choose a plug-in (.py)",
            allowed_extensions=["py"],
            file_type=ft.FilePickerFileType.CUSTOM,
            with_data=True,
        )
        if not files:
            return
        f = files[0]
        source = f.bytes
        if source is None and f.path:
            source = await asyncio.to_thread(Path(f.path).read_bytes)
        try:
            await asyncio.to_thread(self.registry.install_plugin, f.name, source or b"")
        except StoreError as exc:
            self.toast(str(exc), error=True)
            return
        self.load_receipts()
        self.rerender()
        self.toast(f"Installed {f.name}")

    def plugin_link_dialog(self) -> None:
        field = ft.TextField(
            label="Link to the plug-in (.py)",
            hint_text="https://github.com/…/my_shop.py",
            multiline=True,
            min_lines=2,
            autofocus=True,
        )

        async def paste(_e):
            field.value = await self.clipboard.get() or ""
            self.page.update()

        async def submit(_e):
            url = (field.value or "").strip()
            try:
                name, source = await asyncio.to_thread(download_plugin, url)
                await asyncio.to_thread(self.registry.install_plugin, name, source)
            except StoreError as exc:
                self.toast(str(exc), error=True)
                return
            self.close_dialog()
            self.load_receipts()
            self.rerender()
            self.toast(f"Installed {name}")

        self.page.show_dialog(
            ft.AlertDialog(
                title=ft.Text("Install a plug-in"),
                content=ft.Container(
                    ft.Column(
                        [
                            muted(
                                "Paste a link to a plug-in file, for example on GitHub. Only install "
                                "plug-ins "
                                "from people you trust: they can read your saved sign-ins."
                            ),
                            field,
                        ],
                        tight=True,
                        spacing=12,
                        horizontal_alignment=ft.CrossAxisAlignment.STRETCH,
                    ),
                    width=460,
                ),
                actions=[
                    ft.TextButton("Paste", icon=ft.Icons.CONTENT_PASTE, on_click=paste),
                    ft.TextButton("Cancel", on_click=self.close_dialog),
                    ft.FilledButton("Install", icon=ft.Icons.DOWNLOAD, on_click=submit),
                ],
            )
        )

    def reload_plugins(self, _e=None) -> None:
        self.registry.reload()
        self.load_receipts()
        self.rerender()
        self.toast(
            "Plug-ins reloaded"
            + (f" · {len(self.registry.problems)} failed" if self.registry.problems else "")
        )

    def remove_plugin(self, store: Store) -> None:
        clear_cache(store.id)
        self.registry.remove_plugin(store)
        self.load_receipts()
        self.rerender()
        self.toast(f"Removed {store.name}")

    # ================= connecting =================

    def connect(self, store: Store) -> None:
        if store.auth == "password":
            self.password_dialog(store)
        elif store.auth == "browser":
            if self.page.platform in MOBILE and not self.page.web and self._webview_available():
                self.page.run_task(self.webview_login, store)
            elif self.page.platform in DESKTOP and not self.page.web:
                self.desktop_browser_login(store)
            else:
                self.paste_login_dialog(store)
        elif store.auth == "token":
            self.paste_login_dialog(store, token_only=True)
        else:
            self.toast(f"{store.name} needs no sign-in.")

    async def after_connect(self, store: Store, label: str) -> None:
        self.toast(f"{store.name} connected as {label}")
        if self.demo:
            self.set_demo(False)
        self.rerender()
        await self.refresh([store.id])

    def password_dialog(self, store: Store) -> None:
        email = ft.TextField(
            label="Email",
            prefix_icon=ft.Icons.ALTERNATE_EMAIL,
            keyboard_type=ft.KeyboardType.EMAIL,
            autofill_hints=[ft.AutofillHint.EMAIL],
            autofocus=True,
        )
        password = ft.TextField(
            label="Password",
            prefix_icon=ft.Icons.LOCK_OUTLINE,
            password=True,
            can_reveal_password=True,
            autofill_hints=[ft.AutofillHint.PASSWORD],
        )
        progress = ft.ProgressBar(visible=False, border_radius=4)
        button = ft.FilledButton("Sign in", icon=ft.Icons.LOGIN)

        async def submit(_e=None):
            if not email.value or not password.value:
                self.toast("Enter your email and password.", error=True)
                return
            for c in (email, password, button):
                c.disabled = True
            progress.visible = True
            self.page.update()
            loop = asyncio.get_running_loop()

            def otp() -> str:
                return asyncio.run_coroutine_threadsafe(self.ask_otp(store), loop).result(timeout=600)

            try:
                label = await asyncio.to_thread(store.login, email.value.strip(), password.value, otp=otp)
            except Exception as exc:  # noqa: BLE001
                for c in (email, password, button):
                    c.disabled = False
                progress.visible = False
                self.page.update()
                self.toast(
                    str(exc) if isinstance(exc, StoreError) else f"Unexpected error: {exc}", error=True
                )
                return
            self.close_dialog()
            await self.after_connect(store, label)

        button.on_click = submit
        password.on_submit = submit
        email.on_submit = lambda _e: password.focus()
        domain = {"kaufland": "account.kaufland.com"}.get(store.id)
        self.page.show_dialog(
            ft.AlertDialog(
                modal=True,
                title=ft.Row(
                    [avatar(store.name, store.color, 36), ft.Text(f"Connect {store.name}")], spacing=12
                ),
                content=ft.Container(
                    ft.Column(
                        [
                            muted(f"Sign in with your {store.name} account."),
                            self.country_dropdown(lambda v: None),
                            email,
                            password,
                            progress,
                            ft.Row(
                                [
                                    ft.Icon(
                                        ft.Icons.SHIELD_OUTLINED, size=16, color=ft.Colors.ON_SURFACE_VARIANT
                                    ),
                                    muted(
                                        f"Your password goes only to {domain}."
                                        if domain
                                        else "Your password goes only to the store.",
                                        size=12,
                                        expand=True,
                                    ),
                                ]
                            ),
                        ],
                        tight=True,
                        horizontal_alignment=ft.CrossAxisAlignment.STRETCH,
                        spacing=14,
                    ),
                    width=420,
                ),
                actions=[ft.TextButton("Cancel", on_click=self.close_dialog), button],
            )
        )

    async def ask_otp(self, store: Store) -> str:
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
                content=ft.Column([muted(f"{store.name} sent you a verification code."), field], tight=True),
                actions=[ft.FilledButton("Verify", on_click=done)],
            )
        )
        return await future

    def _webview_available(self) -> bool:
        try:
            import flet_webview  # noqa: F401
        except ImportError:
            return False
        return True

    async def finish_browser_login(self, store: Store, callback: str) -> None:
        try:
            label = await asyncio.to_thread(store.complete_login, callback)
        except StoreError as exc:
            self.toast(str(exc), error=True)
            return
        await self.after_connect(store, label)

    async def webview_login(self, store: Store) -> None:
        import flet_webview as fw

        url = store.login_url()
        prefix = store.callback_prefix()
        done = {"value": False}

        def check(value) -> None:
            text = str(value or "")
            index = text.find(prefix)
            if index < 0 or done["value"]:
                return
            done["value"] = True
            callback = text[index:].split()[0].strip("\"'")
            self.close_dialog()
            self.page.run_task(self.finish_browser_login, store, callback)

        def on_event(e) -> None:
            check(getattr(e, "data", None))

        view = fw.WebView(
            url=url,
            expand=True,
            prevent_links=[prefix],
            on_page_started=on_event,
            on_url_change=on_event,
            on_web_resource_error=on_event,
            on_page_ended=on_event,
        )
        self.page.show_dialog(
            ft.AlertDialog(
                title=ft.Text(f"Sign in to {store.name}"),
                content=ft.Container(view, width=420, height=620),
                content_padding=0,
                actions=[
                    ft.TextButton(
                        "Paste a link or code instead",
                        on_click=lambda _e: (self.close_dialog(), self.paste_login_dialog(store)),
                    ),
                    ft.TextButton("Cancel", on_click=self.close_dialog),
                ],
                inset_padding=ft.Padding.all(8),
            )
        )
        # Some redirects to the app link don't fire an event; poll the current URL as a fallback.
        for _ in range(900):
            await asyncio.sleep(1)
            if done["value"]:
                return
            try:
                check(await view.get_current_url())
            except Exception:  # noqa: BLE001 - the dialog was closed or the view is gone
                return

    def desktop_browser_login(self, store: Store) -> None:
        url = store.login_url()
        cancel = threading.Event()
        status = muted("Opening a browser window…")
        loop = asyncio.get_event_loop()

        def worker():
            try:
                callback = browser_login.capture_callback(
                    url, store.callback_prefix(), cancelled=cancel.is_set
                )
            except browser_login.BrowserLoginError as exc:
                if cancel.is_set():
                    return
                message = str(exc)

                def failed():
                    status.value = (
                        f"{message} You can still sign in in any browser and paste the result below."
                    )
                    self.page.update()

                loop.call_soon_threadsafe(failed)
                return

            def finished():
                self.close_dialog()
                self.page.run_task(self.finish_browser_login, store, callback)

            loop.call_soon_threadsafe(finished)

        def stop(_e=None):
            cancel.set()
            self.close_dialog()

        def manual(_e):
            cancel.set()
            self.close_dialog()
            self.paste_login_dialog(store)

        self.page.show_dialog(
            ft.AlertDialog(
                modal=True,
                title=ft.Row(
                    [avatar(store.name, store.color, 36), ft.Text(f"Connect {store.name}")], spacing=12
                ),
                content=ft.Container(
                    ft.Column(
                        [
                            ft.ProgressBar(border_radius=4),
                            ft.Text(
                                f"Sign in to {store.name} in the browser window that just opened "
                                "(password and verification code). It closes by itself when you're done."
                            ),
                            status,
                            muted(
                                "A fresh browser profile is used and deleted afterwards. Your password goes "
                                "only to the store's sign-in page.",
                                size=12,
                            ),
                        ],
                        tight=True,
                        horizontal_alignment=ft.CrossAxisAlignment.STRETCH,
                        spacing=14,
                    ),
                    width=440,
                ),
                actions=[
                    ft.TextButton("Paste a link or code instead", on_click=manual),
                    ft.TextButton("Cancel", on_click=stop),
                ],
            )
        )
        if browser_login.find_browser() is None:
            status.value = "No Chrome, Edge or Brave browser found. Use “Paste a link or code instead”."
            self.page.update()
            return
        status.value = "Waiting for you to finish signing in…"
        threading.Thread(target=worker, daemon=True).start()

    def paste_login_dialog(self, store: Store, *, token_only: bool = False) -> None:
        field = ft.TextField(label="Link or code", multiline=True, min_lines=2, autofocus=True)
        if token_only:
            help_lines = [muted(f"Paste the API token for {store.name}.")]
        else:
            url = store.login_url()
            help_lines = [
                muted("Two ways to connect without the automatic sign-in:"),
                ft.Text(
                    f"• On a phone or PC where {store.name} is already connected in this app, choose "
                    "“Copy sign-in for another device” and paste the code here."
                ),
                ft.Text(
                    "• Or open the sign-in link below in Chrome or Edge, open DevTools (F12) › Network "
                    f"before signing in, then copy the request that starts with {store.callback_prefix()} "
                    "and paste it here."
                ),
                ft.Row(
                    [
                        ft.TextButton(
                            "Open sign-in link",
                            icon=ft.Icons.OPEN_IN_NEW,
                            on_click=lambda _e: self.page.run_task(self.launcher.launch_url, url),
                        ),
                        ft.TextButton(
                            "Copy sign-in link",
                            icon=ft.Icons.COPY,
                            on_click=lambda _e: self.page.run_task(self.clipboard.set, url),
                        ),
                    ],
                    wrap=True,
                ),
            ]

        async def paste(_e):
            field.value = await self.clipboard.get() or ""
            self.page.update()

        async def submit(_e):
            value = (field.value or "").strip()
            if not value:
                return
            try:
                if not token_only and value.startswith(store.callback_prefix() or "\0"):
                    label = await asyncio.to_thread(store.complete_login, value)
                else:
                    label = await asyncio.to_thread(store.set_token, value)
            except (StoreError, NotImplementedError) as exc:
                self.toast(str(exc) or "This store does not accept codes.", error=True)
                return
            self.close_dialog()
            await self.after_connect(store, label)

        self.page.show_dialog(
            ft.AlertDialog(
                title=ft.Text(f"Connect {store.name}"),
                content=ft.Container(
                    ft.Column(
                        [*help_lines, field],
                        tight=True,
                        spacing=10,
                        horizontal_alignment=ft.CrossAxisAlignment.STRETCH,
                    ),
                    width=480,
                ),
                actions=[
                    ft.TextButton("Paste", icon=ft.Icons.CONTENT_PASTE, on_click=paste),
                    ft.TextButton("Cancel", on_click=self.close_dialog),
                    ft.FilledButton("Connect", on_click=submit),
                ],
            )
        )

    # ================= settings =================

    def country_dropdown(self, on_change: Callable[[str], None] | None = None) -> ft.Dropdown:
        def changed(e):
            self.settings.country = e.control.value
            self.settings.save()
            if on_change:
                on_change(e.control.value)

        return ft.Dropdown(
            label="Country",
            value=self.settings.country,
            leading_icon=ft.Icons.PUBLIC,
            expand=True,
            options=[
                ft.DropdownOption(key=code, text=f"{name} ({code})") for code, name in data.COUNTRIES.items()
            ],
            on_select=changed,
        )

    def set_demo(self, on: bool) -> None:
        self.demo = on
        self.merchant_filter = None
        if on:
            self.receipts = data.demo_receipts()
            self.toast("Demo mode: made-up receipts, nothing is sent anywhere.")
        else:
            self.load_receipts()
        self.rerender()

    def view_settings(self) -> ft.Control:
        s = self.settings
        country = self.country_dropdown(lambda v: self.toast(f"Country set to {v}. Fetch again to reload."))

        limit = ft.Dropdown(
            label="Receipts per store and fetch",
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
                    "All data (JSON)",
                    icon=ft.Icons.DATA_OBJECT,
                    on_click=lambda _e: self.page.run_task(self.export, "receipts-json"),
                ),
            ],
            wrap=True,
            spacing=8,
            run_spacing=8,
        )

        def section(name: str, *controls: ft.Control) -> ft.Control:
            return surface(ft.Column([title(name), *controls], spacing=14))

        demo = (
            ft.Row(
                [
                    ft.OutlinedButton(
                        "Leave demo mode", icon=ft.Icons.LOGOUT, on_click=lambda _e: self.set_demo(False)
                    )
                ]
            )
            if self.demo
            else ft.Row(
                [
                    ft.TextButton(
                        "Explore with demo data",
                        icon=ft.Icons.AUTO_AWESOME,
                        on_click=lambda _e: self.set_demo(True),
                    )
                ]
            )
        )
        return self.page_frame(
            self.header("Settings", "Fetching, export and appearance"),
            section(
                "Fetching",
                ft.ResponsiveRow(
                    [
                        ft.Container(country, col={"xs": 12, "md": 6}),
                        ft.Container(limit, col={"xs": 12, "md": 6}),
                    ],
                    run_spacing=12,
                ),
                muted(
                    "Kaufland and Lidl use unofficial app APIs. Full history can take a while, so go easy "
                    "on it.",
                    size=12,
                ),
            ),
            section(
                "Export",
                muted(
                    f"{len(self.visible_receipts())} receipts"
                    + (f" from {self.merchant_filter}" if self.merchant_filter else "")
                ),
                exports,
            ),
            section("Appearance", ft.Row([theme_picker], scroll=ft.ScrollMode.AUTO)),
            section("Data", muted(f"Stored on this device in {data_dir()}", size=12, selectable=True), demo),
            ft.Row(
                [muted("Unofficial app · not affiliated with any of the stores", size=12)],
                alignment=ft.MainAxisAlignment.CENTER,
            ),
            ft.Container(height=12),
        )

    async def export(self, kind: str) -> None:
        rows = self.visible_receipts()
        if not rows:
            self.toast("No receipts to export yet.", error=True)
            return
        text = data.export_text(rows, kind, self.settings)
        name, ext = data.EXPORTS[kind]
        payload = text.encode("utf-8")
        try:
            path = await self.file_picker.save_file(
                dialog_title="Export", file_name=f"{name}.{ext}", allowed_extensions=[ext], src_bytes=payload
            )
            if path and self.page.platform in DESKTOP and not self.page.web:
                with open(path, "wb") as fh:
                    fh.write(payload)
            if path:
                self.toast(f"Saved {name}.{ext}")
        except Exception as exc:  # noqa: BLE001
            await self.clipboard.set(text)
            self.toast(f"Could not save a file ({exc}); copied to clipboard instead.")


def download_plugin(url: str) -> tuple[str, bytes]:
    """Fetch a plug-in file over HTTPS. GitHub page links are turned into raw-file links."""
    parts = urllib.parse.urlparse(url)
    if parts.scheme != "https" or not parts.path.endswith(".py"):
        raise StoreError("Use an https:// link to a .py file.")
    if parts.netloc == "github.com" and "/blob/" in parts.path:
        user_repo, _, rest = parts.path.lstrip("/").partition("/blob/")
        url = f"https://raw.githubusercontent.com/{user_repo}/{rest}"
    try:
        with urllib.request.urlopen(
            urllib.request.Request(url, headers={"User-Agent": "receipts-app"}), timeout=30
        ) as response:
            source = response.read(1_000_001)
    except OSError as exc:
        raise StoreError(f"Could not download the plug-in: {exc}") from exc
    if len(source) > 1_000_000:
        raise StoreError("That file is too big to be a plug-in.")
    return Path(parts.path).name, source


def main(page: ft.Page) -> None:
    App(page).start()
