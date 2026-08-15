import asyncio
import contextlib
import signal


def install_sigterm_handler() -> asyncio.Event:
    """Registers a SIGTERM/SIGINT handler on the running event loop that sets the returned
    event. Worker run_forever loops check this between iterations: stop pulling new work,
    finish whatever's already in flight, then exit -- so a deploy swap never truncates a
    delivery mid-flight, and Ctrl-C during local dev behaves the same way.
    """
    shutdown = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, shutdown.set)
    return shutdown


async def wait_or_shutdown(shutdown: asyncio.Event, seconds: float) -> None:
    """Sleeps up to `seconds`, or returns as soon as `shutdown` fires -- used between idle
    poll ticks so a worker doesn't sit in a plain asyncio.sleep() ignoring a shutdown signal
    for up to a full poll interval.
    """
    with contextlib.suppress(TimeoutError):
        async with asyncio.timeout(seconds):
            await shutdown.wait()
