"""Presentation data for the dashboard's next-30-days billing rail."""

from datetime import date, timedelta

from core.presentation import service_mark


def build_rail(timeline, today=None, days=30, width=350):
    """Project timeline events onto a track; keep geometry out of templates."""
    today = today or date.today()
    if days <= 0 or width < 56:
        raise ValueError("The rail needs positive days and at least 56px of width")
    end = today + timedelta(days=days)
    events = sorted((event for week in timeline for event in week["events"]
                     if today <= date.fromisoformat(event["date"]) <= end),
                    key=lambda event: event["date"])

    def x_for(day):
        return round(14 + (day - today).days / days * (width - 28), 2)

    track_y = 120
    bills = []
    row_ends = [float("-inf"), float("-inf")]
    tones = {"returns": "green", "nothing_new": "yellow"}
    for event in events:
        if event["type"] != "bill":
            continue
        x = x_for(date.fromisoformat(event["date"]))
        size = round(max(26, min(48, 26 + float(event["amount"]) * .45)), 2)
        tile_x = round(max(1, min(width - size - 1, x - size / 2)), 2)
        # Prefer the lower row, using the upper row when its neighbour overlaps.
        row = next((i for i in (0, 1) if row_ends[i] + 4 <= tile_x),
                   min(range(2), key=lambda i: row_ends[i]))
        row_ends[row] = tile_x + size
        tile_y = 54 - row * 30
        bills.append({key: event.get(key) for key in
                      ("amount", "service_name", "icon_path", "service_id", "date")} | {
            "x": x, "size": size, "row": row,
            "mark": service_mark(event["service_name"]),
            "tone": tones.get(event["verdict"]["kind"], "quiet"),
            "verdict": event["verdict"]["text"], "verdict_kind": event["verdict"]["kind"],
            "tile_x": tile_x, "tile_y": tile_y, "tile_bottom": tile_y + size,
            "amount_x": round(tile_x + size / 2, 2), "amount_y": tile_y - 8,
            "initials_x": round(tile_x + size / 2, 2),
            "initials_y": round(tile_y + size / 2, 2), "sequence": len(bills),
        })

    # A 30px stagger alone overlaps larger tiles and the lower amount label.
    upper_sizes = [bill["size"] for bill in bills if bill["row"] == 1]
    clearance = max(upper_sizes, default=0)
    extra = max(0, clearance + 18 - 30) if upper_sizes else 0
    for bill in bills:
        if bill["row"] == 0:
            for key in ("tile_y", "tile_bottom", "amount_y", "initials_y"):
                bill[key] += extra
    track_y += extra

    # Trim the viewBox to the amount text, including its ascenders.
    top = min((bill["amount_y"] - 12 for bill in bills), default=0) - 4
    for bill in bills:
        for key in ("tile_y", "tile_bottom", "amount_y", "initials_y"):
            bill[key] -= top
    track_y = max((bill["tile_bottom"] for bill in bills), default=track_y - top - 36) + 36

    returns, arcs, clusters = [], [], []
    for event in events:
        if event["type"] != "return":
            continue
        x = x_for(date.fromisoformat(event["date"]))
        # Dots closer than 12 units blur together on a phone, so they share one numbered dot.
        cluster = next((group for group in clusters if x - group["x"] <= 12), None)
        if cluster is None:
            cluster = {"x": x, "y": track_y + 9, "count": 0, "titles": [], "kind": "elsewhere"}
            clusters.append(cluster)
        cluster["count"] += 1
        cluster["titles"].append(f"{event['title']} · {event['date']}")
        kind = "counts" if event.get("context", "").startswith("counts toward") else "elsewhere"
        if kind == "counts":
            cluster["kind"] = "counts"
        bill = next((bill for bill in bills if kind == "counts"
                     and event.get("service_id") is not None
                     and bill["service_id"] == event["service_id"]
                     and bill["date"] >= event["date"]), None)
        x, y = cluster["x"], cluster["y"]
        returns.append({"x": x, "y": y, "kind": kind,
                        "title": event["title"], "date": event["date"],
                        "bill_x": bill["x"] if bill else None})
        if bill:
            bill.setdefault("show_titles", []).append(event["title"])
            depth = round(min(70, 14 + abs(bill["x"] - x) * .32), 2)
            control_x = round((x + bill["x"]) / 2, 2)
            control_y = round(y + depth, 2)
            t = (y - control_y) / (y - 2 * control_y + track_y)
            bottom = (1 - t) ** 2 * y + 2 * (1 - t) * t * control_y + t ** 2 * track_y
            arcs.append({"path": f"M {x} {y} Q {control_x} {control_y} {bill['x']} {track_y}",
                         "sequence": len(arcs), "depth": depth, "bottom": round(bottom, 2)})
    label_ends = [0, 0]
    label_rows = [index % 2 if any(abs(bill["x"] - other["x"]) < 90 for other in bills if other is not bill)
                  else 0 for index, bill in enumerate(bills)]
    for index, bill in enumerate(bills):
        titles = bill.get("show_titles", [])
        row = label_rows[index]
        # Labels are centred on their bill, so each gets the gap to its nearest same-row neighbour.
        gap = min((abs(bill["x"] - other["x"]) for j, other in enumerate(bills)
                   if j != index and label_rows[j] == row), default=width)
        suffix = f" +{len(titles) - 1}" if len(titles) > 1 else ""
        limit = max(6, min(18, int((gap - 6) / 5.6))) - len(suffix)
        lead = titles[0] if titles else ""
        bill["show_label"] = (lead if len(lead) <= limit else lead[:limit - 1].rstrip() + "…") + suffix
        half_width = len(bill["show_label"]) * 2.8
        while True:
            if row == len(label_ends):
                label_ends.append(0)
            label_x = max(half_width + 2, min(width - half_width - 2, bill["x"]))
            label_x = max(label_x, label_ends[row] + half_width + 6)
            if label_x + half_width <= width - 2:
                break
            row += 1
        bill["label_y"] = track_y - 12 if row == 0 else track_y + 29 + (row - 1) * 16
        bill["label_x"] = round(label_x, 2)
        if bill["show_label"]:
            label_ends[row] = bill["label_x"] + half_width
        bill["hit_x"] = max(0, min(width - 48, bill["amount_x"] - 24))
        bill["hit_y"] = bill["initials_y"] - 24
        bill["hit_height"] = 48
    bottom = max([track_y + 9] + [arc["bottom"] for arc in arcs]
                 + [bill["label_y"] + 3 for bill in bills if bill["show_label"]])
    ticks = [{"x": x_for(today + timedelta(days=offset)),
              "label": "Today" if offset == 0 else
              f"{today + timedelta(days=offset):%b} {(today + timedelta(days=offset)).day}",
              "today": offset == 0, "label_y": bottom + 16,
              "anchor": "start" if offset == 0 else "end" if offset == days else "middle"}
             for offset in range(0, days + 1, 7)]
    return {"bills": bills, "returns": returns, "clusters": clusters, "arcs": arcs, "ticks": ticks,
            "height": max(tick["label_y"] for tick in ticks) + 12, "empty": not bills, "width": width,
            "track_y": track_y, "track_start": 14, "track_end": width - 14,
            "tick_bottom": track_y + 5}
