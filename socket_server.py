#!/usr/bin/env python3
"""
Socket server module for OBS Splits auto-splitting integration.
Provides bi-directional Unix socket communication for external auto-splitting scripts.
"""

import socket
import json
import threading
import os
import time
from typing import Callable, Dict, Any, Optional


class SplitSocketServer:
    """Unix socket server for auto-splitting integration."""

    def __init__(self, socket_path: str = "/tmp/obs_splits.sock"):
        self.socket_path = socket_path
        self.server_socket: Optional[socket.socket] = None
        self.running = False
        self.thread: Optional[threading.Thread] = None
        self.command_handler: Optional[Callable[[Dict[str, Any]], Dict[str, Any]]] = None

    def start(self, command_handler: Callable[[Dict[str, Any]], Dict[str, Any]]) -> bool:
        """Start the socket server in a background thread."""
        if self.running:
            return True

        self.command_handler = command_handler

        # Clean up any existing socket file
        if os.path.exists(self.socket_path):
            try:
                os.unlink(self.socket_path)
            except OSError:
                pass

        try:
            self.server_socket = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            self.server_socket.bind(self.socket_path)
            self.server_socket.listen(1)
            self.server_socket.settimeout(1.0)  # Non-blocking with timeout

            self.running = True
            self.thread = threading.Thread(target=self._server_loop, daemon=True)
            self.thread.start()

            return True
        except OSError as e:
            print(f"[Splits] Failed to start socket server: {e}")
            return False

    def stop(self) -> None:
        """Stop the socket server."""
        self.running = False

        if self.server_socket:
            try:
                self.server_socket.close()
            except OSError:
                pass
            self.server_socket = None

        # Clean up socket file
        if os.path.exists(self.socket_path):
            try:
                os.unlink(self.socket_path)
            except OSError:
                pass

        if self.thread and self.thread.is_alive():
            self.thread.join(timeout=2.0)

    def _server_loop(self) -> None:
        """Main server loop handling client connections."""
        while self.running:
            try:
                client_socket, _ = self.server_socket.accept()
                self._handle_client(client_socket)
            except socket.timeout:
                continue  # Expected timeout for clean shutdown
            except OSError:
                break  # Socket closed

    def _handle_client(self, client_socket: socket.socket) -> None:
        """Handle a single client connection."""
        try:
            # Receive command
            data = client_socket.recv(1024)
            if not data:
                return

            try:
                command = json.loads(data.decode('utf-8'))
                if not isinstance(command, dict) or 'command' not in command:
                    response = {"response": "error", "error": "invalid_command"}
                else:
                    response = self.command_handler(command)
            except json.JSONDecodeError:
                response = {"response": "error", "error": "invalid_json"}

            # Send response
            response_data = json.dumps(response).encode('utf-8')
            client_socket.send(response_data)

        except Exception as e:
            print(f"[Splits] Socket client error: {e}")
        finally:
            try:
                client_socket.close()
            except OSError:
                pass