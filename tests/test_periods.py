from copy import deepcopy
from datetime import date

from core import timeline


def fixture():
    def bill(day, kind, service=1):
        return dict(type='bill', date=day, service_id=service, service_name=f'Service {service}',
                    amount=10, verdict={'kind': kind, 'text': kind})
    def returning(day, service=1, context='counts toward a bill', status='subscribed'):
        return dict(type='return', date=day, service_id=service, service_name=f'Service {service}',
                    title=f'Return {service}', context=context, service_status=status,
                    resubscribe_todo=service == 3)
    return [{'events': [bill('2026-11-04', 'returns'), bill('2026-12-04', 'returns'),
                        bill('2026-12-19', 'unknown'), bill('2026-12-20', 'prepaid', 2),
                        returning('2026-11-01'), returning('2026-11-10'),
                        returning('2026-11-02', 2, 'annual · prepaid'),
                        returning('2026-11-03', 3, 'not subscribed', 'unsubscribed'),
                        returning('2026-12-21', context='after your last bill in this window')]}]


def test_group_periods_windows_later_elsewhere_and_summary():
    source = fixture()
    before = deepcopy(source)
    grouped = timeline.group_periods(source, date(2026, 10, 29))
    first, second = grouped['periods']
    assert (first['window_start'], first['window_end']) == ('2026-10-29', '2026-11-04')
    assert first['returns'][0]['date'] == '2026-11-01'
    assert first['returns'][0]['pos'] == first['fill_pct'] == 50
    assert second['window_start'] == '2026-11-04'
    assert second['returns'][0]['date'] == '2026-11-10'
    assert first['anchor_id'] == 'bill-1-2026-11-04'
    assert grouped['later'] == {'count': 2, 'dates': ['2026-12-19', '2026-12-20'], 'total': 20.0,
                                'anchors': ['bill-1-2026-12-19', 'bill-2-2026-12-20']}
    assert [e['service_id'] for e in grouped['elsewhere']] == [2, 3, 1]
    assert grouped['off_returns'][3]['resubscribe_todo'] is True
    assert grouped['summary'] == dict(worth_paying=2, total=20.0, by='2026-12-04',
                                      nothing_new=0, bills=4, returns=5, elsewhere=3)
    assert source == before
    for period in grouped['periods']:
        assert 0 <= period['fill_pct'] <= 100
        assert all(0 <= event['pos'] <= 100 for event in period['returns'])


def test_empty_and_same_day_bounds():
    assert timeline.group_periods([], date(2026, 10, 29))['summary']['by'] is None
    events = fixture()[0]['events'][:1]
    events[0]['date'] = '2026-10-29'
    events[0]['verdict']['kind'] = 'nothing_new'
    grouped = timeline.group_periods([{'events': events}], date(2026, 10, 29))
    assert grouped['periods'][0]['fill_pct'] == 0
    assert grouped['summary']['nothing_new'] == 1


def test_period_boundaries_and_previous_unlisted_bill():
    events = fixture()[0]['events']
    # The return on the first bill belongs only to that window; today is excluded.
    base = next(e for e in events if e['type'] == 'return')
    events.extend([dict(base, date='2026-11-04'), dict(base, date='2026-10-29')])
    grouped = timeline.group_periods([{'events': list(reversed(events))}], date(2026, 10, 29))
    first, second = grouped['periods']
    assert first['fill_pct'] == 100
    assert [e['date'] for e in first['returns']] == ['2026-11-01', '2026-11-04']
    assert [e['date'] for e in second['returns']] == ['2026-11-10']
    assert grouped['elsewhere'][0]['date'] == '2026-10-29'
    first['bill']['verdict']['kind'] = 'unknown'
    grouped = timeline.group_periods([{'events': events}], date(2026, 10, 29))
    assert grouped['periods'][0]['window_start'] == '2026-11-04'


def example_timeline():
    """Synthetic monthly, annual, and off subscriptions for the review artifact."""
    import db
    with db.db_session() as conn:
        conn.executemany(
            "INSERT INTO services (id, name, status, billing_cycle, billing_anchor_date) VALUES (?, ?, ?, ?, ?)",
            [(1, 'Monthly Example', 'subscribed', 'monthly', '2026-11-04'),
             (2, 'Annual Example', 'subscribed', 'annual', '2027-07-29'),
             (3, 'Off Example', 'unsubscribed', 'monthly', None)])
        conn.executemany(
            "INSERT INTO shows (trakt_id, title, service_id, next_air_date, show_state) VALUES (?, ?, ?, ?, 'anticipated')",
            [('a', 'First return', 1, '2026-10-30'), ('b', 'Second return', 1, '2026-11-10'),
             ('c', 'Annual return', 2, '2026-11-01'), ('d', 'Off return', 3, '2026-10-30')])
    return timeline.build_timeline(
        {'keep': [dict(id=1, name='Monthly Example', status='subscribed', billing_cycle='monthly',
                       billing_date='2026-11-04', monthly_cost=10)],
         'resubscribe': [{'id': 3}]}, date(2026, 10, 29))


def test_synthetic_example_matches_review_json(tmp_path, monkeypatch):
    import json
    from pathlib import Path
    import db
    monkeypatch.setattr(db, 'DB_PATH', str(tmp_path / 'example.db'))
    db.init_db()
    result = timeline.group_periods(example_timeline(), date(2026, 10, 29))
    expected = json.loads((Path(__file__).parent / 'fixtures' / 'periods-example.json').read_text())
    assert json.loads(json.dumps(result)) == expected
