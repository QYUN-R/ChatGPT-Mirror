class BillingError(Exception):
    code = "billing_error"

    def __init__(self, message, *, code=None):
        super().__init__(message)
        self.message = message
        if code:
            self.code = code


class CapacityUnavailable(BillingError):
    code = "pool_full"


class SubscriptionInactive(BillingError):
    code = "subscription_inactive"


class PaymentRejected(BillingError):
    code = "payment_rejected"
