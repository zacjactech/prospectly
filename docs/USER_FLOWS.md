# User Flows

Single-user tool; every flow is designed for one operator moving prospects through the pipeline. Steps marked **[system]** are automated; everything else is a human action. Guard steps (⚠) are hard blocks, not suggestions.

---

## Flow 1 — Discover and add a prospect (manual)

1. Operator finds a business (Google Maps browsing, directory, street knowledge, word-of-mouth).
2. **Add prospect** → enters name, category, location, phone, email, website if any, source.
3. ⚠ System checks dedupe keys (name/phone/email/domain):
   - exact match → block with link to existing prospect;
   - fuzzy match → warning badge "possible duplicate" (operator decides).
4. Saved with `stage=new`, activity `created`.

## Flow 2 — CSV import

1. **Import** → upload CSV (≤2 MB).
2. **[system]** Parse, normalize phones (E.164), map columns, validate rows → **preview**: valid / duplicate / error rows with reasons.
3. Operator reviews, unchecks rows they don't want → **Commit**.
4. **[system]** Insert valid rows under one `csv_import` source; duplicates skipped; fuzzy matches flagged `stage=new` + tag `possible-duplicate`.
5. Result summary: created / skipped / flagged. Backup reminder shown (P: backup before import).

## Flow 3 — Research a prospect (core evidence loop)

1. Open prospect → **Run research**.
2. **[system]** Job pipeline: website inspection (HTTP/DNS/HTML signals) → web search (enabled provider) → optional discovery enrichment → optional AI summary (if configured).
3. UI shows live step progress (HTMX poll); **findings appear as they land** with source URL, type, timestamp, verification, confidence, origin badges.
4. Run finishes: `succeeded` / `partially_completed` (some providers failed — findings kept) / `failed` (error shown, retry offered).
5. Operator reviews each finding:
   - **Verify** (origin preserved; verification becomes human-confirmed),
   - **Edit** (statement corrected; original kept in audit),
   - **Delete** (wrong/irrelevant),
   - **Add manually** (a fact the machine missed, `origin=human`, `source_type=user`).
6. ⚠ "No website" is only recorded as fact when evidence supports it: search found nothing AND/OR DNS/HTTP failure captured in `website_checks`. Otherwise `website_status=check_failed` — the UI shows "could not verify", never "no website".
7. Prospect stage → `researched`.

## Flow 4 — Identify and validate an opportunity

1. **[system]** After research, rule engine proposes opportunities (e.g., "no standalone website found — searches + DNS evidence attached"), each linked to supporting findings.
2. Optional: **Analyze with AI** (if configured) → additional `origin=ai` proposals marked `proposed`, never auto-validated.
3. Operator reviews: **Validate** (moves prospect to `opportunity_identified`) or **Dismiss** (with reason).
4. ⚠ Validation requires ≥1 evidence link; AI-only evidence cannot be validated without the operator adding/verifying a finding first.

## Flow 5 — Draft → approve → WhatsApp → confirm sent (the heart of the product)

1. **Generate draft** → context = validated opportunities + verified/high-confidence findings + business profile.
2. **[system]** `MessageGenerator` (AI or template) produces text with grounding IDs; draft opens in editor.
3. Operator edits freely (goal: their voice, accurate specifics; edits logged).
4. ⚠ **Submit for review** → `pending_review` (editable, returns to `draft` on edit).
5. ⚠ **Approve** → `approved`. Blocked if: suppressed prospect, empty text, or missing grounding without the "I accept ungrounded content" acknowledgement.
6. **Open in WhatsApp** → browser opens `wa.me` with prefilled message → status `opened_in_whatsapp`. *(Status does not imply sending.)*
7. Operator sends manually in WhatsApp.
8. ⚠ Back in app: **I have sent this message** → `sent_confirmed` + activity + `last_contacted_at` + stage `contacted` + prompt to schedule follow-up (default: +3 days).
   - If they did *not* send: **Not sent** → returns to `approved` (ready to re-open); editing the text returns to `draft` and requires re-approval.

## Flow 6 — Reply handling

1. Prospect replies on WhatsApp (operator sees it in WhatsApp).
2. In app: **Record reply** → paste/summarize text + timestamp → activity `reply_received`, stage → `replied`.
3. Operator chooses: ** Interested** (stage `interested`), **Not interested** (stage `not_interested`, terminal for outreach), or **Do not contact** (⚠ terminal suppression, reason logged).

## Flow 7 — Follow-up

1. **Follow-ups** page shows due/overdue (sorted overdue-first).
2. For each: open prospect → context panel (last message, reply, findings) → **Complete** with note, or **Snooze** (new due date), or transition per Flow 6.
3. ⚠ Starting *new* outreach on a prospect already `sent_confirmed` requires explicit "start new outreach" and is blocked for `do_not_contact`.

## Flow 8 — Opt-out / do not contact

1. Any time: **Do not contact** button (also available from reply recording).
2. ⚠ Effect: blocks draft generation, approval, opening WhatsApp, and new-outreach actions; badge shown on prospect everywhere; counted in dashboard "requires review" only if set mid-pipeline.
3. Clearing requires explicit action + reason (audit-logged). Opt-out requests received via WhatsApp are recorded here (P10).

## Flow 9 — Conversion

1. Prospect agrees → operator sets stage `converted`.
2. Handoff note (what was agreed) recorded as a note; prospect leaves active counts but remains searchable. Invoicing/payments are out of MVP scope.

## Flow 10 — Review & correction day (maintenance loop)

Operator weekly: Dashboard "requires review" → fix unverified findings → approve pending drafts → clear overdue follow-ups → check Settings → Usage (provider errors/rate limits) → run backup.

---

## Flow constraints summary (enforced in code)

| Constraint | Where enforced |
|---|---|
| No message without approval | `messages` state machine; no API path skips `approve` |
| `sent_confirmed` only by explicit confirmation | `confirm-sent` is the sole writer of `sent_confirmed`/`sent_confirmed_at` |
| Suppression blocks all outbound | guard in draft/approve/open services |
| No duplicate prospects | unique partial indexes + add/import pre-checks |
| AI claims start unverified | finding invariant in service layer |
| History preserved | `activities` append-only; message text versions kept (`generated_text` vs `current_text`) |
