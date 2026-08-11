# Chris's brain dump — the original pitch

*Lightly cleaned-up transcript of the voice-note-style pitch that started this
project (2026-08-11). The raw idea, preserved; see the README for the
structured version and `docs/viability.md` for the stress-test.*

---

The pitch to Griffin was two-part:

**1. Arbitrage.** There are potentially arbitrage opportunities on prediction
markets like Kalshi that an agent getting called fairly frequently could find
and check for exploitability.

**2. Hits-based betting on real mispricings.** There are opportunities for
concentrated bets on outcomes where there's actual mispricing in markets — where
having a genuinely better forecast is worth money.

## The seed example: Michigan

The example I used with Griffin was the recent Michigan Democratic Senate
primary. Abdul El-Sayed was priced at ~98.5% chance of winning the primary on
Kalshi. I looked at that and thought it was too high — it seemed like it was
going to be a close race with Haley Stevens, and she had a ton of outside
spending in her favor.

What happened: on election night the race was much closer than public polling
had predicted. El-Sayed's price crashed from ~98.5 to ~77 — that's roughly a
20x return if you had bought and sold at the right moments — and then it went
back up and resolved at 100, because he did end up winning. It was just super
close. And there were also markets on things like victory margin, where there
was real alpha on the table for anyone with better forecasts than the market.

The part that matters: I *instinctively* thought 98.5% was priced wrong before
election night. The question is whether an agent can systematize that instinct.

## The hypothesis

Prediction markets seem to **overcorrect in response to particular events**.
Early on election night it was still true that El-Sayed was going to beat
Stevens — but his price fell from 98.5 to ~77 anyway, then recovered, then
resolved YES. So when there's a *time-bound* event where a market is going to
move in big swings and there's money on the table, a bot that can (a) spot
those setups, (b) forecast correctly, and (c) time entries and exits, seems
plausibly buildable.

## What we'd build

An agent with real forecasting ability as its backbone — built on the existing
NVF forecasting tooling in `/forecasting` — that either:

- does arbitrage trading across/within prediction markets, and/or
- makes high-reward "hits-based" plays on detected mispricings, especially
  around scheduled high-volatility events (election nights, court rulings,
  data releases) where overcorrection is likely.
