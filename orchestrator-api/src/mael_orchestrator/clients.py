"""Where a browser is: on this server's machine, or not.

A **Remote client** is a browser on a **Remote host** of this server. The
server tells the two apart by IP: the peer address of the request, and its
own address on that socket. See "Remote clients" in ``docs/dev/cmux.md``.
"""

import ipaddress


def normal_ip(text: str | None) -> str | None:
    """``text`` as one canonical IP, or ``None`` when it is not one.

    An IPv4 address mapped into IPv6 reads as the IPv4 address, and a scope
    id is dropped, so a dual-stack socket's peer compares with a resolved name.
    """
    if not text:
        return None
    try:
        address = ipaddress.ip_address(text.split("%")[0])
    except ValueError:
        return None
    if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped:
        address = address.ipv4_mapped
    return str(address)


def client_is_local(peer: str | None, own: str | None) -> bool:
    """Whether the peer ``peer`` runs on this machine.

    True for a loopback peer, and for a peer whose address is the server's
    own address on the socket: a browser on this machine that uses its
    tailnet name.
    """
    address = normal_ip(peer)
    if address is None:
        return False
    if ipaddress.ip_address(address).is_loopback:
        return True
    return address == normal_ip(own)
