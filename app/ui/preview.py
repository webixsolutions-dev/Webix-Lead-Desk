"""Fictional, read-only examples that never become real contacts or jobs."""

from typing import TypedDict


class PreviewLead(TypedDict):
    """A synthetic lead card, with no usable contact address."""

    id: str
    name: str
    initials: str
    industry: str
    city: str
    service: str
    observation: str
    question: str
    website: str
    score: int
    color: str


LEADS: list[PreviewLead] = [
    {
        "id": "prairie",
        "name": "Prairie Peak Heating",
        "initials": "PP",
        "industry": "HVAC",
        "city": "Calgary",
        "service": "After-hours enquiries",
        "score": 8,
        "color": "violet",
        "website": "prairie-peak.example",
        "observation": "Example finding: the contact page shows a phone number and a quote form.",
        "question": "Who looks after quote requests that arrive after the office closes?",
    },
    {
        "id": "cedar",
        "name": "Cedarline Exteriors",
        "initials": "CE",
        "industry": "Roofing",
        "city": "Airdrie",
        "service": "A clearer quote journey",
        "score": 7,
        "color": "blue",
        "website": "cedarline.example",
        "observation": "Example finding: a service page and a separate request-an-estimate page.",
        "question": "Are there any questions customers usually ask before requesting an estimate?",
    },
    {
        "id": "north",
        "name": "Northfield Clean Co.",
        "initials": "NC",
        "industry": "Cleaning",
        "city": "Calgary",
        "service": "Client updates",
        "score": 7,
        "color": "green",
        "website": "northfield-clean.example",
        "observation": "Example finding: recurring commercial cleaning is listed as a service.",
        "question": "How do your regular clients currently request a change to their schedule?",
    },
]

NAVIGATION = [
    ("today", "/", "Today", "grid"),
    ("leads", "/leads", "Leads", "search"),
    ("outbox", "/outbox", "Outbox", "send"),
    ("pipeline", "/pipeline", "Replies & Pipeline", "chat"),
    ("demos", "/demos", "Demos", "window"),
    ("settings", "/settings", "Playbook & Settings", "sliders"),
]
