import asyncio
import logging
from collections import Counter
from typing import Optional, Set, Tuple

import discord
from redbot.core import Config, checks, commands
from redbot.core.bot import Red


log = logging.getLogger("red.neuropolimer.accessrolecheck")


class AccessRoleCheck(commands.Cog):
    """Keep one visual access role synchronized with a set of key roles."""

    __author__ = "neuropolimer"
    __version__ = "1.1.1"

    def __init__(self, bot: Red):
        self.bot = bot
        self.config = Config.get_conf(self, identifier=842731905, force_registration=True)
        self.config.register_guild(access_role_id=None, key_role_ids=[])
        self._startup_task: Optional[asyncio.Task] = None

    async def cog_load(self) -> None:
        self._startup_task = asyncio.create_task(self._startup_sync())

    def cog_unload(self) -> None:
        if self._startup_task and not self._startup_task.done():
            self._startup_task.cancel()

    async def _startup_sync(self) -> None:
        """Synchronize configured guilds after Red has finished connecting."""
        try:
            await self.bot.wait_until_ready()
            await asyncio.sleep(2)

            for guild in self.bot.guilds:
                guild_config = self.config.guild(guild)
                settings = await guild_config.all()

                access_role_id = settings["access_role_id"]
                access_role = guild.get_role(access_role_id) if access_role_id else None
                if access_role_id and access_role is None:
                    await guild_config.access_role_id.set(None)
                    access_role_id = None

                raw_key_ids = [int(role_id) for role_id in settings["key_role_ids"]]
                valid_key_ids = [role_id for role_id in raw_key_ids if guild.get_role(role_id)]
                if valid_key_ids != raw_key_ids:
                    await guild_config.key_role_ids.set(valid_key_ids)

                if access_role is None:
                    continue

                if valid_key_ids:
                    result = await self._sync_guild(guild)
                else:
                    result = await self._remove_role_from_all(
                        guild,
                        access_role,
                        reason="AccessRoleCheck: no valid key roles remain after startup",
                    )

                log.info(
                    "Startup sync for %s (%s): %s",
                    guild.name,
                    guild.id,
                    dict(result[0]),
                )
        except asyncio.CancelledError:
            return
        except Exception:
            log.exception("Unexpected error during startup synchronization")

    async def _prepare_member_cache(self, guild: discord.Guild) -> bool:
        """Try to ensure that guild.members contains every member.

        Returns True when the cache appears complete. A complete sync requires the
        Server Members intent when Discord does not already provide the full cache.
        """
        expected = guild.member_count
        if expected is None or len(guild.members) >= expected:
            return True

        try:
            await guild.chunk(cache=True)
        except (discord.HTTPException, asyncio.TimeoutError, RuntimeError):
            log.warning(
                "Could not chunk guild %s (%s); member sync may be incomplete",
                guild.name,
                guild.id,
            )

        expected = guild.member_count
        return expected is None or len(guild.members) >= expected

    def _can_manage_access_role(self, guild: discord.Guild, role: discord.Role) -> bool:
        me = guild.me
        if me is None:
            return False
        if not me.guild_permissions.manage_roles:
            return False
        if role == guild.default_role or role.managed:
            return False
        return role < me.top_role

    def _can_manage_member(self, member: discord.Member) -> bool:
        guild = member.guild
        me = guild.me
        if me is None:
            return False
        if member.id == guild.owner_id:
            return False
        return member.top_role < me.top_role

    async def _configured_roles(
        self, guild: discord.Guild
    ) -> Tuple[Optional[discord.Role], Set[int], Set[int]]:
        settings = await self.config.guild(guild).all()
        access_role_id = settings["access_role_id"]
        key_role_ids = {int(role_id) for role_id in settings["key_role_ids"]}

        access_role = guild.get_role(access_role_id) if access_role_id else None
        valid_key_ids = {role_id for role_id in key_role_ids if guild.get_role(role_id)}
        stale_key_ids = key_role_ids - valid_key_ids
        return access_role, valid_key_ids, stale_key_ids

    async def _sync_member_with_settings(
        self,
        member: discord.Member,
        access_role: discord.Role,
        key_role_ids: Set[int],
        *,
        reason: str,
    ) -> str:
        if member.bot:
            return "skipped"

        has_key = any(role.id in key_role_ids for role in member.roles)
        has_access = access_role in member.roles

        if has_key == has_access:
            return "unchanged"

        if not self._can_manage_access_role(member.guild, access_role):
            return "failed"
        if not self._can_manage_member(member):
            return "failed"

        try:
            if has_key:
                await member.add_roles(access_role, reason=reason)
                return "added"

            await member.remove_roles(access_role, reason=reason)
            return "removed"
        except (discord.Forbidden, discord.HTTPException):
            log.exception(
                "Failed to synchronize access role for %s (%s) in guild %s (%s)",
                member,
                member.id,
                member.guild.name,
                member.guild.id,
            )
            return "failed"

    async def _sync_member(self, member: discord.Member, *, reason: str) -> str:
        access_role, key_role_ids, _ = await self._configured_roles(member.guild)
        if access_role is None or not key_role_ids:
            return "unconfigured"

        return await self._sync_member_with_settings(
            member,
            access_role,
            key_role_ids,
            reason=reason,
        )

    async def _sync_guild(self, guild: discord.Guild) -> Tuple[Counter, bool]:
        access_role, key_role_ids, _ = await self._configured_roles(guild)
        counts: Counter = Counter()

        if access_role is None or not key_role_ids:
            counts["unconfigured"] += 1
            return counts, True

        cache_complete = await self._prepare_member_cache(guild)

        for member in list(guild.members):
            result = await self._sync_member_with_settings(
                member,
                access_role,
                key_role_ids,
                reason="AccessRoleCheck: full guild synchronization",
            )
            counts[result] += 1

        return counts, cache_complete

    async def _remove_role_from_all(
        self,
        guild: discord.Guild,
        role: discord.Role,
        *,
        reason: str,
    ) -> Tuple[Counter, bool]:
        counts: Counter = Counter()
        cache_complete = await self._prepare_member_cache(guild)

        for member in list(guild.members):
            if member.bot:
                counts["skipped"] += 1
                continue
            if role not in member.roles:
                counts["unchanged"] += 1
                continue
            if not self._can_manage_access_role(guild, role) or not self._can_manage_member(member):
                counts["failed"] += 1
                continue

            try:
                await member.remove_roles(role, reason=reason)
                counts["removed"] += 1
            except (discord.Forbidden, discord.HTTPException):
                counts["failed"] += 1
                log.exception(
                    "Failed to remove role %s (%s) from %s (%s) in guild %s (%s)",
                    role.name,
                    role.id,
                    member,
                    member.id,
                    guild.name,
                    guild.id,
                )

        return counts, cache_complete

    async def _remove_access_from_all(self, guild: discord.Guild) -> Tuple[Counter, bool]:
        settings = await self.config.guild(guild).all()
        access_role_id = settings["access_role_id"]
        access_role = guild.get_role(access_role_id) if access_role_id else None
        counts: Counter = Counter()

        if access_role is None:
            counts["unconfigured"] += 1
            return counts, True

        return await self._remove_role_from_all(
            guild,
            access_role,
            reason="AccessRoleCheck: no key roles remain configured",
        )

    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member) -> None:
        # Invite/onboarding systems can give a member a key role as part of joining.
        # A short delay also covers systems that attach the role immediately after join.
        await asyncio.sleep(2)
        current_member = member.guild.get_member(member.id) or member
        await self._sync_member(
            current_member,
            reason="AccessRoleCheck: member joined with a configured key role",
        )

    @commands.Cog.listener()
    async def on_member_update(self, before: discord.Member, after: discord.Member) -> None:
        before_ids = {role.id for role in before.roles}
        after_ids = {role.id for role in after.roles}
        changed_role_ids = before_ids.symmetric_difference(after_ids)

        if not changed_role_ids:
            return

        settings = await self.config.guild(after.guild).all()
        access_role_id = settings["access_role_id"]
        key_role_ids = {int(role_id) for role_id in settings["key_role_ids"]}

        if not access_role_id or not key_role_ids:
            return

        relevant_role_ids = set(key_role_ids)
        relevant_role_ids.add(int(access_role_id))
        if changed_role_ids.isdisjoint(relevant_role_ids):
            return

        await self._sync_member(
            after,
            reason="AccessRoleCheck: member roles changed",
        )

    @commands.Cog.listener()
    async def on_guild_role_delete(self, role: discord.Role) -> None:
        guild_config = self.config.guild(role.guild)
        settings = await guild_config.all()

        if settings["access_role_id"] == role.id:
            await guild_config.access_role_id.set(None)
            return

        old_key_role_ids = [int(role_id) for role_id in settings["key_role_ids"]]
        key_role_ids = [role_id for role_id in old_key_role_ids if role_id != role.id]
        if key_role_ids == old_key_role_ids:
            return

        await guild_config.key_role_ids.set(key_role_ids)

        access_role_id = settings["access_role_id"]
        access_role = role.guild.get_role(access_role_id) if access_role_id else None
        if access_role is None:
            return

        if key_role_ids:
            await self._sync_guild(role.guild)
        else:
            await self._remove_role_from_all(
                role.guild,
                access_role,
                reason="AccessRoleCheck: last configured key role was deleted",
            )

    @commands.group(name="accessrole", aliases=["ar"], invoke_without_command=True)
    @commands.guild_only()
    @checks.admin_or_permissions(manage_roles=True)
    async def accessrole(self, ctx: commands.Context) -> None:
        """Configure automatic key-role -> access-role synchronization."""
        await self._send_command_overview(ctx)

    @accessrole.command(name="set")
    async def accessrole_set(self, ctx: commands.Context, role: discord.Role) -> None:
        """Set the visual access role that the cog should grant/remove."""
        if role == ctx.guild.default_role:
            await ctx.send("`@everyone` нельзя использовать как роль доступа.")
            return
        if role.managed:
            await ctx.send("Эта роль управляется Discord или интеграцией и не может выдаваться ботом.")
            return
        if not self._can_manage_access_role(ctx.guild, role):
            await ctx.send(
                "Я не могу управлять этой ролью. Подними роль бота выше неё и проверь право `Manage Roles`."
            )
            return

        settings = await self.config.guild(ctx.guild).all()
        if role.id in {int(role_id) for role_id in settings["key_role_ids"]}:
            await ctx.send("Одна и та же роль не может быть одновременно ролью доступа и ролью-ключом.")
            return

        previous_access_role_id = settings["access_role_id"]
        previous_access_role = (
            ctx.guild.get_role(previous_access_role_id)
            if previous_access_role_id and previous_access_role_id != role.id
            else None
        )

        await self.config.guild(ctx.guild).access_role_id.set(role.id)
        await ctx.send(f"Роль доступа установлена: **{role.name}**. Проверяю существующих участников…")

        if previous_access_role is not None:
            async with ctx.typing():
                old_counts, old_complete = await self._remove_role_from_all(
                    ctx.guild,
                    previous_access_role,
                    reason=f"AccessRoleCheck: access role replaced by {ctx.author} ({ctx.author.id})",
                )
            await ctx.send(
                "Старая роль доступа очищена: "
                f"снято **{old_counts.get('removed', 0)}**, "
                f"ошибок **{old_counts.get('failed', 0)}**."
                + (
                    " Кэш участников был неполным."
                    if not old_complete
                    else ""
                )
            )

        if settings["key_role_ids"]:
            async with ctx.typing():
                counts, complete = await self._sync_guild(ctx.guild)
            await ctx.send(self._format_sync_result(counts, complete))
        else:
            await ctx.send("Добавь хотя бы одну роль-ключ командой `accessrole addkey`.")

    @accessrole.command(name="unset")
    async def accessrole_unset(self, ctx: commands.Context) -> None:
        """Forget the configured access role without changing members."""
        await self.config.guild(ctx.guild).access_role_id.set(None)
        await ctx.send("Роль доступа сброшена в настройках. Уже выданные роли участников не изменялись.")

    @accessrole.command(name="addkey")
    async def accessrole_addkey(self, ctx: commands.Context, role: discord.Role) -> None:
        """Add a role that should imply the access role."""
        if role == ctx.guild.default_role:
            await ctx.send("`@everyone` нельзя использовать как роль-ключ.")
            return

        guild_config = self.config.guild(ctx.guild)
        settings = await guild_config.all()
        access_role_id = settings["access_role_id"]
        key_role_ids = [int(role_id) for role_id in settings["key_role_ids"]]

        if access_role_id == role.id:
            await ctx.send("Роль доступа нельзя одновременно использовать как роль-ключ.")
            return
        if role.id in key_role_ids:
            await ctx.send(f"**{role.name}** уже находится в списке ролей-ключей.")
            return

        key_role_ids.append(role.id)
        await guild_config.key_role_ids.set(key_role_ids)
        await ctx.send(f"Добавлена роль-ключ **{role.name}**. Проверяю существующих участников…")

        if access_role_id:
            async with ctx.typing():
                counts, complete = await self._sync_guild(ctx.guild)
            await ctx.send(self._format_sync_result(counts, complete))
        else:
            await ctx.send("Сначала установи роль доступа командой `accessrole set`.")

    @accessrole.command(name="removekey", aliases=["delkey"])
    async def accessrole_removekey(self, ctx: commands.Context, role: discord.Role) -> None:
        """Remove a configured key role."""
        guild_config = self.config.guild(ctx.guild)
        settings = await guild_config.all()
        key_role_ids = [int(role_id) for role_id in settings["key_role_ids"]]

        if role.id not in key_role_ids:
            await ctx.send(f"**{role.name}** не находится в списке ролей-ключей.")
            return

        key_role_ids.remove(role.id)
        await guild_config.key_role_ids.set(key_role_ids)
        await ctx.send(f"Роль-ключ **{role.name}** удалена. Пересчитываю доступ…")

        if not settings["access_role_id"]:
            return

        async with ctx.typing():
            if key_role_ids:
                counts, complete = await self._sync_guild(ctx.guild)
            else:
                counts, complete = await self._remove_access_from_all(ctx.guild)
        await ctx.send(self._format_sync_result(counts, complete))

    @accessrole.command(name="clear")
    async def accessrole_clear_keys(self, ctx: commands.Context) -> None:
        """Remove all key roles and remove the access role from qualifying members."""
        await self.config.guild(ctx.guild).key_role_ids.set([])
        await ctx.send("Все роли-ключи удалены из настроек. Снимаю роль доступа с участников…")

        async with ctx.typing():
            counts, complete = await self._remove_access_from_all(ctx.guild)
        await ctx.send(self._format_sync_result(counts, complete))

    @accessrole.command(name="list", aliases=["status"])
    async def accessrole_list(self, ctx: commands.Context) -> None:
        """Show the current configuration."""
        await self._send_status(ctx)

    @accessrole.command(name="sync")
    async def accessrole_sync(
        self,
        ctx: commands.Context,
        member: Optional[discord.Member] = None,
    ) -> None:
        """Synchronize one member, or every existing member when omitted."""
        access_role, key_role_ids, _ = await self._configured_roles(ctx.guild)
        if access_role is None:
            await ctx.send("Роль доступа ещё не настроена. Используй `accessrole set @роль`.")
            return
        if not key_role_ids:
            await ctx.send("Нет настроенных ролей-ключей. Используй `accessrole addkey @роль`.")
            return

        if member is not None:
            result = await self._sync_member(
                member,
                reason=f"AccessRoleCheck: manual sync requested by {ctx.author} ({ctx.author.id})",
            )
            messages = {
                "added": "Роль доступа выдана.",
                "removed": "Роль доступа снята.",
                "unchanged": "У участника уже правильный набор ролей.",
                "failed": "Не удалось изменить роль. Проверь иерархию ролей и право `Manage Roles`.",
                "skipped": "Боты пропускаются.",
                "unconfigured": "Cog настроен не полностью.",
            }
            await ctx.send(messages.get(result, result))
            return

        await ctx.send("Проверяю всех существующих участников…")
        async with ctx.typing():
            counts, complete = await self._sync_guild(ctx.guild)
        await ctx.send(self._format_sync_result(counts, complete))

    async def _send_command_overview(self, ctx: commands.Context) -> None:
        prefix = ctx.clean_prefix
        command = ctx.invoked_with or "accessrole"

        text = (
            "**AccessRoleCheck — команды**\n"
            f"`{prefix}{command} set @роль` — установить общую роль доступа.\n"
            f"`{prefix}{command} addkey @роль` — добавить роль-ключ.\n"
            f"`{prefix}{command} removekey @роль` — удалить роль-ключ.\n"
            f"`{prefix}{command} clear` — удалить все роли-ключи.\n"
            f"`{prefix}{command} unset` — сбросить роль доступа в настройках.\n"
            f"`{prefix}{command} list` — показать текущий конфиг.\n"
            f"`{prefix}{command} sync` — пересчитать всех участников.\n"
            f"`{prefix}{command} sync @участник` — пересчитать одного участника.\n\n"
            "Логика: есть хотя бы одна роль-ключ → роль доступа выдаётся; "
            "не осталось ни одной роли-ключа → роль доступа снимается."
        )
        await ctx.send(text)

    async def _send_status(self, ctx: commands.Context) -> None:
        settings = await self.config.guild(ctx.guild).all()
        access_role_id = settings["access_role_id"]
        access_role = ctx.guild.get_role(access_role_id) if access_role_id else None

        key_role_ids = [int(role_id) for role_id in settings["key_role_ids"]]
        existing_key_roles = [ctx.guild.get_role(role_id) for role_id in key_role_ids]
        existing_key_roles = [role for role in existing_key_roles if role is not None]
        stale_ids = [role_id for role_id in key_role_ids if ctx.guild.get_role(role_id) is None]

        access_text = f"**{access_role.name}** (`{access_role.id}`)" if access_role else "не настроена"
        if existing_key_roles:
            key_text = "\n".join(
                f"• **{role.name}** (`{role.id}`)" for role in existing_key_roles
            )
        else:
            key_text = "не настроены"

        text = (
            "**AccessRoleCheck**\n"
            f"Роль доступа: {access_text}\n"
            f"Роли-ключи:\n{key_text}"
        )
        if stale_ids:
            text += "\nУдалённые/не найденные ID: " + ", ".join(f"`{role_id}`" for role_id in stale_ids)

        text += (
            "\n\nЛогика: есть хотя бы одна роль-ключ → роль доступа есть; "
            "нет ни одной роли-ключа → роль доступа снимается."
        )
        await ctx.send(text)

    def _format_sync_result(self, counts: Counter, complete: bool) -> str:
        if counts.get("unconfigured"):
            return "Синхронизация не выполнена: настрой роль доступа и хотя бы одну роль-ключ."

        text = (
            "Синхронизация завершена: "
            f"выдано **{counts.get('added', 0)}**, "
            f"снято **{counts.get('removed', 0)}**, "
            f"без изменений **{counts.get('unchanged', 0)}**, "
            f"ошибок **{counts.get('failed', 0)}**."
        )
        if counts.get("skipped", 0):
            text += f" Ботов пропущено: **{counts['skipped']}**."
        if not complete:
            text += (
                " Внимание: кэш участников выглядит неполным. Для полной проверки включи "
                "Server Members Intent у бота и повтори `accessrole sync`."
            )
        return text
