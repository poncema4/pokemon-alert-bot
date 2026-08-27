# Accuracy data

`ground_truth.json` is the clean human-verified dataset used to measure the bot.

For every Big 4 alert you actually check, record:

- retailer
- product URL
- alert type (`stock`, `new`, or `unknown`)
- whether the product was actually purchasable when you clicked (`actual_in_stock`)
- the time you checked it
- optional notes, such as "out of stock on first load but appeared after refresh"

You can also send those facts through the ChatGPT conversation. The assistant can convert them into a clean observation for this file when you ask it to record them.

Do not label an event based only on what the bot predicted. The point of this dataset is independent ground truth.
