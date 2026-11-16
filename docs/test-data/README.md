# Synthetic test data — NOT real

Everything in this folder is **fictional, machine-generated test data**. The names, merchants, account fragments and amounts do not describe any real person or bank account.

| File | Purpose |
|---|---|
| `sample_bank_statement.csv` | HDFC-style statement (Aug–Oct 2026) in the format a user would upload: `dd/mm/yy` dates, `Withdrawal Amt.` / `Deposit Amt.` columns, Indian digit grouping in quoted amounts |
| `sample_bank_statement.truth.csv` | What each row *should* be classified as (type + category) |
| `expected_figures.json` | Monthly aggregates computed independently with `Decimal` straight from the truth file (not by application code) |
| `generate_synthetic_data.py` | Regenerates the three files |

Deliberate test cases: monthly salary, rent, EMI, insurance, utilities (one with varying amounts), two monthly subscriptions, groceries/food/transport/health/entertainment/shopping, ATM cash, a refund of an earlier purchase, self-transfers, credit-card bill payments, a two-legged own-account transfer, an unrelated NEFT credit with the **same amount and date as the rent** (must not be treated as a transfer), an identical twin Swiggy charge (possible duplicate), a ₹6,800 party order, a ₹9,800 hospital bill, a ₹64,900 purchase and a ₹18,500 flight (unusual spending).

## payment-slips/
Fictional payment slips for the Payment Slip Scanner (also **synthetic, not real**): ten slips used for the 10-file acceptance test (`slip_01 ... slip_10`: UPI app screenshots as PNG/JPG/JPEG, a digital PDF, a scanned PDF, a card receipt, a cash receipt, a failed, a pending and a refunded payment), `edge-cases/` (missing date, missing amount, blurry/noisy, blank, not a slip) and `expected_slips.json` (ground truth). Regenerate with `python docs/test-data/payment-slips/generate_synthetic_slips.py`.
