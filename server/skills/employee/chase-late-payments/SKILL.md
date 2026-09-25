---
name: chase-late-payments
description: Use when an invoice is past its due date. Sends polite, firm reminders that get firmer over time, and hands anything disputed to the owner.
metadata:
  author: opencompany
  version: "1.0"
  category: money
  icon: "lucide:Receipt"
  color: "#ffb86c"
  title: Chase late payments
  summary: Polite, firm reminders for overdue invoices, then hands off to you.
---

# Chase late payments

## The reminders

1. **A few days late**: a friendly nudge. Name the invoice, the amount and the due date, and include how to pay.
2. **About two weeks late**: firmer. Say the payment is now overdue, and ask when they expect to pay.
3. **A month late**: stop. Don't send a third reminder; tell the owner instead.

## Rules

- Always include the invoice number, the amount with its currency, and the original due date.
- Stay polite even when you are firm. Never threaten, and never mention late fees the owner has not set.
- If the customer says they already paid, or disputes the amount, don't argue. Thank them, say you will check, and report it to the owner straight away.
- Never change an invoice, cancel one, or promise a discount yourself.
- In your report, list what you sent to whom, and who has not answered.
