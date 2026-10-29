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
    assert [b["size"] for b in data["bills"]] == [26, 48]
    assert rail(bill(15, 20), width=400)["bills"][0]["size"] == 35
    assert rail(bill(30), width=400)["bills"][0]["x"] == 386
    assert [t["label"] for t in data["ticks"]][:2] == ["Today", "Nov 5"]


def test_nearby_bills_stagger_and_returns_cluster():
    data = rail(bill(7), bill(8, service=2), returning(1), returning(1), returning(2))
    first, second = data["bills"]
    assert [first["row"], second["row"]] == [0, 1]
    assert first["tile_y"] - second["tile_y"] >= 30
    assert second["tile_bottom"] + 10 <= first["amount_y"]
    # Day 1 and day 2 are about 11 units apart, close enough to share one numbered dot.
    assert [c["count"] for c in data["clusters"]] == [3]
    assert data["returns"][1]["y"] == data["returns"][0]["y"]


def test_returns_link_to_next_bill_for_their_service():
    data = rail(bill(4), bill(10), bill(7, service=2), returning(5),
                returning(1, "annual · prepaid"), returning(2, service=9))
    assert [r['anchor'] for r in data['returns']] == ['elsewhere', 'elsewhere', 'bill-1-2026-11-08']
    assert data['returns'][2]['kind'] == 'counts'


def test_empty_and_return_only_rail():
    assert rail()['empty'] is True
    data = rail(*(returning(1) for _ in range(20)))
    assert data['empty'] is False
    assert data['clusters'][0]['kind'] == 'elsewhere'
    assert data['height'] > max(r['y'] for r in data['returns'])


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


def test_large_staggered_tiles_and_amount_labels_do_not_collide():
    data = rail(bill(7, 100), bill(8, 100, service=2))
    lower, upper = data["bills"]
    assert upper["tile_bottom"] + 10 <= lower["amount_y"]
    assert lower["tile_bottom"] < data["track_y"]


def test_badge_copy_and_authoritative_verdict_titles():
    data = rail(bill(15), dict(returning(1), title="Love is Blind"), returning(2))
    assert data['bills'][0]['badge'] == '2 back first'
    for kind, label in [('nothing_new', 'Nothing new'), ('unknown', 'Too early'), ('prepaid', 'Annual')]:
        assert rail(bill(15, kind=kind))['bills'][0]['badge'] == label
    source = bill(15)
    source['verdict']['shows'] = [dict(title='Anticipated', date='2026-10-30')]
    data = rail(source, returning(1), returning(2))
    assert data['bills'][0]['badge'] == '1 back first'
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
        assert data['height'] == max(t['label_y'] for t in data['ticks']) + 12
        assert min(b['amount_y'] for b in data['bills']) == 18
        assert all(b['badge_y'] + 24 < data['track_y'] for b in data['bills'])
        assert all(b['hit_height'] >= 48 for b in data['bills'])


def test_dense_badges_and_stems_never_cross_text():
    data = rail(*(bill(29, 100, service=i) for i in range(6)))
    assert len({b['row'] for b in data['bills']}) == 6
    for item in data['bills']:
        for other in data['bills']:
            if other['badge_x'] <= item['x'] <= other['badge_x'] + other['badge_width']:
                assert all(stem['end'] <= other['badge_y'] or stem['start'] >= other['badge_y'] + 24
                           for stem in item['stems'])


def cluster_example():
    return rail(bill(7, 10, 1), bill(10, 15, 2, 'nothing_new'), bill(27, 20, 3, 'unknown'),
                dict(returning(2, service=1), title='Example one'),
                dict(returning(2, service=1), title='Example two'),
                dict(returning(2, 'not subscribed', 9), title='Elsewhere example'))


def test_synthetic_fixtures_match_review_json():
    import json
    from pathlib import Path
    for name in ['rail-cluster-example.json', 'rail_synthetic.json']:
        expected = json.loads((Path(__file__).parent / 'fixtures' / name).read_text())
        assert cluster_example() == expected


def test_badges_separate_kinds_periods_and_stems():
    data = cluster_example()
    assert [(c['kind'], c['count']) for c in data['clusters']] == [('counts', 2), ('elsewhere', 1)]
    assert data['clusters'][0]['anchor'] == 'bill-1-2026-11-05'
    assert data['clusters'][1]['anchor'] == 'elsewhere'
    assert data['bills'][0]['badge'] == '2 back first'
    assert data['bills'][0]['row'] != data['bills'][1]['row']
    lower, upper = data['bills'][:2]
    assert upper['badge_y'] + 24 + 4 <= lower['amount_y'] - 16
    assert abs(data['clusters'][0]['x'] - data['clusters'][1]['x']) >= 17
    for i, a in enumerate(data['bills']):
        for b in data['bills'][i + 1:]:
            if a['row'] == b['row']:
                assert a['badge_x'] + a['badge_width'] + 4 <= b['badge_x']


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
    assert data['clusters'][0]['kind'] == 'elsewhere'
    assert data['clusters'][0]['anchor'] == 'elsewhere'
