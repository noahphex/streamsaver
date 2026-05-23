"""
gui/services_page.py
Streamsaver — Streaming Services management tab
Author: Noah Lee

Displays all services in a Treeview with status, cost and billing date.
Allows adding, editing, deleting services and changing their status.
Service names are selected from a searchable dropdown of TMDb-sourced
providers, ensuring exact name matching during show sync.
"""

import tkinter as tk
from tkinter import ttk, messagebox
from datetime import date

from PIL import Image, ImageTk

from gui import theme
from gui.widgets import SectionHeader
from core.service_manager import (
    add_service, update_service, delete_service,
    get_all_services, get_cost_totals, VALID_STATUSES,
    get_cached_network_names, get_network_provider_id,
    get_bundle_providers, set_bundle_providers,
)

STATUSES = ["subscribed", "suspended", "unsubscribed", "not_subscribed"]

_ICON_SIZE = (24, 24)   # px displayed in treeview rows

COL_DEFS = [
    ("",             32,  "center"),   # icon column — no heading text
    ("Name",        200,  "w"),
    ("Status",      120,  "center"),
    ("Monthly ($)",  90,  "e"),
    ("Annual ($)",   90,  "e"),
    ("Next Billing",110,  "center"),
]


def _load_icon(path: str | None) -> ImageTk.PhotoImage | None:
    """Load and resize a provider logo. Returns None on any failure."""
    if not path:
        return None
    try:
        img = Image.open(path).convert("RGBA").resize(_ICON_SIZE, Image.LANCZOS)
        return ImageTk.PhotoImage(img)
    except Exception:
        return None


# ── Services tab ───────────────────────────────────────────────────────────────

class ServicesPage(ttk.Frame):
    """Tab: manage streaming services."""

    def __init__(self, parent, **kwargs):
        super().__init__(parent, **kwargs)
        self.configure(style="TFrame")
        self._icon_cache: dict[str, ImageTk.PhotoImage] = {}   # keep refs alive
        self._build_ui()
        self.refresh()

    # ── Layout ─────────────────────────────────────────────────────────────────

    def _build_ui(self) -> None:
        # Header row
        header = ttk.Frame(self, style="TFrame")
        header.pack(fill="x", padx=theme.PAD, pady=(theme.PAD, 0))
        SectionHeader(header, text="Streaming Services").pack(side="left")
        ttk.Button(header, text="+ Add Service", style="Accent.TButton",
                   command=self._on_add).pack(side="right", padx=(theme.PAD_SMALL, 0))
        ttk.Button(header, text="✎ Edit Selected",
                   command=self._on_edit).pack(side="right", padx=(theme.PAD_SMALL, 0))
        ttk.Button(header, text="✕ Delete", style="Danger.TButton",
                   command=self._on_delete).pack(side="right", padx=(theme.PAD_SMALL, 0))

        # Treeview
        tree_frame = ttk.Frame(self, style="TFrame")
        tree_frame.pack(fill="both", expand=True, padx=theme.PAD, pady=theme.PAD_SMALL)

        self._tree = ttk.Treeview(
            tree_frame,
            columns=[c[0] for c in COL_DEFS],
            show="headings",
            selectmode="browse",
        )
        for col, width, anchor in COL_DEFS:
            # Icon column gets no heading and no sort
            if col == "":
                self._tree.heading(col, text="")
            else:
                self._tree.heading(col, text=col,
                                   command=lambda c=col: self._sort_by(c))
            self._tree.column(col, width=width, anchor=anchor,
                              minwidth=width, stretch=(col == "Name"))

        # Tag-based status colors
        for status, color in theme.STATUS_COLORS.items():
            self._tree.tag_configure(status, foreground=color)

        vsb = ttk.Scrollbar(tree_frame, orient="vertical",   command=self._tree.yview)
        hsb = ttk.Scrollbar(tree_frame, orient="horizontal",  command=self._tree.xview)
        self._tree.configure(yscrollcommand=vsb.set, xscrollcommand=hsb.set)
        self._tree.grid(row=0, column=0, sticky="nsew")
        vsb.grid(row=0, column=1, sticky="ns")
        hsb.grid(row=1, column=0, sticky="ew")
        tree_frame.rowconfigure(0, weight=1)
        tree_frame.columnconfigure(0, weight=1)
        self._tree.bind("<Double-1>", lambda e: self._on_edit())

        # Cost summary bar
        cost_frame = ttk.Frame(self, style="Alt.TFrame")
        cost_frame.pack(fill="x", padx=theme.PAD, pady=(0, theme.PAD))
        self._monthly_lbl = ttk.Label(cost_frame, style="TLabel", font=theme.FONT_BOLD)
        self._annual_lbl  = ttk.Label(cost_frame, style="TLabel", font=theme.FONT_BOLD)
        ttk.Label(cost_frame, text="Subscribed services — ",
                  style="Dim.TLabel").pack(side="left", padx=(theme.PAD, 0), pady=4)
        self._monthly_lbl.pack(side="left", padx=(0, theme.PAD))
        ttk.Label(cost_frame, text="|", style="Dim.TLabel").pack(side="left")
        self._annual_lbl.pack(side="left", padx=theme.PAD)

        self._sort_col = "Name"
        self._sort_asc = True

    # ── Data ───────────────────────────────────────────────────────────────────

    def refresh(self) -> None:
        """Reload service data from DB and repopulate the tree."""
        for item in self._tree.get_children():
            self._tree.delete(item)

        # Rebuild icon cache for current services only (prevents unbounded growth)
        self._icon_cache.clear()

        services = get_all_services()
        for svc in services:
            icon_path = svc.get("icon_path")
            img = _load_icon(icon_path) if icon_path else None
            if img:
                self._icon_cache[str(svc["id"])] = img

            status_label = theme.STATUS_LABELS.get(svc["status"], svc["status"])
            self._tree.insert(
                "", "end",
                iid=str(svc["id"]),
                image=self._icon_cache.get(str(svc["id"]), ""),
                values=(
                    "",                          # icon column — image shown via `image=`
                    svc["name"],
                    status_label,
                    f"{svc['monthly_cost']:.2f}" if svc["monthly_cost"] else "—",
                    f"{svc['annual_cost']:.2f}"  if svc["annual_cost"]  else "—",
                    svc["next_billing_date"] or "—",
                ),
                tags=(svc["status"],),
            )

        totals = get_cost_totals(status_filter="subscribed")
        self._monthly_lbl.config(text=f"${totals['monthly_total']:.2f}/mo",
                                  foreground=theme.GREEN)
        self._annual_lbl.config(text=f"${totals['annual_total']:.2f}/yr",
                                 foreground=theme.CYAN)

    def _selected_service_id(self) -> int | None:
        sel = self._tree.selection()
        return int(sel[0]) if sel else None

    def _sort_by(self, col: str) -> None:
        if self._sort_col == col:
            self._sort_asc = not self._sort_asc
        else:
            self._sort_col = col
            self._sort_asc = True
        items = [(self._tree.set(k, col), k) for k in self._tree.get_children("")]
        items.sort(reverse=not self._sort_asc)
        for idx, (_, k) in enumerate(items):
            self._tree.move(k, "", idx)

    # ── Actions ────────────────────────────────────────────────────────────────

    def _on_add(self) -> None:
        network_names = get_cached_network_names()
        dlg = ServiceDialog(self, title="Add Service", network_names=network_names)
        self.wait_window(dlg)
        if dlg.result:
            try:
                bundle_providers = dlg.result.pop("bundle_providers", [])
                new_id = add_service(**dlg.result)
                if bundle_providers:
                    set_bundle_providers(new_id, bundle_providers)
                self.refresh()
            except Exception as exc:
                messagebox.showerror("Error", str(exc), parent=self)

    def _on_edit(self) -> None:
        sid = self._selected_service_id()
        if sid is None:
            messagebox.showinfo("No selection", "Please select a service to edit.",
                                parent=self)
            return
        services = {s["id"]: s for s in get_all_services()}
        svc = services.get(sid)
        if not svc:
            return
        network_names = get_cached_network_names()
        dlg = ServiceDialog(self, title="Edit Service", service=svc,
                            network_names=network_names)
        self.wait_window(dlg)
        if dlg.result:
            try:
                bundle_providers = dlg.result.pop("bundle_providers", [])
                update_service(sid, **dlg.result)
                set_bundle_providers(sid, bundle_providers)
                self.refresh()
            except Exception as exc:
                messagebox.showerror("Error", str(exc), parent=self)

    def _on_delete(self) -> None:
        sid = self._selected_service_id()
        if sid is None:
            messagebox.showinfo("No selection", "Please select a service to delete.",
                                parent=self)
            return
        name = self._tree.item(str(sid), "values")[1]   # index 1 = Name (icon is index 0)
        if messagebox.askyesno(
            "Delete Service",
            f"Delete '{name}'?\n\nShows assigned to this service will be unlinked.",
            parent=self,
        ):
            delete_service(sid)
            self.refresh()


# ── Add / Edit dialog ──────────────────────────────────────────────────────────

class ServiceDialog(tk.Toplevel):
    """
    Modal dialog for adding or editing a streaming service.

    The service name is chosen from a searchable combobox populated with
    the cached TMDb provider list, guaranteeing the stored name matches
    exactly what TMDb returns for show provider lookups.
    """

    def __init__(
        self,
        parent,
        title: str,
        service: dict | None = None,
        network_names: list[str] | None = None,
    ):
        super().__init__(parent)
        self.title(title)
        self.configure(bg=theme.BG)
        self.resizable(False, True)
        self.result: dict | None = None
        self._svc          = service or {}
        self._all_networks = network_names or []
        self._icon_img: ImageTk.PhotoImage | None = None   # keep ref
        # Bundle state: list of {tmdb_provider_id, provider_name}
        self._bundle: list[dict] = list(get_bundle_providers(self._svc["id"])) if self._svc.get("id") else []
        self._build()
        self.grab_set()
        self.transient(parent)
        self.update_idletasks()
        px, py = parent.winfo_rootx(), parent.winfo_rooty()
        pw, ph = parent.winfo_width(), parent.winfo_height()
        w, h   = self.winfo_width(), self.winfo_height()
        self.geometry(f"+{px + pw//2 - w//2}+{py + ph//2 - h//2}")

    def _entry_row(self, frame, label: str, row: int) -> ttk.Entry:
        ttk.Label(frame, text=label, style="TLabel").grid(
            row=row, column=0, sticky="w", padx=(0, theme.PAD), pady=4
        )
        entry = ttk.Entry(frame, width=32)
        entry.grid(row=row, column=1, sticky="ew", pady=4)
        return entry

    def _build(self) -> None:
        f = ttk.Frame(self, style="TFrame", padding=theme.PAD * 2)
        f.pack(fill="both", expand=True)
        f.columnconfigure(1, weight=1)

        # ── Service name: searchable provider picker ─────────────────────
        ttk.Label(f, text="Service / Network:", style="TLabel").grid(
            row=0, column=0, sticky="w", padx=(0, theme.PAD), pady=4
        )
        name_frame = ttk.Frame(f, style="TFrame")
        name_frame.grid(row=0, column=1, sticky="ew", pady=4)
        name_frame.columnconfigure(0, weight=1)

        self._name_var = tk.StringVar()
        self._name_cb  = ttk.Combobox(
            name_frame, textvariable=self._name_var,
            values=self._all_networks, width=30,
        )
        self._name_cb.grid(row=0, column=0, sticky="ew")

        # Icon preview next to the combobox
        self._icon_lbl = ttk.Label(name_frame, style="TLabel")
        self._icon_lbl.grid(row=0, column=1, padx=(theme.PAD_SMALL, 0))

        if self._all_networks:
            hint = f"({len(self._all_networks)} SVOD providers — type to filter)"
        else:
            hint = "(No provider cache — go to Settings → Refresh Provider List)"
        tk.Label(name_frame, text=hint, bg=theme.BG, fg=theme.FG_DIM,
                 font=theme.FONT_SMALL).grid(row=1, column=0, sticky="w")

        # Live filter + icon preview update on name change
        self._name_var.trace_add("write", self._on_name_change)

        # ── Cost / billing fields ────────────────────────────────────────
        # Billing cycle selector
        ttk.Label(f, text="Billing Cycle:", style="TLabel").grid(
            row=1, column=0, sticky="w", padx=(0, theme.PAD), pady=4
        )
        self._cycle_var = tk.StringVar(value="monthly")
        cycle_frame = ttk.Frame(f, style="TFrame")
        cycle_frame.grid(row=1, column=1, sticky="w", pady=4)
        ttk.Radiobutton(cycle_frame, text="Monthly", variable=self._cycle_var,
                        value="monthly", command=self._on_cycle_change).pack(side="left", padx=(0, theme.PAD))
        ttk.Radiobutton(cycle_frame, text="Annual", variable=self._cycle_var,
                        value="annual", command=self._on_cycle_change).pack(side="left")

        self._monthly_lbl_w = ttk.Label(f, text="Monthly Cost ($):", style="TLabel")
        self._monthly_lbl_w.grid(row=2, column=0, sticky="w", padx=(0, theme.PAD), pady=4)
        self._monthly = ttk.Entry(f, width=32)
        self._monthly.grid(row=2, column=1, sticky="ew", pady=4)

        self._annual_lbl_w = ttk.Label(f, text="Annual Cost ($):", style="TLabel")
        self._annual_lbl_w.grid(row=3, column=0, sticky="w", padx=(0, theme.PAD), pady=4)
        self._annual = ttk.Entry(f, width=32)
        self._annual.grid(row=3, column=1, sticky="ew", pady=4)

        self._billing = self._entry_row(f, "Billing Anchor Date\n(YYYY-MM-DD):", 4)

        # ── Status ────────────────────────────────────────────────────────
        ttk.Label(f, text="Status:", style="TLabel").grid(
            row=5, column=0, sticky="w", padx=(0, theme.PAD), pady=4
        )
        self._status_var = tk.StringVar()
        ttk.Combobox(
            f, textvariable=self._status_var,
            values=STATUSES, state="readonly", width=30,
        ).grid(row=5, column=1, sticky="ew", pady=4)

        # ── Bundle Providers ─────────────────────────────────────────────
        ttk.Separator(f, orient="horizontal").grid(
            row=6, column=0, columnspan=2, sticky="ew", pady=(theme.PAD, 4)
        )
        bundle_lf = ttk.LabelFrame(f, text="Bundle / Included Providers", padding=6)
        bundle_lf.grid(row=7, column=0, columnspan=2, sticky="ew", pady=(0, 4))
        bundle_lf.columnconfigure(0, weight=1)

        tk.Label(
            bundle_lf,
            text="Providers listed here are treated as part of this subscription.\n"
                 "e.g. Map 'Disney Plus' → Hulu if you subscribe to the Disney Bundle.",
            bg=theme.BG, fg=theme.FG_DIM, font=theme.FONT_SMALL, justify="left",
        ).grid(row=0, column=0, columnspan=2, sticky="w", pady=(0, 4))

        list_frame = ttk.Frame(bundle_lf, style="TFrame")
        list_frame.grid(row=1, column=0, columnspan=2, sticky="ew")
        list_frame.columnconfigure(0, weight=1)

        self._bundle_lb = tk.Listbox(
            list_frame, height=4, selectmode="single",
            bg=theme.BG_ALT, fg=theme.FG, relief="flat",
            highlightthickness=1, highlightcolor=theme.PURPLE,
            font=theme.FONT_NORMAL,
        )
        self._bundle_lb.grid(row=0, column=0, sticky="ew")
        bundle_sb = ttk.Scrollbar(list_frame, orient="vertical", command=self._bundle_lb.yview)
        bundle_sb.grid(row=0, column=1, sticky="ns")
        self._bundle_lb.configure(yscrollcommand=bundle_sb.set)

        add_frame = ttk.Frame(bundle_lf, style="TFrame")
        add_frame.grid(row=2, column=0, columnspan=2, sticky="ew", pady=(4, 0))
        add_frame.columnconfigure(0, weight=1)

        self._bundle_add_var = tk.StringVar()
        self._bundle_cb = ttk.Combobox(
            add_frame, textvariable=self._bundle_add_var,
            values=self._all_networks, width=28,
        )
        self._bundle_cb.grid(row=0, column=0, sticky="ew", padx=(0, theme.PAD_SMALL))
        self._bundle_add_var.trace_add("write", self._on_bundle_filter)

        ttk.Button(add_frame, text="+ Add", command=self._bundle_add).grid(
            row=0, column=1, padx=(0, theme.PAD_SMALL)
        )
        ttk.Button(add_frame, text="✕ Remove", style="Danger.TButton",
                   command=self._bundle_remove).grid(row=0, column=2)

        # ── Pre-fill for edit mode ────────────────────────────────────────
        if self._svc:
            self._name_var.set(self._svc.get("name", ""))
            cycle = self._svc.get("billing_cycle") or "monthly"
            self._cycle_var.set(cycle)
            if cycle == "annual":
                self._monthly.insert(0, "")
                self._annual.insert(0,  str(self._svc.get("annual_cost", "") or ""))
            else:
                self._monthly.insert(0, str(self._svc.get("monthly_cost", "") or ""))
                self._annual.insert(0,  "")
            anchor = self._svc.get("billing_anchor_date") or self._svc.get("next_billing_date") or ""
            self._billing.insert(0, anchor)
            self._status_var.set(self._svc.get("status", "not_subscribed"))
            self._update_icon_preview(self._svc.get("icon_path"))
            self._on_cycle_change()
        else:
            self._status_var.set("subscribed")

        self._refresh_bundle_listbox()

        # ── Buttons ───────────────────────────────────────────────────────
        btn_frame = ttk.Frame(f, style="TFrame")
        btn_frame.grid(row=9, column=0, columnspan=2, pady=(theme.PAD, 0))
        ttk.Button(btn_frame, text="Save", style="Accent.TButton",
                   command=self._on_save).pack(side="left", padx=theme.PAD_SMALL)
        ttk.Button(btn_frame, text="Cancel",
                   command=self.destroy).pack(side="left", padx=theme.PAD_SMALL)

    def _refresh_bundle_listbox(self) -> None:
        self._bundle_lb.delete(0, "end")
        for p in self._bundle:
            self._bundle_lb.insert("end", f"{p['provider_name']}  (id: {p['tmdb_provider_id']})")

    def _on_bundle_filter(self, *_) -> None:
        typed = self._bundle_add_var.get().lower()
        filtered = [n for n in self._all_networks if typed in n.lower()] if typed else self._all_networks
        self._bundle_cb["values"] = filtered

    def _bundle_add(self) -> None:
        name = self._bundle_add_var.get().strip()
        if not name:
            return
        pid = get_network_provider_id(name)
        if pid is None:
            messagebox.showwarning(
                "Unknown Provider",
                f"'{name}' was not found in the provider cache.\n"
                "Refresh Provider List in Settings first.",
                parent=self,
            )
            return
        if any(p["tmdb_provider_id"] == pid for p in self._bundle):
            return
        self._bundle.append({"tmdb_provider_id": pid, "provider_name": name})
        self._refresh_bundle_listbox()
        self._bundle_add_var.set("")

    def _bundle_remove(self) -> None:
        sel = self._bundle_lb.curselection()
        if not sel:
            return
        idx = sel[0]
        if 0 <= idx < len(self._bundle):
            del self._bundle[idx]
            self._refresh_bundle_listbox()

    def _on_name_change(self, *_) -> None:
        """Filter the dropdown and update the icon preview."""
        typed = self._name_var.get().lower()
        filtered = [n for n in self._all_networks if typed in n.lower()] if typed else self._all_networks
        self._name_cb["values"] = filtered
        if filtered and typed:
            try:
                self._name_cb.event_generate("<Down>")
            except Exception:
                pass

        # Update icon preview from local cache
        from core.service_manager import get_icon_for_service_name
        icon_path = get_icon_for_service_name(self._name_var.get().strip())
        self._update_icon_preview(icon_path)

    def _update_icon_preview(self, icon_path: str | None) -> None:
        img = _load_icon(icon_path)
        if img:
            self._icon_img = img
            self._icon_lbl.configure(image=img, text="")
        else:
            self._icon_img = None
            self._icon_lbl.configure(image="", text="")

    def _on_cycle_change(self) -> None:
        """Show/hide cost fields based on billing cycle selection."""
        cycle = self._cycle_var.get()
        if cycle == "annual":
            self._monthly_lbl_w.grid_remove()
            self._monthly.grid_remove()
            self._annual_lbl_w.grid()
            self._annual.grid()
        else:
            self._annual_lbl_w.grid_remove()
            self._annual.grid_remove()
            self._monthly_lbl_w.grid()
            self._monthly.grid()

    def _on_save(self) -> None:
        name = self._name_var.get().strip()
        if not name:
            messagebox.showwarning("Validation", "Service name is required.", parent=self)
            return

        def _float(val: str) -> float:
            try:
                return float(val.strip()) if val.strip() else 0.0
            except ValueError:
                return 0.0

        billing = self._billing.get().strip() or None
        if billing:
            try:
                date.fromisoformat(billing)
            except ValueError:
                messagebox.showwarning(
                    "Validation",
                    "Billing date must be in YYYY-MM-DD format.",
                    parent=self,
                )
                return

        cycle = self._cycle_var.get()
        monthly_cost = _float(self._monthly.get()) if cycle == "monthly" else 0.0
        annual_cost  = _float(self._annual.get())  if cycle == "annual"  else 0.0

        self.result = {
            "name":                name,
            "trakt_network_id":    name,
            "tmdb_provider_id":    get_network_provider_id(name),
            "monthly_cost":        monthly_cost,
            "annual_cost":         annual_cost,
            "next_billing_date":   billing,
            "billing_cycle":       cycle,
            "billing_anchor_date": billing,
            "status":              self._status_var.get() or "subscribed",
            "bundle_providers":    list(self._bundle),
        }
        self.destroy()
