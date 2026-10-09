"""IPv4 listener selection shared by the desktop controller and gateway."""
import ipaddress


def selected_addresses(value):
    if value is None:
        return None
    if not isinstance(value, list) or len(value) > 256:
        raise ValueError('请选择有效的局域网 IPv4 地址')
    result = []
    for item in value:
        try:
            address = ipaddress.IPv4Address(item) if isinstance(item, str) else None
        except ipaddress.AddressValueError:
            address = None
        if address is None or address.is_loopback or address.is_unspecified or address.is_multicast or str(address) == '255.255.255.255':
            raise ValueError('请选择有效的局域网 IPv4 地址')
        if str(address) not in result:
            result.append(str(address))
    return result


def bindings(preferences):
    selected = selected_addresses(preferences.get('lanAddresses'))
    if preferences.get('lan') and selected is None:
        return ['0.0.0.0']
    return ['127.0.0.1'] + (selected if preferences.get('lan') else [])
