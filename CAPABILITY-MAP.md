# Capability Map: Jastip Dashboard

| Module id | Responsibility | Depends on |
|---|---|---|
| `admin-access` | Admin login and protected access | — |
| `order-management` | Parse, review, save, and update customer items and statuses | `admin-access` |
| `price-estimator` | Admin-managed JPY rate and public IDR estimates | `admin-access` |
| `customer-lookup` | Public search by LINE display name or item ID | `order-management` |

Build order: `admin-access` → `order-management` → `customer-lookup`. `price-estimator` can follow `admin-access` independently.

Payment verification is postponed.
