"""Zustellung der Outbox (D6): eigener Thread, Wiederholungen, nie Netzwerk bei offener DB-Transaktion."""
import logging
import threading
import time

from ..config import Settings
from ..secretbox import SecretBox, SecretError
from . import alert_channels, db
from .alert_channels import ChannelStore, SendError

log = logging.getLogger("moni.notifier")

RETRY = (60, 300, 900, 3600)
MAX_ATTEMPTS = 5
_BATCH = 20
_EVENT_COLS = ("event", "severity", "rule_name", "server_name", "key", "value", "threshold", "reason", "ts")


class Notifier:
    def __init__(self, settings: Settings, box: SecretBox, deliver=alert_channels.deliver, interval: float = 5):
        self._settings, self._box, self._deliver, self._interval = settings, box, deliver, interval
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        # zugestellt, Ergebnis aber nicht gespeichert: im nächsten Lauf ohne Neuversand speichern (D6, P5-R19)
        # shortcut: nur im Speicher, ein Neustart dazwischen sendet einmal doppelt; reicht für den seltenen DB-Fehler
        self._unsaved: list[dict] = []

    def _read(self, now: int) -> tuple[list[dict], str]:
        """Eigene Verbindung, am Ende geschlossen: beim Versand hält niemand eine Transaktion."""
        conn = db.connect(self._settings.db_path)
        try:
            r = conn.execute("SELECT name FROM servers WHERE is_local=1").fetchone()
            rows = conn.execute(
                "SELECT o.id, o.channel_id, o.attempts, e.*, (SELECT name FROM alert_channels WHERE id=o.channel_id) AS cname FROM alert_outbox o JOIN alert_events e ON e.id=o.event_id "
                "WHERE o.status='pending' AND o.next_try<=? ORDER BY o.id LIMIT ?", (now, _BATCH)).fetchall()
            store, items = ChannelStore(conn, self._box), []
            for x in rows:
                item = {"id": x["id"], "attempts": x["attempts"], "event": {k: x[k] for k in _EVENT_COLS}, "cname": x["cname"],
                        "channel": None, "secrets": None, "error": None}
                try:
                    item["channel"] = store.get(x["channel_id"])
                    if item["channel"] and item["channel"].enabled:
                        item["secrets"] = store.secrets(x["channel_id"])
                except SecretError:
                    item["error"] = "Kanal-Geheimnis nicht entschlüsselbar"
                except Exception as e:  # ein defekter Kanal darf die übrigen nicht blockieren
                    log.error("Kanal nicht ladbar (%s)", type(e).__name__)
                    item["error"] = "Versand fehlgeschlagen"
                items.append(item)
            return items, (r["name"] if r else "moni")
        finally:
            conn.close()

    def _send(self, item: dict, manager_name: str) -> None:
        """Setzt item['error'] auf einen festen Text oder lässt es None (Erfolg)."""
        ch = item["channel"]
        if item["error"] or ch is None:
            return
        if not ch.enabled:
            item["error"], item["final"] = "Kanal deaktiviert", True  # ohne Versuch, daher kein attempts+1
            return
        try:
            self._deliver(ch, item["secrets"], item["event"], manager_name)
        except SendError as e:
            item["error"] = str(e)
        except Exception as e:
            log.error("Versand fehlgeschlagen (%s)", type(e).__name__)  # nie Text: kann URL oder Antwort enthalten
            item["error"] = "Versand fehlgeschlagen"

    def _persist(self, it: dict, now: int) -> None:
        """Ergebnis eines Eintrags sofort festhalten, damit ein Abbruch nichts doppelt sendet."""
        if it["channel"] is None and not it["error"]:
            return  # Kanal inzwischen gelöscht (Cascade entfernt den Eintrag)
        if it["error"] is None:
            sql, args = "status='sent', last_error=NULL", ()
        elif it.get("final"):
            sql, args = "status='failed', last_error=?", (it["error"],)
        else:
            n = it["attempts"] + 1
            if n >= MAX_ATTEMPTS:
                sql, args = "attempts=?, status='failed', last_error=?", (n, it["error"])
                log.warning("Benachrichtigung über %s endgültig fehlgeschlagen", it["cname"])
            else:
                sql, args = "attempts=?, last_error=?, next_try=?", (n, it["error"], now + RETRY[min(n - 1, 3)])
        conn = db.connect(self._settings.db_path)
        try:
            conn.execute("BEGIN IMMEDIATE")
            conn.execute(f"UPDATE alert_outbox SET {sql} WHERE id=? AND status='pending'", (*args, it["id"]))
            conn.commit()
        finally:
            conn.close()

    def run_once(self, now: int) -> int:
        done = 0
        while self._unsaved:
            try:
                self._persist(self._unsaved[0], now)
            except Exception as e:  # noch pending: nicht lesen, sonst ginge er erneut raus
                log.error("Ergebnis der Benachrichtigung nicht gespeichert (%s)", type(e).__name__)
                return done
            self._unsaved.pop(0)
            done += 1
        items, manager_name = self._read(now)
        for item in items:
            if self._stop.is_set():
                break  # Rest bleibt pending
            self._send(item, manager_name)
            try:
                self._persist(item, now)
            except Exception as e:  # ohne Festhalten nicht weitersenden
                log.error("Ergebnis der Benachrichtigung nicht gespeichert (%s)", type(e).__name__)
                if item["error"] is None and item["channel"] is not None:
                    self._unsaved.append({**item, "secrets": None})
                break
            done += 1
        return done

    def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                self.run_once(int(time.time()))
            except Exception as e:
                log.error("Benachrichtigungslauf fehlgeschlagen (%s)", type(e).__name__)
            self._stop.wait(self._interval)

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, name="moni-notifier", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=15)
