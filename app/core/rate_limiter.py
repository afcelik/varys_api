"""In-memory rate limiter for brute-force protection."""

import time
from collections import defaultdict
from dataclasses import dataclass, field
from threading import Lock


@dataclass
class RateLimitEntry:
    """Tracks failed login attempts for a given IP."""

    attempts: list[float] = field(default_factory=list)
    blocked_until: float = 0


class InMemoryRateLimiter:
    """Simple in-memory rate limiter for login attempts.

    Tracks failed attempts per IP and blocks after exceeding the limit.
    Uses a sliding window approach.

    Limitations:
    - Data is lost on server restart
    - Not suitable for multi-instance deployments (no shared state)
    - Memory grows unbounded without cleanup (though cleanup runs on each check)
    """

    def __init__(
        self,
        max_attempts: int = 5,
        window_seconds: int = 900,  # 15 minutes
        block_duration_seconds: int = 900,  # 15 minutes
    ):
        """Initialize rate limiter.

        Args:
            max_attempts: Maximum failed attempts allowed in the window.
            window_seconds: Time window in seconds (default 15 minutes).
            block_duration_seconds: How long to block after exceeding limit (default 15 minutes).
        """
        self.max_attempts = max_attempts
        self.window_seconds = window_seconds
        self.block_duration_seconds = block_duration_seconds
        self._data: dict[str, RateLimitEntry] = defaultdict(RateLimitEntry)
        self._lock = Lock()

    def _cleanup_old_attempts(self, entry: RateLimitEntry, now: float) -> None:
        """Remove attempts older than the window."""
        cutoff = now - self.window_seconds
        entry.attempts = [t for t in entry.attempts if t > cutoff]

    def record_failed_attempt(self, ip: str) -> None:
        """Record a failed login attempt for an IP.

        Args:
            ip: Client IP address.
        """
        now = time.time()
        with self._lock:
            entry = self._data[ip]
            self._cleanup_old_attempts(entry, now)
            entry.attempts.append(now)

    def is_blocked(self, ip: str) -> tuple[bool, int]:
        """Check if an IP is currently blocked.

        Args:
            ip: Client IP address.

        Returns:
            Tuple of (is_blocked, seconds_remaining).
        """
        now = time.time()
        with self._lock:
            entry = self._data[ip]

            # Check if currently blocked
            if entry.blocked_until > now:
                return True, int(entry.blocked_until - now) + 1

            # Clean up old attempts
            self._cleanup_old_attempts(entry, now)

            # Check if over limit
            if len(entry.attempts) >= self.max_attempts:
                entry.blocked_until = now + self.block_duration_seconds
                return True, self.block_duration_seconds

            return False, 0

    def get_remaining_attempts(self, ip: str) -> int:
        """Get remaining attempts before blocking.

        Args:
            ip: Client IP address.

        Returns:
            Number of remaining attempts (0 if blocked).
        """
        now = time.time()
        with self._lock:
            entry = self._data[ip]
            self._cleanup_old_attempts(entry, now)

            if entry.blocked_until > now:
                return 0

            return max(0, self.max_attempts - len(entry.attempts))

    def clear_attempts(self, ip: str) -> None:
        """Clear all attempts for an IP (e.g., after successful login).

        Args:
            ip: Client IP address.
        """
        with self._lock:
            if ip in self._data:
                del self._data[ip]


# Global rate limiter instance
rate_limiter = InMemoryRateLimiter()