# Data

- `state.json`: everything the bot has seen, one entry per listing (`retailer::canonical URL`): last reading, signal, price, retail price, when it was last readable, and alert bookkeeping. Written by the watcher; do not edit by hand.
- `ground_truth.json`: human-checked outcomes used to measure the bot (`bot/accuracy.py`).

For every alert you actually check, record: retailer, product URL, alert type (`stock` or `new`), whether the product was really purchasable when you clicked (`actual_in_stock`), when you checked, and an optional note such as "out of stock on first load but appeared after refresh".

Never label an event from the bot's own prediction: the point of this file is independent ground truth.
