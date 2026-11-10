"""Explainable conversation heuristics; they do not verify truth or infer consent."""

import re
from difflib import SequenceMatcher
from typing import Any

from app.drafting.content import fingerprint

BANNED = (
    "i hope this message finds you well",
    "thank you for your response",
    "thank you for your reply",
    "thank you for your message",
    "i wanted to reach out",
    "just following up",
    "just checking in",
    "touching base",
    "circling back",
    "as per my last message",
    "we are a full-service agency",
    "we offer a wide range of services",
    "leverage",
    "synergy",
    "cutting-edge",
    "game-changer",
    "take your business to the next level",
    "does that make sense",
    "i'd love to pick your brain",
)
STOP_WORDS = set(
    [
        "a",
        "an",
        "the",
        "this",
        "that",
        "is",
        "are",
        "was",
        "were",
        "be",
        "been",
        "to",
        "of",
        "and",
        "or",
        "for",
        "in",
        "on",
        "at",
        "it",
        "its",
        "your",
        "you",
        "our",
        "we",
        "i",
        "my",
        "me",
        "us",
        "with",
        "have",
        "has",
        "do",
        "does",
        "did",
        "would",
        "could",
        "can",
        "will",
        "should",
        "about",
        "how",
        "what",
        "when",
        "where",
        "which",
        "please",
        "thanks",
        "thank",
        "yes",
        "no",
        "hi",
        "hello",
        "hey",
        "as",
        "if",
        "from",
        "by",
        "so",
        "not",
        "but",
        "just",
        "all",
        "need",
        "like",
        "more",
        "some",
        "there",
        "they",
        "their",
        "them",
        "get",
        "got",
        "want",
        "know",
    ]
)
PRICE = (
    "[$€£]|\\b(?:CAD|USD|GBP|EUR|AED|NZD|PKR|AUD)\\s*\\d|\\b\\d[\\d,.]*\\s*(?"
    ":CAD|USD|GBP|EUR|AED|NZD|PKR|AUD|dollars?)\\b"
)
MEETING = (
    "\\b(?:book|schedule|arrange|join|have|short|quick|video|available "
    "for)\\b.{0,35}\\b(?:call|meeting|chat)\\b|\\bmeet (?:on|at|next)\\b"
)
STOP = (
    "\\b(?:unsubscribe|remove me|do not contact|don't contact|stop "
    "(?:emailing|messaging|contacting)|not interested)\\b|^stop[.!\\s]*$"
)
SERVICE_TERMS = (
    "website",
    "seo",
    "crm",
    "automation",
    "ads",
    "social media",
    "portal",
    "voice agent",
    "e-commerce",
    "design",
    "video",
)


def _terms(text: str) -> set[str]:
    return {
        w for w in re.findall(r"[a-z0-9]+", text.casefold()) if len(w) > 2 and w not in STOP_WORDS
    }


def _sentences(text: str) -> list[str]:
    return [x.strip().casefold() for x in re.split(r"[.!?\n]+", text) if len(_terms(x)) >= 2]


def _similar(a: str, b: str) -> bool:
    return (
        len(a) >= 20
        and len(b) >= 20
        and SequenceMatcher(None, a.casefold(), b.casefold()).ratio() >= 0.88
    )


def derived_context(context: dict[str, Any]) -> dict[str, Any]:
    """Summarize bounded exact-person history; answered-question matches remain heuristic."""
    messages = context.get("recent_messages_data_only", [])
    incoming = [
        m for m in messages if m.get("direction") == "inbound" and m.get("text", "").strip()
    ]
    outgoing = [
        m for m in messages if m.get("direction") == "outbound" and m.get("text", "").strip()
    ]
    last = incoming[-1]["text"] if incoming else ""
    previous = outgoing[-1]["text"] if outgoing else ""
    questions: list[str] = []
    answered: list[str] = []
    for index, m in enumerate(messages):
        if m.get("direction") != "outbound":
            continue
        asked = re.findall(r"[^.!?\n]*\?", m.get("text", ""))
        questions.extend(asked)
        if any(
            x.get("direction") == "inbound" and x.get("text", "").strip()
            for x in messages[index + 1 :]
        ):
            answered.extend(asked)
    substantive = [m for m in incoming if len(_terms(m["text"])) >= 4]
    explicit = bool(
        re.search(
            (
                "\\b(?:problem|struggl|goal|need|looking "
                "for|improve|proposal|suggest|price|cost|pricing|timeline|process|"
                "how (?:does|would|can|do)|when (?:can|could|would))"
            ),
            last,
            re.I,
        )
    )
    previous_call = bool(re.search(MEETING, previous, re.I) and "?" in previous)
    meeting = bool(incoming) and (
        explicit
        or len(substantive) >= 2
        or (previous_call and last.strip().casefold().strip(".!") in {"yes", "sure", "okay", "ok"})
    )
    covered = bool(
        re.search(r"\b(?:we(?:'re| are) covered|already (?:have|use)|all covered)\b", last, re.I)
    )
    if covered or re.search(STOP, last, re.I):
        meeting = False
    return {
        "last_incoming": last,
        "previous_outgoing": previous,
        "open_question": questions[-1]
        if questions and messages and messages[-1].get("direction") == "outbound"
        else "",
        "possibly_answered_questions": answered[-8:],
        "incoming_length": len(last),
        "observed_style": "casual"
        if re.search(r"\b(?:hey|yep|yeah|cheers|nah)\b", last, re.I)
        else "neutral",
        "meeting_signal": meeting,
        "covered": covered,
        "already_thanked": any(
            re.match(r"\s*(?:thanks|thank you)\b", m["text"], re.I) for m in outgoing
        ),
    }


def review_copy(
    body: str,
    context: dict[str, Any],
    *,
    channel: str,
    action: str,
    offers: list[dict[str, Any]] | None = None,
    allow_price: bool = False,
) -> dict[str, Any]:
    """Return visible flags; no provider call, no automatic outbound action."""
    derived = derived_context(context)
    last = str(derived["last_incoming"])
    previous = str(derived["previous_outgoing"])
    messages = context.get("recent_messages_data_only", [])
    outbound = [m["text"] for m in messages if m.get("direction") == "outbound"]
    flags: list[dict[str, str]] = []

    def flag(code: str, reason: str) -> None:
        flags.append({"code": code, "reason": reason})

    text = body.casefold()
    if re.match(r"\s*(thanks|thank you)\b", body, re.I) and re.match(
        r"\s*(thanks|thank you)\b", previous, re.I
    ):
        flag("repeated_thanks", "The preceding outgoing message also opened with thanks.")
    if any(phrase in text for phrase in BANNED):
        flag("banned_phrase", "Replace a generic or pressured phrase with a direct answer.")
    if any(
        phrase in text and any(phrase in old.casefold() for old in outbound)
        for phrase in ("thanks for getting back to me", "quick question")
    ):
        flag("repeated_phrase", "This conversation already used that opener.")
    if body.count("?") > 1:
        flag("multiple_questions", "Ask one question at a time.")
    if any(_similar(a, b) for a in _sentences(body) for old in outbound for b in _sentences(old)):
        flag(
            "repeated_sentence", "A sentence or opener closely repeats an earlier outgoing message."
        )
    if last and _terms(last) and not _terms(last).intersection(_terms(body)):
        flag(
            "no_reference",
            "No shared key term with their latest message; check that this answers it.",
        )
    if action == "reply" and not last:
        flag(
            "missing_incoming",
            "Record this person's actual incoming message before preparing a reply.",
        )
    if (
        (last and len(body) > max(300, 2 * len(last)))
        or len(body.split()) > 120
        or (channel == "linkedin" and len(body) > 700)
    ):
        flag("length", "Shorten the reply to fit their message and the channel limit.")
    if any(
        _similar(q, old)
        for q in re.findall(r"[^.!?\n]*\?", body)
        for old in derived["possibly_answered_questions"]
    ):
        flag(
            "answered_question",
            (
                "This question was followed by an earlier reply; verify whether "
                "it was already answered."
            ),
        )
    if re.search(PRICE, body, re.I) and not (allow_price and action == "reply"):
        flag(
            "price",
            (
                "A price needs the owner's explicit permission for this reply and "
                "exact scope/currency."
            ),
        )
    if re.search(r"\bguarantee(?:d)?\b", body, re.I):
        flag("guarantee", "Remove unsupported guaranteed outcomes.")
    if re.search(MEETING, body, re.I) and not derived["meeting_signal"]:
        flag("premature_meeting", "No recorded interest signal supports a meeting ask yet.")
    if (
        derived["covered"]
        and sum(bool(re.search(r"\b" + re.escape(term) + r"\b", text)) for term in SERVICE_TERMS)
        > 1
    ):
        flag("pitch_stacking", "They said they are covered; do not stack alternative services.")
    for offer in offers or []:
        names = [str(offer.get("service", "")), str(offer.get("name", ""))]
        words = _terms(" ".join(names))
        mentioned = any(len(n) > 3 and n.casefold() in text for n in names) or bool(
            words and len(words.intersection(_terms(body))) >= min(2, len(words))
        )
        if offer.get("offer_type") == "bundle" and any(
            len(n) > 3 and n.casefold() in text for n in names
        ):
            flag(
                "bundle_pitch",
                (
                    "Keep bundle packages in discovery or a reviewed proposal, not "
                    "conversation pitches."
                ),
            )
        elif (
            offer.get("offer_type") != "bundle"
            and offer.get("status") in {"paused", "discovery_only"}
            and mentioned
            and re.search(
                r"\b(?:we (?:can|will)|ready|launch|deploy|deliver|install|set up|build)\b",
                body,
                re.I,
            )
        ):
            flag(
                "unavailable_promise",
                (
                    "A paused/discovery-only service is described as deliverable; ask "
                    "about feasibility first."
                ),
            )
    if re.search(STOP, last, re.I) or any(
        m.get("outcome") in {"stop", "not_interested", "unsubscribe"} for m in messages[-1:]
    ):
        flag(
            "stop",
            "The latest recipient message requests no further outreach; do not prepare a reply.",
        )
    unique = {f["code"]: f for f in flags}
    return {
        "flags": list(unique.values()),
        "derived": derived,
        "context_digest": fingerprint({"context": context, "offers": offers or []}),
    }


def block_ai(report: dict[str, Any]) -> None:
    """Reject failed suggestions before any draft revision becomes reviewable."""
    if report["flags"]:
        raise ValueError("Conversation checks: " + "; ".join(f["reason"] for f in report["flags"]))


def require_override(report: dict[str, Any], note: str) -> None:
    """A human may override style heuristics with a reason, never a stop request."""
    if any(f["code"] == "stop" for f in report["flags"]):
        raise ValueError("Recipient requested no further outreach")
    if report["flags"] and not 10 <= len(note.strip()) <= 1000:
        raise ValueError(
            "Fix the conversation warnings or record a 10–1000 character override reason"
        )
