from aioify import aioify
from discord.ext import commands, tasks
import aiohttp
import aiosqlite
import asyncio
import discord
import json
import os
import shutil


class Events(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.os = aioify(os, name='os')
        self.shutil = aioify(shutil, name='shutil')
        self.utils = self.bot.get_cog('Utils')
        self.auto_clean_db.start()
        self.auto_invalid_device_check.start()

    @tasks.loop(minutes=5)
    async def auto_clean_db(self) -> None:
        async with aiosqlite.connect('Data/autotss.db') as db, db.execute('SELECT devices from autotss') as cursor:
            data = await cursor.fetchall()

        for user_devices in data:
            devices = json.loads(user_devices[0])
            if devices == list():
                async with aiosqlite.connect('Data/autotss.db') as db:
                    await db.execute('DELETE FROM autotss WHERE devices = ?', (user_devices[0],))
                    await db.commit()

    @auto_clean_db.before_loop
    async def before_auto_clean_db(self) -> None:
        await self.bot.wait_until_ready()
        await asyncio.sleep(3) # If first run, give on_ready() some time to create the database

    @tasks.loop(hours=72)
    async def auto_invalid_device_check(self) -> None: # If any users are saving SHSH blobs for A12+ devices without using custom apnonces, attempt to DM them saying they need to re-add the device
        async with aiosqlite.connect('Data/autotss.db') as db, db.execute('SELECT * FROM autotss') as cursor:
            data = await cursor.fetchall()

        if len(data) == 0:
            return

        invalid_devices = dict()
        async with aiohttp.ClientSession() as session:
            for userinfo in data:
                userid = userinfo[0]
                devices = json.loads(userinfo[1])
                invalid_devices[userid] = list()

                for device in devices:
                    cpid = await self.utils.get_cpid(session, device['identifier'], device['boardconfig'])
                    if (device['apnonce'] is not None) and (await self.utils.check_apnonce(cpid, device['apnonce']) == False):
                        invalid_devices[userid].append(device)
                        continue

                    if (device['generator'] is not None) and (await self.utils.check_generator(device['generator']) == False):
                        invalid_devices[userid].append(device)
                        continue

                    if (32800 <= cpid < 35072) and (device['apnonce'] is None):
                        invalid_devices[userid].append(device)

        for userid in [x for x in invalid_devices.keys() if len(invalid_devices[x]) > 0]:
            embed = discord.Embed(title='Hey!')
            embed.description = 'One or more of your devices were added incorrectly to AutoTSS, and are saving **invalid SHSH blobs**. \
                To fix this, remove these devices then re-add them with custom apnonces:'

            for device in invalid_devices[userid]:
                device_info = [
                    f"Device Identifier: `{device['identifier']}`",
                    f"ECID: `{device['ecid']}`",
                    f"Boardconfig: `{device['boardconfig']}`",
                    f"SHSH Blobs saved: **{len(device['saved_blobs'])}**"
                ]

                if device['generator'] is not None:
                    device_info.insert(-1, f"Custom generator: `{device['generator']}`")

                if device['apnonce'] is not None:
                    device_info.insert(-1, f"Custom ApNonce: `{device['apnonce']}`")

                embed.add_field(name=f"**{device['name']}**", value='\n'.join(device_info))

            user = await self.bot.fetch_user(userid)

            try:
                await user.send(embed=embed)
            except: # The device is already saving invalid blobs, so no point in continuing to save blobs for it if we can't contact the user about it.
                await self.shutil.rmtree(f"Data/Blobs/{device['ecid']}")

                async with aiosqlite.connect('Data/autotss.db') as db:
                    async with db.execute('SELECT devices FROM autotss WHERE user = ?', (userid,)) as cursor:
                        devices = json.loads((await cursor.fetchone())[0])

                    devices.pop(next(devices.index(x) for x in devices if x['ecid'] == device['ecid']))

                    await db.execute('UPDATE autotss SET devices = ? WHERE user = ?', (json.dumps(devices), userid))
                    await db.commit()

    @auto_invalid_device_check.before_loop
    async def before_invalid_device_check(self) -> None:
        await self.bot.wait_until_ready()
        await asyncio.sleep(3) # If first run, give on_ready() some time to create the database

    @commands.Cog.listener()
    async def on_guild_join(self, guild: discord.Guild) -> None:
        await self.bot.wait_until_ready()

        async with aiosqlite.connect('Data/autotss.db') as db:
            async with db.execute('SELECT prefix from prefix WHERE guild = ?', (guild.id,)) as cursor:
                if await cursor.fetchone() is not None:
                    await db.execute('DELETE from prefix where guild = ?', (guild.id,))
                    await db.commit()

            await db.execute('INSERT INTO prefix(guild, prefix) VALUES(?,?)', (guild.id, 'b!'))
            await db.commit()


        embed = await self.utils.info_embed('b!', self.bot.user)
        for channel in guild.text_channels:
            try:
                await channel.send(embed=embed)
                break
            except:
                pass

    @commands.Cog.listener()
    async def on_guild_remove(self, guild: discord.Guild) -> None:
        await self.bot.wait_until_ready()

        async with aiosqlite.connect('Data/autotss.db') as db:
            await db.execute('DELETE from prefix where guild = ?', (guild.id,))
            await db.commit()

    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member) -> None:
        await self.bot.wait_until_ready()

        async with aiosqlite.connect('Data/autotss.db') as db, db.execute('SELECT * from autotss WHERE user = ?', (member.id,)) as cursor:
            data = await cursor.fetchone()

        if data is None:
            return

        async with aiosqlite.connect('Data/autotss.db') as db:
            await db.execute('UPDATE autotss SET enabled = ? WHERE user = ?', (True, member.id))
            await db.commit()

        await self.utils.update_device_count()

    @commands.Cog.listener()
    async def on_member_remove(self, member: discord.Member) -> None:
        await self.bot.wait_until_ready()

        async with aiosqlite.connect('Data/autotss.db') as db, db.execute('SELECT * from autotss WHERE user = ?', (member.id,)) as cursor:
            data = await cursor.fetchone()

        if data is None:
            return

        if len(member.mutual_guilds) == 0:
            async with aiosqlite.connect('Data/autotss.db') as db:
                await db.execute('UPDATE autotss SET enabled = ? WHERE user = ?', (False, member.id))
                await db.commit()

            await self.utils.update_device_count()

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message) -> None:
        await self.bot.wait_until_ready()

        if message.channel.type == discord.ChannelType.private:
            return

        if message.content.replace(' ', '').replace('!', '') == self.bot.user.mention:
            whitelist = await self.utils.get_whitelist(message.guild.id)
            if (whitelist is not None) and (whitelist.id != message.channel.id):
                return

            prefix = await self.utils.get_prefix(message.guild.id)

            embed = discord.Embed(title='AutoTSS', description=f'My prefix is `{prefix}`. To see all of my commands, run `{prefix}help`.')
            embed.set_footer(text=message.author.name, icon_url=message.author.avatar_url_as(static_format='png'))
            try:
                await message.channel.send(embed=embed)
            except:
                pass

    @commands.Cog.listener()
    async def on_ready(self) -> None:
        await self.os.makedirs('Data', exist_ok=True)

        async with aiosqlite.connect('Data/autotss.db') as db:
            await db.execute('''
                CREATE TABLE IF NOT EXISTS autotss(
                user INTEGER,
                devices JSON,
                enabled BOOLEAN
                )
                ''')
            await db.commit()

            await db.execute('''
                CREATE TABLE IF NOT EXISTS prefix(
                guild INTEGER,
                prefix TEXT
                )
                ''')
            await db.commit()

            await db.execute('''
                CREATE TABLE IF NOT EXISTS whitelist(
                guild INTEGER,
                channel INTEGER,
                enabled BOOLEAN
                )
                ''')
            await db.commit()

        await self.utils.update_device_count()
        print('AutoTSS is now online.')

    @commands.Cog.listener()
    async def on_command_error(self, ctx: commands.Context, error) -> None:
        await self.bot.wait_until_ready()

        embed = discord.Embed(title='Error')

        if ctx.message.channel.type == discord.ChannelType.private:
            embed.description = 'AutoTSS cannot be used in DMs. Please use AutoTSS in a Discord server.'
            await ctx.send(embed=embed)
            return

        whitelist = await self.utils.get_whitelist(ctx.guild.id)
        if (whitelist is not None) and (whitelist.id != ctx.channel.id):
            embed = discord.Embed(title='Hey!', description=f'AutoTSS can only be used in {whitelist.mention}.')
            await ctx.send(embed=embed)
            return

        prefix = await self.utils.get_prefix(ctx.guild.id)
        if isinstance(error, commands.CommandNotFound):
            if ctx.prefix.replace('!', '').replace(' ', '') == self.bot.user.mention:
                return

            embed.description = f"That command doesn't exist! Use `{prefix}help` to see all the commands I can run."
            await ctx.send(embed=embed)

        elif isinstance(error, commands.MaxConcurrencyReached):
            embed.description = f"`{prefix + ctx.command.qualified_name}` cannot be ran more than once at the same time!"
            await ctx.send(embed=embed)

        elif isinstance(error, commands.errors.CommandInvokeError):
            if isinstance(error.original, discord.errors.Forbidden):
                embed.description = f"I don't have the proper permissions to run correctly! \
                    Please ping an Administrator and tell them to kick & re-invite me using \
                    [this]({await self.utils.invite}) link to fix this issue."

                message_sent = False
                for channel in ctx.guild.text_channels:
                    try:
                        await channel.send(embed=embed)
                        message_sent = True
                        break
                    except:
                        pass

                if message_sent:
                    return

                try:
                    embed.description = f"I don't have the proper permissions to run correctly! \
                        Please kick me from `{ctx.guild.name}` & re-invite me using \
                        [this]({await self.utils.invite}) link to fix this issue."

                    await ctx.guild.owner.send(embed=embed)
                except: # We can't tell the user to tell an admin to fix our permissions, we can't DM the owner to fix it, we might as well leave.
                    await ctx.guild.leave()

            else:
                raise error

        elif isinstance(error, commands.ChannelNotFound):
            embed = discord.Embed(title='Error', description='That channel does not exist.')
            await ctx.send(embed=embed)

        elif (isinstance(error, commands.errors.NotOwner)) or \
        (isinstance(error, commands.MissingPermissions)):
            return

        else:
            raise error


def setup(bot):
    bot.add_cog(Events(bot))
