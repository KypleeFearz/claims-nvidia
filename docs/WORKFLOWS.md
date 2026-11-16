# The three claim workflows

The line of business of the policy sets the workflow. The checks, the score and the routing are shared (`docs/FRAUD.md`).

These lists are a first reading of a typical claims flow. Paul must confirm them (open point O12 in `docs/DECISIONS.md`). The code uses them as written.

## Shared fields of every claim

`policy_number`, `claimant_name`, `incident_date`, `incident_place`, `cause`, `claimed_amount`, `currency`, `documents_present`.

## A: Property / household

| | |
|---|---|
| Causes | Water, fire, theft, breakage |
| Required documents | Repair invoice **or** repair quote; at least one damage photo |
| Required for theft | Police report |
| Optional | Purchase receipts of the damaged items |
| Fields | Damaged items (description, purchase date, value), repair company, invoice number, invoice date, line items, total |
| Straight-through limit | CHF 2,000 |
| Own rules | Police report present if the cause is theft. Photo shows the damage the claim describes (VLM). |

## B: Health / accident

| | |
|---|---|
| Causes | Accident, illness |
| Required documents | Medical invoice; medical report or certificate |
| Fields | Patient name, provider, treatment dates, diagnosis in short, invoice number, line items, total |
| Straight-through limit | CHF 1,000 |
| Own rules | Patient is the insured person. Treatment dates are on or after the incident date. |
| Data | The most sensitive workflow. Medical text must never appear in logs outside the pipeline events. |

## C: Travel

| | |
|---|---|
| Causes | Trip cancellation, luggage, medical cost abroad |
| Required documents | Booking confirmation; cancellation confirmation or receipts (for the cause `luggage`, the airline's luggage report counts too) |
| Required for illness | Medical certificate |
| Fields | Booking reference, departure date, trip price, cancellation date, refund received, cause |
| Straight-through limit | CHF 2,500 |
| Own rules | Cancellation date is between the incident date and the departure date. Claimed amount = trip price − refund. |

## Settlement: what the policy pays (issue #86)

A first reading of the practice, as the lists above (O12). The code is `backend/app/settlement.py`; the calculation by hand of the 10 kit stories is `data/mock/settlements.json`.

| Step | Rule | Article key |
|---|---|---|
| 1. Documented loss | Travel: trip price − refund received. Travel, cause `luggage`: the sum of the receipts. Property: the total of the repair invoice or quote. Health: the total of the medical invoice. When the lines of an invoice do not add up to its total, the lines count. | `loss`, `lines` |
| 2. Claimed amount | Not more than the customer claims. When the customer claims more than the documents prove, the documents count. | `claimed`, `documented` |
| 3. Covered cause | A cause that the policy does not name gives CHF 0. | `cause` |
| 4. Share of the customer | A percentage of the cost (the accident policy: 10%). | `coinsurance` |
| 5. Deductible | A fixed amount of the policy. | `deductible` |
| 6. Coverage limit | Not more than the limit of the policy. | `limit` |

Each line of the proposal has an amount, a reason, an article of the mock general conditions (`data/mock/policy_wordings.json`) and, for step 1, the source page.

**A person decides** when a line reduces the amount for another reason than steps 4 and 5: the lines of an invoice do not add up, the customer claims more than the documents prove, the cause is not covered, the limit cuts the amount, no document gives an amount, or the proposal is CHF 0. The claim then goes to `handler_review` (or stays `investigation`). The system never pays less alone than the contract alone gives.

Not built: depreciation by the age of an item, sub-limits per category, line items left out one by one, earlier payments against a yearly limit, payment.

## Build order

Travel first (simplest documents), then property, then health. All three are built.
