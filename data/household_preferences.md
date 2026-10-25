# Household food preferences

Updated: 2026-10-24.

This file stores long-term household food preferences. Unspecified details remain unknown; they are not default shopping choices.

## Usual produce purchases

Source: the household setup request explicitly listed onions, tomatoes, garlic, and avocados as usual purchases. This is a manually supplied preference, not a conclusion from camera or order history.

- [onions] Usually purchased. Variety, form, package size, and target stock are not specified.
- [tomatoes] Usually purchased. Variety, form, package size, and target stock are not specified.
- [garlic] Usually purchased. Preferred form, package size, and target stock are not specified.
- [avocados] Usually purchased. Variety, size, ripeness, and target stock are not specified.

### Product selection details

| Food | Variety or form | Quantity / package size | Additional preference to learn |
| --- | --- | --- | --- |
| Onions | Not specified; do not assume yellow, red, white, or pre-cut | Not specified | Which variety is used most often and how quickly it is used |
| Tomatoes | Not specified; do not assume cherry, Roma, vine, fresh, or canned | Not specified | Usual variety and intended use, such as cooking or salads |
| Garlic | Not specified; do not assume whole bulbs, cloves, minced, or powder | Not specified | Preferred preparation form and consumption rate |
| Avocados | Not specified | Not specified | Preferred size, ripeness on delivery, and expected day of use |

Usual purchase means a recurring household preference. It does not mean the item must be bought on every order, is currently missing, or has already been detected in the fridge.

## Existing staple preferences

- [eggs] Not specified.
- [milk] Not specified.
- [yogurt] Not specified.

## Other food preferences to learn

| Area | Current preference |
| --- | --- |
| Other vegetables, leafy greens, mushrooms, and herbs | Not specified |
| Other fruit | Not specified |
| Proteins, seafood, and meat alternatives | Not specified |
| Bread, rice, pasta, and other grains | Not specified |
| Cooking oils, sauces, and seasonings | Not specified |
| Snacks, drinks, and frozen food | Not specified |

## Cooking and meal preferences

- Favorite cuisines: not specified.
- Regularly cooked dishes: not specified.
- Taste preferences, such as spicy, sweet, salty, or mild: not specified.
- Cooking frequency and household size: not specified.
- Dietary requirements and ingredients to avoid: not specified. Missing information does not mean there are no restrictions.

## Shopping and quality preferences

- Intended grocery retailer: Whole Foods, as specified for the product workflow.
- Preferred brands: not specified.
- Organic, conventional, local, or seasonal preferences: not specified.
- Fresh, frozen, canned, or prepared alternatives: not specified.
- Substitution preferences: not specified; do not record an unconfirmed alternative as a lasting preference.
- Replenishment quantities and frequency: learn from reliable inventory changes and order history; no fixed quantities have been supplied for the produce above.
- Budget: unlimited, as explicitly requested. Calculate totals for speech and records; do not check a spending limit.
- Purchase authorization: use the application settings; this document does not grant or change purchasing authorization.

## How the assistant should use this profile

- Read this file before choosing products or preparing a restock plan.
- Give explicit household preferences priority over inferred habits.
- Keep usual-purchase preferences separate from current inventory and incoming deliveries.
- Photo-test policy: treat supported foods not seen in the photo as empty for restock planning. This is a configured assumption, not proof of absence. Visible but ambiguous or obscured items remain uncertain. Use --unseen-policy unknown for conservative handling.
- Do not infer favorite dishes from a single ingredient purchase.
- Save a clear long-term instruction, such as “From now on, buy red onions,” under the relevant food. A request such as “Add two tomatoes today” changes only that order.
- When real history becomes available, update the inferred summary below with the evidence window and estimated consumption. Do not overwrite confirmed preferences with guesses.

## Current prototype support

This profile can describe a broader grocery list than the current demo can execute. The shared catalog now covers 31 food categories across dairy, produce, proteins, bread, drinks, and condiments. These categories are available to visual recognition, voice additions, preference memory, and inventory-based planning. Recognition results remain estimates. By default, supported foods not seen in a photo are assumed empty; visibly obscured or ambiguous foods remain uncertain. Auto-restocking requires reliable inventory and consumption history. The fully stocked scene and its history are synthetic examples, not this household’s actual stock or confirmed food preferences. All catalog prices are synthetic demo data. See data/full_fridge_inventory.md for the full example.

## Inferred consumption history

<!-- inferred:start -->
No real consumption history yet.
<!-- inferred:end -->
