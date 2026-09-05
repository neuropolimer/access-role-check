import asyncio
import logging
from collections import Counter
from typing import Dict, Optional, Set, Tuple

import discord
from redbot.core import Config, checks, commands
from redbot.core.bot import Red


log = logging.getLogger("red.neuropolimer.accessrolecheck")


class AccessRoleCheck(commands.Cog):
    """Synchronize multiple independent access roles with their own key roles."""

    __author__ = "neuropolimer"
    __version__ = "2.0.0"

    def __init__(self, bot: Red):
        self.bot = bot
        self.config = Config.get_conf(self, identifier=842731905, force_registration=True)
        # v1.x fields are kept only for automatic migration.
        self.config.register_guild(
            access_role_id=None,
            key_role_ids=[],
            profiles={},
        )
        self._startup_task: Optional[asyncio.Task] = None

    async def cog_load(self) -> None:
        self._startup_task = asyncio.create_task(self._startup_sync())

    def cog_unload(self) -> None:
        if self._startup_task and not self._startup_task.done():
            self._startup_task.cancel()

    async def _startup_sync(self) -> None:
        try:
            await self.bot.wait_until_ready()
            await asyncio.sleep(2)

            for guild in self.bot.guilds:
                await self._migrate_legacy_config(guild)
                profiles, changed = await self._configured_profiles(guild)
                if changed:
                    log.info("Cleaned stale AccessRoleCheck config in %s (%s)", guild.name, guild.id)

                if not profiles:
                    continue

                counts, complete = await self._sync_guild(guild, profiles=profiles)
                log.info(
                    "Startup sync for %s (%s): %s; cache_complete=%s",
                    guild.name,
                    guild.id,
                    dict(counts),
                    complete,
                )
        except asyncio.CancelledError:
            return
        except Exception:
            log.exception("Unexpected error during startup synchronization")

    async def _migrate_legacy_config(self, guild: discord.Guild) -> bool:
        """Move the old single access-role config into the v2 profiles structure."""
        guild_config = self.config.guild(guild)
        settings = await guild_config.all()

        access_role_id = settings.get("access_role_id")
        old_key_ids = [int(role_id) for role_id in settings.get("key_role_ids", [])]
        if not access_role_id:
            return False

        profiles = dict(settings.get("profiles", {}))
        target_key = str(int(access_role_id))
        current = dict(profiles.get(target_key, {}))
        merged = {int(role_id) for role_id in current.get("key_role_ids", [])}
        merged.update(old_key_ids)
        profiles[target_key] = {"key_role_ids": sorted(merged)}

        await guild_config.profiles.set(profiles)
        await guild_config.access_role_id.set(None)
        await guild_config.key_role_ids.set([])

        log.info(
            "Migrated legacy AccessRoleCheck config in %s (%s): target=%s, keys=%s",
            guild.name,
            guild.id,
            access_role_id,
            sorted(merged),
        )
        return True

    async def _prepare_member_cache(self, guild: discord.Guild) -> bool:
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

    def _can_manage_role(self, guild: discord.Guild, role: discord.Role) -> bool:
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

    async def _configured_profiles(
        self, guild: discord.Guild
    ) -> Tuple[Dict[int, Set[int]], bool]:
        """Return valid target-role -> key-role mappings and clean stale IDs."""
        raw_profiles = await self.config.guild(guild).profiles()
        valid: Dict[int, Set[int]] = {}
        cleaned: Dict[str, Dict[str, list[int]]] = {}
        changed = False

        for raw_target_id, data in dict(raw_profiles).items():
            try:
                target_id = int(raw_target_id)
            except (TypeError, ValueError):
                changed = True
                continue

            target_role = guild.get_role(target_id)
            if target_role is None:
                changed = True
                continue

            raw_keys = {
                int(role_id)
                for role_id in dict(data or {}).get("key_role_ids", [])
            }
            key_ids = {
                role_id
                for role_id in raw_keys
                if role_id != target_id and guild.get_role(role_id) is not None
            }
            if key_ids != raw_keys:
                changed = True

            valid[target_id] = key_ids
            cleaned[str(target_id)] = {"key_role_ids": sorted(key_ids)}

        if cleaned != raw_profiles:
            changed = True

        if changed:
            await self.config.guild(guild).profiles.set(cleaned)

        return valid, changed

    async def _sync_member_with_profiles(
        self,
        member: discord.Member,
        profiles: Dict[int, Set[int]],
        *,
        reason: str,
    ) -> Counter:
        counts: Counter = Counter()

        if member.bot:
            counts["skipped"] += len(profiles) or 1
            return counts

        member_role_ids = {role.id for role in member.roles}

        if not self._can_manage_member(member):
            for target_id, key_ids in profiles.items():
                should_have = bool(member_role_ids & key_ids)
                has_target = target_id in member_role_ids
                counts["unchanged" if should_have == has_target else "failed"] += 1
            return counts

        to_add = []
        to_remove = []

        for target_id, key_ids in profiles.items():
            target = member.guild.get_role(target_id)
            if target is None:
                continue

            should_have = bool(member_role_ids & key_ids)
            has_target = target_id in member_role_ids

            if should_have == has_target:
                counts["unchanged"] += 1
                continue

            if not self._can_manage_role(member.guild, target):
                counts["failed"] += 1
                continue

            if should_have:
                to_add.append(target)
            else:
                to_remove.append(target)

        if to_add:
            try:
                await member.add_roles(*to_add, reason=reason)
                counts["added"] += len(to_add)
            except (discord.Forbidden, discord.HTTPException):
                counts["failed"] += len(to_add)
                log.exception(
                    "Failed to add synchronized roles %s to %s (%s) in %s (%s)",
                    [role.id for role in to_add],
                    member,
                    member.id,
                    member.guild.name,
                    member.guild.id,
                )

        if to_remove:
            try:
                await member.remove_roles(*to_remove, reason=reason)
                counts["removed"] += len(to_remove)
            except (discord.Forbidden, discord.HTTPException):
                counts["failed"] += len(to_remove)
                log.exception(
                    "Failed to remove synchronized roles %s from %s (%s) in %s (%s)",
                    [role.id for role in to_remove],
                    member,
                    member.id,
                    member.guild.name,
                    member.guild.id,
                )

        return counts

    async def _sync_member(self, member: discord.Member, *, reason: str) -> Counter:
        await self._migrate_legacy_config(member.guild)
        profiles, _ = await self._configured_profiles(member.guild)
        if not profiles:
            counts: Counter = Counter()
            counts["unconfigured"] += 1
            return counts
        return await self._sync_member_with_profiles(member, profiles, reason=reason)

    async def _sync_guild(
        self,
        guild: discord.Guild,
        *,
        profiles: Optional[Dict[int, Set[int]]] = None,
    ) -> Tuple[Counter, bool]:
        if profiles is None:
            await self._migrate_legacy_config(guild)
            profiles, _ = await self._configured_profiles(guild)

        counts: Counter = Counter()
        if not profiles:
            counts["unconfigured"] += 1
            return counts, True

        cache_complete = await self._prepare_member_cache(guild)

        for member in list(guild.members):
            result = await self._sync_member_with_profiles(
                member,
                profiles,
                reason="AccessRoleCheck: full guild synchronization",
            )
            counts.update(result)

        return counts, cache_complete

    async def _remove_target_from_all(
        self,
        guild: discord.Guild,
        target_role: discord.Role,
        *,
        reason: str,
    ) -> Tuple[Counter, bool]:
        counts: Counter = Counter()
        cache_complete = await self._prepare_member_cache(guild)

        for member in list(guild.members):
            if member.bot:
                counts["skipped"] += 1
                continue
            if target_role not in member.roles:
                counts["unchanged"] += 1
                continue
            if not self._can_manage_role(guild, target_role) or not self._can_manage_member(member):
                counts["failed"] += 1
                continue

            try:
                await member.remove_roles(target_role, reason=reason)
                counts["removed"] += 1
            except (discord.Forbidden, discord.HTTPException):
                counts["failed"] += 1
                log.exception(
                    "Failed to remove target role %s (%s) from %s (%s)",
                    target_role.name,
                    target_role.id,
                    member,
                    member.id,
                )

        return counts, cache_complete

    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member) -> None:
        await asyncio.sleep(2)
        current_member = member.guild.get_member(member.id) or member
        await self._sync_member(
            current_member,
            reason="AccessRoleCheck: member joined",
        )

    @commands.Cog.listener()
    async def on_member_update(self, before: discord.Member, after: discord.Member) -> None:
        before_ids = {role.id for role in before.roles}
        after_ids = {role.id for role in after.roles}
        changed_role_ids = before_ids.symmetric_difference(after_ids)
        if not changed_role_ids:
            return

        await self._migrate_legacy_config(after.guild)
        profiles, _ = await self._configured_profiles(after.guild)
        if not profiles:
            return

        relevant_ids = set(profiles)
        for key_ids in profiles.values():
            relevant_ids.update(key_ids)

        if changed_role_ids.isdisjoint(relevant_ids):
            return

        await self._sync_member_with_profiles(
            after,
            profiles,
            reason="AccessRoleCheck: member roles changed",
        )

    @commands.Cog.listener()
    async def on_guild_role_delete(self, role: discord.Role) -> None:
        await self._migrate_legacy_config(role.guild)
        profiles, _ = await self._configured_profiles(role.guild)
        if profiles:
            await self._sync_guild(role.guild, profiles=profiles)

    @commands.group(name="accessrole", aliases=["ar"], invoke_without_command=True)
    @commands.guild_only()
    @checks.admin_or_permissions(manage_roles=True)
    async def accessrole(self, ctx: commands.Context) -> None:
        """Configure independent target-role/key-role mappings."""
        await self._send_command_overview(ctx)

    @accessrole.command(name="addcategory", aliases=["add", "create"])
    async def accessrole_addcategory(
        self,
        ctx: commands.Context,
        target_role: discord.Role,
        *key_roles: discord.Role,
    ) -> None:
        """Add a managed target role and optionally its initial key roles."""
        if target_role == ctx.guild.default_role:
            await ctx.send("\`@everyone\` нельзя использовать как выдаваемую роль.")
            return
        if target_role.managed:
            await ctx.send("Эта роль управляется Discord или интеграцией.")
            return
        if not self._can_manage_role(ctx.guild, target_role):
            await ctx.send(
                "Я не могу управлять этой ролью. Подними роль бота выше неё и проверь \`Manage Roles\`."
            )
            return
        if any(role.id == target_role.id for role in key_roles):
            await ctx.send("Выдаваемая роль не может быть ключом сама для себя.")
            return

        await self._migrate_legacy_config(ctx.guild)
        guild_config = self.config.guild(ctx.guild)
        profiles = dict(await guild_config.profiles())
        target_key = str(target_role.id)

        existing = dict(profiles.get(target_key, {}))
        key_ids = {int(role_id) for role_id in existing.get("key_role_ids", [])}
        key_ids.update(role.id for role in key_roles)
        profiles[target_key] = {"key_role_ids": sorted(key_ids)}
        await guild_config.profiles.set(profiles)

        if key_roles:
            await ctx.send(
                f"Добавлена категория **{target_role.name}** с ключами: "
                + ", ".join(f"**{role.name}**" for role in key_roles)
                + ". Пересчитываю участников…"
            )
            async with ctx.typing():
                counts, complete = await self._sync_guild(ctx.guild)
            await ctx.send(self._format_sync_result(counts, complete))
        else:
            await ctx.send(
                f"Добавлена категория **{target_role.name}**. "
                f"Теперь добавь ключ: \`{ctx.clean_prefix}accessrole addkey @{target_role.name} @РольКлюч\`."
            )

    @accessrole.command(name="removecategory", aliases=["delcategory", "delete"])
    async def accessrole_removecategory(
        self,
        ctx: commands.Context,
        target_role: discord.Role,
    ) -> None:
        """Remove a managed category and clean its target role from members."""
        await self._migrate_legacy_config(ctx.guild)
        guild_config = self.config.guild(ctx.guild)
        profiles = dict(await guild_config.profiles())
        target_key = str(target_role.id)

        if target_key not in profiles:
            await ctx.send(f"**{target_role.name}** не настроена как категория AccessRoleCheck.")
            return

        profiles.pop(target_key, None)
        await guild_config.profiles.set(profiles)
        await ctx.send(
            f"Категория **{target_role.name}** удалена из настроек. Снимаю её роль с участников…"
        )
        async with ctx.typing():
            counts, complete = await self._remove_target_from_all(
                ctx.guild,
                target_role,
                reason=f"AccessRoleCheck: category removed by {ctx.author} ({ctx.author.id})",
            )
        await ctx.send(self._format_sync_result(counts, complete, allow_unconfigured=False))

    @accessrole.command(name="addkey")
    async def accessrole_addkey(
        self,
        ctx: commands.Context,
        target_role: discord.Role,
        key_role: discord.Role,
    ) -> None:
        """Add a key role to one target-role category."""
        if target_role.id == key_role.id:
            await ctx.send("Выдаваемая роль не может быть ключом сама для себя.")
            return

        await self._migrate_legacy_config(ctx.guild)
        guild_config = self.config.guild(ctx.guild)
        profiles = dict(await guild_config.profiles())
        target_key = str(target_role.id)

        if target_key not in profiles:
            await ctx.send(
                f"Сначала добавь **{target_role.name}** как категорию: "
                f"\`{ctx.clean_prefix}accessrole addcategory @{target_role.name}\`."
            )
            return

        data = dict(profiles[target_key])
        key_ids = {int(role_id) for role_id in data.get("key_role_ids", [])}
        if key_role.id in key_ids:
            await ctx.send(
                f"**{key_role.name}** уже является ключом для **{target_role.name}**."
            )
            return

        key_ids.add(key_role.id)
        profiles[target_key] = {"key_role_ids": sorted(key_ids)}
        await guild_config.profiles.set(profiles)

        await ctx.send(
            f"Ключ **{key_role.name}** добавлен к категории **{target_role.name}**. "
            "Пересчитываю участников…"
        )
        async with ctx.typing():
            counts, complete = await self._sync_guild(ctx.guild)
        await ctx.send(self._format_sync_result(counts, complete))

    @accessrole.command(name="removekey", aliases=["delkey"])
    async def accessrole_removekey(
        self,
        ctx: commands.Context,
        target_role: discord.Role,
        key_role: discord.Role,
    ) -> None:
        """Remove a key role from one target-role category."""
        await self._migrate_legacy_config(ctx.guild)
        guild_config = self.config.guild(ctx.guild)
        profiles = dict(await guild_config.profiles())
        target_key = str(target_role.id)

        if target_key not in profiles:
            await ctx.send(f"Категория **{target_role.name}** не настроена.")
            return

        data = dict(profiles[target_key])
        key_ids = {int(role_id) for role_id in data.get("key_role_ids", [])}
        if key_role.id not in key_ids:
            await ctx.send(
                f"**{key_role.name}** не является ключом для **{target_role.name}**."
            )
            return

        key_ids.remove(key_role.id)
        profiles[target_key] = {"key_role_ids": sorted(key_ids)}
        await guild_config.profiles.set(profiles)

        await ctx.send(
            f"Ключ **{key_role.name}** удалён из категории **{target_role.name}**. "
            "Пересчитываю участников…"
        )
        async with ctx.typing():
            counts, complete = await self._sync_guild(ctx.guild)
        await ctx.send(self._format_sync_result(counts, complete))

    @accessrole.command(name="clear")
    async def accessrole_clear(
        self,
        ctx: commands.Context,
        target_role: Optional[discord.Role] = None,
    ) -> None:
        """Clear keys of one category, or remove all categories when no role is given."""
        await self._migrate_legacy_config(ctx.guild)
        guild_config = self.config.guild(ctx.guild)
        profiles = dict(await guild_config.profiles())

        if target_role is not None:
            target_key = str(target_role.id)
            if target_key not in profiles:
                await ctx.send(f"Категория **{target_role.name}** не настроена.")
                return
            profiles[target_key] = {"key_role_ids": []}
            await guild_config.profiles.set(profiles)
            await ctx.send(
                f"Все ключи категории **{target_role.name}** удалены. "
                "Её выдаваемая роль будет снята у участников."
            )
            async with ctx.typing():
                counts, complete = await self._sync_guild(ctx.guild)
            await ctx.send(self._format_sync_result(counts, complete))
            return

        targets = [
            ctx.guild.get_role(int(target_id))
            for target_id in profiles
            if str(target_id).isdigit()
        ]
        targets = [role for role in targets if role is not None]
        await guild_config.profiles.set({})

        total: Counter = Counter()
        complete = True
        async with ctx.typing():
            for role in targets:
                counts, role_complete = await self._remove_target_from_all(
                    ctx.guild,
                    role,
                    reason=f"AccessRoleCheck: all categories cleared by {ctx.author} ({ctx.author.id})",
                )
                total.update(counts)
                complete = complete and role_complete

        await ctx.send("Все категории AccessRoleCheck удалены.")
        await ctx.send(self._format_sync_result(total, complete, allow_unconfigured=False))

    @accessrole.command(name="list", aliases=["status"])
    async def accessrole_list(self, ctx: commands.Context) -> None:
        """Show every managed target role and its keys."""
        await self._send_status(ctx)

    @accessrole.command(name="sync")
    async def accessrole_sync(
        self,
        ctx: commands.Context,
        member: Optional[discord.Member] = None,
    ) -> None:
        """Synchronize one member or the whole guild."""
        await self._migrate_legacy_config(ctx.guild)
        profiles, _ = await self._configured_profiles(ctx.guild)
        if not profiles:
            await ctx.send("Нет настроенных категорий. Используй \`accessrole addcategory\`.")
            return

        if member is not None:
            counts = await self._sync_member_with_profiles(
                member,
                profiles,
                reason=f"AccessRoleCheck: manual sync requested by {ctx.author} ({ctx.author.id})",
            )
            await ctx.send(self._format_member_sync_result(counts))
            return

        await ctx.send("Проверяю всех существующих участников…")
        async with ctx.typing():
            counts, complete = await self._sync_guild(ctx.guild, profiles=profiles)
        await ctx.send(self._format_sync_result(counts, complete))

    async def _send_command_overview(self, ctx: commands.Context) -> None:
        prefix = ctx.clean_prefix
        command = ctx.invoked_with or "accessrole"

        text = (
            "**AccessRoleCheck — команды**\n"
            f"\`{prefix}{command} addcategory @ВыдаваемаяРоль [@Ключ ...]\` — создать независимую категорию.\n"
            f"\`{prefix}{command} addkey @ВыдаваемаяРоль @Ключ\` — добавить ключ к конкретной категории.\n"
            f"\`{prefix}{command} removekey @ВыдаваемаяРоль @Ключ\` — удалить ключ.\n"
            f"\`{prefix}{command} removecategory @ВыдаваемаяРоль\` — удалить категорию и снять её роль.\n"
            f"\`{prefix}{command} clear @ВыдаваемаяРоль\` — удалить все ключи одной категории.\n"
            f"\`{prefix}{command} clear\` — удалить все категории и снять управляемые роли.\n"
            f"\`{prefix}{command} list\` — показать весь конфиг.\n"
            f"\`{prefix}{command} sync\` — пересчитать всех участников.\n"
            f"\`{prefix}{command} sync @Участник\` — пересчитать одного участника.\n\n"
            "Каждая выдаваемая роль имеет собственный набор ключей и не зависит от остальных."
        )
        await ctx.send(text)

    async def _send_status(self, ctx: commands.Context) -> None:
        await self._migrate_legacy_config(ctx.guild)
        profiles, _ = await self._configured_profiles(ctx.guild)

        if not profiles:
            await ctx.send("**AccessRoleCheck**\nКатегории не настроены.")
            return

        blocks = ["**AccessRoleCheck — независимые категории**"]
        for target_id, key_ids in profiles.items():
            target = ctx.guild.get_role(target_id)
            if target is None:
                continue

            keys = [ctx.guild.get_role(role_id) for role_id in sorted(key_ids)]
            keys = [role for role in keys if role is not None]
            if keys:
                key_text = ", ".join(f"**{role.name}** (\`{role.id}\`)" for role in keys)
            else:
                key_text = "ключей нет"

            blocks.append(
                f"\n**{target.name}** (\`{target.id}\`)\n"
                f"Ключи: {key_text}"
            )

        blocks.append(
            "\nЛогика каждой категории: есть хотя бы один её ключ → выдаваемая роль есть; "
            "не осталось ни одного ключа → выдаваемая роль снимается."
        )
        await ctx.send("\n".join(blocks))

    def _format_member_sync_result(self, counts: Counter) -> str:
        if counts.get("unconfigured"):
            return "AccessRoleCheck не настроен."
        return (
            "Участник пересчитан: "
            f"выдано ролей **{counts.get('added', 0)}**, "
            f"снято **{counts.get('removed', 0)}**, "
            f"без изменений **{counts.get('unchanged', 0)}**, "
            f"ошибок **{counts.get('failed', 0)}**."
        )

    def _format_sync_result(
        self,
        counts: Counter,
        complete: bool,
        *,
        allow_unconfigured: bool = True,
    ) -> str:
        if allow_unconfigured and counts.get("unconfigured"):
            return "Синхронизация не выполнена: категории не настроены."

        text = (
            "Синхронизация завершена: "
            f"выдано ролей **{counts.get('added', 0)}**, "
            f"снято **{counts.get('removed', 0)}**, "
            f"без изменений **{counts.get('unchanged', 0)}**, "
            f"ошибок **{counts.get('failed', 0)}**."
        )
        if counts.get("skipped", 0):
            text += f" Ботов пропущено: **{counts['skipped']}**."
        if not complete:
            text += (
                " Внимание: кэш участников выглядит неполным. Для полной проверки включи "
                "Server Members Intent у бота и повтори \`accessrole sync\`."
            )
        return text
