# AGROCAST — User manual

## Accessing the system

1. Open the system URL. The homepage shows the project documentation and does not require an account.
2. **Create account** registers a producer account (name, email, optional department, password with at
   least 8 characters including letters and numbers).
3. **Sign in** with email and password. **Sign out** is in the top-right corner.
4. Analyst and administrator accounts are assigned by an administrator.

## Producer

### Farms (Planning → Farms)
Register each production unit with name, department, municipality, main crop and area in hectares.
Farms can be edited at any time. A farm that has purchase plans cannot be deleted.

### Purchase plans (Planning → Purchase Plans → New plan)
1. Enter a title, choose the farm, the input category (fertilizers, fungicides, herbicides or
   insecticides) and the purchase month. Only the six months after the latest official index month
   are accepted, because those are the months the model can forecast.
2. In the plan page, add products and quantities. Prices are taken automatically from the latest
   SIPSA-I record.
3. Press **Evaluate with model**. The result shows:
   - the recommendation: **Buy now**, **Defer purchase** or **Buy as planned**;
   - the expected change of the index between the latest official month and the purchase month;
   - the projected cost of the plan and its 80% range;
   - a chart with the forecast and its interval, and an explanation of its meaning and limitations.
4. Record the decision. If you choose something different from the recommendation, write the reason.
   - *Approve* (buy now / as planned) → the plan moves to execution.
   - *Defer* → the plan stays open; evaluate it again later.
5. After buying, enter the unit prices actually paid and press **Close as purchased**. The plan shows
   planned, projected and actual cost.

Plans can be edited while they are draft, evaluated or deferred. Changing products or the month
returns the plan to draft. Open plans can be cancelled; draft and cancelled plans can be deleted.

### Inference history (Planning → Inference History)
Every evaluation is listed with its date, input, result, model version and response time. Click an
entry to see the full forecast path and intervals.

### Support (Planning → Support & Feedback)
Report an error, a data problem, a doubtful model result, an access problem or a suggestion. You
can follow the status of your reports (open, in progress, resolved, closed) and read the resolution.

### Food prices and price calculator (Market → Price Calculator & PDF)
1. Create a list (for example "Weekly market") and, if you only buy in one city, choose it.
2. Type part of the name in the filter (for example *frijol*, *papa*, *tomate*), choose the product and
   the quantity (kilograms; eggs in units, oil in litres) and press **Add**. Repeat for every product.
3. The table shows, for each product, the cheapest wholesale market, the price per unit and its date
   (*today* = DANE daily bulletin; *month* = latest monthly series), the cost at that market, the cost at
   the median market and the 3-month outlook (Buy now = expected to rise, Wait = expected to fall).
4. The totals show the cost at the cheapest markets, at the median market and how much you save.
5. **Download PDF** produces the list ordered by saving, ready to print or share.

**Market → Food Prices Today** lists every product (375) with its lowest price; search without accents.
Open a product to see its price in every market, its history since 2018 and the 3-month forecast with
its 80% range. Prices are refreshed automatically every day.

### Agricultural input views
Six-month index forecast, what to buy today, inputs by category, where inputs are cheapest by
department, input price evolution and product alerts (fertilizers and pesticides, DANE SIPSA-I).

### My account and personal data
Click your name in the top bar. There you can update your name and department, change your password,
**download all your data** (JSON) and **delete your account** (type DELETE and your password). The
privacy policy, terms of use and cookie policy are linked in the footer of every page.

## Analyst

- **Analysis → Index records**: register each new UPRA month for the three series (total,
  fertilizers, pesticides), correct values, delete only the latest month.
- **Analysis → Forecast lab**: run the model on the stored records or on a scenario (up to three
  manually entered months) for any series and horizon. Every run is stored.
- **Analysis → Model monitoring**: response times, errors, forecasts compared with the actual value,
  and the drift and performance check (button *Run drift & performance check*).
- **Analysis → Indicators**: plans by status, recommendations issued, percentage of decisions that
  follow the model, planned vs actual cost; CSV export of the inference history.
- **Purchase Plans** shows all plans in read-only mode; **Support & Feedback** allows assigning and
  resolving reports.

## Administrator

- **Admin → Users & roles**: change the role of a user or disable an account (not your own).
- **Admin → Model versions**: review the registered versions and activate one.
- **Admin → Operational log**: authentication, business, inference and error events.
- The administrator also has all producer and analyst functions.

## Messages you may see

| Message | What to do |
|---|---|
| "The planned month must be between …" | choose a month inside the model window |
| "Add at least one product before evaluating the plan." | add products first |
| "Explain … why the decision differs from the model." | write a justification of at least 10 characters |
| "The active model version changed. Evaluate the plan again." | press Re-evaluate |
| "Access denied" | the function is not available for your role |
