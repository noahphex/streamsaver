"""Presentation data for the dashboard's next-30-days billing rail."""

from datetime import date, timedelta

from core.presentation import service_mark
from core.timeline import group_periods


def build_rail(timeline, today=None, days=30, width=350):
    """Project bills, verdict badges and return links; templates only render."""
    today = today or date.today()
    if days <= 0 or width < 56:
        raise ValueError("The rail needs positive days and at least 56px of width")
    end = today + timedelta(days=days)
    all_events = [event for week in timeline for event in week['events']]
    events = sorted((event for event in all_events
                     if today <= date.fromisoformat(event['date']) <= end),
                    key=lambda event: event['date'])

    def x_for(day):
        return round(14 + (day - today).days / days * (width - 28), 2)

    bills = []
    authored_titles = set()
    for event in events:
        if event['type'] != 'bill':
            continue
        x = x_for(date.fromisoformat(event['date']))
        size = round(max(26, min(48, 26 + float(event['amount']) * .45)), 2)
        if 'shows' in event['verdict']:
            authored_titles.add(f"bill-{event.get('service_id')}-{event['date']}")
        bills.append({key: event.get(key) for key in
                      ('amount', 'service_name', 'icon_path', 'service_id', 'date')} | {
            'x': x, 'size': size, 'mark': service_mark(event['service_name']),
            'tone': {'returns': 'green', 'nothing_new': 'yellow'}.get(event['verdict']['kind'], 'quiet'),
            'verdict': event['verdict']['text'], 'verdict_kind': event['verdict']['kind'],
            'show_titles': [show['title'] for show in event['verdict'].get('shows', [])],
            'anchor': f"bill-{event.get('service_id')}-{event['date']}", 'sequence': len(bills),
        })

    return_anchors = {(event.get('service_id'), event['date'], event['title']): period['anchor_id']
                      for period in group_periods(timeline, today)['periods'] for event in period['returns']}
    returns, clusters, return_clusters = [], [], []
    for event in events:
        if event['type'] != 'return':
            continue
        anchor = return_anchors.get((event.get('service_id'), event['date'], event['title']), 'elsewhere')
        kind = 'elsewhere' if anchor == 'elsewhere' else 'counts'
        bill = next((bill for bill in bills if bill['anchor'] == anchor), None)
        if bill and bill['anchor'] not in authored_titles:
            bill['show_titles'].append(event['title'])
        x = x_for(date.fromisoformat(event['date']))
        # A cluster has one destination, and never mixes counts with elsewhere.
        cluster = next((group for group in clusters if group['kind'] == kind
                        and group['anchor'] == anchor and x - group['x'] <= 12), None)
        if cluster is None:
            cluster = dict(x=x, count=0, titles=[], kind=kind, anchor=anchor)
            clusters.append(cluster)
        cluster['count'] += 1
        cluster['titles'].append(f"{event['title']} · {event['date']}")
        return_clusters.append(cluster)
        returns.append(dict(x=cluster['x'], kind=kind, title=event['title'], date=event['date'], anchor=anchor))

    # Allocate whole tile/badge units to lanes, with two lanes for close bills.
    # Dense fixtures may need more lanes to preserve readable text without overlaps.
    row_ends = []
    for bill in bills:
        count = len(bill['show_titles'])
        bill['badge'] = (f'{count} back first' if bill['verdict_kind'] == 'returns' else
                         {'nothing_new': 'Nothing new', 'prepaid': 'Annual'}.get(bill['verdict_kind'], 'Too early'))
        badge_width = min(width - 4, max(70, len(bill['badge']) * 7.5 + 16))
        badge_x = round(max(2, min(width - badge_width - 2, bill['x'] - badge_width / 2)), 2)
        row = next((i for i, right in enumerate(row_ends) if right + 4 <= badge_x), len(row_ends))
        if row == len(row_ends):
            row_ends.append(0)
        row_ends[row] = badge_x + badge_width
        bill.update(row=row, badge_width=badge_width, badge_x=badge_x)
    for bill in bills:
        # The first lane sits nearest the track, as in the original tile stagger.
        y = 28 + (len(row_ends) - 1 - bill['row']) * 112
        tile_x = round(max(1, min(width - bill['size'] - 1, bill['x'] - bill['size'] / 2)), 2)
        bill.update(tile_x=tile_x, tile_y=y, tile_bottom=y + bill['size'],
                    amount_x=tile_x + bill['size'] / 2, amount_y=y - 10,
                    initials_x=tile_x + bill['size'] / 2, initials_y=y + bill['size'] / 2,
                    badge_y=y + 56, badge_text_y=y + 68,
                    badge_center=bill['badge_x'] + bill['badge_width'] / 2,
                    hit_x=max(0, min(width - 48, tile_x + bill['size'] / 2 - 24)),
                    hit_y=y - 4, hit_height=48)
    track_y = max((b['badge_y'] + 24 for b in bills), default=70) + 22
    # Break every stem at all intervening badges/tiles, including other lanes.
    for bill in bills:
        start = bill['tile_bottom']
        segments = []
        obstacles = sorted([(b['badge_y'] - 2, b['badge_y'] + 26) for b in bills
                            if b['badge_x'] - 2 <= bill['x'] <= b['badge_x'] + b['badge_width'] + 2]
                           + [(b['amount_y'] - 16, b['tile_bottom'] + 2) for b in bills if b is not bill
                              and b['tile_x'] - 15 <= bill['x'] <= b['tile_x'] + b['size'] + 15])
        for top, bottom in obstacles:
            if bottom <= start:
                continue
            if top > start:
                segments.append(dict(start=start, end=top))
            start = max(start, bottom)
        if start < track_y:
            segments.append(dict(start=start, end=track_y))
        bill['stems'] = segments
    # Offset coincident kinds/destinations along the track so both remain visible.
    occupied = []
    for cluster in clusters:
        radius = max(10, len(str(cluster['count'])) * 4 + 4) if cluster['count'] > 1 else 4
        x = max(radius + 2, min(width - radius - 2, cluster['x']))
        candidates = [x] + list(range(radius + 2, int(width - radius - 1)))
        x = min(candidates, key=lambda candidate: (
            sum(max(0, radius + old_r + 3 - abs(candidate - old_x)) for old_x, old_r in occupied),
            abs(candidate - x)))
        cluster.update(x=round(x, 2), y=track_y, radius=radius,
                       hit_x=round(max(0, min(width - 56, x - 28)), 2), hit_y=track_y - 28)
        occupied.append((x, radius))
    for event, cluster in zip(returns, return_clusters):
        event.update(x=cluster['x'], y=track_y)
    ticks = [dict(x=x_for(today + timedelta(days=offset)),
                  label='Today' if offset == 0 else f'{today + timedelta(days=offset):%b} {(today + timedelta(days=offset)).day}',
                  today=offset == 0, label_y=track_y + 34,
                  anchor='start' if offset == 0 else 'end' if offset == days else 'middle')
             for offset in range(0, days + 1, 7)]
    return dict(bills=bills, returns=returns, clusters=clusters, ticks=ticks,
                height=track_y + 46, empty=not bills and not returns, width=width,
                track_y=track_y, track_start=14, track_end=width - 14, tick_bottom=track_y + 5)
