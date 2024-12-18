# cogs/SecretSanta_cog.py

import disnake
from disnake.ext import commands
import logging
import random
from rdoclient import RandomOrgClient
import asyncio
import json
from datetime import datetime
import os
import openai  # For ChatGPT API usage
import aiohttp  # For async HTTP requests

async def is_moderator(interaction):
    moderator_role_id = int(interaction.bot.config["discord"]["moderator_role_id"])
    return any(role.id == moderator_role_id for role in interaction.author.roles)

class SecretSantaCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.config = bot.config
        self.logger = bot.logger
        self.random_client = RandomOrgClient(self.config["random_org"]["api_key"])
        self.signed_random_links = []
        self.participants = {}
        self.assignments = {}
        self.pending_questions = {}
        self.active = False
        self.join_closed = False
        self.lock = asyncio.Lock()
        self.data_file = os.path.join(os.path.dirname(__file__), "secret_santa_data.json")
        self.event_type = "Secret"
        self.moderator_channel_id = int(self.config["discord"]["moderator_channel_id"])
        self.announcement_message_id = int(self.config["discord"]["announcement_message_id"])
        self.openai_api_key = self.config.get("openai_api_key")
        openai.api_key = self.openai_api_key
        self.bot.loop.create_task(self.load_assignments())

    def save_assignments(self):
        data = {
            "participants": {str(k): v for k, v in self.participants.items()},
            "assignments": {str(k): v for k, v in self.assignments.items()},
            "pending_questions": self.pending_questions,
            "active": self.active,
            "join_closed": self.join_closed,
            "event_type": self.event_type,
            "signed_random_links": self.signed_random_links,
        }
        try:
            with open(self.data_file, "w") as f:
                json.dump(data, f)
            self.logger.info(f"Secret Santa data saved to {self.data_file}.")
        except Exception as e:
            self.logger.error(f"Error saving Secret Santa data: {e}", exc_info=True)

    async def load_assignments(self):
        await self.bot.wait_until_ready()
        try:
            if os.path.exists(self.data_file):
                with open(self.data_file, "r") as f:
                    data = json.load(f)
                self.logger.info(f"Secret Santa data loaded from {self.data_file}.")
                self.participants = {int(k): v for k, v in data.get("participants", {}).items()}
                self.assignments = {int(k): int(v) for k, v in data.get("assignments", {}).items()}
                self.pending_questions = data.get("pending_questions", {})
                self.active = data.get("active", False)
                self.join_closed = data.get("join_closed", False)
                self.event_type = data.get("event_type", "Secret")
                self.signed_random_links = data.get("signed_random_links", [])
            else:
                self.logger.info(f"No existing Secret Santa data file found at {self.data_file}.")
        except Exception as e:
            self.logger.error(f"Error loading Secret Santa data: {e}", exc_info=True)

    @commands.slash_command(
        name="start_santa",
        description="Starts a Secret Santa event using an existing announcement."
    )
    @commands.check(is_moderator)
    async def start_santa(
        self,
        inter: disnake.ApplicationCommandInteraction,
        event_type: str = commands.Param(
            choices=["Regular", "Secret"],
            description="Choose the type of Secret Santa event."
        ),
    ):
        if self.active:
            await inter.response.send_message(
                "🔔 A Secret Santa event is already active.", ephemeral=True
            )
            return

        self.active = True
        self.join_closed = False
        # Do not reset self.participants
        self.assignments = {}
        self.pending_questions = {}
        self.event_type = event_type

        try:
            announcement_message_id = int(self.config["discord"]["announcement_message_id"])

            announcement = None

            # Attempt to find the announcement message in cached messages
            announcement = disnake.utils.get(self.bot.cached_messages, id=announcement_message_id)

            if announcement is None:
                # If not in cache, search through all text channels in the guild
                for channel in inter.guild.text_channels:
                    try:
                        announcement = await channel.fetch_message(announcement_message_id)
                        if announcement:
                            break
                    except disnake.NotFound:
                        continue
                    except Exception as e:
                        self.logger.error(f"Error fetching message from channel {channel.id}: {e}", exc_info=True)
                        continue

            if not announcement:
                await inter.response.send_message(
                    "❌ Announcement message not found in any text channel. Please check the message ID.",
                    ephemeral=True,
                )
                return

            self.logger.info(
                f"Secret Santa event started by {inter.author}. Using existing announcement message ID: {announcement_message_id}"
            )
            await inter.response.send_message(
                f"🔔 Secret Santa event of type '{event_type}' has been started! Using the existing announcement message.",
                ephemeral=True,
            )
        except Exception as e:
            await inter.response.send_message(
                "❌ An error occurred while starting the Secret Santa event.", ephemeral=True
            )
            self.logger.error(f"Error while starting Secret Santa event: {e}", exc_info=True)
            return

        self.save_assignments()

    @commands.slash_command(
        name="close_joining",
        description="Closes the joining phase of the current Secret Santa event.",
    )
    @commands.check(is_moderator)
    async def close_joining(self, inter: disnake.ApplicationCommandInteraction):
        if not self.active:
            await inter.response.send_message(
                "🔔 No active Secret Santa event to close joining.", ephemeral=True
            )
            return

        if self.join_closed:
            await inter.response.send_message(
                "🔔 The joining phase has already been ended.", ephemeral=True
            )
            return

        self.join_closed = True

        try:
            announcement_message_id = int(self.config["discord"]["announcement_message_id"])

            announcement = None

            # Attempt to find the announcement message in cached messages
            announcement = disnake.utils.get(self.bot.cached_messages, id=announcement_message_id)

            if announcement is None:
                # If not in cache, search through all text channels in the guild
                for channel in inter.guild.text_channels:
                    try:
                        announcement = await channel.fetch_message(announcement_message_id)
                        if announcement:
                            break
                    except disnake.NotFound:
                        continue
                    except Exception as e:
                        self.logger.error(f"Error fetching message from channel {channel.id}: {e}", exc_info=True)
                        continue

            if not announcement:
                await inter.response.send_message(
                    "❌ Announcement message not found in any text channel. Please check the message ID.",
                    ephemeral=True,
                )
                return

            await announcement.clear_reactions()

            await inter.channel.send("🔒 The Secret Santa event is now closed for new participants.")

            self.logger.info("Joining phase has been closed.")
            await inter.response.send_message(
                "🔒 Joining phase has been closed. No further participants can join.",
                ephemeral=True,
            )
        except disnake.NotFound:
            await inter.response.send_message(
                "❌ Announcement message not found.", ephemeral=True
            )
            self.logger.error(
                "Announcement message not found when attempting to close joining."
            )
        except Exception as e:
            await inter.response.send_message(
                "❌ An error occurred while closing the joining phase.", ephemeral=True
            )
            self.logger.error(f"Error while closing joining phase: {e}", exc_info=True)

        self.save_assignments()

    @commands.slash_command(
        name="end_santa",
        description="Ends the current Secret Santa event.",
    )
    @commands.check(is_moderator)
    async def end_santa(self, inter: disnake.ApplicationCommandInteraction):
        if not self.active:
            await inter.response.send_message(
                "🔔 No active Secret Santa event to end.", ephemeral=True
            )
            return

        self.logger.info(
            f"Secret Santa event ended by {inter.author}. Assignments were: {self.assignments}"
        )

        if self.event_type == "Regular":
            reveal_text = "🎁 **Secret Santa Assignments:**\n"
            for santa_id, receiver_id in self.assignments.items():
                santa_name = await self.get_user_display_name(santa_id)
                receiver_name = await self.get_user_display_name(receiver_id)
                reveal_text += f"{santa_name} ➡️ {receiver_name}\n"

            embed = disnake.Embed(
                title="🎁 Secret Santa Assignments Revealed! 🎁",
                description=reveal_text,
                color=disnake.Color.gold(),
                timestamp=datetime.utcnow()
            )
            await inter.channel.send(embed=embed)

        self.participants.clear()
        self.assignments.clear()
        self.pending_questions.clear()
        self.active = False
        self.join_closed = False
        self.event_type = "Secret"
        await inter.response.send_message(
            "🔔 Secret Santa event has been ended. All assignments have been cleared.",
            ephemeral=True,
        )

        self.save_assignments()

    @commands.slash_command(
        name="list_participants",
        description="Lists all participants in the current Secret Santa event.",
    )
    @commands.check(is_moderator)
    async def list_participants(self, inter: disnake.ApplicationCommandInteraction):
        if not self.active:
            await inter.response.send_message(
                "🔔 No active Secret Santa event.", ephemeral=True
            )
            return

        if not self.participants:
            await inter.response.send_message(
                "🎄 **No participants have joined yet.**\nReact to the announcement message to join!",
                ephemeral=True,
            )
            return

        participant_names = [
            await self.get_user_display_name(user_id) for user_id in self.participants.keys()
        ]
        participant_list = "\n".join(participant_names)
        embed = disnake.Embed(
            title="🎄 Secret Santa Participants 🎄",
            description=participant_list,
            color=disnake.Color.blue(),
            timestamp=datetime.utcnow()
        )
        await inter.response.send_message(embed=embed, ephemeral=True)

    @commands.slash_command(
        name="assign_santas",
        description="Assigns each Secret Santa to a giftee and notifies them via DM."
    )
    @commands.check(is_moderator)
    async def assign_santas_command(self, inter: disnake.ApplicationCommandInteraction):
        await inter.response.defer(ephemeral=True)

        if not self.active:
            await inter.edit_original_response(
                content="🔔 No active Secret Santa event to assign."
            )
            return

        try:
            min_participants = int(self.config.get('SecretSanta', {}).get('minimum_participants', 2))
        except Exception as e:
            self.logger.error(f"Error accessing configuration: {e}", exc_info=True)
            min_participants = 2

        self.logger.info(f"Current number of participants: {len(self.participants)}")
        for participant_id in self.participants.keys():
            participant_name = await self.get_user_display_name(participant_id)
            self.logger.info(f"Participant: {participant_name} (ID: {participant_id})")

        if len(self.participants) < min_participants:
            await inter.edit_original_response(
                content="❌ Not enough participants to assign Secret Santas."
            )
            return

        self.logger.debug(f"Participants: {self.participants.keys()}")

        async with self.lock:
            if self.assignments:
                await inter.edit_original_response(
                    content="🔔 Secret Santa assignments have already been made."
                )
                return

            try:
                self.assign_santas()
                self.logger.info("Secret Santa assignments have been made.")
            except Exception as e:
                self.logger.error(
                    f"Error during Secret Santa assignment: {e}", exc_info=True
                )
                await inter.edit_original_response(
                    content="❌ An error occurred while assigning Secret Santas."
                )
                return

            failed_assignments = []

            for santa_id, receiver_id in self.assignments.items():
                try:
                    santa_user = await self.fetch_user(santa_id)
                    receiver_name = await self.get_user_display_name(receiver_id)
                    if santa_user:
                        try:
                            await santa_user.send(
                                f"🎄 **Your Secret Santa Assignment!** 🎄\n"
                                f"You are the Secret Santa for: **{receiver_name}** 🎁"
                            )
                        except disnake.Forbidden:
                            failed_assignments.append(await self.get_user_display_name(santa_id))
                            self.logger.warning(
                                f"Failed to send DM to user ID {santa_id}"
                            )
                    else:
                        failed_assignments.append(f"User ID {santa_id}")
                        self.logger.warning(
                            f"Could not fetch user with ID {santa_id} to send DM."
                        )
                except Exception as e:
                    self.logger.error(f"Error handling assignment for user ID {santa_id}: {e}", exc_info=True)
                    failed_assignments.append(f"User ID {santa_id}")

            if failed_assignments:
                failed_list = ", ".join(failed_assignments)
                await inter.edit_original_response(
                    content=f"🔔 Assignments have been made, but failed to send DMs to: {failed_list}."
                )
            else:
                await inter.edit_original_response(
                    content="🎁 Secret Santa assignments have been successfully made and notified!"
                )

        self.logger.info("Saving current state of assignments and participants.")
        self.save_assignments()
        self.logger.info("State saved successfully.")

    def assign_santas(self):
        santa_ids = list(self.participants.keys())
        # Multiple shuffles for higher entropy
        for _ in range(10):
            santa_ids = self.create_shuffled_list(santa_ids)

        santa_ids.append(santa_ids[0])
        new_assignments = {}
        for i in range(0, len(santa_ids) - 1):
            new_assignments[santa_ids[i]] = santa_ids[i + 1]

        self.assignments = new_assignments

    async def fetch_user(self, user_id):
        user = self.bot.get_user(user_id)
        if user is None:
            try:
                user = await self.bot.fetch_user(user_id)
            except Exception as e:
                self.logger.error(f"Error fetching user with ID {user_id}: {e}", exc_info=True)
                user = None
        return user

    async def get_user_display_name(self, user_id):
        user = await self.fetch_user(user_id)
        return user.display_name if user else f"User ID {user_id}"

    @commands.Cog.listener()
    async def on_raw_reaction_add(self, payload: disnake.RawReactionActionEvent):
        self.logger.debug(f"on_raw_reaction_add called with payload: {payload}")

        if not self.active or self.join_closed:
            self.logger.debug(f"Event inactive or joining closed. Active: {self.active}, Join Closed: {self.join_closed}")
            return

        if payload.message_id != self.announcement_message_id:
            self.logger.debug(f"Reaction not on announcement message. Payload message ID: {payload.message_id}, Announcement message ID: {self.announcement_message_id}")
            return

        guild = self.bot.get_guild(payload.guild_id)
        if guild is None:
            self.logger.error("Guild not found for reaction.")
            return

        async with self.lock:
            if payload.user_id not in self.participants:
                self.participants[payload.user_id] = None
                self.logger.info(f"Added participant with user ID: {payload.user_id}")
            else:
                self.logger.info(f"Participant with user ID {payload.user_id} already added.")

        self.logger.info("Saving state after new participant added.")
        self.save_assignments()
        self.logger.info("State saved successfully after new participant added.")

    @commands.Cog.listener()
    async def on_raw_reaction_remove(self, payload: disnake.RawReactionActionEvent):
        if not self.active:
            return

        if payload.message_id != self.announcement_message_id:
            return

        guild = self.bot.get_guild(payload.guild_id)
        if not guild:
            return

        async with self.lock:
            if payload.user_id in self.participants:
                self.participants.pop(payload.user_id, None)
                self.logger.info(f"User ID {payload.user_id} removed from Secret Santa participants.")

                user = await self.fetch_user(payload.user_id)
                if user:
                    try:
                        await user.send("❌ You have been removed from the Secret Santa event.")
                    except disnake.Forbidden:
                        self.logger.warning(f"Could not send DM to user ID {payload.user_id}. They might have DMs disabled.")

                self.save_assignments()

    @commands.Cog.listener()
    async def on_message_delete(self, message):
        if message.id == self.announcement_message_id:
            self.logger.warning("Announcement message was deleted. Ending Secret Santa event.")
            self.active = False
            self.join_closed = False
            self.participants.clear()
            self.assignments.clear()
            self.pending_questions.clear()
            self.event_type = "Secret"
            self.save_assignments()

    @commands.slash_command(
        name="reveal_santas",
        description="Reveals all Secret Santa assignments to the server.",
    )
    @commands.check(is_moderator)
    async def reveal_santas(self, inter: disnake.ApplicationCommandInteraction):
        if not self.active:
            await inter.response.send_message(
                "🔔 No active Secret Santa event.", ephemeral=True
            )
            return

        if not self.assignments:
            await inter.response.send_message(
                "🔔 Secret Santa assignments have not been made yet.", ephemeral=True
            )
            return

        reveal_text = "🎁 **Secret Santa Assignments:**\n"
        for santa_id, receiver_id in self.assignments.items():
            santa_name = await self.get_user_display_name(santa_id)
            receiver_name = await self.get_user_display_name(receiver_id)
            reveal_text += f"{santa_name} ➡️ {receiver_name}\n"

        embed = disnake.Embed(
            title="🎁 Secret Santa Assignments Revealed! 🎁",
            description=reveal_text,
            color=disnake.Color.gold(),
            timestamp=datetime.utcnow()
        )
        await inter.channel.send(embed=embed)
        self.logger.info(f"Secret Santa assignments revealed by {inter.author}.")
        await inter.response.send_message(
            "🎉 Secret Santa assignments have been revealed!", ephemeral=True
        )

    @commands.slash_command(
        name="submit_gift",
        description="Submit your Secret Santa gift details to the moderators.",
    )
    async def submit_gift_command(
            self,
            inter: disnake.ApplicationCommandInteraction,
            description: str = commands.Param(
                description="Describe your gift.",
                max_length=2000
            ),
            image1: disnake.Attachment = commands.Param(
                default=None,
                description="First image of your gift (optional)."
            ),
            image2: disnake.Attachment = commands.Param(
                default=None,
                description="Second image of your gift (optional)."
            ),
    ):
        await inter.response.defer(ephemeral=True)

        rephrased_description = await self.rephrase_text(description)

        embed = disnake.Embed(
            title="🎁 Secret Santa Gift Submission",
            description=rephrased_description,
            color=disnake.Color.green(),
            timestamp=datetime.utcnow()
        )
        embed.set_author(name=inter.author.display_name, icon_url=inter.author.avatar.url)

        files = []
        if image1:
            if image1.content_type.startswith('image/'):
                image1_file = await image1.to_file()
                embed.set_image(url=f"attachment://{image1.filename}")
                files.append(image1_file)
            else:
                await inter.edit_original_response(content="❌ The first file you uploaded is not an image.")
                return

        if image2:
            if image2.content_type.startswith('image/'):
                image2_file = await image2.to_file()
                if not embed.image.url:
                    embed.set_image(url=f"attachment://{image2.filename}")
                else:
                    embed.set_thumbnail(url=f"attachment://{image2.filename}")
                files.append(image2_file)
            else:
                await inter.edit_original_response(content="❌ The second file you uploaded is not an image.")
                return

        try:
            moderator_channel = self.bot.get_channel(self.moderator_channel_id)
            if not moderator_channel:
                await inter.edit_original_response(
                    content="❌ Moderator channel not found. Please contact an administrator."
                )
                return

            await moderator_channel.send(embed=embed, files=files)
            await inter.edit_original_response(
                content="✅ Your gift submission has been sent to the moderators!"
            )
            self.logger.info(f"{inter.author} submitted a gift to the moderators.")
        except Exception as e:
            await inter.edit_original_response(
                content="❌ An error occurred while submitting your gift."
            )
            self.logger.error(
                f"Error while submitting gift from {inter.author}: {e}", exc_info=True
            )

    async def rephrase_text(self, text: str) -> str:
        self.logger.debug("Starting text rephrasing using ChatGPT API.")
        try:
            prompt = (
                "Please rephrase the following gift description to make it more clear and concise, without changing its meaning:\n\n"
                f"{text}"
            )
            response = await self.call_chatgpt_api(prompt)
            return response.strip() if response else text
        except Exception as e:
            self.logger.error(f"Failed to rephrase text using ChatGPT API: {e}", exc_info=True)
            return text

    async def call_chatgpt_api(self, prompt: str) -> str:
        try:
            async with aiohttp.ClientSession() as session:
                headers = {
                    "Content-Type": "application/json",
                    "Authorization": f"Bearer {self.openai_api_key}"
                }
                json_data = {
                    "model": "gpt-3.5-turbo",
                    "messages": [
                        {"role": "system", "content": "You are a helpful assistant that rephrases text."},
                        {"role": "user", "content": prompt}
                    ],
                    "max_tokens": 500,
                    "temperature": 0.7,
                }
                async with session.post("https://api.openai.com/v1/chat/completions", headers=headers, json=json_data) as resp:
                    if resp.status != 200:
                        self.logger.error(f"Error calling OpenAI API: {resp.status} {await resp.text()}")
                        return ""
                    data = await resp.json()
                    reply = data['choices'][0]['message']['content']
                    return reply
        except Exception as e:
            self.logger.error(f"Error calling OpenAI API: {e}", exc_info=True)
            return ""

    @commands.slash_command(
        name="ask_santa",
        description="Send an anonymous question to your giftee."
    )
    async def ask_santa_command(
        self,
        inter: disnake.ApplicationCommandInteraction,
        question: str = commands.Param(
            description="Your question to your giftee.",
            max_length=2000
        ),
    ):
        await inter.response.defer(ephemeral=True)

        if not self.active:
            await inter.edit_original_response(content="🔔 No active Secret Santa event.")
            return

        santa_id = inter.author.id
        giftee_id = self.assignments.get(santa_id)

        if not giftee_id:
            await inter.edit_original_response(content="❌ You do not have an assigned giftee or assignments have not been made yet.")
            return

        giftee_user = await self.fetch_user(giftee_id)
        if not giftee_user:
            await inter.edit_original_response(content="❌ Unable to find your assigned giftee.")
            return

        try:
            question_id = str(datetime.utcnow().timestamp()).replace('.', '')

            if str(giftee_id) not in self.pending_questions:
                self.pending_questions[str(giftee_id)] = []

            self.pending_questions[str(giftee_id)].append({
                "question_id": question_id,
                "santa_id": santa_id,
                "question": question,
            })
            self.save_assignments()

            await giftee_user.send(
                f"📩 **You have received an anonymous question from your Secret Santa:**\n\n{question}\n\n"
                f"Please reply to this message to answer."
            )
            await inter.edit_original_response(content="✅ Your question has been sent anonymously to your giftee.")
            self.logger.info(f"{inter.author} sent an anonymous question to user ID {giftee_id}.")
        except disnake.Forbidden:
            await inter.edit_original_response(content="❌ Unable to send the question. The giftee may have DMs disabled.")
            self.logger.warning(f"Could not send anonymous question to user ID {giftee_id}.")
        except Exception as e:
            await inter.edit_original_response(content="❌ An error occurred while sending your question.")
            self.logger.error(f"Error sending anonymous question: {e}", exc_info=True)

    @commands.Cog.listener()
    async def on_message(self, message):
        if message.author.bot:
            return

        if not isinstance(message.channel, disnake.DMChannel):
            return

        giftee_id = message.author.id

        pending = self.pending_questions.get(str(giftee_id))
        if not pending:
            return

        last_question = pending[-1]
        santa_id = last_question['santa_id']
        santa_user = await self.fetch_user(santa_id)
        if not santa_user:
            self.logger.error(f"Unable to find Santa user with ID {santa_id}")
            return

        try:
            await santa_user.send(
                f"📬 **Your giftee has replied to your question:**\n\n{message.content}"
            )
            self.logger.info(f"Forwarded giftee's reply to Santa user ID {santa_id}.")

            pending.pop()
            if not pending:
                del self.pending_questions[str(giftee_id)]
            else:
                self.pending_questions[str(giftee_id)] = pending

            self.save_assignments()

        except disnake.Forbidden:
            self.logger.warning(f"Could not send reply to Santa user ID {santa_id}.")
        except Exception as e:
            self.logger.error(f"Error forwarding reply to Santa: {e}", exc_info=True)

        try:
            await message.channel.send("✅ Your reply has been sent to your Secret Santa.")
        except Exception as e:
            self.logger.error(f"Error sending acknowledgment to giftee: {e}", exc_info=True)

    def cog_unload(self):
        self.logger.info("SecretSantaCog has been unloaded.")

    def generate_integers(self, n, min, max, optional_data=None):
        try:
            response = self.random_client.generate_signed_integers(n, min, max, replacement=False, user_data=optional_data)
            integers = response["random"]["data"]
            link = self.random_client.create_url(response["random"], response["signature"])
            self.signed_random_links.append(link)
            self.logger.info(f'Random.org used. Link: {link}')
            return integers
        except Exception as e:
            self.logger.info(f"Random.org API failed, using Python random instead.\n{e}")
            return random.sample(range(min, max + 1), k=n)

    def create_shuffled_list(self, x):
        x_len = len(x)
        new_order = self.generate_integers(x_len, 0, x_len - 1, optional_data=x)

        shuffled_x = [None] * x_len
        for i in range(x_len):
            shuffled_x[i] = x[new_order[i]]

        return shuffled_x

def setup(bot):
    bot.add_cog(SecretSantaCog(bot))
