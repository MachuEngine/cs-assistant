# Northwind Retail — Order Cancellation Fee Policy

## CANC-01: No Fee — Order Not Yet Processing
Orders with status "pending" may be canceled at any time with no fee.

## CANC-02: Reduced Fee — Order Processing
Orders with status "processing" (payment captured, warehouse picking has not
yet started) incur a cancellation fee of 5% of the order total, except for VIP
tier customers, for whom this fee is waived.

## CANC-03: Cannot Cancel — Order Shipped
Orders with status "shipped" or "delivered" cannot be canceled. The customer
should be directed to the return process (see RET-01 through RET-06) instead.

## CANC-04: Order Modifications
Requests to modify an order (e.g., change quantity, size, or item) follow the
same eligibility window as cancellations in CANC-01 and CANC-02.
Modifications to an order that has already shipped are not possible; the
customer should be advised to complete the return/exchange process instead.
