# Reusable offers and scoped proof

The owner needs one reusable service catalogue with multiple scopes and prices, while campaigns
can change industry and location. Previously every offer required two CAD amounts and the brief
selected the first five proof cards regardless of service.

## Decision

- Keep `Offer.service` as the service-family label used by campaign services. Add package name,
  custom-quote/fixed mode, currency and recurring period. Several packages may share a family.
- Preserve existing rates as fixed CAD. Custom quotes store NULL amounts, never a fake zero price.
  Retain legacy amount column names for older read-only consumers; the currency field defines the
  unit. No exchange-rate conversion or changes to CAD opportunity reporting are included.
- Keep an existing service-family label stable during package edits. Add another family and pause
  the old offer when changing the service itself; do not silently rewrite historical campaigns.
- Add reviewed service/industry labels and attribution to proof. Empty service/attribution excludes
  legacy cards until reviewed. Filtering happens before the five-card limit. Blank industry means
  any industry for the matching service; a restriction is an exact case-insensitive label match.
- Reuse global, partner and campaign guidance. No new prompt precedence layer or model is needed.
- Expose saved service choices and country during campaign creation. City/state/province remains
  the existing flexible area field. CA/US targeting and sending rules remain the supported set;
  adding a quote currency does not activate a new country or convert reporting values.
- Real services, prices and proof are private workspace data, never seeded in public source code.

## Delivery and compatibility

Alembic revision 0021 adds columns to existing RLS-protected tables and relaxes only offer amount
nullability. PostgreSQL keeps RLS; SQLite uses batch migration. Downgrade refuses quote/non-CAD
rows rather than misrepresenting unknown prices. Back up before rollback and review manually.

Install the current worker once for the new migration and scoped brief generation. Older workers
cannot resolve the new Alembic revision. Subsequent catalogue/guidance edits need no reinstall.
Existing message snapshots and approvals stay unchanged; the update does not enable sending.

## Validation

Tests cover multiple packages, fixed/unknown prices, currencies, exact cents, stale updates,
proof relevance and attribution, unchanged offers across markets, form validation and schema
parity. Existing PostgreSQL CI tests exercise the entire migration chain and RLS checks; Windows
CI builds and exercises installation and upgrades. All test identities and content are synthetic.
