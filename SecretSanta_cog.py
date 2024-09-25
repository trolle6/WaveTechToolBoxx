# SecretSanta_cog.py

import disnake
from disnake.ext import commands
import logging
import random
import asyncio
import json
from datetime import datetime
import os

def setup_logger(name, log_file, level=logging.INFO):
    """Sets up a logger."""
    formatter = logging.Formatter(
        "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
    )
    logger = logging.getLogger(name)
    if not logger.handlers:
        handler = logging.FileHandler(log_file, mode="a", encoding="utf-8")
        handler.setFormatter(formatter)
        logger.setLevel(level)
        logger.addHandler(handler)
    return logger

def is_owner_or_admin():
    """A decorator that checks if the user is the guild owner or has administrator permissions."""

    async def predicate(inter):
        if not inter.guild:
            return False  # Not in a guild
        return (
            inter.author.id == inter.guild.owner_id
            or inter.author.guild_permissions.administrator
        )

    return commands.check(predicate)

class SecretSantaCog(commands.Cog):
    def __init__(self, bot, config):
        self.bot = bot
        self.config = config
        self.logger = setup_logger("SecretSanta", "SecretSanta.log")
        self.participants = {}  # Maps user_id to member object
        self.assignments = {}  # Maps santa_id to giftee_id
        self.active = False
        self.join_closed = False  # Flag to indicate if joining is closed
        self.announcement_message_id = None  # To track the announcement message
        self.lock = asyncio.Lock()  # To manage concurrent access
        self.data_file = "secret_santa_data.json"
        self.load_assignments()

    def save_assignments(self):
        """Saves the current state to a JSON file."""
        data = {
            "participants": {str(k): v.id for k, v in self.participants.items()},
            "assignments": {str(k): v for k, v in self.assignments.items()},
            "active": self.active,
            "join_closed": self.join_closed,
            "announcement_message_id": self.announcement_message_id,
        }
        with open(self.data_file, "w") as f:
            json.dump(data, f)
        self.logger.info("Secret Santa data saved to secret_santa_data.json.")

    def load_assignments(self):
        """Loads the state from a JSON file if it exists."""
        if os.path.exists(self.data_file):
            with open(self.data_file, "r") as f:
                data = json.load(f)
            self.participants = {
                int(k): self.bot.get_user(int(v))
                for k, v in data.get("participants", {}).items()
            }
            self.assignments = {
                int(k): int(v) for k, v in data.get("assignments", {}).items()
            }
            self.active = data.get("active", False)
            self.join_closed = data.get("join_closed", False)
            self.announcement_message_id = data.get("announcement_message_id")
            self.logger.info("Secret Santa data loaded from secret_santa_data.json.")

    @commands.slash_command(
        name="start_santa", description="Starts a Secret Santa event."
    )
    @is_owner_or_admin()
    async def start_santa(
            self,
            inter: disnake.ApplicationCommandInteraction,
            event_type: str = commands.Param(
                choices=["Regular", "Secret"],
                description="Choose the type of Secret Santa event.",
            ),
    ):
        """
        Starts the Secret Santa event with a specified type.
        Sends an embedded announcement message for users to react and join.
        """
        if self.active:
            await inter.response.send_message(
                "🔔 A Secret Santa event is already active.", ephemeral=True
            )
            return

        self.active = True
        self.join_closed = False
        self.participants = {}
        self.assignments = {}

        # Create an embedded announcement message
        embed = disnake.Embed(
            title="🎄 Secret Santa Event Started! 🎄",
            description=(
                f"**Type:** {event_type}\n\n"
                "React with 🎁 to join the Secret Santa!"
            ),
            color=disnake.Color.green(),
            timestamp=datetime.utcnow()
        )
        announcement = await inter.channel.send(embed=embed)
        self.announcement_message_id = announcement.id

        # Add reaction to the announcement
        await announcement.add_reaction("🎁")

        self.logger.info(
            f"Secret Santa event started by {inter.author}. Announcement message ID: {self.announcement_message_id}"
        )
        await inter.response.send_message(
            f"🔔 Secret Santa event of type '{event_type}' has been started! Check the announcement message and react "
            f"with 🎁 to join.",
            ephemeral=True,
        )

        # Save the state
        self.save_assignments()

    @commands.slash_command(
        name="close_joining",
        description="Closes the joining phase of the current Secret Santa event.",
    )
    @is_owner_or_admin()
    async def close_joining(self, inter: disnake.ApplicationCommandInteraction):
        """
        Closes the joining phase, preventing further users from reacting to join the Secret Santa event.
        """
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

        # Attempt to fetch the announcement message
        try:
            guild = self.bot.get_guild(int(self.config["discord"]["guild_id"]))
            if not guild:
                await inter.response.send_message("❌ Guild not found.", ephemeral=True)
                return

            channel = guild.get_channel(int(self.config["discord"]["channel_id"]))
            if not channel:
                await inter.response.send_message(
                    "❌ Announcement channel not found.", ephemeral=True
                )
                return

            announcement = await channel.fetch_message(self.announcement_message_id)

            # Edit the announcement message to indicate that joining is closed
            embed = announcement.embeds[0]
            embed.add_field(name="Status", value="🔒 Joining is now closed.", inline=False)
            await announcement.edit(embed=embed)

            # Remove all reactions to prevent further joins
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

        # Save the state
        self.save_assignments()

    @commands.slash_command(
        name="end_santa", description="Ends the current Secret Santa event."
    )
    @is_owner_or_admin()
    async def end_santa(self, inter: disnake.ApplicationCommandInteraction):
        """
        Ends the Secret Santa event.
        Clears all assignments and participants.
        """
        if not self.active:
            await inter.response.send_message(
                "🔔 No active Secret Santa event to end.", ephemeral=True
            )
            return

        self.logger.info(
            f"Secret Santa event ended by {inter.author}. Assignments were: {self.assignments}"
        )
        self.participants.clear()
        self.assignments.clear()
        self.active = False
        self.join_closed = False
        self.announcement_message_id = None
        await inter.response.send_message(
            "🔔 Secret Santa event has been ended. All assignments have been cleared.",
            ephemeral=True,
        )

        # Save the state
        self.save_assignments()

    @commands.slash_command(
        name="list_participants",
        description="Lists all participants in the current Secret Santa event.",
    )
    @is_owner_or_admin()
    async def list_participants(self, inter: disnake.ApplicationCommandInteraction):
        """
        Lists all participants in the current Secret Santa event.
        """
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
        name="reveal_santas",
        description="Reveals all Secret Santa assignments to the server.",
    )
    @is_owner_or_admin()
    async def reveal_santas(self, inter: disnake.ApplicationCommandInteraction):
        """
        Reveals all Secret Santa assignments to the server.
        Only recommended at the end of the event.
        """
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
            title="🎁 Secret Santa Assignments 🎁",
            description=reveal_text,
            color=disnake.Color.gold(),
            timestamp=datetime.utcnow()
        )
        await inter.response.send_message(embed=embed, ephemeral=True)
        self.logger.info(f"Secret Santa assignments revealed by {inter.author}.")

    @commands.slash_command(
        name="assign_santas",
        description="Assigns each Secret Santa to a giftee and notifies them via DM.",
    )
    @is_owner_or_admin()
    async def assign_santas_command(self, inter: disnake.ApplicationCommandInteraction):
        """
        Assigns each Secret Santa to a giftee and sends them a DM with their assignment.
        """
        if not self.active:
            await inter.response.send_message(
                "🔔 No active Secret Santa event to assign.", ephemeral=True
            )
            return

        if len(self.participants) < 2:
            await inter.response.send_message(
                "❌ Not enough participants to assign Secret Santas.", ephemeral=True
            )
            return

        async with self.lock:
            if self.assignments:
                await inter.response.send_message(
                    "🔔 Secret Santa assignments have already been made.",
                    ephemeral=True,
                )
                return

            try:
                self.assign_santas()
                self.logger.info("Secret Santa assignments have been made.")
            except Exception as e:
                self.logger.error(
                    f"Error during Secret Santa assignment: {e}", exc_info=True
                )
                await inter.response.send_message(
                    "❌ An error occurred while assigning Secret Santas.",
                    ephemeral=True,
                )
                return

            failed_assignments = []

            # Notify each Santa via DM
            for santa_id, receiver_id in self.assignments.items():
                santa = self.participants.get(santa_id)
                receiver = self.participants.get(receiver_id)

                if santa and receiver:
                    try:
                        await santa.send(
                            f"🎄 **Your Secret Santa Assignment!** 🎄\n\n"
                            f"You have been assigned to give a gift to {receiver.mention}!\n\n"
                            f"Feel free to send them questions or hints anonymously using the `/send_question` command."
                        )
                        self.logger.info(f"Sent assignment DM to {santa}.")
                    except disnake.Forbidden:
                        self.logger.warning(
                            f"Could not send DM to {santa}. They might have DMs disabled."
                        )
                        failed_assignments.append(santa)
                    except Exception as e:
                        self.logger.error(
                            f"Unexpected error while sending DM to {santa}: {e}",
                            exc_info=True,
                        )
                        failed_assignments.append(santa)
                else:
                    self.logger.error(
                        f"Invalid assignment: Santa ID {santa_id} or Receiver ID {receiver_id} not found."
                    )

            if failed_assignments:
                failed_names = ", ".join([member.display_name for member in failed_assignments])
                await inter.channel.send(
                    f"❌ Could not send DM to the following participants: {failed_names}. They might have DMs disabled.",
                    ephemeral=True,
                )

            await inter.response.send_message(
                "✅ Secret Santa assignments have been made and notified via DM!",
                ephemeral=True,
            )

            # Save the state
            self.save_assignments()

    def assign_santas(self):
        """
        Assigns Secret Santas to participants ensuring no one is assigned to themselves.
        Utilizes the derangement algorithm for a complete derangement.
        """
        santa_ids = list(self.participants.keys())
        receivers = santa_ids.copy()
        deranged = False

        while not deranged:
            random.shuffle(receivers)
            deranged = all(santa != receiver for santa, receiver in zip(santa_ids, receivers))

        self.assignments = {santa: receiver for santa, receiver in zip(santa_ids, receivers)}

    @commands.slash_command(
        name="send_question", description="Send a question to your Secret Santa giftee."
    )
    async def send_question_command(
            self,
            inter: disnake.ApplicationCommandInteraction,
            question: str = commands.Param(
                description="Your question to your Secret Santa giftee.",
                max_length=2000  # Discord message limit
            ),
    ):
        """
        Allows Santas to send questions to their assigned giftees via slash commands.
        """
        sender_id = inter.author.id

        # Check if the sender is a Santa (has a giftee assigned)
        if sender_id in self.assignments:
            receiver_id = self.assignments[sender_id]
            receiver = self.participants.get(receiver_id)

            if receiver:
                try:
                    # Send the question to the giftee as a DM
                    await receiver.send(
                        f"🎄 **Secret Santa Message from {inter.author.display_name}:**\n{question}"
                    )
                    await inter.response.send_message(
                        "✅ Your question has been sent to your Secret Santa giftee!",
                        ephemeral=True,
                    )
                    self.logger.info(
                        f"Forwarded question from {inter.author} to {receiver}."
                    )
                except disnake.Forbidden:
                    await inter.response.send_message(
                        "❌ I couldn't send a message to your Secret Santa giftee. They might have DMs disabled.",
                        ephemeral=True,
                    )
                    self.logger.error(
                        f"Failed to send DM to {receiver}. They might have DMs disabled."
                    )
                except Exception as e:
                    await inter.response.send_message(
                        "❌ An unexpected error occurred while sending your message.",
                        ephemeral=True,
                    )
                    self.logger.error(
                        f"Unexpected error while sending DM to {receiver}: {e}",
                        exc_info=True,
                    )
            else:
                await inter.response.send_message(
                    "❌ Your Secret Santa giftee was not found. Please contact an administrator.",
                    ephemeral=True,
                )
                self.logger.error(
                    f"Giftee with ID {receiver_id} for Santa {sender_id} not found."
                )
        else:
            await inter.response.send_message(
                "❌ You are not assigned as a Secret Santa in the current event.",
                ephemeral=True,
            )
            self.logger.warning(
                f"User {inter.author} attempted to send a Secret Santa message but is not a Santa."
            )

    @commands.Cog.listener()
    async def on_raw_reaction_add(self, payload: disnake.RawReactionActionEvent):
        """
        Listens for reactions added to the announcement message to add participants.
        """
        if not self.active or self.join_closed:
            return

        if payload.message_id != self.announcement_message_id:
            return

        if str(payload.emoji) != "🎁":
            return

        guild = self.bot.get_guild(payload.guild_id)
        if not guild:
            return

        member = guild.get_member(payload.user_id)
        if member is None or member.bot:
            return

        async with self.lock:
            if member.id not in self.participants:
                self.participants[member.id] = member
                self.logger.info(f"User {member} added to Secret Santa participants.")

                # Send a confirmation DM
                try:
                    await member.send(
                        "✅ You have successfully joined the Secret Santa event! 🎄\n"
                        "You will be notified once the assignments are made."
                    )
                except disnake.Forbidden:
                    self.logger.warning(
                        f"Could not send DM to {member}. They might have DMs disabled."
                    )

                # Save the state
                self.save_assignments()

    @commands.Cog.listener()
    async def on_raw_reaction_remove(self, payload: disnake.RawReactionActionEvent):
        """
        Listens for reactions removed from the announcement message to remove participants.
        """
        if not self.active:
            return

        if payload.message_id != self.announcement_message_id:
            return

        if str(payload.emoji) != "🎁":
            return

        async with self.lock:
            if payload.user_id in self.participants:
                removed_member = self.participants.pop(payload.user_id)
                self.logger.info(
                    f"User {removed_member} removed from Secret Santa participants."
                )

                # Send a confirmation DM
                try:
                    await removed_member.send(
                        "❌ You have been removed from the Secret Santa event."
                    )
                except disnake.Forbidden:
                    self.logger.warning(
                        f"Could not send DM to {removed_member}. They might have DMs disabled."
                    )

                # Save the state
                self.save_assignments()

    @commands.Cog.listener()
    async def on_message_delete(self, message):
        """
        If the announcement message is deleted, end the Secret Santa event.
        """
        if message.id == self.announcement_message_id:
            self.logger.warning(
                "Announcement message was deleted. Ending Secret Santa event."
            )
            self.active = False
            self.join_closed = False
            self.participants.clear()
            self.assignments.clear()
            self.announcement_message_id = None
            # Save the state
            self.save_assignments()

    @commands.Cog.listener()
    async def on_message(self, message):
        """
        Listens for DMs from Santas to forward questions to their assigned giftees.
        """
        # Ignore messages from bots
        if message.author.bot:
            return

        # Check if the message is a DM
        if isinstance(message.channel, disnake.DMChannel):
            sender_id = message.author.id

            # Check if the sender is a Santa (has a giftee assigned)
            if sender_id in self.assignments:
                receiver_id = self.assignments[sender_id]
                receiver = self.participants.get(receiver_id)

                if receiver:
                    try:
                        # Send the question to the giftee as a DM
                        await receiver.send(
                            f"🎄 **Secret Santa Message from {message.author.display_name}:**\n{message.content}"
                        )
                        await message.channel.send(
                            "✅ Your message has been sent to your Secret Santa giftee!"
                        )
                        self.logger.info(
                            f"Forwarded message from {message.author} to {receiver}."
                        )
                    except disnake.Forbidden:
                        await message.channel.send(
                            "❌ I couldn't send a message to your Secret Santa giftee. They might have DMs disabled."
                        )
                        self.logger.error(
                            f"Failed to send DM to {receiver}. They might have DMs disabled."
                        )
                    except Exception as e:
                        await message.channel.send(
                            "❌ An unexpected error occurred while sending your message."
                        )
                        self.logger.error(
                            f"Unexpected error while sending DM to {receiver}: {e}",
                            exc_info=True,
                        )
                else:
                    await message.channel.send(
                        "❌ Your Secret Santa giftee was not found. Please contact an administrator."
                    )
                    self.logger.error(
                        f"Giftee with ID {receiver_id} for Santa {sender_id} not found."
                    )
            else:
                # Optionally, handle DMs from users not assigned as Santas
                await message.channel.send(
                    "❌ You are not assigned as a Secret Santa in the current event."
                )
                self.logger.warning(
                    f"User {message.author} attempted to send a Secret Santa message but is not a Santa."
                )

    async def cog_unload(self):
        """Handles cleanup when the cog is unloaded."""
        self.logger.info("SecretSanta_cog has been unloaded.")

    @commands.Cog.listener()
    async def on_ready(self):
        # Optionally, automatically assign Santas when the bot is ready and there are participants
        if self.active and self.participants and not self.assignments:
            self.logger.info(
                "Active Secret Santa event detected on startup. Awaiting manual assignment."
            )
            # You can choose to auto-assign or require manual assignment via /assign_santas.
            pass

# The 'setup' function must be asynchronous
async def setup(bot):
    await bot.add_cog(SecretSantaCog(bot, bot.config))
