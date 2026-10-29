"""Synthetic billing rail and spoken headline contracts."""
from datetime import date, timedelta

from core.rail import build_rail
from core.spend import spend_headline

TODAY = date(2026, 10, 29)


def bill(day, amount=10, service=1, kind="returns"):
    return dict(type="bill", date=str(TODAY + timedelta(days=day)), amount=amount,
                service_id=service, service_name=f"Service {service}", icon_path=None,
                verdict=dict(kind=kind, text="1 show returns before this bill"))


def returning(day, context="counts toward the next bill", service=1):
    return dict(type="return", date=str(TODAY + timedelta(days=day)),
                service_id=service, title="Sample show", context=context)


def rail(*events, **kwargs):
    return build_rail([{"events": list(events)}], today=TODAY, **kwargs)


def test_window_edges_sizes_and_filtering():
    data = rail(bill(-1), bill(0, -10), bill(30, 100, 2), bill(31))
    assert [b["x"] for b in data["bills"]] == [14, 336]
    assert [b["size"] for b in data["bills"]] == [32, 32]
    assert rail(bill(15, 20), width=400)["bills"][0]["size"] == 32
    assert rail(bill(30), width=400)["bills"][0]["x"] == 386
    assert [t["label"] for t in data["ticks"]][:2] == ["Today", "Nov 5"]


def test_nearby_bills_nudge_on_one_row_and_returns_cluster():
    data = rail(bill(7), bill(8, service=2), returning(1), returning(1), returning(2))
    first, second = data["bills"]
    assert [first["row"], second["row"]] == [0, 0]
    assert first["tile_y"] == second["tile_y"]
    assert second["tile_x"] >= first["tile_x"] + 38
    assert [b["x"] for b in data["bills"]] == [89.13, 99.87]
    assert [c["count"] for c in data["clusters"]] == [3]
    assert data["returns"][1]["y"] == data["returns"][0]["y"]


def test_returns_link_to_next_bill_and_elsewhere_is_omitted():
    data = rail(bill(4), bill(10), bill(7, service=2), returning(5),
                returning(1, "annual · prepaid"), returning(2, service=9))
    assert [r['anchor'] for r in data['returns']] == ['bill-1-2026-11-08']
    assert all(c['kind'] == 'counts' for c in data['clusters'])


def test_empty_and_elsewhere_only_rail():
    assert rail()['empty'] is True
    data = rail(*(returning(1) for _ in range(20)))
    assert data['empty'] is True
    assert data['clusters'] == data['returns'] == []


def test_spoken_headline_variants_and_actual_bill_amounts():
    totals = {"monthly_total": 108.66}
    headline = spend_headline(totals, rail(bill(1, 81.66)), 12, 6)
    assert headline["lead"] == "You pay"
    assert headline["amount"] == "$108.66"
    assert headline["tail"] == "a month for 6 services."
    assert headline["sub_parts"][0]["text"] == "$81.66"
    assert headline["sub_parts"][-1] == {"text": "every bill has a show coming back first.", "tone": "green", "emphasis": True}
    assert headline["savings"] == "Could save $12.00/mo."
    mixed = spend_headline(totals, rail(bill(1), bill(2, kind="nothing_new")), 0, 1)
    assert mixed["tail"] == "a month for 1 service."
    assert mixed["sub_parts"][-1]["text"] == "1 bill has nothing coming back first."
    quiet = spend_headline(totals, rail(bill(1, kind="unknown")), 0, 1)
    assert all(p.get("tone") != "green" for p in quiet["sub_parts"])
    empty = spend_headline(totals, [], 0, 0)
    assert empty["lead"] == "You're not paying for any streaming right now."
    assert empty["sub_parts"] == [{"text": "Nothing bills in the next 30 days.", "tone": "quiet"}]


def test_tiles_stay_inside_track_even_when_dates_coincide_at_edges():
    for day in (0, 1, 29, 30):
        data = rail(*(bill(day, 100, service=i) for i in range(8)))
        assert {b['row'] for b in data['bills']} == {0}
        assert data['bills'][0]['tile_x'] >= data['track_start']
        assert data['bills'][-1]['tile_x'] + 32 <= data['track_end']
        assert all(b['tile_x'] - a['tile_x'] >= 38
                   for a, b in zip(data['bills'], data['bills'][1:]))


def test_badge_copy_and_authoritative_verdict_titles():
    data = rail(bill(15), dict(returning(1), title="Love is Blind"), returning(2))
    assert data['bills'][0]['badge'] == '2 back'
    for kind, label in [('nothing_new', 'Nothing new'), ('unknown', 'Too early'), ('prepaid', 'Annual')]:
        assert rail(bill(15, kind=kind))['bills'][0]['badge'] == label
    source = bill(15)
    source['verdict']['shows'] = [dict(title='Anticipated', date='2026-10-30')]
    data = rail(source, returning(1), returning(2))
    assert data['bills'][0]['badge'] == '1 back'
    assert data['bills'][0]['show_titles'] == ['Anticipated']


def test_cluster_threshold_and_period_destinations():
    data = rail(bill(5), bill(15, service=2), returning(1), returning(1, service=2), returning(1))
    assert [c['count'] for c in data['clusters']] == [2, 1]
    assert len({c['anchor'] for c in data['clusters']}) == 2
    assert len(rail(bill(30), returning(1), returning(2), width=100)['clusters']) == 1
    assert len(rail(bill(30), returning(1), returning(7), width=100)['clusters']) == 2


def test_axis_and_height_follow_badges():
    for data in (rail(bill(3), returning(2)), rail(bill(29), returning(2)),
                 rail(bill(7, 100), bill(8, 100, service=2))):
        assert len({t['label_y'] for t in data['ticks']}) == 1
        assert data['height'] == max(t['label_y'] for t in data['ticks']) + 10
        assert min(b['amount_y'] for b in data['bills']) == 14
        assert all(b['badge_y'] + 22 < data['track_y'] for b in data['bills'])
        assert all(b['hit_height'] >= 48 for b in data['bills'])


def test_dense_badges_and_stems_never_cross_text():
    data = rail(*(bill(29, 100, service=i) for i in range(6)))
    assert len({b['row'] for b in data['bills']}) == 1
    for item in data['bills']:
        for other in data['bills']:
            if other['badge_x'] <= item['x'] <= other['badge_x'] + other['badge_width']:
                assert all(stem['end'] <= other['badge_y'] or stem['start'] >= other['badge_y'] + 22
                           for stem in item['stems'])


def cluster_example():
    return rail(bill(7, 10, 1), bill(10, 15, 2, 'nothing_new'), bill(27, 20, 3, 'unknown'),
                dict(returning(2, service=1), title='Example one'),
                dict(returning(2, service=1), title='Example two'),
                dict(returning(2, 'not subscribed', 9), title='Elsewhere example'))


def test_synthetic_fixtures_match_review_json():
    import json
    from pathlib import Path
    for name, example in [('rail-cluster-example.json', cluster_example), ('rail_synthetic.json', flat_example)]:
        expected = json.loads((Path(__file__).parent / 'fixtures' / name).read_text())
        assert example() == expected


def test_cluster_example_has_only_counts_and_one_tile_row():
    data = cluster_example()
    assert [(c['kind'], c['count']) for c in data['clusters']] == [('counts', 2)]
    assert data['clusters'][0]['anchor'] == 'bill-1-2026-11-05'
    assert data['bills'][0]['badge'] == '2'
    assert {b['row'] for b in data['bills']} == {0}


def test_second_row_only_when_tiles_exceed_track_capacity():
    data = rail(*(bill(15, service=i) for i in range(9)))
    assert [b['row'] for b in data['bills']] == [0] * 8 + [1]
    assert data['height'] > 152
    for b in data['bills']:
        assert 14 <= b['tile_x'] <= 304


def flat_example():
    events = []
    for service, day, amount, count in [(1, 6, 19.99, 3), (2, 15, 21.64, 1), (3, 21, 40.03, 2)]:
        events.append(bill(day, amount, service))
        events.extend(dict(returning(day - 2, service=service), title=f'Sample {service}.{i}')
                      for i in range(count))
    return rail(*events)


def test_requested_flat_example():
    data = flat_example()
    assert data['height'] == 152
    assert [b['tile_x'] for b in data['bills']] == [62.4, 159.0, 223.4]
    assert [b['badge'] for b in data['bills']] == ['3 back', '1 back', '2 back']
    assert {b['row'] for b in data['bills']} == {0}
    assert all(t['label_y'] == data['track_y'] + 24 for t in data['ticks'])  # radius-10 cluster + 14


def test_overlapping_return_badges_shorten_without_changing_rows():
    data = rail(bill(15), bill(16, service=2), returning(1), returning(1, service=2))
    assert [b['badge'] for b in data['bills']] == ['1', '1']
    first, second = data['bills']
    assert first['badge_x'] + first['badge_width'] + 4 <= second['badge_x']
    assert first['row'] == second['row'] == 0


def test_return_links_to_period_beyond_visible_rail():
    data = rail(bill(40), returning(20))
    assert not data['bills']
    assert data['clusters'][0]['anchor'] == 'bill-1-2026-12-08'
    assert data['clusters'][0]['kind'] == 'counts'


def test_prepaid_returns_match_coming_up_elsewhere():
    from core.timeline import group_periods
    source = [{'events': [bill(40, kind='prepaid'), returning(10)]}]
    data = build_rail(source, today=TODAY)
    assert group_periods(source, TODAY)['elsewhere']
    assert data['clusters'] == []
    assert data['returns'] == []


def test_mixed_badges_share_horizontal_space_without_overlap():
    data = cluster_example()
    for left, right in zip(data['bills'], data['bills'][1:]):
        assert left['badge_x'] + left['badge_width'] + 4 <= right['badge_x']
    for b in data['bills']:
        assert b['stems'][-1]['end_x'] == b['x']
        assert b['stems'][-1]['end'] == data['track_y']


def test_dense_full_labels_remain_inside_viewbox():
    data = rail(*(bill(15, service=i, kind='nothing_new') for i in range(6)))
    for b in data['bills']:
        assert 2 <= b['badge_x'] <= data['width'] - b['badge_width'] - 2
    for row in {b['row'] for b in data['bills']}:
        items = [b for b in data['bills'] if b['row'] == row]
        assert all(a['badge_x'] + a['badge_width'] + 4 <= b['badge_x']
                   for a, b in zip(items, items[1:]))


def test_grouped_dots_leave_space_for_tick_labels():
    data = rail(bill(6), *(returning(2) for _ in range(100)))
    assert data['ticks'][0]['label_y'] >= data['track_y'] + data['clusters'][0]['radius'] + 8
    assert data['height'] == data['ticks'][0]['label_y'] + 10


def test_dense_prices_fit_between_adjacent_amount_centers():
    for amount in (19.99, 100, 1234.56):
        data = rail(*(bill(29, amount, service=i) for i in range(8)))
        for a, b in zip(data['bills'], data['bills'][1:]):
            assert a['amount_x'] + a['amount_width'] / 2 + 4 <= b['amount_x'] - b['amount_width'] / 2
        assert all(b['amount_x'] - b['amount_width'] / 2 >= 2
                   and b['amount_x'] + b['amount_width'] / 2 <= 348 for b in data['bills'])


def test_tick_labels_clear_numbered_dots():
    # An 11px label rises about 9px above its baseline; it must start below the dot.
    data = flat_example()
    lowest_dot = max(c['y'] + c['radius'] for c in data['clusters'])
    assert all(t['label_y'] - 9 > lowest_dot for t in data['ticks'])
