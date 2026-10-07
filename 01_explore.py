"""01_explore.py — Phase 0: first contact with both venues' REST APIs.

Design spec (agreed 2026-10-06):
- Fetchers return RAW venue JSON, untouched. Normalizing across venues is
  Phase 2's job (the matcher); doing it now would be premature abstraction.
- Error policy: FAIL LOUD. No retries, no try/except. This is an exploration
  script — a crash with full detail is the most informative outcome.
- Kalshi pagination: one page per call, caller loops. Keeps the caller in
  control of request rate, which matters once rate limits do (Phase 1).
- Config: base URLs as constants. Nothing here is secret, so no env vars.

Run:
    python 01_explore.py                          # auto-picks one market per venue
    python 01_explore.py KALSHI_TICKER TOKEN_ID   # compare a specific pair you eyeballed
"""

import json
import sys

import httpx

GAMMA_BASE = "https://gamma-api.polymarket.com"
CLOB_BASE = "https://clob.polymarket.com"
KALSHI_BASE = "https://api.elections.kalshi.com/trade-api/v2"


def fetch_polymarket_events(limit: int = 20) -> list[dict]:
    """First `limit` open events from Polymarket's Gamma (metadata) API.

    Returns the raw list of event dicts exactly as Gamma sends it.
    """
    resp = httpx.get(f"{GAMMA_BASE}/events", params={"closed": "false", "limit": limit})
    resp.raise_for_status()
    return resp.json()  # Gamma returns a bare JSON array, not a wrapper object


def fetch_kalshi_markets(
    limit: int = 100, cursor: str | None = None, exclude_combos: bool = True
) -> tuple[list[dict], str | None]:
    """One page of open Kalshi markets, plus the cursor for the next page.

    Returns (markets, next_cursor). next_cursor is None when there are no
    more pages. Caller does the looping — see design spec.
    exclude_combos drops multivariate (KXMVE...) parlay markets server-side;
    unfiltered, they crowd out single-game markets on the first pages.
    """
    params: dict = {"status": "open", "limit": limit}
    if cursor:
        params["cursor"] = cursor
    if exclude_combos:
        params["mve_filter"] = "exclude"
    resp = httpx.get(f"{KALSHI_BASE}/markets", params=params)
    resp.raise_for_status()
    data = resp.json()  # Kalshi wraps results: {"markets": [...], "cursor": "..."}
    # Prices are dollar STRINGS under *_dollars keys (yes_bid_dollars="0.5500"),
    # not integer cents under yes_bid/yes_ask.
    return data["markets"], data.get("cursor") or None


def fetch_kalshi_orderbook(ticker: str) -> dict:
    """Current order book snapshot for one Kalshi market."""
    resp = httpx.get(f"{KALSHI_BASE}/markets/{ticker}/orderbook")
    resp.raise_for_status()
    return resp.json()


def fetch_polymarket_book(token_id: str) -> dict:
    """Current order book for one Polymarket outcome token (CLOB API)."""
    resp = httpx.get(f"{CLOB_BASE}/book", params={"token_id": token_id})
    resp.raise_for_status()
    return resp.json()


def extract_token_ids(market: dict) -> list[str]:
    """Outcome token ids for a Polymarket market.

    Venue migration gotcha: pre-April-2026 (V1) markets carry clobTokenIds,
    newer (V2) markets carry positionIds — and either can arrive as a JSON
    string that still needs parsing, not a list.
    """
    raw = market.get("clobTokenIds") or market.get("positionIds") or []
    return json.loads(raw) if isinstance(raw, str) else raw


def main() -> None:
    # Same-event pair: Rays @ Yankees, MLB Game 3, 2026-10-07 8:00pm ET. YES = Yankees win on both venues.
    #     python 01_explore.py KXMLBGAME-26OCT072000TBNYY-NYY 107183587774135915712840111388233775367532027092060605006409836840297385071732
    #     (valid through Oct 7, 2026 — market closes after the game)
    # --- Task 1: Polymarket events -----------------------------------------
    print("=== Polymarket: first 20 open events (Gamma API) ===")
    events = fetch_polymarket_events(limit=20)
    for ev in events:
        print(f"  {ev.get('title')}  [{ev.get('slug')}]")

    first = events[0]
    print(f"\n  Markets inside {first.get('title')!r}:")
    for m in first.get("markets", []):
        print(f"    {m.get('question')}  closed={m.get('closed')}  tokens={extract_token_ids(m)}")

    # --- Task 2: Kalshi markets, two pages to prove the cursor works -------
    print("\n=== Kalshi: open markets, page 1 ===")
    page1, cursor = fetch_kalshi_markets(limit=10)
    for m in page1:
        print(f"  {m['ticker']}  {m.get('title', '')}  yes_bid={m.get('yes_bid_dollars')} yes_ask={m.get('yes_ask_dollars')}")
    print(f"  next cursor -> {cursor!r}")

    if cursor:
        print("\n=== Kalshi: page 2 (same endpoint + cursor param) ===")
        page2, _ = fetch_kalshi_markets(limit=10, cursor=cursor)
        for m in page2:
            print(f"  {m['ticker']}  {m.get('title', '')}  yes_bid={m.get('yes_bid_dollars')} yes_ask={m.get('yes_ask_dollars')}")

    # --- Task 3: one order book from each venue -----------------------------
    # Matching the SAME game across venues is a manual eyeball job in Phase 0;
    # automating it is Phase 2 (the matcher). Pass a pair you spotted above:
    #     python 01_explore.py KXNBAGAME-...-LAL 1234567890
    if len(sys.argv) == 3:
        ticker, token_id = sys.argv[1], sys.argv[2]
    else:
        ticker = page1[0]["ticker"]
        # closed=false filters EVENTS, not their markets: an open event can still
        # hold closed markets, and the CLOB 404s on a closed market's book.
        token_id = extract_token_ids(next(m for m in first["markets"] if not m.get("closed")))[0]
        print("\n(no args given — using the first market from each venue; almost certainly NOT the same event)")

    print(f"\n=== Kalshi order book: {ticker} ===")
    print(json.dumps(fetch_kalshi_orderbook(ticker), indent=2)[:800])

    print(f"\n=== Polymarket order book: token {token_id} ===")
    print(json.dumps(fetch_polymarket_book(token_id), indent=2)[:800])


if __name__ == "__main__":
    main()
