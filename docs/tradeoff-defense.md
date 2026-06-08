# Tradeoff defense — owning the decisions

A reviewer's strongest interview question for this project is *"do you own these
decisions, or did you orchestrate an agent and accept its output?"* This doc is
the honest answer: for each non-obvious choice, **why it's that way, the code
that implements it, and what I'd concede under push-back.** Every claim points at
the file that backs it so the answer is checkable, not rhetorical.

---

## 1. Why Complement NB over plain Multinomial NB?

**Short answer.** The data regime — 6 intents, short telecom utterances, uneven
per-class support — is exactly the skewed/short-text case Multinomial NB handles
badly and Complement NB (Rennie et al., 2003) was designed to fix.

**The mechanism.** Multinomial NB estimates `P(word | class)` from *only that
class's own* documents. On skewed data that means: (a) high-variance estimates
for small classes (few documents → noisy parameters), and (b) a systematic tilt
toward frequent classes, because magnitude of the per-class weight vector tracks
how much text that class had. CNB instead estimates each class's weights from the
**complement** — every *other* class's tokens — and then **L1-normalizes** the
per-class weight vector. That buys two things:

- **Lower variance:** the complement is a much larger sample than any single
  class, so parameters are estimated from more data.
- **No length/frequency bias:** L1 normalization makes the per-class weight
  vectors comparable in magnitude, so a class doesn't win just because it had
  more training text.

This is visible in the code: weights come from the complement count
`global_tokens[w] - class_tokens[c][w]`, and are normalized by
`norm = sum(abs(x))`, in [trained_classifier.py:71-80](../src/router/trained_classifier.py#L71-L80).
Prediction picks the **lowest** complement score — best fit against "everything
that is *not* this class."

**What I'd concede.** On *this* synthetic set both estimators would likely score
high, so CNB isn't a dramatic accuracy win here — it's the principled default for
the data shape, and the honest framing is "I chose the estimator that matches the
regime and can explain why," not "CNB unlocked the numbers." In production this
slot is a fine-tuned small LM anyway ([trained_classifier.py:11-16](../src/router/trained_classifier.py#L11-L16));
CNB is the zero-dependency offline baseline.

---

## 2. Why calibrate confidence instead of just lowering the escalation threshold?

**Short answer.** CNB's raw softmax confidence is **nearly flat and doesn't
rank-order by correctness** — so a single threshold on it is uninformative.
Lowering the threshold is one blunt global knob on a meaningless score; it can't
tell a route the model gets right 95% of the time from one it gets right 55% of
the time. Calibration makes the score *mean something* first, then the threshold
stays principled.

**The mechanism.** Raw CNB scores cluster so tightly that *every* prediction sits
below the router's escalation threshold ([calibration.py:1-8](../src/router/calibration.py#L1-L8)) —
so "lower the threshold until good predictions pass" also lets the bad ones
through, because good and bad predictions have near-identical raw confidence. The
calibrator instead maps **predicted class → observed validation precision** on a
held-out fold ([calibration.py:137-158](../src/router/calibration.py#L137-L158)). After
that, `confidence == empirical precision`, so the router's one threshold becomes
interpretable: *"escalate routes we're less than X% precise on"* is now literally
true.

**Why this design specifically.**
- **Per-class, not global:** different intents have different reliability;
  collapsing them to one threshold on a flat score throws that away.
- **Learned on a separate fold:** calibration uses a 3-way train/calibration/test
  split ([calibration.py:226-238](../src/router/calibration.py#L226-L238)) so the
  precision estimate isn't read off the data the model trained on.
- **Inspectable and conservative:** it's an empirical precision map with no hidden
  optimizer and no change to the predicted *intent* — only the confidence
  ([calibration.py:125-131](../src/router/calibration.py#L125-L131)). `diagnostics()`
  lets you read the per-class numbers.
- **Policy stays out of the weights:** deterministic high-risk cue overrides
  (billing/customer-data/prompt-injection) run *in front of* the calibrated model
  ([calibration.py:194-223](../src/router/calibration.py#L194-L223)), so safety
  routing isn't something the learner can "unlearn."

**What I'd concede.** This is a simple empirical-precision calibrator, not
Platt/isotonic — chosen for zero deps and inspectability. With more data I'd move
to isotonic regression and reliability diagrams. And the cue overrides are a
deliberate belt-and-suspenders: they're why the *raw* NB "looks safe only because
it escalates everything," and why I report **route safety**, not just accuracy.

---

## 3. What does the per-customer scope check protect against — and what does it not?

The check is one line in the tool body:
`if device.owner_id != ctx.customer_id: return scope_violation`
([tools.py:52-55](../src/mcp/tools.py#L52-L55)).

**What it protects against.**
- **Cross-customer access (confused deputy).** Even if the model is talked into
  resetting *another* customer's device — by a prompt-injected user, a jailbreak,
  or just a model mistake — ownership is verified **server-side against the gate's
  identity**, not against anything the model or request supplies. The model cannot
  widen scope by asking nicely. This is the load-bearing property: *the defense
  does not depend on the model behaving.*
- **Defense in depth.** A deterministic capability check runs first
  (`ctx.may(Action.DEVICE_RESET)`, [tools.py:47](../src/mcp/tools.py#L47)) before
  any per-resource check, and the access gate establishes `ctx.customer_id` from
  the token *before any model runs* ([gate.py:24-29](../src/access/gate.py#L24-L29)).
  Lookups are scoped to the caller's own id by construction
  ([tools.py:25-30](../src/mcp/tools.py#L25-L30)).

**What it explicitly does *not* protect against.**
- **A wrong identity upstream.** The check trusts `ctx.customer_id`, which comes
  from `resolve_token`. Token theft, session fixation, or account takeover produce
  a *legitimate-looking* context for the wrong person — and the scope check would
  happily authorize it. Authentication is assumed, not solved here (tokens are
  synthetic, in-memory).
- **Bugs in the data layer.** It's only as correct as `get_device` / `owner_id`.
  If the store returns wrong ownership, the check rubber-stamps it. There's no
  independent audit of the ownership claim.
- **Finer-grained / same-customer policy.** It enforces *cross-customer*
  ownership only — not "this customer may not reset *this* device for a billing
  reason." That kind of policy isn't modeled.
- **Operational threats.** No rate limiting, no abuse throttling, no protection
  against a malicious server-side operator, no real IAM. Money-touching actions
  are routed to a human rather than guarded at the tool level.

**One-sentence version for an interview.** "It stops the model from reaching
across customers regardless of what it's told to do, because ownership is checked
server-side against the authenticated identity — but it assumes that identity is
correct, so it's an *authorization* control, not an *authentication* one."

---

## 4. The guardrail's recall/precision tradeoff (and why it's not just regex)

The approved-offers catalog permits only **`free` / `$0`**
([catalog.py:15](../src/guardrails/catalog.py#L15)). That single fact sets the
design: since *every* nonzero money claim must block, the offer check optimizes
for **recall**, not a clever single regex. It blocks money across symbols
(`$/€/£`), currency words ("20 dollars", "5 bucks"), spelled-out amounts ("nine
ninety-nine a month"), discount phrasings ("half off", "20 percent off"), and
bare numbers next to a money cue ("a fee of 15") — while clean operational
numbers ("reset takes 5 minutes") pass
([guardrail.py:_money_violations](../src/guardrails/guardrail.py)). Coverage is
locked by regression tests in [test_step2.py](../tests/test_step2.py).

**Why this is defensible rather than brittle.** Because the policy is
"nonzero ⇒ block," I can afford to err toward over-blocking: a false block escalates
to a human (safe), while a false pass ships an invented price (unsafe). The
asymmetry of the cost is what justifies tuning for recall.

**Honest limit, and the seam for it.** Lexical patterns are still
pattern-matching and *can* be out-phrased ("we'll comp your next cycle"). So
`check()` takes an optional `semantic_backstop` callable — e.g. an LLM judge —
consulted only when the cheap checks pass
([guardrail.py:check](../src/guardrails/guardrail.py)). That makes the layer
**cheap-first, layered, off by default** (deterministic and offline in tests),
with a real place to add a model-based net rather than claiming the regex is
complete.

---

## 5. The meta-question: timeline and ownership

The project was built fast and with heavy AI assistance — that's true and not
hidden. The way I'd answer "do you own it" is: **ask me to defend any specific
tradeoff above and I'll give you the mechanism, the file, and the concession.**
The docs separate *built* from *designed*, flag the synthetic-data ceiling on the
0.999 number wherever it appears, and narrate a real gap the LLM judge caught and
the fix that followed. Owning the decisions means being able to say where each one
is *wrong* or *limited* — which is what sections 1–4 each end on.
