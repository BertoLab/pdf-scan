"""Keep the native UI responsive while closing across JS and HTTP boundaries."""
from __future__ import annotations

import threading


class DesktopCloseCoordinator:
    def __init__(self, window, server, *, has_ocr, stop_ocr, resume_ocr, close_previews, show_error):
        self.window = window
        self.server = server
        self.has_ocr = has_ocr
        self.stop_ocr = stop_ocr
        self.resume_ocr = resume_ocr
        self.close_previews = close_previews
        self.show_error = show_error
        self.allowed = False
        self._lock = threading.Lock()

    def on_closing(self):
        if self.allowed:
            return None
        if self._lock.acquire(blocking=False):
            # Closing is a synchronous WinForms event: never wait for JS on it.
            threading.Thread(target=self._attempt_close, name="desktop-close").start()
        return False

    def _prepare_frontend(self):
        done = threading.Event()
        result = []

        def receive(value):
            result.append(value)
            done.set()

        self.window.evaluate_js(
            "Promise.resolve(window.estrattoreDesktop ? "
            "window.estrattoreDesktop.prepareClose() : {ok: true})",
            callback=receive,
        )
        if not done.wait(30):
            raise RuntimeError("Il salvataggio richiede ancora tempo. Attendi e riprova a chiudere.")
        response = result[0]
        if not isinstance(response, dict) or response.get("ok") is not True:
            message = response.get("message") if isinstance(response, dict) else None
            raise RuntimeError(message or "Non riesco a verificare il salvataggio. Riprova a chiudere.")
        return response

    def _attempt_close(self):
        try:
            state = self._prepare_frontend()
            if state.get("unsavedEditor") and not self.window.create_confirmation_dialog(
                "Modifiche non salvate",
                "La tabella e' in modifica. Chiudere senza salvare le modifiche alla tabella?",
            ):
                return
            # Freeze admission first: an accepted import may still start OCR.
            if not self.server.drain_mutations(timeout=30):
                raise RuntimeError("Un salvataggio e' ancora in corso. Attendi e riprova a chiudere.")
            if self.has_ocr() and not self.window.create_confirmation_dialog(
                "Lavorazione in corso",
                "Una lavorazione OCR e' in corso. Interromperla e chiudere l'app? "
                "Riprendera' alla prossima apertura dal PDF non completato.",
            ):
                return
            if not self.stop_ocr(timeout=30):
                raise RuntimeError("L'interruzione richiede ancora tempo. Riprova a chiudere tra poco.")
            self.close_previews()
            self.allowed = True
            self.window.destroy()
        except Exception as error:
            self.show_error(str(error))
        finally:
            if not self.allowed:
                self.server.resume_mutations()
                self.resume_ocr()
                try:
                    self.window.evaluate_js("window.estrattoreDesktop?.cancelClose()")
                except Exception:
                    pass
            self._lock.release()
