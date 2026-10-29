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


def test_nearby_bills_stagger_and_returns_stack():
    data = rail(bill(7), bill(8, service=2), returning(1), returning(1), returning(2))
    first, second = data["bills"]
    assert [first["row"], second["row"]] == [0, 1]
    assert first["tile_y"] - second["tile_y"] >= 30
    assert second["tile_bottom"] + 10 <= first["amount_y"]
    assert [r["stack"] for r in data["returns"]] == [0, 1, 0]
    assert data["returns"][1]["y"] - data["returns"][0]["y"] == 9


def test_arcs_only_match_counts_to_next_bill_for_service():
    data = rail(bill(4), bill(10), bill(7, service=2), returning(5),
                returning(1, "annual · prepaid"), returning(2, service=9))
    assert len(data["arcs"]) == 1
    assert data["returns"][1]["bill_x"] is None
    assert data["returns"][2]["bill_x"] == data["bills"][2]["x"]
    assert data["arcs"][0]["sequence"] == 0
    assert " Q " in data["arcs"][0]["path"]
    assert 14 <= data["arcs"][0]["depth"] <= 70


def test_empty_and_height_encloses_stacked_returns():
    assert rail()["empty"] is True
    data = rail(*(returning(1) for _ in range(20)))
    assert data["empty"] is True
    assert data["height"] > max(r["y"] for r in data["returns"])


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
