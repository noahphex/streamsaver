from core.presentation import service_mark, ledger_reason, library_states


def test_channel_initials_use_base_name():
    assert service_mark("Screambox Amazon Channel")["initials"] == "SC"
    assert service_mark("Starz Amazon Channel")["initials"] == "ST"
    assert service_mark("Prime Video Amazon Channel")["initials"] == "PV"
    assert service_mark("")["initials"] == ""


def test_ledger_reason_is_presentation_only():
    assert ledger_reason("Annual plan prepaid through 2027-01-01 (90 days). No action needed.") == ""
    assert ledger_reason("Free ($0) — nothing to save by pausing or not renewing.") == ""
    for level, copy in [("High", "You watch it a lot"), ("Medium", "You watch it sometimes"), ("Low", "You rarely watch it")]:
        assert ledger_reason(f"1 active show(s). Viewer activity: {level}.") == f"1 active show(s). {copy}."


def test_library_groups_and_exact_legacy_state():
    assert library_states() == ()
    assert library_states("All", "Watching") == ("active", "occasional")
    assert library_states("All", "Waiting") == ("anticipated",)
    assert set(library_states("All", "Done")) == {"completed", "abandoned", "dormant", "archived", "ignored"}
    assert library_states("Active", "Done") == ("active",)
    assert library_states("All", "unknown") == ()
