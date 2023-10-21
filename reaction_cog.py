from disnake.ext import commands
import random

class ReactionCog(commands.Cog):
    def __init__(self, bot, target_message_id):
        self.bot = bot
        self.target_message_id = target_message_id  # Initialize with the target_message_id
        self.reaction_users_dict = {}  # Initialize an empty dictionary for reaction users
        self.reaction_users_dict[self.target_message_id] = []  # Initialize an empty list for the target message

    @commands.Cog.listener()
    async def on_raw_reaction_add(self, payload):
        if payload.message_id == self.target_message_id:
            user = self.bot.get_user(payload.user_id)
            if user and user != self.bot.user and user not in self.reaction_users_dict[self.target_message_id]:
                self.reaction_users_dict[self.target_message_id].append(user)

def setup(bot):
    # Access the target_message_id from the bot's config attribute
    target_message_id = bot.target_message_id
    bot.add_cog(ReactionCog(bot, target_message_id))
