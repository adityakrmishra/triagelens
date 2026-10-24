# ShopFlow Demo Target Repository

> **Internal use only — TriageLens demo fixture**

This repository is a synthetic but realistic Python codebase used as the
**analysis target** for the [TriageLens](https://github.com/yourorg/triagelens)
hackathon project.

It contains four intentionally planted **silent business-logic bugs** — no
stack traces, no exceptions — that TriageLens must discover solely from
plain-language customer support tickets.

## Module Map

| Module | Owner | Bug |
|---|---|---|
| `shopflow/pricing.py` | @aarav-payments | VIP25 silently skipped on carts with sale items |
| `shopflow/checkout.py` | @priya-checkout | Off-by-one on free-shipping threshold (> vs >=) |
| `shopflow/auth_session.py` | @rohan-auth | Magic-link TTL in seconds not minutes (missing ×60) |
| `shopflow/subscriptions.py` | @sneha-billing | Proration charges days *used* instead of days *remaining* |

## Running

```bash
pip install -r requirements.txt
pytest tests/ -v
```

## Git Blame

Each module was committed by its respective owner so that `git blame` maps
every function back to the responsible engineer:

```bash
git blame shopflow/pricing.py
git blame shopflow/checkout.py
git blame shopflow/auth_session.py
git blame shopflow/subscriptions.py
```
