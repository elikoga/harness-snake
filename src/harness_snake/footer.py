"""Reusable animated braille-snake footer for CLI agents.

``SnakeGame`` knows how to render a snake and advance its animation; this
module adds the orchestration that turns it into a drop-in CLI-agent
component.  :class:`SnakeFooter` owns a :class:`SnakeGame` and runs the
animation on a background thread at ``SNAKE_TICK``, pushing each new frame to
a host-supplied render callback.  A CLI agent / TUI only supplies a callback
that draws a frame wherever its footer lives -- stepping, timing, lifecycle
and stats are all handled here.
"""

from __future__ import annotations

from threading import Event, Thread
from typing import Callable

from . import SNAKE_STEPS, SNAKE_TICK, GameConfig, SnakeGame


class SnakeFooter:
    """A self-playing snake animated as a CLI footer widget.

    The widget owns a :class:`SnakeGame` (created from ``width``/``rows`` and
    an optional :class:`GameConfig`) plus a daemon animation thread.  Each
    animation cycle advances the game ``SNAKE_STEPS`` ticks then calls
    ``render(frame)`` with the newest braille frame (newline-separated rows).

    Usage for a CLI agent::

        footer = SnakeFooter(width, rows, config=cfg, render=my_draw_fn)
        my_draw_fn(footer.frame())   # establish the footer first
        footer.start()               # begin animating on a thread
        ...
        footer.stop()                # stop and join the thread

    Threading: ``render`` runs on the animation thread (after the one,
    synchronous initial frame the host draws itself), so hosts that draw to a
    shared terminal should make the callback thread-safe (e.g. lock around the
    draw).  :meth:`stop` signals the thread and joins by default; pass
    ``join=False`` to just signal (the daemon thread exits on its own) -- which
    avoids deadlock when stopping while already holding the host's lock.
    """

    #: Type of the host render callback: ``render(frame: str) -> None``.
    Render = Callable[[str], object]

    def __init__(
        self,
        width: int = 6,
        rows: int = 2,
        config: GameConfig | None = None,
        render: "SnakeFooter.Render" = lambda frame: None,
    ) -> None:
        self.game = SnakeGame(width, rows=rows, config=config)
        self._render = render
        self._done: Event | None = None
        self._thread: Thread | None = None

    # ---- lifecycle ----------------------------------------------------
    def start(self) -> "SnakeFooter":
        """Begin animating on a background thread.  Idempotent: starting an
        already-running widget is a no-op.  The host should draw
        :meth:`frame` (the initial frame) *before* calling this."""
        if self._thread is not None:
            return self
        self._done = Event()
        self._thread = Thread(target=self._run, name="snake-footer", daemon=True)
        self._thread.start()
        return self

    def _run(self) -> None:
        done = self._done
        game, render = self.game, self._render
        while done is not None and not done.is_set():
            for _ in range(SNAKE_STEPS):
                game.advance()  # step, dead-hold flash, and reset
            render(game.frame())
            done.wait(SNAKE_TICK)  # ~20 FPS

    def stop(self, join: bool = True) -> None:
        """Stop the animation thread, joining it unless ``join=False``."""
        thread = self._thread
        self._thread = None
        if self._done is not None:
            self._done.set()
        self._done = None
        if join and thread is not None:
            thread.join()

    @property
    def running(self) -> bool:
        """True while the animation thread is live."""
        return self._thread is not None

    # ---- accessors ----------------------------------------------------
    def frame(self) -> str:
        """Current braille frame (newline-separated footer rows)."""
        return self.game.frame()

    def rows(self) -> list[str]:
        """Current braille footer rows."""
        return self.game.render()

    def footer_stats(self) -> str:
        """Live snake game/algorithm stats line."""
        return self.game.footer_stats()

    def __repr__(self) -> str:
        return (
            f"SnakeFooter({self.game.cols}x{self.game.lines} "
            f"running={self.running})"
        )
