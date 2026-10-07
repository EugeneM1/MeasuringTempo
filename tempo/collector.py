"""The collector supervisor: keep every stream recording, forever.

This is the piece that turns Phase 0's fail-loud scripts into an always-on
service. Each venue module exposes run_once() — one session, no retry logic —
and this module decides what happens when a session ends.

    python -m tempo.collector --once    run each stream for one session
                                        (today's behavior: ends on first drop)
    python -m tempo.collector           run forever via supervise() — EUGENE'S
                                        COMPONENT, stubbed below

supervise() is deliberately left unimplemented: the reconnect loop is one of
the project's hand-written components (see the learning contract in the
master plan). The spec in its docstring is complete; everything around it
already works, and --once proves it.
"""

import argparse
import asyncio
import logging
from collections.abc import Awaitable, Callable

from . import config as config_mod
from . import kalshi, polymarket
from .config import Config
from .recorder import Recorder

log = logging.getLogger("tempo.collector")

# A stream is a name plus an async callable of (Config, Recorder) that runs
# one session and returns on clean close / raises on failure.
Stream = Callable[[Config, Recorder], Awaitable[None]]

STREAMS: dict[str, Stream] = {
    "kalshi_ws": kalshi.run_once,
    "polymarket_ws": polymarket.run_once,
}


async def supervise(name: str, stream: Stream, config: Config, recorder: Recorder) -> None:
    """Run one stream forever: reconnect on every exit, with backoff. STUB.

    Spec (implement me):
    - Loop forever. Each iteration awaits stream(config, recorder).
    - If it returns cleanly (server closed politely): log it, reconnect after
      a SHORT fixed delay (~1s) — clean closes are routine (deploys,
      rebalances), not failures.
    - If it raises: log the exception WITH traceback, then reconnect with
      EXPONENTIAL BACKOFF — delay starts at 1s and doubles per consecutive
      failure, capped at 60s.
    - Add JITTER: multiply each delay by random.uniform(0.5, 1.5). Why: if
      the venue restarts, every client reconnects at once; jitter spreads the
      stampede (and spreads OUR two streams apart).
    - RESET the backoff to 1s after a session survives longer than 60s —
      a long-lived session proves the connection was healthy, so the next
      failure is a fresh incident, not a continuation.
    - Never let an exception escape this function: supervise() crashing is
      the collector dying, which is the one unacceptable outcome. (asyncio
      CancelledError is the exception to the rule: let it propagate so
      shutdown works.)

    Interview question this answers: "what happens when your feed drops at
    2am?" — and after implementing it you'll have the real answer.
    """
    raise NotImplementedError("supervise() is Eugene's component — spec above")


async def run(once: bool) -> None:
    config = config_mod.load()
    streams = {name: (fn, Recorder(name, config.raw_dir)) for name, fn in STREAMS.items()}
    log.info(
        "collector starting: %d kalshi tickers, %d polymarket tokens, once=%s",
        len(config.kalshi_tickers), len(config.polymarket_tokens), once,
    )
    try:
        async with asyncio.TaskGroup() as tg:
            for name, (fn, recorder) in streams.items():
                if once:
                    tg.create_task(fn(config, recorder), name=name)
                else:
                    tg.create_task(supervise(name, fn, config, recorder), name=name)
    finally:
        for _, recorder in streams.values():
            recorder.close()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--once", action="store_true", help="one session per stream, no reconnect")
    args = parser.parse_args()
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    asyncio.run(run(once=args.once))


if __name__ == "__main__":
    main()
