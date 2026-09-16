# Copyright 2026 Prash Balan (@its-me-prash) — GNU AGPL v3.0-or-later
# SPDX-License-Identifier: AGPL-3.0-or-later
"""MyŠkoda 8.16.0 — the predictive-maintenance response grew a ``predictions``
array beside ``reminders``.

Grounded on PredictiveMaintenanceDto / PredictionDto{type, name, status,
statusDescription, activeLeadId} from the 8.16.0 APK. PredictionTypeDto has
exactly one value today (BRAKE_PADS). The matching reset endpoint is an
owner-only WRITE and is deliberately not wired.
"""
from __future__ import annotations

from custom_components.vag_connect.coordinator import _parse_predictive_maintenance

_PREDICTION = {
    "type": "BRAKE_PADS",
    "name": "Brake pads",
    "status": "WARNING",
    "statusDescription": "Replace soon",
    "activeLeadId": "lead-42",
}


def test_reminders_and_predictions_parse_together() -> None:
    out = _parse_predictive_maintenance({
        "reminders": [
            {
                "type": "TECHNICAL_INSPECTION",
                "dueDate": "2026-11-01",
                "status": "DUE_SOON",
            },
        ],
        "predictions": [_PREDICTION],
    })
    # existing reminder output is untouched
    assert out["reminder_technical_inspection"] == "2026-11-01"
    # state is the lowercased status, details go to the sensor's attributes
    assert out["brake_pads_prediction"] == "warning"
    assert out["brake_pads_prediction_type"] == "BRAKE_PADS"
    assert out["brake_pads_prediction_name"] == "Brake pads"
    assert out["brake_pads_prediction_status_description"] == "Replace soon"
    assert out["brake_pads_prediction_active_lead_id"] == "lead-42"


def test_predictions_only_payload() -> None:
    """A response without ``reminders`` used to short-circuit to {}."""
    out = _parse_predictive_maintenance({"predictions": [_PREDICTION]})
    assert out["brake_pads_prediction"] == "warning"
    assert not any(k.startswith("reminder_") for k in out)


def test_missing_predictions_leaves_reminders_unchanged() -> None:
    payload = {"reminders": [{"type": "FIRST_AID_KIT", "status": "EXPIRED"}]}
    assert _parse_predictive_maintenance(payload) == {
        "reminder_first_aid_kit": "EXPIRED",
    }


def test_no_data_status_is_surfaced() -> None:
    """NO_DATA is a status the app itself renders (the wear model hasn't
    converged yet) — unlike the reminders' "NOT_SET" sentinel it is kept, so the
    entity exists from the first poll and can later flip to ok/warning/critical.
    """
    out = _parse_predictive_maintenance({"predictions": [
        {"type": "BRAKE_PADS", "name": "Brake pads", "status": "NO_DATA",
         "statusDescription": None, "activeLeadId": None},
    ]})
    assert out["brake_pads_prediction"] == "no_data"
    assert out["brake_pads_prediction_name"] == "Brake pads"
    # nullable DTO fields never become the string "None"
    assert "brake_pads_prediction_status_description" not in out
    assert "brake_pads_prediction_active_lead_id" not in out


def test_unknown_prediction_type_and_junk_are_dropped() -> None:
    out = _parse_predictive_maintenance({"predictions": [
        {"type": "FUTURE_THING", "status": "OK"},   # unknown enum value
        {"type": "BRAKE_PADS"},                     # no status → no state
        "not-a-dict",
    ]})
    assert out == {}


def test_non_dict_and_non_list_inputs() -> None:
    assert _parse_predictive_maintenance(None) == {}
    assert _parse_predictive_maintenance([]) == {}
    assert _parse_predictive_maintenance({"predictions": {"type": "BRAKE_PADS"}}) == {}
