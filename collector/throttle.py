"""Small page navigation throttle for collection."""

import random
import time
from threading import Event


class PageThrottle:
    def __init__(self, delay_min: float = 2.0, delay_max: float = 5.0) -> None:
        self.delay_min = delay_min
        self.delay_max = delay_max

    def wait(self, stop_event: Event | None = None) -> bool:
        delay = random.uniform(self.delay_min, self.delay_max)
        if stop_event is not None:
            return stop_event.wait(delay)
        time.sleep(delay)
        return False
