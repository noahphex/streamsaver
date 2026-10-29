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
    assert [c["count"] for c in data["clusters"]] == [2, 1]
    assert data["returns"][1]["y"] == data["returns"][0]["y"]


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


def test_show_label_truncation_and_more_count():
    data = rail(bill(15), dict(returning(1), title="Love is Blind"), returning(2))
    assert data["bills"][0]["show_label"] == "Love is Bl… +1"
    assert len(data["bills"][0]["show_label"]) == 14
    assert rail(bill(15), dict(returning(1), title="Short"))["bills"][0]["show_label"] == "Short"
    assert rail(bill(15), returning(1), returning(2))["bills"][0]["show_label"] == "Sample show +1"


def test_cluster_threshold_and_arcs_start_at_shared_dot():
    data = rail(bill(5), bill(15, service=2), bill(25, service=3),
                returning(1), returning(1, service=2), returning(1, service=3), returning(1))
    assert len(data["clusters"]) == 1
    assert data["clusters"][0]["count"] == 4
    assert len(data["arcs"]) == 4
    assert len({arc["path"].split(" Q ")[0] for arc in data["arcs"]}) == 1
    assert len(rail(returning(1), returning(2), width=100)["clusters"]) == 1
    assert len(rail(returning(1), returning(4), width=100)["clusters"]) == 2


def test_axis_and_height_follow_actual_geometry():
    shallow = rail(bill(3), returning(2))
    deep = rail(bill(29), returning(2))
    assert deep["ticks"][1]["label_y"] > shallow["ticks"][1]["label_y"]
    for data in (shallow, deep, rail(bill(7, 100), bill(8, 100, service=2))):
        assert data["ticks"][0]["label_y"] == data["track_y"] + 19
        assert data["height"] == max(t["label_y"] for t in data["ticks"]) + 12
        assert min(b["amount_y"] for b in data["bills"]) == 16
        assert data["track_y"] == max(b["tile_bottom"] for b in data["bills"]) + 36
        assert all(b["hit_height"] >= 48 for b in data["bills"])
    assert deep["ticks"][1]["label_y"] == max(a["bottom"] for a in deep["arcs"]) + 16


def test_dense_right_edge_labels_keep_separate_lanes_and_targets_are_local():
    data = rail(*(bill(29, 20, service=i) for i in range(6)),
                *(returning(1, service=i) for i in range(6)))
    labels = [(b["label_x"], b["label_y"]) for b in data["bills"]]
    assert len(set(labels)) == 6
    for i, a in enumerate(data["bills"]):
        assert a["hit_y"] + 24 == a["initials_y"]
        assert a["hit_height"] == 48
        for b in data["bills"][i + 1:]:
            if a["label_y"] == b["label_y"]:
                assert b["label_x"] - a["label_x"] >= (len(a["show_label"]) + len(b["show_label"])) * 2.8 + 6


def cluster_example():
    return rail(bill(7, 10, 1), bill(17, 15, 2), bill(27, 20, 3),
                dict(returning(2, service=1), title="Example one"),
                dict(returning(2, service=2), title="Example two"),
                dict(returning(2, service=3), title="Example three"),
                dict(returning(2, service=1), title="Example four"))


def test_cluster_example_matches_review_json():
    import json
    from pathlib import Path
    expected = json.loads((Path(__file__).parent / "fixtures" / "rail-cluster-example.json").read_text())
    assert cluster_example() == expected
