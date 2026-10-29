"""Shared visual identity for service marks."""

import hashlib


def service_mark(name):
    accents = ("purple", "pink", "cyan", "green", "orange", "yellow", "red")
    base = name.removesuffix(" Amazon Channel").strip()
    words = base.split()
    initials = "".join(word[0] for word in words[:2]) if len(words) > 1 else base[:2]
    return {"initials": initials.upper(),
            "accent": accents[hashlib.sha256(name.encode()).digest()[0] % len(accents)]}


SHOW_GROUPS = {
    "Watching": ("active", "occasional"),
    "Waiting": ("anticipated",),
    "Done": ("completed", "abandoned", "dormant", "archived", "ignored"),
}


def library_states(state="All", group="All"):
    """An exact legacy state takes precedence over the broad library filter."""
    return (state.lower(),) if state and state != "All" else SHOW_GROUPS.get(group, ())


def ledger_reason(reason):
    """Short ledger copy; analysis and email wording stay unchanged."""
    if reason.startswith("Annual plan prepaid through") or reason.startswith("Free ($0)"):
        return ""
    for activity, copy in (("High", "You watch it a lot"),
                           ("Medium", "You watch it sometimes"), ("Low", "You rarely watch it")):
        reason = reason.replace(f"Viewer activity: {activity}", copy)
    return reason
