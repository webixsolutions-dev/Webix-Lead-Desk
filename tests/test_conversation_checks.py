"""Twenty-five synthetic conversations cover both channels and business lanes."""

from typing import Any

import pytest

from app.drafting.checks import block_ai, derived_context, require_override, review_copy

# id, incoming, suggestion, earlier outbound, required flag (empty means clean)
CASES = [
    (
        "email_client_thanks",
        "The booking workflow is manual.",
        "Thanks for explaining the booking workflow.",
        "Thanks for describing your team.",
        "repeated_thanks",
    ),
    (
        "linkedin_partner_generic",
        "Send portfolio details.",
        "Thank you for your reply. Here are portfolio details.",
        "",
        "banned_phrase",
    ),
    (
        "email_client_two_questions",
        "The booking workflow is manual.",
        "Is booking manual? Who handles booking?",
        "",
        "multiple_questions",
    ),
    (
        "linkedin_partner_repetition",
        "Our booking workflow is manual.",
        "We can review the booking workflow.",
        "We can review the booking workflow.",
        "repeated_sentence",
    ),
    (
        "email_client_unrelated",
        "Our team uses HubSpot.",
        "Your website could use a new design.",
        "",
        "no_reference",
    ),
    (
        "linkedin_partner_length",
        "Send details.",
        "Details for the project workflow. " * 13,
        "",
        "length",
    ),
    (
        "email_client_answered",
        "HubSpot is the CRM we use.",
        "Which CRM does your business use?",
        "Which CRM does your business use?",
        "answered_question",
    ),
    ("linkedin_client_oneword", "Okay", "Can we schedule a call?", "", "premature_meeting"),
    ("email_partner_price", "What is the price?", "The price is CAD 500.", "", "price"),
    (
        "linkedin_client_guarantee",
        "We need more bookings.",
        "We guarantee more bookings.",
        "",
        "guarantee",
    ),
    (
        "email_client_covered",
        "We're covered on booking.",
        "We can offer CRM and SEO beyond booking.",
        "",
        "pitch_stacking",
    ),
    ("linkedin_partner_stop", "Stop messaging me.", "We can help with messaging.", "", "stop"),
    (
        "email_client_hostile",
        "Not interested, remove me.",
        "I understand your decision.",
        "",
        "stop",
    ),
    (
        "linkedin_partner_portfolio",
        "Can you send portfolio details?",
        "Which type of portfolio project would be most useful?",
        "",
        "",
    ),
    (
        "email_client_wrongperson",
        "Alex handles booking, not me.",
        "Is Alex the right contact for the booking workflow?",
        "",
        "",
    ),
    (
        "linkedin_client_casual",
        "Hey, our booking is manual",
        "Is that manual booking done in a shared calendar?",
        "",
        "",
    ),
    (
        "email_partner_process",
        "How would your delivery process work?",
        "We would agree the delivery scope first. Would a short call help clarify the process?",
        "",
        "",
    ),
    (
        "linkedin_client_goal",
        "Our goal is fewer manual booking steps.",
        "A booking workflow review could help. Would a short call be useful?",
        "",
        "",
    ),
    (
        "email_client_long",
        (
            "We handle booking with two receptionists. Each booking is "
            "entered into the calendar manually. Some customers request "
            "repeat appointments, and these need a separate check."
        ),
        (
            "For repeat appointments, is the calendar check handled by the "
            "same receptionist who takes the booking?"
        ),
        "",
        "",
    ),
    (
        "linkedin_partner_acceptance",
        "",
        "Would you like to book a meeting?",
        "",
        "missing_incoming",
    ),
    (
        "email_client_callanswer",
        "Yes",
        "Would a video call work for you?",
        "Would a short call help discuss the workflow?",
        "",
    ),
    (
        "linkedin_partner_repeatopener",
        "Send project details.",
        "Quick question: which project details would help?",
        "Quick question: which platform do you use?",
        "repeated_phrase",
    ),
    (
        "email_client_price_scope",
        "What is the website price?",
        "For the website price, how many pages are needed?",
        "",
        "",
    ),
    (
        "linkedin_partner_nopitch",
        "We're covered on booking.",
        "Understood, glad your booking workflow is covered.",
        "",
        "",
    ),
    (
        "email_client_unavailable",
        "Could AI voice agents take bookings?",
        "We can deploy AI voice agents for your bookings.",
        "",
        "unavailable_promise",
    ),
]


def context(last: str, previous: str = "") -> dict[str, Any]:
    """Use minimal actual-turn data, not inferred connection acceptance."""
    return {
        "recent_messages_data_only": (
            [{"direction": "outbound", "text": previous}] if previous else []
        )
        + ([{"direction": "inbound", "text": last}] if last else [])
    }


@pytest.mark.parametrize("case,last,body,previous,expected", CASES, ids=[c[0] for c in CASES])
def test_evaluation_conversations(
    case: str, last: str, body: str, previous: str, expected: str
) -> None:
    """Every flagged suggestion is blocked; clean cases stay available for exact owner review."""
    report = review_copy(
        body,
        context(last, previous),
        channel=case.split("_")[0],
        action="reply",
        offers=[
            {
                "service": "AI voice agents",
                "name": "Inbound receptionist pilot",
                "status": "discovery_only",
                "offer_type": "service",
            }
        ],
    )
    codes = {f["code"] for f in report["flags"]}
    if expected:
        assert expected in codes
        with pytest.raises(ValueError, match="Conversation checks"):
            block_ai(report)
    else:
        assert not codes
        block_ai(report)


def test_bundle_price_override_and_non_overridable_stop() -> None:
    """Price permission is stage-specific; style overrides cannot bypass a stop."""
    ctx = context("What is the project price?")
    priced = "The project price is CAD 500 for the agreed scope."
    assert not review_copy(priced, ctx, channel="email", action="reply", allow_price=True)["flags"]
    assert any(
        f["code"] == "price"
        for f in review_copy(priced, ctx, channel="email", action="follow_up", allow_price=True)[
            "flags"
        ]
    )
    report = review_copy(
        "The project fits our Content Engine bundle.",
        ctx,
        channel="email",
        action="reply",
        offers=[
            {
                "service": "Content Engine",
                "name": "Content Engine",
                "offer_type": "bundle",
                "status": "available",
            }
        ],
    )
    assert any(f["code"] == "bundle_pitch" for f in report["flags"])
    with pytest.raises(ValueError, match="override"):
        require_override(report, "")
    require_override(report, "Reviewed the requested package description with the owner.")
    stop = review_copy(
        "We will stop messaging.", context("Stop messaging me."), channel="linkedin", action="reply"
    )
    with pytest.raises(ValueError, match="no further"):
        require_override(stop, "Even a detailed note cannot override a stop.")


def test_two_substantive_replies_and_connection_observation() -> None:
    """An acceptance observation is not a message; two substantive turns are interest signals."""
    ctx = context("We enter all enquiries into the shared spreadsheet.")
    ctx["recent_messages_data_only"].insert(
        0, {"direction": "inbound", "text": "Our office team handles each booking manually."}
    )
    assert derived_context(ctx)["meeting_signal"]
    observed = {"recent_messages_data_only": [{"direction": "observation", "text": "accepted"}]}
    assert not derived_context(observed)["meeting_signal"]
