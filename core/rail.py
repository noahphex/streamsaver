"""Presentation data for the dashboard's next-30-days billing rail."""

from datetime import date, timedelta

from core.presentation import service_mark
from core.timeline import group_periods


def build_rail(timeline, today=None, days=30, width=350):
    """Project bills, verdict badges and return links; templates only render."""
    today = today or date.today()
    if days <= 0 or width < 60:
        raise ValueError("The rail needs positive days and at least 60px of width")
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
        size = 32
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
        if anchor == 'elsewhere':
            continue
        kind = 'counts'
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

    # Full non-return labels cannot shorten to a return count.
    capacity = max(1, int((width - 28 + 6) // 38))
    rows, items, badge_space = [], [], 0
    for bill in bills:
        count = len(bill['show_titles'])
        bill['badge'] = (f'{count} back' if bill['verdict_kind'] == 'returns' else
                         {'nothing_new': 'Nothing new', 'prepaid': 'Annual'}.get(bill['verdict_kind'], 'Too early'))
        bill['badge_dot'] = bill['verdict_kind'] == 'returns'
        minimum = len(str(count)) * 6 + 24 if bill['badge_dot'] else len(bill['badge']) * 6 + 16
        if items and (len(items) == capacity or badge_space + 4 + minimum > width - 4):
            rows.append(items)
            items, badge_space = [], 0
        badge_space += minimum + (4 if items else 0)
        items.append(bill)
    if items:
        rows.append(items)
    # Pack fixed tiles forward, then back from the right edge; dates never move.
    for row, items in enumerate(rows):
        right = 14 - 6
        for bill in items:
            bill['tile_x'] = max(14, bill['x'] - 16, right + 6)
            right = bill['tile_x'] + 32
        right = width - 14
        for bill in reversed(items):
            bill['tile_x'] = round(min(bill['tile_x'], right - 32), 2)
            right = bill['tile_x'] - 6
        for bill in items:
            bill['row'] = row
            bill['badge_center'] = bill['tile_x'] + 16

        def place_badge(bill):
            bill['badge_width'] = min(width - 4, len(bill['badge']) * 6 + (24 if bill['badge_dot'] else 16))
            bill['badge_x'] = round(max(2, min(width - bill['badge_width'] - 2,
                                              bill['badge_center'] - bill['badge_width'] / 2)), 2)

        for bill in items:
            place_badge(bill)
        shorten = set()
        for i, (left, right) in enumerate(zip(items, items[1:])):
            if left['badge_x'] + left['badge_width'] + 4 > right['badge_x']:
                shorten.update((i, i + 1))
        for i in shorten:
            if items[i]['badge_dot']:
                items[i]['badge'] = str(len(items[i]['show_titles']))
                place_badge(items[i])
        # Preserve the full non-return labels by sharing horizontal badge space.
        right = 2 - 4
        for bill in items:
            bill['badge_x'] = max(bill['badge_x'], right + 4)
            right = bill['badge_x'] + bill['badge_width']
        right = width - 2
        for bill in reversed(items):
            bill['badge_x'] = round(min(bill['badge_x'], right - bill['badge_width']), 2)
            right = bill['badge_x'] - 4
        for bill in items:
            y = 24 + row * 92
            tile_x = bill['tile_x']
            bill.update(tile_y=y, tile_bottom=y + 32,
                        amount_x=tile_x + 16, amount_y=y - 10,
                        initials_x=tile_x + 16, initials_y=y + 16,
                        badge_y=y + 40, badge_text_y=y + 51,
                        hit_x=max(0, min(width - 48, tile_x - 8)),
                        hit_y=y - 4, hit_height=48)
            bill['badge_center'] = bill['badge_x'] + bill['badge_width'] / 2
        for i, bill in enumerate(items):
            center = bill['amount_x']
            room = min(2 * (center - 2), 2 * (width - 2 - center))
            if i:
                room = min(room, center - items[i - 1]['amount_x'] - 4)
            if i + 1 < len(items):
                room = min(room, items[i + 1]['amount_x'] - center - 4)
            # Condense only crowded prices; tile size and amount text stay intact.
            bill['amount_width'] = round(min(room, len(f"${float(bill['amount'] or 0):,.2f}") * 6.6), 2)
    track_y = max((b['badge_y'] + 22 for b in bills), default=22) + 32
    # Stems lead from badges to true dates, breaking around intervening content.
    for bill in bills:
        origin = start = bill['badge_y'] + 26
        center = bill['badge_center']
        left, right = sorted((center, bill['x']))
        obstacles = sorted([(b['badge_y'] - 2, b['badge_y'] + 24) for b in bills
                            if b['badge_x'] - 2 <= right and left <= b['badge_x'] + b['badge_width'] + 2]
                           + [(b['amount_y'] - 14, b['tile_bottom'] + 2) for b in bills if b is not bill
                              and b['tile_x'] - 15 <= right and left <= b['tile_x'] + b['size'] + 15])
        def segment(top, bottom):
            def stem_x(y):
                return round(center + (bill['x'] - center) * (y - origin) / (track_y - origin), 2)
            return dict(start=top, end=bottom, start_x=stem_x(top), end_x=stem_x(bottom))
        segments = []
        for top, bottom in obstacles:
            if bottom <= start:
                continue
            if top > start:
                segments.append(segment(start, top))
            start = max(start, bottom)
        if start < track_y:
            segments.append(segment(start, track_y))
        bill['stems'] = segments
    # Keep separate billing-period dots visible, including beside Today.
    occupied = [(14, 4)]
    for cluster in clusters:
        radius = max(10, len(str(cluster['count'])) * 4 + 4) if cluster['count'] > 1 else 4
        x = max(radius + 2, min(width - radius - 2, cluster['x']))
        candidates = [x] + list(range(radius + 2, int(width - radius - 1)))
        x = min(candidates, key=lambda candidate: (
            sum(max(0, radius + old_r + 3 - abs(candidate - old_x)) for old_x, old_r in occupied),
            abs(candidate - x)))
        cluster.update(x=round(x, 2), y=track_y, radius=radius,
                       hit_x=round(max(0, min(width - 44, x - 22)), 2), hit_y=track_y - 22)
        occupied.append((x, radius))
    for event, cluster in zip(returns, return_clusters):
        event.update(x=cluster['x'], y=track_y)
    label_offset = max(18, max((c['radius'] + 8 for c in clusters), default=0))
    ticks = [dict(x=x_for(today + timedelta(days=offset)),
                  label='Today' if offset == 0 else f'{today + timedelta(days=offset):%b} {(today + timedelta(days=offset)).day}',
                  today=offset == 0, label_y=track_y + label_offset,
                  anchor='start' if offset == 0 else 'end' if offset == days else 'middle')
             for offset in range(0, days + 1, 7)]
    return dict(bills=bills, returns=returns, clusters=clusters, ticks=ticks,
                height=track_y + label_offset + 10, empty=not bills and not returns, width=width,
                track_y=track_y, track_start=14, track_end=width - 14, tick_bottom=track_y + 5)
