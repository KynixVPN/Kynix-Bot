import logging
import httpx
import uuid
import time
import json
import asyncio
import ssl
import hashlib
from datetime import datetime, timezone
from urllib.parse import urlparse
from config import settings

logger = logging.getLogger("xui_client")


class XuiError(Exception):
    pass




def _get_httpx_tls_kwargs() -> dict:
    """Build kwargs for httpx client with TLS settings from .env."""
    kwargs: dict = {}
    # CA pinning: if XUI_TLS_CA_CERT is set, httpx trusts only that CA bundle
    if getattr(settings, "XUI_TLS_CA_CERT", None):
        kwargs["verify"] = settings.XUI_TLS_CA_CERT
    # mTLS (client certificate)
    cert_path = getattr(settings, "XUI_TLS_CLIENT_CERT", None)
    key_path = getattr(settings, "XUI_TLS_CLIENT_KEY", None)
    if cert_path and key_path:
        kwargs["cert"] = (cert_path, key_path)
    elif cert_path and not key_path:
        # allow single file containing both cert+key
        kwargs["cert"] = cert_path
    return kwargs


async def _check_xui_cert_fingerprint() -> None:
    """Optional strict certificate fingerprint pinning (sha256 of DER cert)."""
    expected = getattr(settings, "XUI_TLS_FINGERPRINT_SHA256", None)
    if not expected:
        return

    base_url = settings.XUI_BASE_URL
    u = urlparse(base_url)
    if u.scheme != "https":
        raise XuiError("XUI_TLS_FINGERPRINT_SHA256 requires https:// XUI_BASE_URL")
    host = u.hostname
    if not host:
        raise XuiError("Failed to parse host from XUI_BASE_URL for fingerprint pinning")
    port = u.port or 443

    cafile = getattr(settings, "XUI_TLS_CA_CERT", None)
    ctx = ssl.create_default_context(cafile=cafile if cafile else None)
    # ensure hostname verification
    ctx.check_hostname = True
    ctx.verify_mode = ssl.CERT_REQUIRED

    reader = writer = None
    try:
        reader, writer = await asyncio.open_connection(
            host,
            port,
            ssl=ctx,
            server_hostname=host,
        )
        ssl_obj = writer.get_extra_info("ssl_object")
        if ssl_obj is None:
            raise XuiError("TLS handshake failed: no ssl_object")
        cert_bin = ssl_obj.getpeercert(binary_form=True)
        if not cert_bin:
            raise XuiError("TLS handshake failed: empty peer cert")
        actual = hashlib.sha256(cert_bin).hexdigest().lower()

        if actual != expected:
            raise XuiError(
                "XUI TLS certificate fingerprint mismatch. "
                f"expected={expected} actual={actual} host={host}:{port}"
            )
    finally:
        try:
            if writer is not None:
                writer.close()
                await writer.wait_closed()
        except Exception:
            pass


def _build_xui_http_client() -> httpx.AsyncClient:
    tls_kwargs = _get_httpx_tls_kwargs()
    return httpx.AsyncClient(
        base_url=settings.XUI_BASE_URL,
        timeout=10.0,
        follow_redirects=True,
        **tls_kwargs,
    )

# ============================
# LOGIN
# ============================

async def xui_login(client: httpx.AsyncClient):
    resp = await client.post(
        "/login",
        data={"username": settings.XUI_USERNAME, "password": settings.XUI_PASSWORD},
        follow_redirects=True,
    )
    if resp.status_code != 200:
        raise XuiError(f"Failed to login: {resp.text}")


# ============================
# GET INBOUND BY ID
# ============================

async def get_inbound(client: httpx.AsyncClient, inbound_id: int):
    resp = await client.get("/panel/api/inbounds/list")

    if resp.status_code != 200:
        raise XuiError(f"Failed to fetch inbounds: {resp.text}")

    for inbound in resp.json()["obj"]:
        if inbound["id"] == inbound_id:
            return inbound

    raise XuiError(f"Inbound {inbound_id} not found")


# ============================
# PARSE HOST FROM XUI_BASE_URL
# ============================

def get_base_host():
    """
    Берёт хост из XUI_BASE_URL:
    http://1.1.1.1:1111→ 1.1.1.1
    https://vpn.domain.com → vpn.domain.com
    """
    url = settings.XUI_BASE_URL.replace("http://", "").replace("https://", "")
    return url.split(":")[0]


# ============================
# BUILD VLESS REALITY LINK
# ============================

def build_vless(uid, host, port, tag, fake_id, pbk, sid):
    return (
        f"vless://{uid}@{host}:{port}"
        f"?type=tcp"
        f"&encryption=none"
        f"&security=reality"
        f"&pbk={pbk}"
        f"&fp=chrome"
        f"&sni=google.com"
        f"&sid={sid}"
        f"&spx=%2F"
        f"&flow=xtls-rprx-vision"
        f"#Kynix-VPN-{tag}-{fake_id}"
    )


# ============================
# ADD CLIENT
# ============================

async def create_xui_client(fake_id: int, expiry_ts: int, tag: str, inbound_id: int):
    await _check_xui_cert_fingerprint()

    async with _build_xui_http_client() as client:

        # login
        await xui_login(client)

        # inbound info
        inbound = await get_inbound(client, inbound_id)

        stream_obj = json.loads(inbound["streamSettings"])
        reality = stream_obj["realitySettings"]

        pbk = reality["settings"]["publicKey"]
        sid = reality["shortIds"][0]

        # host = inbound.get("listen") or inbound.get("address")
        host = get_base_host()  # <--- заменено

        port = inbound["port"]

        # client values
        uid = str(uuid.uuid4())
        subid = uuid.uuid4().hex[:16]
        email = f"{fake_id}"

        client_js = {
            "id": uid,
            "email": email,
            "enable": True,
            "expiryTime": expiry_ts,
            "limitIp": 0,
            "totalGB": 0,
            "tgId": 0,
            "reset": 0,
            "flow": "xtls-rprx-vision",
        }

        # Send request to add client
        resp = await client.post(
            "/panel/api/inbounds/addClient",
            json={
                "id": inbound_id,
                "settings": json.dumps({"clients": [client_js]}, ensure_ascii=False),
            },
        )

        if resp.status_code != 200:
            raise XuiError(f"addClient failed: {resp.text}")

        try:
            j = resp.json()
            if isinstance(j, dict) and not j.get("success", True):
                raise XuiError(f"addClient rejected: {resp.text}")
        except:
            pass

        # Build final VLESS
        vless = build_vless(uid, host, port, tag, fake_id, pbk, sid)

        return {
            "uuid": uid,
            "subId": subid,
            "email": email,
            "vless": vless,
        }


# ============================
# CREATE PLUS
# ============================

async def create_client_for_user(fake_id: int, days: int):
    expiry_ts = int(time.time() * 1000 + days * 86400 * 1000)
    inbound_plus = int(settings.XUI_INBOUND_ID)

    return await create_xui_client(
        fake_id=fake_id,
        expiry_ts=expiry_ts,
        tag="Plus",
        inbound_id=inbound_plus,
    )


# ============================
# CREATE PLUS UNTIL EXACT DATETIME (UTC)
# ============================

async def create_client_for_user_until(fake_id: int, expires_at: datetime):
    """Создаёт Plus клиента в X-UI до конкретной даты/времени.

    В БД в проекте используются naive datetime в UTC (через datetime.utcnow()),
    поэтому здесь считаем, что expires_at тоже в UTC.
    """
    # Convert to epoch milliseconds; treat naive datetime as UTC
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=timezone.utc)
    expiry_ts = int(expires_at.timestamp() * 1000)

    inbound_plus = int(settings.XUI_INBOUND_ID)
    return await create_xui_client(
        fake_id=fake_id,
        expiry_ts=expiry_ts,
        tag="Plus",
        inbound_id=inbound_plus,
    )


# ============================
# CREATE INFINITE
# ============================

async def create_client_inf(fake_id: int):
    inbound_inf = int(settings.XUI_INBOUND_ID_INF)

    return await create_xui_client(
        fake_id=fake_id,
        expiry_ts=0,
        tag="Inf",
        inbound_id=inbound_inf,
    )


# ============================
# DELETE CLIENT BY EMAIL
# ============================

async def delete_xui_client(email: str, inbound_id: int | None = None):
    inbound_id = inbound_id or int(settings.XUI_INBOUND_ID)

    await _check_xui_cert_fingerprint()

    async with _build_xui_http_client() as client:
        await xui_login(client)
        inbound = await get_inbound(client, inbound_id)

        settings_obj = json.loads(inbound["settings"])
        clients = settings_obj.get("clients", [])

        client_to_delete = next(
            (c for c in clients if str(c.get("email")) == str(email)),
            None,
        )

        if client_to_delete is None:
            raise XuiError(f"Client {email} not found in inbound {inbound_id}")

        client_uuid = client_to_delete.get("id") or client_to_delete.get("uuid")

        resp = await client.post(
            f"/panel/api/inbounds/{inbound_id}/delClient/{client_uuid}"
        )

        if resp.status_code != 200:
            raise XuiError(f"deleteClient failed: {resp.text}")

        try:
            j = resp.json()
            if isinstance(j, dict) and not j.get("success", True):
                raise XuiError(f"deleteClient rejected: {resp.text}")
        except:
            pass

        logger.info(
            "Deleted X-UI client email=%s uuid=%s inbound=%s",
            email,
            client_uuid,
            inbound_id,
        )
