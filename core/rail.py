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
            "hit_radius": 1,
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

    returns, arcs = [], []
    stacks = []
    for event in events:
        if event["type"] != "return":
            continue
        x = x_for(date.fromisoformat(event["date"]))
        group = next((group for group in stacks if abs(group["x"] - x) <= 6), None)
        if group is None:
            group = {"x": x, "count": 0}
            stacks.append(group)
        stack = group["count"]
        group["count"] += 1
        kind = "counts" if event.get("context", "").startswith("counts toward") else "elsewhere"
        bill = next((bill for bill in bills if kind == "counts"
                     and event.get("service_id") is not None
                     and bill["service_id"] == event["service_id"]
                     and bill["date"] >= event["date"]), None)
        y = track_y + 9 + stack * 9
        returns.append({"x": x, "y": y, "stack": stack, "kind": kind,
                        "title": event["title"], "date": event["date"],
                        "bill_x": bill["x"] if bill else None})
        if bill:
            depth = round(min(70, 14 + abs(bill["x"] - x) * .32), 2)
            control_x = round((x + bill["x"]) / 2, 2)
            control_y = round(max(track_y, y) + depth, 2)
            arcs.append({"path": f"M {x} {y} Q {control_x} {control_y} {bill['x']} {track_y}",
                         "sequence": len(arcs), "depth": depth, "bottom": control_y})
    # Put labels below dots and curves so dense same-day returns remain legible.
    bottom = max([track_y + 42] + [r["y"] + 8 for r in returns]
                 + [arc["bottom"] for arc in arcs])
    ticks = [{"x": x_for(today + timedelta(days=offset)),
              "label": "Today" if offset == 0 else
              f"{today + timedelta(days=offset):%b} {(today + timedelta(days=offset)).day}",
              "today": offset == 0, "label_y": bottom + 16,
              "anchor": "start" if offset == 0 else "end" if offset == days else "middle"}
             for offset in range(0, days + 1, 7)]
    return {"bills": bills, "returns": returns, "arcs": arcs, "ticks": ticks,
            "height": bottom + 28, "empty": not bills, "width": width,
            "track_y": track_y, "track_start": 14, "track_end": width - 14,
            "tick_bottom": track_y + 5}
