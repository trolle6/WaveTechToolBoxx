from disnake.ext import commands, tasks
import disnake
from datetime import datetime, timedelta

class StatisticCog(commands.Cog):
    def __init__(self, bot, config):
        self.bot = bot
        self.config = config
        self.message_count = 0
        self.stats_message = None  # This will hold the stats message object
        self.update_stats.start()

    @tasks.loop(minutes=1)  # Loop every hour
    async def update_stats(self):
        guild_id = int(self.config['discord']['guild_id'])
        stats_channel_id = int(self.config['discord']['stats_channel_id'])

        guild = self.bot.get_guild(guild_id)
        if not guild:
            return

        stats_channel = guild.get_channel(stats_channel_id)
        if not stats_channel:
            return

        if self.stats_message is None:
            self.stats_message = (await stats_channel.history(limit=1).flatten())[0]

        total_members = guild.member_count
        online_members = sum(1 for member in guild.members if member.status != disnake.Status.offline)
        voice_channel_users = sum(1 for vc in guild.voice_channels for _ in vc.members)
        text_channels = len(guild.text_channels)
        voice_channels = len(guild.voice_channels)
        num_roles = len(guild.roles)
        num_emojis = len(guild.emojis)
        newest_member = guild.members[-1].name if guild.members else "N/A"

        stats_text = f"👥 **Total Members:** {total_members}\n" \
                     f"🟢 **Online Members:** {online_members}\n" \
                     f"💬 **Messages in last 24h:** {self.message_count}\n" \
                     f"🔊 **Voice Channel Users:** {voice_channel_users}\n" \
                     f"📝 **Text Channels:** {text_channels}\n" \
                     f"🔊 **Voice Channels:** {voice_channels}\n" \
                     f"🌈 **Roles:** {num_roles}\n" \
                     f"😀 **Emojis:** {num_emojis}\n" \
                     f"🆕 **Newest Member:** {newest_member}"

        await self.stats_message.edit(content=stats_text)

    @commands.Cog.listener()
    async def on_message(self, message):
        if message.guild.id == int(self.config['discord']['guild_id']):
            self.message_count += 1

    @update_stats.before_loop
    async def before_update_stats(self):
        await self.bot.wait_until_ready()


def setup(bot):
    bot.add_cog(StatisticCog(bot, bot.config))
