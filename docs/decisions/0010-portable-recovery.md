# ADR 0010: Offline portable recovery with a retained maintenance gate

Status: implemented in Phase 10.

SQLite and PostgreSQL need a reviewed transfer path without losing sender identity, approvals, suppression or uncertain SMTP history. An automatic online copy could miss writes or leave two active senders.

Use an explicit shared maintenance gate plus an operator-confirmed offline window. Reject live leases, hold interrupted transmissions, export a consistent snapshot of known tables and encrypt/authenticate it with a separate environment-held Fernet key. Verify stored credential ciphertext using the original credential key. Restore only the matching schema into an empty database in one transaction; compare counts and canonical content before commit. Omit login sessions and retain maintenance.

The operator must stop all writers; a maintenance flag alone cannot cancel an in-flight request. An already accepted SMTP DATA cannot be rolled back. Uncertain results remain held after transfer. Resuming is an explicit local command on one chosen database. Changing APP_MODE does not move data, provision resources or enable delivery.

Snapshots are buffered and bounded to 100 MB by default. Large installations need a separate database-native backup design. VPS units and reverse-proxy examples are templates, not a provisioned or verified server. CI rehearses transfer using synthetic SQLite data and a disposable local PostgreSQL service; production acceptance remains separate.
