"""Credential-free optional local proxy check; never calls the model provider."""
import ipaddress
import os
import socket
from urllib.parse import urlsplit


class ModelProxyUnavailableError(ConnectionError):
    pass


def require_model_proxy():
    proxy=os.environ.get('KIMI_PROXY','').strip()
    if not proxy:
        return False  # Direct HTTPS needs no local proxy and performs no probe.
    parsed=urlsplit(proxy)
    try:local=ipaddress.ip_address(parsed.hostname or '').is_loopback
    except ValueError:local=parsed.hostname in ('localhost','host.docker.internal')
    if (parsed.scheme not in ('http','https') or not local or parsed.username is not None
            or parsed.password is not None or parsed.path not in ('','/') or parsed.query
            or parsed.fragment or parsed.port is not None and not 1<=parsed.port<=65535):
        raise ValueError('KIMI_PROXY must be a credential-free local HTTP(S) URL')
    port=parsed.port or (443 if parsed.scheme=='https' else 80)
    try:
        with socket.create_connection((parsed.hostname,port),timeout=2):
            pass
    except OSError as exc:
        raise ModelProxyUnavailableError('configured local model proxy unavailable') from exc
    return True
