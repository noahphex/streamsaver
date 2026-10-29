"""Shared visual identity for service marks."""

import hashlib


def service_mark(name):
    accents = ("purple", "pink", "cyan", "green", "orange", "yellow", "red")
    return {"initials": "".join(word[0] for word in name.split()[:2]).upper(),
            "accent": accents[hashlib.sha256(name.encode()).digest()[0] % len(accents)]}
