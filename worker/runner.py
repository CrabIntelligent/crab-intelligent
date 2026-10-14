"""The worker loop: heartbeat in the background, claim a task, run it, report back."""
from __future__ import annotations

import threading
import time
from typing import Callable, Dict, List

import httpx

from .client import Client, DispatcherError
from .providers import Provider, ProviderError

HEARTBEAT_SECONDS = 15


def run(client: Client, name: str, providers: List[Provider], kinds: List[str], poll: float = 2.0,
        once: bool = False, log: Callable[[str], None] = print) -> int:
    """Process tasks until interrupted. With once=True, stop when no task is available.
    Returns the number of tasks handled."""
    by_family: Dict[str, Provider] = {p.family: p for p in providers}
    families = list(by_family)
    models = {p.family: p.model for p in providers}
    while True:
        try:
            client.login()
            break
        except (DispatcherError, httpx.HTTPError) as exc:
            if once:
                raise
            log(f"cannot reach the dispatcher yet ({exc}); retrying in 5s")
            time.sleep(5)
    log(f"burrow {client.keypair.pubkey} online as '{name}' ({', '.join(f'{f}:{models[f]}' for f in families)})")

    stop = threading.Event()

    def beat() -> None:
        while not stop.is_set():
            try:
                client.heartbeat(name, families, models)
            except Exception as exc:  # keep the worker alive through a dispatcher restart
                log(f"heartbeat failed: {exc}")
            stop.wait(HEARTBEAT_SECONDS)

    if once:
        client.heartbeat(name, families, models)
    else:
        threading.Thread(target=beat, daemon=True).start()

    handled = 0
    try:
        while True:
            try:
                task = client.claim(families, kinds)
            except (DispatcherError, httpx.HTTPError) as exc:
                log(f"claim failed: {exc}")
                if once:
                    return handled
                time.sleep(poll * 3)
                continue
            if not task:
                if once:
                    return handled
                time.sleep(poll)
                continue
            provider = by_family[task["use_family"]]
            label = f"{task['job']['type']}/{task['step']['key']} attempt {task['attempt']}"
            try:
                if task["kind"] == "work":
                    log(f"work    {label}  [{provider.family}]")
                    text, served_by = provider.work(task)
                    result = client.submit(task["task_id"], text, provider.family, served_by)
                else:
                    log(f"review  {label}  [{provider.family}, author {task['author_family']}]")
                    review = provider.review(task)
                    result = client.review(task["task_id"], review.verdict, review.summary, review.findings,
                                           provider.family, review.model)
                    result.setdefault("verdict", review.verdict)
                log(f"        -> {result.get('status')}")
            except ProviderError as exc:
                log(f"        ! {exc} (giving the task back)")
                client.release(task["task_id"], str(exc))
            except (DispatcherError, httpx.HTTPError) as exc:
                log(f"        ! could not report the result: {exc}")
            handled += 1
    except KeyboardInterrupt:
        log("stopping")
        return handled
    finally:
        stop.set()
