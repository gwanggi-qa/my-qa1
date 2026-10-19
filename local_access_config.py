"""Explicit trusted LAN configuration. Restart after an IP/network change."""
BIND_ADDRESS = "0.0.0.0"
PORT = 5000
BROWSER_HOSTNAMES = ("localhost", "127.0.0.1", "192.168.11.217")
ALLOWED_NETWORKS = ("192.168.11.0/24",)
