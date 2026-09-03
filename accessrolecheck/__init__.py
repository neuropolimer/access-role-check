from .accessrolecheck import AccessRoleCheck


async def setup(bot):
    await bot.add_cog(AccessRoleCheck(bot))
