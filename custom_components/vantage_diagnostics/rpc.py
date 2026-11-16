"""Run requests on the stock integration's config connection without ever cancelling one."""

import asyncio
from collections.abc import Awaitable, Callable
from typing import Any

from aiovantage.errors import ClientConnectionError

from .const import LOGGER, UNLOADED

# How long an unload waits for requests already sent
SHUTDOWN_TIMEOUT = 10.0


class SharedRpc:
    """Run each request in a task of its own, so cancelling the caller can't cancel it.

    The config connection belongs to the stock integration.
    A cancelled request leaves its reply unread, and the stock integration's next request would read it.
    So a request already sent always runs to its end, and only the wait for it is cancelled.
    """

    def __init__(
        self, usable: Callable[[], bool], close_connection: Callable[[], None]
    ) -> None:
        """Initialize the runner, given whether the stock client may still be used, and how to close its connection."""
        self._usable = usable
        self._close_connection = close_connection
        self._tasks: set[asyncio.Future[Any]] = set()
        self._closed = False

    @property
    def usable(self) -> bool:
        """Return whether a new request may still be sent."""
        return not self._closed and self._usable()

    async def run[T](self, request: Callable[[], Awaitable[T]]) -> T:
        """Send a request, unless the stock client has gone or this runner is shut down."""
        task: asyncio.Future[T] = asyncio.ensure_future(self._send(request))
        self._tasks.add(task)
        task.add_done_callback(self._done)

        # Shielded, so cancelling this wait leaves the request running
        return await asyncio.shield(task)

    async def _send[T](self, request: Callable[[], Awaitable[T]]) -> T:
        # Checked in the task rather than in run(), since the task's first step comes later and the stock entry may have started unloading by then.
        if not self.usable:
            raise ClientConnectionError(UNLOADED)
        try:
            return await request()
        finally:
            # A request that was reconnecting when the stock unload closed the client finishes opening the connection after that close.
            # Nothing else would close that connection, so close it once the stock client may no longer be used.
            if not self._usable():
                self._close_connection()

    def _done(self, task: asyncio.Future[Any]) -> None:
        self._tasks.discard(task)

        # Nobody may still wait for the result, so don't leave its error unretrieved
        if not task.cancelled():
            task.exception()

    def close(self) -> None:
        """Refuse new requests."""
        self._closed = True

    async def async_shutdown(self) -> None:
        """Refuse new requests, then wait for those already sent."""
        self.close()
        if not self._tasks:
            return

        _, pending = await asyncio.wait(set(self._tasks), timeout=SHUTDOWN_TIMEOUT)
        if pending:
            LOGGER.debug("%d request(s) still running after shutdown", len(pending))
