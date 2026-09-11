import hashlib
import json
import os
import secrets
import time
from urllib.parse import urlencode

import discord
from aiohttp import ClientSession, web

API_BASE = "https://discord.com/api/v10"
CLIENT_ID = os.getenv("DISCORD_CLIENT_ID")
CLIENT_SECRET = os.getenv("DISCORD_CLIENT_SECRET")
REDIRECT_URI = os.getenv("DISCORD_REDIRECT_URI")
PUBLIC_BACKEND_URL = os.getenv("PUBLIC_BACKEND_URL")
WEB_ORIGIN = os.getenv("WEB_ORIGIN", "")

STATE_TTL = 600


def _hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _cors_headers():
    return {
        "Access-Control-Allow-Origin": WEB_ORIGIN,
        "Access-Control-Allow-Headers": "Content-Type",
        "Access-Control-Allow-Methods": "GET,POST,OPTIONS",
        "Vary": "Origin",
    }


def _json(data, status=200):
    return web.json_response(data, status=status, headers=_cors_headers())


def _configured():
    return all((CLIENT_ID, CLIENT_SECRET, REDIRECT_URI, PUBLIC_BACKEND_URL, WEB_ORIGIN))


def _allowed_roles(guild):
    me = guild.me
    if me is None:
        return []
    return [
        role
        for role in guild.roles
        if role.id != guild.default_role.id
        and not role.managed
        and role < me.top_role
    ]


def create_app(bot):
    app = web.Application(client_max_size=1024 * 1024)

    async def options(_request):
        return web.Response(status=204, headers=_cors_headers())

    async def health(_request):
        return _json({"ok": True, "oauth_configured": _configured()})

    async def roles(request):
        try:
            payload = await request.json()
        except (json.JSONDecodeError, web.ContentTypeError):
            return _json({"error": "invalid_json"}, 400)
        key = str(payload.get("key", "")).strip()
        if not key:
            return _json({"error": "recovery_key_required"}, 400)
        row = bot.get_recovery_key(_hash(key))
        if row is None:
            return _json({"error": "invalid_or_expired_key"}, 403)
        guild = bot.get_guild(row["guild_id"])
        if guild is None:
            return _json({"error": "guild_unavailable"}, 503)
        if not bot.user:
            return _json({"error": "bot_not_ready"}, 503)
        if not guild.me or not guild.me.guild_permissions.create_instant_invite:
            return _json({"error": "bot_missing_create_instant_invite"}, 503)
        role_list = [{"id": str(r.id), "name": r.name} for r in _allowed_roles(guild)]
        return _json({"guild_id": str(guild.id), "guild_name": guild.name, "roles": role_list})

    async def start(request):
        if not _configured():
            return _json({"error": "oauth_not_configured"}, 503)
        try:
            payload = await request.json()
        except (json.JSONDecodeError, web.ContentTypeError):
            return _json({"error": "invalid_json"}, 400)
        key = str(payload.get("key", "")).strip()
        role_id = str(payload.get("role_id", "")).strip()
        if not key or not role_id.isdigit():
            return _json({"error": "key_and_role_required"}, 400)
        row = bot.get_recovery_key(_hash(key))
        if row is None:
            return _json({"error": "invalid_or_expired_key"}, 403)
        guild = bot.get_guild(row["guild_id"])
        if guild is None:
            return _json({"error": "guild_unavailable"}, 503)
        role = guild.get_role(int(role_id))
        if role is None or role not in _allowed_roles(guild):
            return _json({"error": "role_not_assignable"}, 400)
        state = secrets.token_urlsafe(32)
        bot.save_oauth_state(_hash(state), row["key_hash"], guild.id, role.id, int(time.time()) + STATE_TTL)
        params = {
            "response_type": "code",
            "client_id": CLIENT_ID,
            "scope": "identify guilds.join",
            "state": state,
            "redirect_uri": REDIRECT_URI,
            "prompt": "consent",
        }
        return _json({"authorization_url": "https://discord.com/oauth2/authorize?" + urlencode(params)})

    async def callback(request):
        error = request.query.get("error")
        if error:
            return web.Response(text="Discord authorization was cancelled.", status=400)
        code = request.query.get("code")
        state = request.query.get("state")
        if not code or not state:
            return web.Response(text="Missing OAuth2 code/state.", status=400)
        state_row = bot.consume_oauth_state(_hash(state))
        if state_row is None or state_row["expires_at"] < int(time.time()):
            return web.Response(text="The authorization session expired or is invalid.", status=400)

        async with ClientSession() as session:
            token_data = {
                "client_id": CLIENT_ID,
                "client_secret": CLIENT_SECRET,
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": REDIRECT_URI,
            }
            async with session.post(f"{API_BASE}/oauth2/token", data=token_data) as response:
                if response.status != 200:
                    return web.Response(text="Discord token exchange failed.", status=502)
                token = await response.json()
            access_token = token.get("access_token")
            if not access_token:
                return web.Response(text="Discord did not return an access token.", status=502)
            headers = {"Authorization": f"Bearer {access_token}"}
            async with session.get(f"{API_BASE}/users/@me", headers=headers) as response:
                if response.status != 200:
                    return web.Response(text="Discord user verification failed.", status=502)
                user = await response.json()

            user_id = str(user.get("id", ""))
            if not user_id:
                return web.Response(text="Discord user ID is missing.", status=502)
            guild = bot.get_guild(state_row["guild_id"])
            if guild is None:
                return web.Response(text="Target guild is unavailable.", status=503)
            role = guild.get_role(state_row["role_id"])
            if role is None or role not in _allowed_roles(guild):
                return web.Response(text="Selected role is no longer assignable.", status=400)
            if not guild.me or not guild.me.guild_permissions.create_instant_invite:
                return web.Response(text="Bot is missing CREATE_INSTANT_INVITE permission.", status=503)

            member = guild.get_member(int(user_id))
            if member is None:
                body = {"access_token": access_token, "roles": [str(role.id)]}
                add_headers = {"Authorization": f"Bot {bot.token}", "Content-Type": "application/json"}
                async with session.put(
                    f"{API_BASE}/guilds/{guild.id}/members/{user_id}",
                    headers=add_headers,
                    json=body,
                ) as response:
                    if response.status not in (201, 204):
                        return web.Response(text="Discord could not restore the guild membership.", status=502)
            else:
                try:
                    await member.add_roles(role, reason="Recovery key OAuth2")
                except (discord.Forbidden, discord.HTTPException):
                    return web.Response(text="The user is already in the server, but the role could not be assigned.", status=502)

        if state_row["key_type"] == "one_time":
            bot.consume_recovery_key(state_row["key_hash"])
        return web.Response(
            text=f"Recovery completed. Discord user {user_id} was restored and the selected role was assigned.",
            status=200,
        )

    app.router.add_route("OPTIONS", "/{tail:.*}", options)
    app.router.add_get("/health", health)
    app.router.add_post("/api/recovery/roles", roles)
    app.router.add_post("/api/recovery/start", start)
    app.router.add_get("/oauth/callback", callback)
    return app


async def start_server(bot):
    if not _configured():
        print("OAuth2 recovery server disabled: set DISCORD_CLIENT_ID, DISCORD_CLIENT_SECRET, DISCORD_REDIRECT_URI, PUBLIC_BACKEND_URL and WEB_ORIGIN.")
        return None
    app = create_app(bot)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", int(os.getenv("PORT", "8080")))
    await site.start()
    print(f"OAuth2 recovery server listening at {PUBLIC_BACKEND_URL}")
    return runner
