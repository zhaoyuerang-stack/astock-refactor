from run_daily import build_rotation_payload

AUTH = {
    "role": "defensive",
    "family": "defensive-ma16-bond",
    "version": "v1.0",
    "spec_hash": "d" * 64,
}


def test_bear_rotation_without_defensive_authorization_is_not_actionable():
    payload = build_rotation_payload("bear", None)
    assert payload["recommend_bond"] is False
    assert payload["bond_code"] == ""
    assert payload["bond_name"] == ""
    assert "defensive_authorization" not in payload
    assert "未授权" in payload["note"]


def test_bear_rotation_with_defensive_authorization_is_actionable():
    payload = build_rotation_payload("bear", AUTH)
    assert payload["recommend_bond"] is True
    assert payload["bond_code"] == "511010"
    assert payload["bond_name"] == "国债ETF"
    assert payload["defensive_authorization"] == AUTH


def test_bull_rotation_keeps_authorization_for_selling_legacy_bond():
    payload = build_rotation_payload("bull", AUTH)
    assert payload["recommend_bond"] is False
    assert payload["defensive_authorization"] == AUTH
