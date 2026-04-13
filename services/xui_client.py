import asyncio
import hashlib
import json
import logging
import ssl
import time
import uuid
from datetime import datetime, timezone
from urllib.parse import quote, urlencode, urlparse

import httpx

from config import settings

logger = logging.getLogger("xui_client")

TRANSPORT_TCP = "tcp"
TRANSPORT_XHTTP = "xhttp"
PLAN_PLUS = "plus"
PLAN_INF = "inf"


class XuiError(Exception):
    pass


def get_supported_transports() -> tuple[str, str]:
    return TRANSPORT_TCP, TRANSPORT_XHTTP


def build_xui_email(fake_id: int, transport: str) -> str:
    transport = str(transport).lower()
    if transport == TRANSPORT_TCP:
        return f"t{fake_id}"
    if transport == TRANSPORT_XHTTP:
        return f"x{fake_id}"
    raise XuiError(f"Unsupported transport: {transport}")


def get_transport_label(transport: str) -> str:
    return "TCP" if transport == TRANSPORT_TCP else "xHTTP"


def get_inbound_id_for_plan_transport(plan: str, transport: str) -> int:
    plan = str(plan).lower()
    transport = str(transport).lower()

    if plan == PLAN_PLUS:
        if transport == TRANSPORT_TCP:
            return int(getattr(settings, "XUI_INBOUND_ID_PLUS_TCP", settings.XUI_INBOUND_ID))
        if transport == TRANSPORT_XHTTP:
            fallback = getattr(settings, "XUI_INBOUND_ID_PLUS_TCP", settings.XUI_INBOUND_ID)
            return int(getattr(settings, "XUI_INBOUND_ID_PLUS_XHTTP", fallback))
    elif plan == PLAN_INF:
        if transport == TRANSPORT_TCP:
            return int(getattr(settings, "XUI_INBOUND_ID_INF_TCP", settings.XUI_INBOUND_ID_INF))
        if transport == TRANSPORT_XHTTP:
            fallback = getattr(settings, "XUI_INBOUND_ID_INF_TCP", settings.XUI_INBOUND_ID_INF)
            return int(getattr(settings, "XUI_INBOUND_ID_INF_XHTTP", fallback))

    raise XuiError(f"Unsupported plan/transport combination: {plan}/{transport}")


def get_plan_for_expires_at(expires_at) -> str:
    return PLAN_INF if expires_at is None else PLAN_PLUS


def _get_httpx_tls_kwargs() -> dict:
    kwargs: dict = {}
    if getattr(settings, "XUI_TLS_CA_CERT", None):
        kwargs["verify"] = settings.XUI_TLS_CA_CERT
    cert_path = getattr(settings, "XUI_TLS_CLIENT_CERT", None)
    key_path = getattr(settings, "XUI_TLS_CLIENT_KEY", None)
    if cert_path and key_path:
        kwargs["cert"] = (cert_path, key_path)
    elif cert_path and not key_path:
        kwargs["cert"] = cert_path
    return kwargs


async def _check_xui_cert_fingerprint() -> None:
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


async def xui_login(client: httpx.AsyncClient):
    resp = await client.post(
        "/login",
        data={"username": settings.XUI_USERNAME, "password": settings.XUI_PASSWORD},
        follow_redirects=True,
    )
    if resp.status_code != 200:
        raise XuiError(f"Failed to login: {resp.text}")


async def get_inbound(client: httpx.AsyncClient, inbound_id: int):
    resp = await client.get("/panel/api/inbounds/list")

    if resp.status_code != 200:
        raise XuiError(f"Failed to fetch inbounds: {resp.text}")

    for inbound in resp.json()["obj"]:
        if inbound["id"] == inbound_id:
            return inbound

    raise XuiError(f"Inbound {inbound_id} not found")



def get_base_host():
    url = settings.XUI_BASE_URL.replace("http://", "").replace("https://", "")
    return url.split(":")[0].split("/")[0]


def _as_list(value) -> list:
    if isinstance(value, list):
        return value
    if value in (None, ""):
        return []
    return [value]


def _first_non_empty(*values):
    for value in values:
        if isinstance(value, list):
            for item in value:
                if item not in (None, ""):
                    return item
        elif value not in (None, ""):
            return value
    return None


def _extract_sni(stream_obj: dict) -> str | None:
    reality = stream_obj.get("realitySettings") or {}
    reality_settings = reality.get("settings") or {}
    sni = _first_non_empty(
        reality_settings.get("serverNames"),
        reality.get("serverNames"),
        reality_settings.get("serverName"),
        reality.get("serverName"),
    )
    if sni:
        return str(sni)

    tls_obj = stream_obj.get("tlsSettings") or {}
    sni = _first_non_empty(
        tls_obj.get("serverName"),
        tls_obj.get("serverNames"),
    )
    if sni:
        return str(sni)

    return None


def _extract_reality_public_key(stream_obj: dict) -> str | None:
    reality = stream_obj.get("realitySettings") or {}
    reality_settings = reality.get("settings") or {}
    value = _first_non_empty(
        reality_settings.get("publicKey"),
        reality.get("publicKey"),
    )
    return str(value) if value else None


def _extract_reality_short_id(stream_obj: dict) -> str | None:
    reality = stream_obj.get("realitySettings") or {}
    reality_settings = reality.get("settings") or {}
    value = _first_non_empty(
        reality.get("shortIds"),
        reality_settings.get("shortIds"),
        reality.get("shortId"),
        reality_settings.get("shortId"),
    )
    return str(value) if value else None


def _extract_spider_x(stream_obj: dict) -> str:
    reality = stream_obj.get("realitySettings") or {}
    reality_settings = reality.get("settings") or {}
    value = _first_non_empty(
        reality_settings.get("spiderX"),
        reality.get("spiderX"),
        "/",
    )
    return str(value)


def _pick_connect_host(inbound: dict, stream_obj: dict) -> str:
    host = inbound.get("listen")
    if host and host not in {"0.0.0.0", "::", "127.0.0.1", "localhost"}:
        return str(host)

    return get_base_host()



def _build_vless_from_parts(uid: str, host: str, port: int, params: dict, tag: str) -> str:
    return f"vless://{uid}@{host}:{port}?{urlencode(params)}#{quote(tag)}"



def build_vless(uid, inbound: dict, fake_id: int, tag: str, transport: str | None = None, email: str | None = None):
    stream_obj = json.loads(inbound["streamSettings"])
    network = str(transport or stream_obj.get("network") or TRANSPORT_TCP).lower()
    security = str(inbound.get("security") or stream_obj.get("security") or "none").lower()
    host = _pick_connect_host(inbound, stream_obj)
    port = int(inbound["port"])

    params: dict[str, str] = {
        "type": network,
        "encryption": "none",
    }

    if security and security != "none":
        params["security"] = security

    if security == "reality":
        reality = stream_obj.get("realitySettings") or {}
        reality_settings = reality.get("settings") or {}
        public_key = _extract_reality_public_key(stream_obj)
        if public_key:
            params["pbk"] = public_key
        params["fp"] = str(_first_non_empty(reality_settings.get("fingerprint"), reality.get("fingerprint"), "chrome"))
        sni = _extract_sni(stream_obj)
        if sni:
            params["sni"] = sni
        short_id = _extract_reality_short_id(stream_obj)
        if short_id:
            params["sid"] = short_id
        params["spx"] = _extract_spider_x(stream_obj)

    if security == "tls":
        tls_settings = stream_obj.get("tlsSettings") or {}
        sni = _extract_sni(stream_obj)
        if sni:
            params["sni"] = sni
        alpn = tls_settings.get("alpn")
        if isinstance(alpn, list) and alpn:
            params["alpn"] = ",".join(str(x) for x in alpn)

    xhttp_settings = stream_obj.get("xhttpSettings") or {}
    if network == TRANSPORT_XHTTP:
        path = xhttp_settings.get("path") or "/"
        params["path"] = str(path)
        host_header = xhttp_settings.get("host")
        if isinstance(host_header, list):
            host_header = host_header[0] if host_header else None
        if host_header:
            params["host"] = str(host_header)
        mode = xhttp_settings.get("mode")
        if mode:
            params["mode"] = str(mode)

    flow = None
    try:
        settings_obj = json.loads(inbound["settings"])
        clients = settings_obj.get("clients", [])
        client_obj = None
        if email is not None:
            client_obj = next((c for c in clients if str(c.get("email")) == str(email)), None)
        if client_obj is not None:
            flow = client_obj.get("flow")
    except Exception:
        flow = None

    if not flow and network == TRANSPORT_TCP and security == "reality":
        flow = "xtls-rprx-vision"
    if flow:
        params["flow"] = str(flow)

    title = f"Kynix-VPN-{tag}-{get_transport_label(network)}-{fake_id}"
    return _build_vless_from_parts(uid=str(uid), host=host, port=port, params=params, tag=title)


async def build_vless_for_email(*, email: str, fake_id: int, expires_at, transport: str) -> str:
    plan = get_plan_for_expires_at(expires_at)
    inbound_id = get_inbound_id_for_plan_transport(plan, transport)
    tag = "Inf" if plan == PLAN_INF else "Plus"

    await _check_xui_cert_fingerprint()

    async with _build_xui_http_client() as client:
        await xui_login(client)
        inbound = await get_inbound(client, int(inbound_id))

        settings_obj = json.loads(inbound["settings"])
        clients = settings_obj.get("clients", [])

        client_obj = next((c for c in clients if str(c.get("email")) == str(email)), None)
        if client_obj is None:
            raise XuiError(f"Client {email} not found in inbound {inbound_id}")

        uid = client_obj.get("id") or client_obj.get("uuid")
        if not uid:
            raise XuiError(f"Client {email} has no uuid/id in inbound {inbound_id}")

        return build_vless(uid, inbound, fake_id, tag, transport=transport, email=email)


async def create_xui_client(fake_id: int, expiry_ts: int, tag: str, plan: str, transport: str):
    await _check_xui_cert_fingerprint()
    transport = str(transport).lower()
    inbound_id = get_inbound_id_for_plan_transport(plan, transport)

    async with _build_xui_http_client() as client:
        await xui_login(client)
        inbound = await get_inbound(client, inbound_id)

        uid = str(uuid.uuid4())
        subid = uuid.uuid4().hex[:16]
        email = build_xui_email(fake_id, transport)

        network = str((json.loads(inbound["streamSettings"])).get("network") or transport).lower()
        flow = "xtls-rprx-vision" if network == TRANSPORT_TCP else ""

        client_js = {
            "id": uid,
            "email": email,
            "enable": True,
            "expiryTime": expiry_ts,
            "limitIp": 0,
            "totalGB": 0,
            "tgId": 0,
            "reset": 0,
            "subId": subid,
            "flow": flow,
        }

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
        except Exception:
            pass

        vless = build_vless(uid, inbound, fake_id, tag, transport=transport, email=email)

        return {
            "uuid": uid,
            "subId": subid,
            "email": email,
            "vless": vless,
            "transport": transport,
            "inbound_id": inbound_id,
        }


async def create_client_for_user(fake_id: int, days: int, transport: str):
    expiry_ts = int(time.time() * 1000 + days * 86400 * 1000)
    return await create_xui_client(
        fake_id=fake_id,
        expiry_ts=expiry_ts,
        tag="Plus",
        plan=PLAN_PLUS,
        transport=transport,
    )


async def create_client_for_user_until(fake_id: int, expires_at: datetime, transport: str):
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=timezone.utc)
    expiry_ts = int(expires_at.timestamp() * 1000)

    return await create_xui_client(
        fake_id=fake_id,
        expiry_ts=expiry_ts,
        tag="Plus",
        plan=PLAN_PLUS,
        transport=transport,
    )


async def create_client_inf(fake_id: int, transport: str):
    return await create_xui_client(
        fake_id=fake_id,
        expiry_ts=0,
        tag="Inf",
        plan=PLAN_INF,
        transport=transport,
    )


async def ensure_clients_for_subscription(fake_id: int, expires_at) -> dict[str, dict]:
    created: dict[str, dict] = {}
    for transport in get_supported_transports():
        if expires_at is None:
            created[transport] = await create_client_inf(fake_id, transport=transport)
        else:
            created[transport] = await create_client_for_user_until(fake_id, expires_at=expires_at, transport=transport)
    return created


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
        except Exception:
            pass

        logger.info(
            "Deleted X-UI client email=%s uuid=%s inbound=%s",
            email,
            client_uuid,
            inbound_id,
        )


async def update_xui_client_expiry(email: str, inbound_id: int, expiry_ts: int) -> dict:
    await _check_xui_cert_fingerprint()

    async with _build_xui_http_client() as client:
        await xui_login(client)
        inbound = await get_inbound(client, inbound_id)

        settings_obj = json.loads(inbound["settings"])
        clients = settings_obj.get("clients", [])

        idx = next(
            (i for i, c in enumerate(clients) if str(c.get("email")) == str(email)),
            None,
        )
        if idx is None:
            raise XuiError(f"Client {email} not found in inbound {inbound_id}")

        clients[idx]["expiryTime"] = int(expiry_ts)

        payload = {
            "id": inbound_id,
            "settings": json.dumps({"clients": clients}, ensure_ascii=False),
        }

        last_err: str | None = None
        for url in (
            "/panel/api/inbounds/updateClient",
            f"/panel/api/inbounds/{inbound_id}/updateClient",
        ):
            try:
                resp = await client.post(url, json=payload)
                if resp.status_code != 200:
                    last_err = f"{url} -> {resp.status_code}: {resp.text}"
                    continue
                try:
                    j = resp.json()
                    if isinstance(j, dict) and not j.get("success", True):
                        last_err = f"{url} rejected: {resp.text}"
                        continue
                except Exception:
                    pass

                logger.info(
                    "Updated X-UI client expiry email=%s inbound=%s expiry_ts=%s",
                    email,
                    inbound_id,
                    expiry_ts,
                )
                return clients[idx]
            except Exception as e:
                last_err = f"{url} exception: {e}"

        raise XuiError(last_err or "Failed to updateClient")
