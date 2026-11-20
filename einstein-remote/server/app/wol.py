"""Wake-on-LAN: liga um computador desligado enviando o "pacote mágico" pela rede local.

Funciona somente quando:
* o servidor está na MESMA rede local do PC (o pacote é broadcast e não atravessa a internet);
* o Wake-on-LAN está ativado na BIOS/UEFI e na placa de rede (Gerenciador de Dispositivos >
  placa de rede > Gerenciamento de energia > "Permitir que este dispositivo ative o computador");
* o PC está ligado na tomada e, de preferência, conectado por cabo.
"""
from __future__ import annotations

import ipaddress
import re
import socket

MAC_RE = re.compile(r"^([0-9a-f]{2}[:-]){5}[0-9a-f]{2}$", re.I)


def magic_packet(mac: str) -> bytes:
    if not MAC_RE.match(mac or ""):
        raise ValueError("Endereço MAC inválido")
    raw = bytes.fromhex(mac.replace(":", "").replace("-", ""))
    return b"\xff" * 6 + raw * 16


def targets(last_ip: str | None) -> list[str]:
    """Broadcast geral + broadcast da sub-rede /24 em que o PC foi visto por último."""
    out = ["255.255.255.255"]
    try:
        ip = ipaddress.ip_address(last_ip or "")
        if ip.version == 4 and ip.is_private:
            out.append(str(ipaddress.ip_network(f"{ip}/24", strict=False).broadcast_address))
    except ValueError:
        pass
    return out


def send(mac: str, last_ip: str | None, ports=(9, 7)) -> list[str]:
    """Envia o pacote; retorna os destinos alcançados (pode ser vazio se a rede bloquear broadcast)."""
    packet = magic_packet(mac)
    sent = []
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
        s.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        for host in targets(last_ip):
            for port in ports:
                try:
                    s.sendto(packet, (host, port))
                    sent.append(f"{host}:{port}")
                except OSError:
                    continue
    return sent
