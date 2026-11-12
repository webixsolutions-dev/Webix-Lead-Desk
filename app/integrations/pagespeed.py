"""One bounded request to Google's mobile Lighthouse endpoint."""

import json
import math
from typing import Any
from urllib.error import URLError
from urllib.parse import urlencode, urlsplit, urlunsplit
from urllib.request import Request, build_opener

from app.integrations.http import NoRedirect
from app.sources.validation import website_url

MAX_BYTES = 3_000_000
METRICS = {
    "largest-contentful-paint": "lcp_ms",
    "cumulative-layout-shift": "cls",
    "total-blocking-time": "tbt_ms",
}
DIAGNOSTICS = (
    "render-blocking-insight",
    "image-delivery-insight",
    "document-latency-insight",
    "render-blocking-resources",
    "unused-javascript",
    "uses-optimized-images",
)


class PageSpeedUnavailable(Exception):
    """A provider failure with no raw response or credential in the error."""


def mobile_audit(url: str, key: str) -> dict[str, Any]:
    """Return labeled lab evidence from a single simulation of the checked public page."""
    target = urlsplit(website_url(url))
    public_url = urlunsplit((target.scheme, target.netloc, target.path, "", ""))
    params = urlencode(
        [
            ("url", public_url),
            ("key", key),
            ("strategy", "mobile"),
            ("category", "performance"),
            ("category", "seo"),
            ("fields", "lighthouseResult(finalUrl,fetchTime,lighthouseVersion,categories,audits)"),
        ]
    )
    request = Request("https://www.googleapis.com/pagespeedonline/v5/runPagespeed?" + params)
    try:
        with build_opener(NoRedirect()).open(request, timeout=45) as result:
            raw = result.read(MAX_BYTES + 1)
        if len(raw) > MAX_BYTES:
            raise ValueError("Result exceeds limit")
        result_json = json.loads(raw)["lighthouseResult"]
        final = urlsplit(website_url(result_json["finalUrl"]))
        if final.hostname != target.hostname or final.path.rstrip("/") != target.path.rstrip("/"):
            raise ValueError("Audit returned a different page")
        audits = result_json["audits"]
        scores = result_json["categories"]
        evidence: dict[str, Any] = {
            "status": "measured",
            "method": "Google Lighthouse mobile lab sample",
            "page": public_url,
            "report_url": "https://pagespeed.web.dev/analysis?"
            + urlencode({"url": public_url, "form_factor": "mobile"}),
            "lighthouse_version": str(result_json["lighthouseVersion"])[:40],
            "measured_at": str(result_json["fetchTime"])[:40],
        }
        for category in ("performance", "seo"):
            number = scores[category]["score"]
            if type(number) in {int, float} and 0 <= number <= 1:
                evidence[category + "_score"] = round(number * 100)
        for audit_id, label in METRICS.items():
            number = audits.get(audit_id, {}).get("numericValue")
            if type(number) in {int, float} and math.isfinite(number) and number >= 0:
                evidence[label] = round(number, 3 if label == "cls" else 0)
        evidence["diagnostics"] = [
            audit_id
            for audit_id in DIAGNOSTICS
            if type(audits.get(audit_id, {}).get("score")) in {int, float}
            and 0 <= audits[audit_id]["score"] < 0.9
        ]
        if "performance_score" not in evidence or "lcp_ms" not in evidence:
            raise ValueError("Missing mobile performance data")
        return evidence
    except (URLError, OSError, KeyError, TypeError, AttributeError, ValueError) as error:
        raise PageSpeedUnavailable("PageSpeed did not return usable mobile evidence") from error
