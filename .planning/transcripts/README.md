# Transcript ledgers

One file per worked transcript, written by the `/transcript` skill:
`YYYY-MM-DD-<topic>.md`.

Each ledger records the goal it was worked against and every item under the
verb the owner chose — Kept, Applied, Later, Skipped — with the verbatim quote
that produced it.

**Why the skipped items are kept.** A re-run reads this file first and triages
only what is new. Without the skipped list, every re-run would re-ask the
questions already answered, which is how a tool like this gets abandoned. It is
also the record of a judgment call: "we saw this and decided it was noise" is
different from "we never noticed it".

`*-raw.md` files are transcripts pasted inline rather than supplied as a path.
They are kept so a quote can be checked against its source later.
