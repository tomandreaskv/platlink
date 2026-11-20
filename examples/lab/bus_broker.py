"""Lokal TCP-buss for PlatLink v1.0-laben.

Kobler sammen flere noder og klienter over en ekte TCP-socket.
Alt som sendes inn fra én tilkoblet klient, videresendes som rå byte
til øvrige tilkoblede klienter (inkludert interaktiv CLI / gateway-monitor).
"""

from __future__ import annotations

import argparse
import socket
import threading


class PlatLinkTcpBus:
    def __init__(self, host: str, port: int) -> None:
        self.host = host
        self.port = port
        self._clients: set[socket.socket] = set()
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._server_sock: socket.socket | None = None

    def _broadcast(self, sender: socket.socket, data: bytes) -> None:
        dead: list[socket.socket] = []
        with self._lock:
            for client in self._clients:
                if client is sender:
                    continue
                try:
                    client.sendall(data)
                except OSError:
                    dead.append(client)
            for client in dead:
                self._clients.discard(client)
                try:
                    client.close()
                except OSError:
                    pass

    def _handle_client(self, conn: socket.socket, addr: tuple[str, int]) -> None:
        print(f"[BUS] Klient tilkoblet fra {addr[0]}:{addr[1]}")
        with self._lock:
            self._clients.add(conn)
        try:
            while not self._stop.is_set():
                data = conn.recv(4096)
                if not data:
                    break
                self._broadcast(conn, data)
        except OSError:
            pass
        finally:
            with self._lock:
                self._clients.discard(conn)
            try:
                conn.close()
            except OSError:
                pass
            print(f"[BUS] Klient frakoblet fra {addr[0]}:{addr[1]}")

    def serve_forever(self) -> None:
        srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        srv.bind((self.host, self.port))
        srv.listen(32)
        srv.settimeout(0.5)
        self._server_sock = srv

        print(f"[BUS] Lytte-buss aktiv på {self.host}:{self.port} (Ctrl+C for å stoppe)")
        try:
            while not self._stop.is_set():
                try:
                    conn, addr = srv.accept()
                except TimeoutError:
                    continue
                t = threading.Thread(
                    target=self._handle_client,
                    args=(conn, addr),
                    daemon=True,
                )
                t.start()
        finally:
            self.stop()

    def stop(self) -> None:
        self._stop.set()
        with self._lock:
            for c in list(self._clients):
                try:
                    c.close()
                except OSError:
                    pass
            self._clients.clear()
        if self._server_sock is not None:
            try:
                self._server_sock.close()
            except OSError:
                pass
            self._server_sock = None


def main() -> None:
    parser = argparse.ArgumentParser(description="PlatLink v1.0 lokal TCP-buss")
    parser.add_argument("--host", default="127.0.0.1", help="Bind-adresse (standard: 127.0.0.1)")
    parser.add_argument("--port", type=int, default=4571, help="TCP-port (standard: 4571)")
    args = parser.parse_args()

    bus = PlatLinkTcpBus(args.host, args.port)
    bus.serve_forever()


if __name__ == "__main__":
    main()