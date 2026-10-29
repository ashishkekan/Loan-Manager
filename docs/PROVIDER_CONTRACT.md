# Deferred payment provider integration

The user chose to configure their existing provider later. This release includes the gateway/mandate workflow and a mock-tested adapter boundary, not an implementation for an unspecified real provider.

Set `PAYMENT_ADAPTER` to a dotted class name implementing these methods:

```python
create_order(amount_minor, currency, reference, idempotency_key)
# -> {"id": "provider-order-id", "checkout_url": "https://allowed-host/..."}

verify_webhook(body, headers)
# Verify the provider signature over raw bytes FIRST, then return:
# {"id": "unique-event", "order_id": "provider-order-id", "currency": "INR",
#  "amount_minor": 12345, "status": "captured" or "failed", "payment_id": "..."}
# Or for mandates:
# {"id": "unique-event", "kind": "mandate", "mandate_id": "provider-mandate-id",
#  "status": "active" or "revoked" or "expired"}

create_mandate(reference, maximum_minor, currency, expires_on)
# -> {"id": "provider-mandate-id", "consent_url": "https://allowed-host/..."}

revoke_mandate(reference)
# Raise on provider failure. Must be safe to retry.

create_recurring_payment(mandate, amount_minor, currency, idempotency_key)
# -> {"id": "provider-order-id"}; posting waits for a captured webhook.
```

Requirements for the provider adapter:

- Read credentials from deployment secrets/environment, never source or templates.
- Validate webhook signature/timestamp/account identity using the selected provider's official SDK/protocol. Reject malformed/untrusted payloads by raising `ValueError`.
- Normalize only confirmed captured payments. An authorised/created payment is not a captured payment. Refunds/chargebacks require an explicit reviewed reconciliation/reversal process.
- Use integer minor units; enforce exact INR currency/amount. Do not trust browser return URLs as proof of payment.
- Enforce provider idempotency for order references, mandate references and recurring idempotency keys, including after a timeout or server rollback.
- Set bounded network timeouts. Never automatically retry a potentially successful non-idempotent debit.
- Return checkout/consent URLs only on hosts listed in `PAYMENT_CHECKOUT_HOSTS`.

Configure an active service account with the `verify_receipt` permission and memberships for the applicable company/branches. Its user ID becomes `PAYMENT_SERVICE_USER_ID`; it must be distinct from the person submitting the receipt. The webhook endpoint is `/operations/gateway/webhook/`.

Recurring collection requires all three: an explicitly consented, provider-confirmed active mandate; amount within the mandate maximum and unexpired date; and `PAYMENT_RECURRING_ENABLED=True`. The worker queues one due instalment at a time and does not post it paid. Do not enable it until provider-specific sandbox tests pass.

No real bank charge, mandate activation or lender payout was performed during implementation. Payout screens currently record externally completed bank transfers; automatic payout-provider initiation is not included.
