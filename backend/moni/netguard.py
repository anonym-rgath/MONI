"""Ziele ausgehender Verbindungen des Managers (Agents, Webhooks, OIDC, SMTP), geprüft beim Verbinden (N5).

Geprüft wird jede aufgelöste Adresse direkt vor dem connect, damit auch ein Hostname, der auf eine gesperrte Adresse
zeigt, nicht durchkommt. Link-Local und Cloud-Metadaten sind immer gesperrt; private Netze nur, wenn
MONI_ALLOW_PRIVATE_TARGETS=0 (Agents laufen oft im Heimnetz, deshalb Standard erlaubt).
"""
import http.client
import ipaddress
import logging
import socket
import urllib.request

log = logging.getLogger("moni.netguard")

_ALWAYS = tuple(ipaddress.ip_network(n) for n in ("169.254.0.0/16", "fe80::/10", "fd00:ec2::254/128"))
allow_private = True  # create_app setzt den Wert aus MONI_ALLOW_PRIVATE_TARGETS


class Blocked(OSError):
    """Ziel gesperrt; als OSError landet es in den bestehenden „nicht erreichbar“-Zweigen."""


def check(addr: str) -> None:
    ip = ipaddress.ip_address(addr.split("%", 1)[0])  # IPv6-Zonen-ID
    if ip.version == 6 and ip.ipv4_mapped:
        ip = ip.ipv4_mapped
    if any(ip in n for n in _ALWAYS) or (not allow_private and not ip.is_global):
        log.warning("Ausgehende Verbindung zu %s gesperrt (MONI_ALLOW_PRIVATE_TARGETS, Link-Local/Metadaten)", ip)
        raise Blocked("Ziel gesperrt")


def create_connection(address, timeout, source_address=None):
    """Wie socket.create_connection, aber nur zu erlaubten Adressen."""
    err: OSError = OSError("keine Adresse")
    for *_, sa in socket.getaddrinfo(address[0], address[1], type=socket.SOCK_STREAM):
        try:
            check(sa[0])
            return socket.create_connection(sa[:2], timeout, source_address)
        except OSError as e:
            err = e
    raise err


class _HTTPConnection(http.client.HTTPConnection):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._create_connection = create_connection


class _HTTPSConnection(http.client.HTTPSConnection):  # TLS mit server_hostname=self.host wie bisher
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._create_connection = create_connection


class HTTPHandler(urllib.request.HTTPHandler):
    def http_open(self, req):
        return self.do_open(_HTTPConnection, req)


class HTTPSHandler(urllib.request.HTTPSHandler):
    def https_open(self, req):
        return self.do_open(_HTTPSConnection, req, context=self._context)
