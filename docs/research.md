# Research controls

The PC/VPS worker performs public website requests; Vercel only queues jobs and displays
results. Sources and imported identities are separate from website identity review.

The website adapter validates URL syntax, every A/AAAA answer and connects directly to one
validated numeric address. TLS still verifies the original hostname. It does not use a proxy,
cookies, automatic redirects or compression. Private, loopback, link-local and mapped IPv6
addresses are blocked. Every research redirect is revalidated and limited to the reviewed
origin. Arbitrary website requests therefore cannot use a proxy that re-resolves destinations.

Robots policies are respected. An unavailable policy holds research; 404/410 allows a bounded
read. Crawl delay is respected up to ten seconds, otherwise manual research is required.
Requests are spaced by at least two seconds; a temporary read failure retries once. Each
response is capped at 512 KB with transport and body deadlines. HTML is parsed without JS.
No forms are submitted, no social platform is scraped and no Google Maps scraper is included.

Source evidence stores a hash, date, URL and bounded facts; the original page is not retained.
A viewport tag does not establish good mobile design. A form element does not establish
successful delivery. Visual quality, traffic, revenue and ad activity remain unknown until
specific reviewer evidence is recorded. Website and email text never becomes agent instructions.

AI uses a fixed provider endpoint and a strict schema. References must match stored evidence
and a recommendation must select one campaign service, or abstain. Semantic correctness still
requires human review. The model cannot approve a message or change the Playbook.

Funding begins at zero. Owner verification records a lifetime USD allowance and expiry.
API keys and current model pricing are environment settings. USD is stored as integer
millionths. Atomic reservations include conservative input and maximum output token costs.
Research pauses before crossing 90%; all future AI work must stay under 100%. Unknown charges
retain their full reservation. Crashed model jobs are held rather than automatically retried.
The provider account budget remains a second, independent spending control.

Tests use synthetic HTML, fake DNS/sockets and fake provider responses. Live arbitrary website
fetching cannot be certified from this restricted execution network; validate on the intended
PC worker. No paid model request was made. Vercel/Neon production setup remains separate.
