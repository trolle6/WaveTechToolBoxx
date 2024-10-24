# cogs/SecretSanta_cog.py

import disnake
from disnake.ext import commands
import logging
import random
import asyncio
import json
from datetime import datetime
import os
import openai  # For ChatGPT API usage
import aiohttp  # For async HTTP requests

def is_moderator():
    """A decorator that checks if the user has administrator permissions."""
    async def predicate(inter):
        return inter.author.guild_permissions.administrator
    return commands.check(predicate)

class SecretSantaCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.config = bot.config  # Ensure bot.config is properly loaded
        self.logger = bot.logger
        self.participants = {}  # Maps user_id to member object
        self.assignments = {}  # Maps santa_id to giftee_id
        self.pending_questions = {}  # Maps giftee_id to list of questions
        self.active = False
        self.join_closed = False  # Flag to indicate if joining is closed
        self.lock = asyncio.Lock()  # To manage concurrent access
        self.data_file = os.path.join(os.path.dirname(__file__), "secret_santa_data.json")
        self.event_type = "Secret"  # Default event type

        # Get the moderator channel ID from the config
        self.moderator_channel_id = int(self.config["discord"]["moderator_channel_id"])
        self.announcement_message_id = int(self.config["discord"]["secret_santa_message_id"])

        # OpenAI API key
        self.openai_api_key = self.config.get("openai_api_key")
        openai.api_key = self.openai_api_key

        # Load assignments when the cog is loaded
        self.bot.loop.create_task(self.load_assignments())

    def save_assignments(self):
        """Saves the current state to a JSON file."""
        data = {
            "participants": {str(k): v.id for k, v in self.participants.items()},
            "assignments": {str(k): v for k, v in self.assignments.items()},
            "pending_questions": self.pending_questions,  # Needs to be serializable
            "active": self.active,
            "join_closed": self.join_closed,
            "event_type": self.event_type,
        }
        try:
            with open(self.data_file, "w") as f:
                json.dump(data, f)
            self.logger.info(f"Secret Santa data saved to {self.data_file}. Data: {data}")
        except Exception as e:
            self.logger.error(f"Error saving Secret Santa data: {e}", exc_info=True)

    async def load_assignments(self):
        """Loads the state from a JSON file if it exists."""
        await self.bot.wait_until_ready()
        try:
            if os.path.exists(self.data_file):
                with open(self.data_file, "r") as f:
                    data = json.load(f)
                self.logger.info(f"Secret Santa data loaded from {self.data_file}. Data: {data}")
                self.participants = {}
                for k, v in data.get("participants", {}).items():
                    user = self.bot.get_user(int(v))
                    if user is None:
                        try:
                            user = await self.bot.fetch_user(int(v))
                        except disnake.NotFound:
                            self.logger.warning(f"User with ID {v} not found.")
                            continue
                    self.participants[int(k)] = user

                self.assignments = {
                    int(k): int(v) for k, v in data.get("assignments", {}).items()
                }
                self.pending_questions = data.get("pending_questions", {})
                self.active = data.get("active", False)
                self.join_closed = data.get("join_closed", False)
                self.event_type = data.get("event_type", "Secret")
            else:
                self.logger.info(f"No existing Secret Santa data file found at {self.data_file}.")
        except Exception as e:
            self.logger.error(f"Error loading Secret Santa data: {e}", exc_info=True)

    @commands.slash_command(
        name="start_santa",
        description="Starts a Secret Santa event."
    )
    @is_moderator()
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
        self.participants = {}
        self.assignments = {}
        self.pending_questions = {}
        self.event_type = event_type  # Set the event type

        try:
            channel = self.bot.get_channel(int(self.config["discord"]["channel_id"]))
            if not channel:
                await inter.response.send_message(
                    "❌ Announcement channel not found. Please check the configuration.", ephemeral=True
                )
                return

            announcement = await channel.fetch_message(self.announcement_message_id)
            await announcement.add_reaction("🎁")

            self.logger.info(f"Secret Santa event started by {inter.author}. Using existing announcement message ID: {self.announcement_message_id}")
            await inter.response.send_message(
                f"🔔 Secret Santa event of type '{event_type}' has been started! Using the existing announcement message.",
                ephemeral=True,
            )
        except disnake.NotFound:
            await inter.response.send_message(
                "❌ Announcement message not found. Please check the message ID in the configuration.", ephemeral=True
            )
            self.logger.error(
                "Announcement message not found when attempting to start the event."
            )
            return
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
    @is_moderator()
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
            channel = self.bot.get_channel(int(self.config["discord"]["channel_id"]))
            if not channel:
                await inter.response.send_message(
                    "❌ Announcement channel not found.", ephemeral=True
                )
                return

            announcement = await channel.fetch_message(self.announcement_message_id)
            await channel.send("🔒 The Secret Santa event is now closed for new participants.")
            await announcement.clear_reactions()

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
    @is_moderator()
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
                santa = self.participants.get(santa_id)
                receiver = self.participants.get(receiver_id)
                if santa and receiver:
                    reveal_text += f"{santa.display_name} ➡️ {receiver.display_name}\n"
                else:
                    reveal_text += f"❓ **Unknown Assignments**\n"

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
        self.event_type = "Secret"  # Reset to default
        await inter.response.send_message(
            "🔔 Secret Santa event has been ended. All assignments have been cleared.",
            ephemeral=True,
        )

        self.save_assignments()

    @commands.slash_command(
        name="list_participants",
        description="Lists all participants in the current Secret Santa event.",
    )
    @is_moderator()
    async def list_participants(self, inter: disnake.ApplicationCommandInteraction):
        if not self.active:
            await inter.response.send_message(
                "🔔 No active Secret Santa event.", ephemeral=True
            )
            return

        if not self.participants:
            await inter.response.send_message(
                "🎄 **No participants have joined yet.**\nReact to the announcement message with 🎁 to join!",
                ephemeral=True,
            )
            return

        participant_names = [
            member.display_name for member in self.participants.values()
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
    @is_moderator()
    async def assign_santas_command(self, inter: disnake.ApplicationCommandInteraction):
        await inter.response.defer(ephemeral=True)  # Defer the interaction

        if not self.active:
            await inter.edit_original_response(
                content="🔔 No active Secret Santa event to assign."
            )
            return

        try:
            min_participants = int(self.config.get('SecretSanta', {}).get('minimum_participants', 2))
        except Exception as e:
            self.logger.error(f"Error accessing configuration: {e}", exc_info=True)
            min_participants = 2  # Default to 2 if there's an error

        self.logger.info(f"Current number of participants: {len(self.participants)}")
        for participant_id, participant in self.participants.items():
            self.logger.info(f"Participant: {participant.display_name} (ID: {participant_id})")

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
                santa = self.participants.get(santa_id)
                receiver = self.participants.get(receiver_id)

                if santa and receiver:
                    try:
                        await santa.send(
                            f"🎄 **Your Secret Santa Assignment!** 🎄\n"
                            f"You are the Secret Santa for: **{receiver.display_name}** 🎁"
                        )
                    except disnake.Forbidden:
                        failed_assignments.append(santa.display_name)
                        self.logger.warning(
                            f"Failed to send DM to {santa.display_name} (ID: {santa_id})"
                        )

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
        """Assigns Secret Santas to participants ensuring no one is assigned to themselves."""
        santa_ids = list(self.participants.keys())
        receivers = santa_ids.copy()
        deranged = False

        while not deranged:
            random.shuffle(receivers)
            deranged = all(santa != receiver for santa, receiver in zip(santa_ids, receivers))

        self.assignments = {santa: receiver for santa, receiver in zip(santa_ids, receivers)}

    async def periodic_save_state(self):
        """Periodically saves the state to ensure minimal data loss."""
        while True:
            await asyncio.sleep(300)  # Save every 5 minutes
            async with self.lock:
                self.logger.info("Periodic save: saving current state of assignments and participants.")
                self.save_assignments()
                self.logger.info("Periodic save: state saved successfully.")

    @commands.Cog.listener()
    async def on_raw_reaction_add(self, payload: disnake.RawReactionActionEvent):
        """Listens for reactions added to the announcement message to add participants."""
        self.logger.debug(f"on_raw_reaction_add called with payload: {payload}")

        if not self.active or self.join_closed:
            self.logger.debug(f"Event inactive or joining closed. Active: {self.active}, Join Closed: {self.join_closed}")
            return

        if payload.message_id != self.announcement_message_id:
            self.logger.debug(f"Reaction not on announcement message. Payload message ID: {payload.message_id}, Announcement message ID: {self.announcement_message_id}")
            return

        if str(payload.emoji) != "🎁":
            self.logger.debug(f"Reaction is not the correct emoji. Emoji: {payload.emoji}")
            return

        guild = self.bot.get_guild(payload.guild_id)
        if guild is None:
            self.logger.error("Guild not found for reaction.")
            return

        member = guild.get_member(payload.user_id)
        if member is None:
            try:
                member = await guild.fetch_member(payload.user_id)
                self.logger.info(f"Fetched member: {member.display_name} (ID: {payload.user_id})")
            except disnake.NotFound:
                self.logger.error(f"Member with ID {payload.user_id} not found.")
                return
            except Exception as e:
                self.logger.error(f"Error fetching member: {e}", exc_info=True)
                return

        if member.bot:
            self.logger.info(f"Ignored reaction from bot: {member.display_name} (ID: {payload.user_id})")
            return

        async with self.lock:
            if payload.user_id not in self.participants:
                self.participants[payload.user_id] = member
                self.logger.info(f"Added participant: {member.display_name} (ID: {payload.user_id})")
            else:
                self.logger.info(f"Participant {member.display_name} (ID: {payload.user_id}) already added.")

        self.logger.info("Saving state after new participant added.")
        self.save_assignments()
        self.logger.info("State saved successfully after new participant added.")

    @commands.Cog.listener()
    async def on_raw_reaction_remove(self, payload: disnake.RawReactionActionEvent):
        """Listens for reactions removed from the announcement message to remove participants."""
        if not self.active:
            return

        if payload.message_id != self.announcement_message_id:
            return

        if str(payload.emoji) != "🎁":
            return

        guild = self.bot.get_guild(payload.guild_id)
        if not guild:
            return

        member = guild.get_member(payload.user_id)
        if member is None:
            try:
                member = await guild.fetch_member(payload.user_id)
            except disnake.NotFound:
                self.logger.error(f"Member with ID {payload.user_id} not found.")
                return
            except Exception as e:
                self.logger.error(f"Error fetching member: {e}", exc_info=True)
                return

        if member.bot:
            return

        async with self.lock:
            if payload.user_id in self.participants:
                removed_member = self.participants.pop(payload.user_id)
                self.logger.info(f"User {removed_member.display_name} removed from Secret Santa participants.")

                try:
                    await removed_member.send("❌ You have been removed from the Secret Santa event.")
                except disnake.Forbidden:
                    self.logger.warning(f"Could not send DM to {removed_member.display_name}. They might have DMs disabled.")

                self.save_assignments()

    @commands.Cog.listener()
    async def on_message_delete(self, message):
        """If the announcement message is deleted, end the Secret Santa event."""
        if message.id == self.announcement_message_id:
            self.logger.warning("Announcement message was deleted. Ending Secret Santa event.")
            self.active = False
            self.join_closed = False
            self.participants.clear()
            self.assignments.clear()
            self.pending_questions.clear()
            self.event_type = "Secret"  # Reset to default
            self.save_assignments()

    @commands.slash_command(
        name="reveal_santas",
        description="Reveals all Secret Santa assignments to the server.",
    )
    @is_moderator()
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
            santa = self.participants.get(santa_id)
            receiver = self.participants.get(receiver_id)
            if santa and receiver:
                reveal_text += f"{santa.display_name} ➡️ {receiver.display_name}\n"
            else:
                reveal_text += f"❓ **Unknown Assignments**\n"

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
                max_length=2000  # Discord message limit
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
        await inter.response.defer(ephemeral=True)  # Defer the interaction

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
            max_length=2000  # Discord message limit
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

        giftee = self.participants.get(giftee_id)
        if not giftee:
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

            await giftee.send(
                f"📩 **You have received an anonymous question from your Secret Santa:**\n\n{question}\n\n"
                f"Please reply to this message to answer."
            )
            await inter.edit_original_response(content="✅ Your question has been sent anonymously to your giftee.")
            self.logger.info(f"{inter.author} sent an anonymous question to {giftee}.")
        except disnake.Forbidden:
            await inter.edit_original_response(content="❌ Unable to send the question. The giftee may have DMs disabled.")
            self.logger.warning(f"Could not send anonymous question to {giftee.display_name}.")
        except Exception as e:
            await inter.edit_original_response(content="❌ An error occurred while sending your question.")
            self.logger.error(f"Error sending anonymous question: {e}", exc_info=True)

    @commands.Cog.listener()
    async def on_message(self, message):
        """Listens for DMs from giftees to handle replies to their Santa's questions."""
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
        santa = self.bot.get_user(santa_id)
        if not santa:
            try:
                santa = await self.bot.fetch_user(santa_id)
            except Exception as e:
                self.logger.error(f"Error fetching Santa user: {e}", exc_info=True)
                return

        try:
            await santa.send(
                f"📬 **Your giftee has replied to your question:**\n\n{message.content}"
            )
            self.logger.info(f"Forwarded giftee's reply to Santa {santa.display_name}.")

            pending.pop()
            if not pending:
                del self.pending_questions[str(giftee_id)]
            else:
                self.pending_questions[str(giftee_id)] = pending

            self.save_assignments()

        except disnake.Forbidden:
            self.logger.warning(f"Could not send reply to Santa {santa.display_name}.")
        except Exception as e:
            self.logger.error(f"Error forwarding reply to Santa: {e}", exc_info=True)

        try:
            await message.channel.send("✅ Your reply has been sent to your Secret Santa.")
        except Exception as e:
            self.logger.error(f"Error sending acknowledgment to giftee: {e}", exc_info=True)

    def cog_unload(self):
        self.logger.info("SecretSantaCog has been unloaded.")

def setup(bot):
    bot.add_cog(SecretSantaCog(bot))
