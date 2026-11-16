# AGROCAST — Short training guide (30 minutes)

Goal: after the session each participant can complete a purchase plan from registration to purchase
and understand what the model recommendation means and what it does not mean.

| Time | Activity | Role | Expected result |
|---|---|---|---|
| 0–5 min | Tour of the homepage and the System Design page | all | Understand the problem, the model and the roles |
| 5–8 min | Create an account and sign in | producer | Producer account active |
| 8–12 min | Register a farm | producer | Farm listed |
| 12–20 min | Create a fertilizer plan for a month inside the window, add 2–3 products, evaluate it | producer | Recommendation with projected cost |
| 20–23 min | Read "How to read this result"; discuss the 80% range and the limitations | producer | Participant explains the recommendation in their own words |
| 23–26 min | Record the decision (try a decision different from the model to see the justification rule) and close the purchase | producer | Plan in *purchased* status |
| 26–28 min | Send a feedback report | producer | Report visible with status *open* |
| 28–30 min | Demonstration by the analyst: register a new index month, run the monitoring check | analyst | Participants see how forecasts are compared with actual values |

## Demonstration script

1. Sign in with the producer test account. Planning → Farms → register "La Esperanza", Cundinamarca,
   Potato, 12.5 ha.
2. Planning → Purchase Plans → New plan: "Fertilization first semester", category Fertilizers,
   month = last month of the window. Add *10-20-20 × 40* and *Cloruro de Potasio × 10*.
3. Evaluate with model → read the recommendation, the index change and the projected cost.
4. Save the decision → Close as purchased with the actual prices.
5. Sign in as analyst → Analysis → Model monitoring → Run drift & performance check.
6. Sign in as admin → Admin → Operational log: find the events generated during the demonstration.

## Key messages for users

- The model forecasts a **national index**, not the price of a specific shop.
- The recommendation is support for planning; supplier quotes remain necessary.
- A wide interval means the market is uncertain: decide considering cash flow and storage.
- Report any result that looks wrong in Support & Feedback; it is used to monitor the model.
