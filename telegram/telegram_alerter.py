"""
Telegram Alerter
================
Sends alerts via Telegram using Telethon (MTProto client).
Starts with system error/health alerts, expandable to whale alerts later.

Credentials loaded from .env file:
    TELEGRAM_API_ID
    TELEGRAM_API_HASH
    TELEGRAM_PHONE

NOTE: Runs Telethon in a dedicated background thread to avoid
      asyncio event loop conflicts with the main application.
"""
import os
import asyncio
import threading
import queue
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv
from telethon import TelegramClient
from telethon.errors import SessionPasswordNeededError

# Load .env from project root
load_dotenv()


class TelegramAlerter:
    """
    Telegram alerter using Telethon with background thread.

    Runs Telethon in its own thread with a dedicated event loop,
    avoiding conflicts with the main application's asyncio usage.

    Usage:
        alerter = TelegramAlerter()
        alerter.connect()  # Blocking, handles auth if needed
        alerter.send_alert("ERROR", "Something broke", {"key": "value"})
        alerter.disconnect()
    """

    def __init__(self, session_name: str = "whale_tracker"):
        """
        Initialize alerter with credentials from environment.

        Args:
            session_name: Name for the .session file (stored in data/)
        """
        self.api_id = os.getenv("TELEGRAM_API_ID")
        self.api_hash = os.getenv("TELEGRAM_API_HASH")
        self.phone = os.getenv("TELEGRAM_PHONE")

        if not all([self.api_id, self.api_hash, self.phone]):
            raise ValueError(
                "Missing Telegram credentials. "
                "Set TELEGRAM_API_ID, TELEGRAM_API_HASH, TELEGRAM_PHONE in .env"
            )

        # Store session in data/ directory
        session_path = Path("data") / session_name
        self.session_path = str(session_path)

        # Background thread components
        self._loop = None
        self._thread = None
        self._client = None
        self._message_queue = queue.Queue()
        self._running = False
        self._connected = False

    def connect(self):
        """
        Connect to Telegram in a background thread.
        Blocks until connected (handles auth prompts if needed).
        """
        if self._connected:
            return

        # Start background thread
        self._running = True
        self._thread = threading.Thread(target=self._run_loop, daemon=True)
        self._thread.start()

        # Wait for connection (with timeout)
        timeout = 60  # seconds for auth
        start = datetime.now()
        while not self._connected and (datetime.now() - start).seconds < timeout:
            if not self._thread.is_alive():
                raise RuntimeError("Telegram thread died during connection")
            threading.Event().wait(0.1)

        if not self._connected:
            raise RuntimeError("Telegram connection timed out")

    def disconnect(self):
        """Disconnect from Telegram and stop background thread."""
        self._running = False
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=5)
        self._connected = False

    def _run_loop(self):
        """Background thread: run Telethon in its own event loop."""
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)

        try:
            self._loop.run_until_complete(self._async_main())
        except Exception as e:
            print(f"Telegram thread error: {e}")
        finally:
            self._loop.close()

    async def _async_main(self):
        """Main async loop: connect, then process message queue."""
        self._client = TelegramClient(
            self.session_path,
            int(self.api_id),
            self.api_hash
        )

        await self._client.connect()

        # Handle authentication if needed
        if not await self._client.is_user_authorized():
            await self._client.send_code_request(self.phone)

            try:
                code = input(f"Enter Telegram code sent to {self.phone}: ")
                await self._client.sign_in(self.phone, code)
            except SessionPasswordNeededError:
                password = input("Enter your 2FA password: ")
                await self._client.sign_in(password=password)

        self._connected = True

        # Process message queue
        while self._running:
            try:
                # Non-blocking check for messages
                try:
                    message = self._message_queue.get_nowait()
                    await self._client.send_message("me", message, parse_mode="markdown")
                except queue.Empty:
                    pass

                await asyncio.sleep(0.1)
            except Exception as e:
                print(f"Error sending Telegram message: {e}")

        await self._client.disconnect()

    # =========================================================================
    # Public Alert Methods (thread-safe, non-blocking)
    # =========================================================================

    def send_alert(self, error_type: str, message: str, details: dict = None):
        """
        Send a system alert (thread-safe, non-blocking).

        Args:
            error_type: Category like "API_FAILURE", "DB_ERROR", "STARTUP"
            message: Human-readable description
            details: Optional dict with extra context
        """
        if not self._connected:
            return

        timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")

        text = f"""🚨 **SYSTEM ALERT**

**Type:** `{error_type}`
**Time:** {timestamp}

{message}"""

        if details:
            details_str = "\n".join(f"  • {k}: `{v}`" for k, v in details.items())
            text += f"\n\n**Details:**\n{details_str}"

        self._message_queue.put(text)

    def send_health_check(self, status: str, stats: dict):
        """
        Send periodic health check.

        Args:
            status: "OK" or "DEGRADED"
            stats: Dict with current system stats
        """
        if not self._connected:
            return

        timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
        emoji = "✅" if status == "OK" else "⚠️"

        stats_str = "\n".join(f"  • {k}: `{v}`" for k, v in stats.items())

        text = f"""{emoji} **Health Check**

**Status:** {status}
**Time:** {timestamp}

**Stats:**
{stats_str}"""

        self._message_queue.put(text)


# =============================================================================
# Standalone test
# =============================================================================

def _test():
    """Quick test - run with: python telegram_alerter.py"""
    print("Testing Telegram Alerter...")

    alerter = TelegramAlerter()
    alerter.connect()

    alerter.send_alert(
        error_type="TEST",
        message="This is a test alert from whale_tracker.",
        details={
            "source": "telegram_alerter.py",
            "test": True
        }
    )

    # Give it a moment to send
    import time
    time.sleep(2)

    alerter.disconnect()
    print("✅ Test alert sent! Check your Saved Messages.")


if __name__ == "__main__":
    _test()