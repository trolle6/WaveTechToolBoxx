from disnake.ext import commands
import random

class ReactionCog(commands.Cog):
    def __init__(self, bot, config):
        self.bot = bot
        self.config = config
        self.target_message_id = int(config['discord'].get('target_message_id', 0))  # Set a default value
        self.reaction_users_dict = {}
        self.reaction_users_dict[self.target_message_id] = []

    @commands.Cog.listener()
    async def on_raw_reaction_add(self, payload):
        print(f"Reaction received in ReactionCog for message {payload.message_id}")
        if payload.message_id == self.target_message_id:
            user = self.bot.get_user(payload.user_id)
            if user and user != self.bot.user and user not in self.reaction_users_dict[self.target_message_id]:
                self.reaction_users_dict[self.target_message_id].append(user)

def setup(bot):
    bot.add_cog(ReactionCog(bot, bot.config))
