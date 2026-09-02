#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Basit SOCKS5 proxy sunucusu — örnek kod (demo/test amaçlı).

Kullanım:
    python demo_proxy.py            # 127.0.0.1:1080
    python demo_proxy.py 1080 0.0.0.0

Nasıl çalışır?
  1. İstemci:  "VER NMETHODS"  ->  sunucu: "VER METHOD"   (hiç kimlik doğrulama yok)
  2. İstemci:  CMD CONNECT ile hedef adres/port
  3. Sunucu hedefe TCP bağlantısı kurar, başarıda bant genişliği (bind) adresi döner
  4. İki yönde ham bayt pompalanır (Tunnel).

UYARI: Kimlik doğrulama YOKTUR; yalnızca yerel test/demo için kullanın.
"""

import asyncio
import socket
import struct
import sys

HOST = "127.0.0.1"
PORT = 1080


def log(*a):
    print("[socks5]", *a, flush=True)


async def pipe(reader: asyncio.StreamReader, writer: asyncio.StreamWriter):
    try:
        while True:
            data = await reader.read(65536)
            if not data:
                break
            writer.write(data)
            await writer.drain()
    except (ConnectionResetError, BrokenPipeError):
        pass
    finally:
        try:
            writer.write_eof()
        except Exception:
            pass
        try:
            writer.close()
        except Exception:
            pass


async def handle(reader: asyncio.StreamReader, writer: asyncio.StreamWriter):
    peer = writer.get_extra_info("peername")
    try:
        # --- 1) Karşılama / method seçimi ---
        ver, nmethods = await reader.readexactly(2)
        if ver != 5:
            log("reddedildi (versiyon != 5):", peer)
            return
        await reader.readexactly(nmethods)
        writer.write(b"\x05\x00")  # method: kimlik doğrulama yok
        await writer.drain()

        # --- 2) Bağlantı isteği ---
        ver, cmd, _rsv, atyp = await reader.readexactly(4)
        if cmd != 1:  # yalnızca CONNECT destekleniyor
            writer.write(b"\x05\x07\x00\x01" + b"\x00\x00\x00\x00\x00\x00")
            await writer.drain()
            return

        if atyp == 1:        # IPv4
            addr = socket.inet_ntoa(await reader.readexactly(4))
        elif atyp == 3:      # domain adı
            ln = (await reader.readexactly(1))[0]
            addr = (await reader.readexactly(ln)).decode("utf-8", "replace")
        elif atyp == 4:      # IPv6
            addr = socket.inet_ntop(socket.AF_INET6, await reader.readexactly(16))
        else:
            writer.write(b"\x05\x08\x00\x01" + b"\x00\x00\x00\x00\x00\x00")
            await writer.drain()
            return
        port = struct.unpack("!H", await reader.readexactly(2))[0]
        log("CONNECT", addr, port, "<-", peer)

        # --- 3) Hedefe bağlan ---
        try:
            rem_reader, rem_writer = await asyncio.wait_for(
                asyncio.open_connection(addr, port), timeout=10
            )
        except Exception:
            # kod 5 = genel hata (erişilemiyor)
            writer.write(b"\x05\x05\x00\x01" + b"\x00\x00\x00\x00\x00\x00")
            await writer.drain()
            log("hedefe bağlanılamadı:", addr, port)
            return

        # --- 4) Başarı yanıtı (bind adres/port) ---
        bindport = rem_writer.get_extra_info("sockname")[1]
        writer.write(
            b"\x05\x00\x00\x01" + socket.inet_aton("0.0.0.0") + struct.pack("!H", bindport)
        )
        await writer.drain()

        # --- 5) İki yönlü bayt pompalama ---
        await asyncio.gather(pipe(reader, rem_writer), pipe(rem_reader, writer))
        log("kapatıldı:", addr, port)
    except (asyncio.IncompleteReadError, ConnectionResetError):
        pass
    except Exception as e:
        log("hata:", e)
    finally:
        try:
            writer.close()
        except Exception:
            pass


async def main(host: str, port: int):
    server = await asyncio.start_server(handle, host, port)
    log(f"SOCKS5 proxy dinliyor: {host}:{port}")
    async with server:
        await server.serve_forever()


if __name__ == "__main__":
    port = int(sys.argv[1]) if len(sys.argv) > 1 else PORT
    host = sys.argv[2] if len(sys.argv) > 2 else HOST
    try:
        asyncio.run(main(host, port))
    except KeyboardInterrupt:
        log("kapatıldı (Ctrl-C)")
