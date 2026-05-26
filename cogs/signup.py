import asyncio
import logging

import discord
from discord.ext import commands

import db

logger = logging.getLogger(__name__)


def format_signup_line(index: int, entry: dict) -> str:
    name = entry["display_name"]
    plus_ones = entry["plus_ones"]
    if plus_ones > 0:
        guest_label = "guest" if plus_ones == 1 else "guests"
        return f"{index + 1}. **{name}** (+{plus_ones} {guest_label})"
    return f"{index + 1}. **{name}**"


def total_headcount(signups: dict[int, dict]) -> int:
    return sum(1 + entry["plus_ones"] for entry in signups.values())


def _headcount_if_updated(
    signups: dict[int, dict], user_id: int, plus_ones: int
) -> int:
    """Total headcount if user_id signs up (or updates) with the given guest count."""
    total = total_headcount(signups)
    if user_id in signups:
        total -= 1 + signups[user_id]["plus_ones"]
    return total + 1 + plus_ones


def _cap_exceeded_message(cap: int) -> str:
    return f"This signup is full ({cap} spots including guests)."


def _signup_footer(
    signup_count: int,
    headcount: int,
    creator_name: str,
    *,
    cap: int | None = None,
) -> str:
    if cap is not None:
        return (
            f"{signup_count} signed up · {headcount}/{cap} spots filled "
            f"(including guests) · Created by {creator_name}"
        )
    return (
        f"{signup_count} signed up · {headcount} total including guests "
        f"· Created by {creator_name}"
    )


def build_empty_signup_embed(
    title: str, creator_name: str, *, cap: int | None = None
) -> discord.Embed:
    description = (
        "No signups yet.\n\n"
        "Choose how many +1s you are bringing from the dropdown, "
        "then click **Sign up**."
    )
    if cap is not None:
        description += f"\n\n_Capacity: **{cap}** people (including guests)._"
    embed = discord.Embed(
        title=title,
        description=description,
        color=discord.Color.green(),
    )
    embed.set_footer(text=_signup_footer(0, 0, creator_name, cap=cap))
    return embed


def _sign_custom_id(message_id: int) -> str:
    return f"signup:sign:{message_id}"


def _leave_custom_id(message_id: int) -> str:
    return f"signup:leave:{message_id}"


def _delete_custom_id(message_id: int) -> str:
    return f"signup:delete:{message_id}"


def _guests_custom_id(message_id: int) -> str:
    return f"signup:guests:{message_id}"


async def _save_signup(view: "SignupView") -> None:
    await db.save_signup_sheet(
        view.message_id,
        view.channel_id,
        view.creator_id,
        view.creator_name,
        view.title,
        view.cap,
        view.signups,
        view.guest_counts,
    )


def _register_signup_view(bot: discord.Client, view: "SignupView") -> None:
    bot.add_view(view)


async def _publish_signup(bot: discord.Client, view: "SignupView") -> None:
    await _save_signup(view)
    _register_signup_view(bot, view)


class PlusOnesSelect(discord.ui.Select):
    def __init__(self, message_id: int):
        options = [
            discord.SelectOption(
                label="Just me",
                value="0",
                description="No additional guests",
            )
        ]
        for count in range(1, 10):
            guest_label = "guest" if count == 1 else "guests"
            options.append(
                discord.SelectOption(
                    label=f"+{count}",
                    value=str(count),
                    description=f"{count} additional {guest_label}",
                )
            )
        super().__init__(
            placeholder="Guests (+1s): 0",
            options=options,
            min_values=1,
            max_values=1,
            row=1,
            custom_id=_guests_custom_id(message_id),
        )

    async def callback(self, interaction: discord.Interaction):
        view: SignupView = self.view
        count = int(self.values[0])
        user_id = interaction.user.id

        if view.cap is not None and user_id in view.signups:
            new_total = _headcount_if_updated(view.signups, user_id, count)
            if new_total > view.cap:
                await interaction.response.send_message(
                    _cap_exceeded_message(view.cap),
                    ephemeral=True,
                )
                return
            view.signups[user_id]["plus_ones"] = count

        view.guest_counts[user_id] = count
        self.placeholder = f"Guests (+1s): {count}"
        await interaction.response.edit_message(embed=view.build_embed(), view=view)
        await _save_signup(view)


class SignUpButton(discord.ui.Button):
    def __init__(self, message_id: int):
        super().__init__(
            label="Sign up",
            style=discord.ButtonStyle.primary,
            row=0,
            custom_id=_sign_custom_id(message_id),
        )

    async def callback(self, interaction: discord.Interaction):
        view: SignupView = self.view
        user = interaction.user
        plus_ones = view.guest_counts.get(user.id, 0)

        if view.cap is not None:
            new_total = _headcount_if_updated(view.signups, user.id, plus_ones)
            if new_total > view.cap:
                await interaction.response.send_message(
                    _cap_exceeded_message(view.cap),
                    ephemeral=True,
                )
                return

        view.signups[user.id] = {
            "display_name": user.display_name,
            "plus_ones": plus_ones,
        }

        await interaction.response.edit_message(embed=view.build_embed(), view=view)
        await _save_signup(view)


class DeleteSignupButton(discord.ui.Button):
    def __init__(self, message_id: int):
        super().__init__(
            label="Delete sheet",
            style=discord.ButtonStyle.danger,
            row=0,
            custom_id=_delete_custom_id(message_id),
        )

    async def callback(self, interaction: discord.Interaction):
        view: SignupView = self.view
        if interaction.user.id != view.creator_id:
            await interaction.response.send_message(
                "Only the person who created this signup sheet can delete it.",
                ephemeral=True,
            )
            return

        for item in view.children:
            item.disabled = True

        embed = discord.Embed(
            title=f"{view.title} (deleted)",
            description="This signup sheet was deleted by its creator.",
            color=discord.Color.dark_grey(),
        )
        await interaction.response.edit_message(embed=embed, view=view)
        await db.delete_signup_sheet(view.message_id)
        view.stop()


class LeaveSignupButton(discord.ui.Button):
    def __init__(self, message_id: int):
        super().__init__(
            label="Leave",
            style=discord.ButtonStyle.secondary,
            row=0,
            custom_id=_leave_custom_id(message_id),
        )

    async def callback(self, interaction: discord.Interaction):
        view: SignupView = self.view
        user = interaction.user

        if user.id not in view.signups:
            await interaction.response.send_message(
                "You are not on the signup sheet.", ephemeral=True
            )
            return

        del view.signups[user.id]
        view.guest_counts.pop(user.id, None)

        await interaction.response.edit_message(embed=view.build_embed(), view=view)
        await _save_signup(view)


class SignupView(discord.ui.View):
    def __init__(
        self,
        title: str,
        creator_id: int,
        creator_name: str,
        channel_id: int,
        message_id: int,
        *,
        cap: int | None = None,
        signups: dict[int, dict] | None = None,
        guest_counts: dict[int, int] | None = None,
    ):
        super().__init__(timeout=None)
        self.title = title
        self.creator_id = creator_id
        self.creator_name = creator_name
        self.channel_id = channel_id
        self.message_id = message_id
        self.cap = cap
        self.signups = dict(signups) if signups is not None else {}
        self.guest_counts = dict(guest_counts) if guest_counts is not None else {}
        self.add_item(SignUpButton(message_id))
        self.add_item(LeaveSignupButton(message_id))
        self.add_item(DeleteSignupButton(message_id))
        self.add_item(PlusOnesSelect(message_id))

    @classmethod
    def from_stored(cls, stored: dict) -> "SignupView":
        return cls(
            stored["title"],
            stored["creator_id"],
            stored["creator_name"],
            stored["channel_id"],
            stored["message_id"],
            cap=stored["cap"],
            signups=stored["signups"],
            guest_counts=stored["guest_counts"],
        )

    def build_embed(self) -> discord.Embed:
        if not self.signups:
            return build_empty_signup_embed(
                self.title, self.creator_name, cap=self.cap
            )

        entries = list(self.signups.values())
        description = "\n".join(
            format_signup_line(index, entry) for index, entry in enumerate(entries)
        )
        signup_count = len(self.signups)
        headcount = total_headcount(self.signups)
        embed = discord.Embed(
            title=self.title,
            description=description,
            color=discord.Color.green(),
        )
        embed.set_footer(
            text=_signup_footer(
                signup_count,
                headcount,
                self.creator_name,
                cap=self.cap,
            )
        )
        return embed


class SignupCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self._restored = False

    @commands.Cog.listener()
    async def on_ready(self):
        if self._restored:
            return
        for _ in range(20):
            if db.pool is not None:
                self._restored = True
                await self._restore_signup_sheets()
                return
            await asyncio.sleep(0.25)
        logger.warning("Signup: database pool not ready; sheets not restored")

    async def _restore_signup_sheets(self):
        stored_sheets = await db.get_all_signup_sheets()
        for stored in stored_sheets:
            view = SignupView.from_stored(stored)
            _register_signup_view(self.bot, view)
        if stored_sheets:
            logger.info("Restored %d signup sheet(s)", len(stored_sheets))

    @commands.command(
        name="signup",
        help="Create a signup sheet. Usage: !signup [cap] [title]",
    )
    async def signup(self, ctx, *, args: str = None):
        if not args or not args.strip():
            await ctx.send(
                "Usage: `!signup [cap] [title]`\n"
                "Example: `!signup 20 Game night` — max 20 people including guests."
            )
            return

        parts = args.strip().split(maxsplit=1)
        cap = None
        if parts[0].isdigit():
            cap = int(parts[0])
            if cap < 1:
                await ctx.send("Cap must be at least 1.")
                return
            if cap > 500:
                await ctx.send("Cap must be 500 or fewer.")
                return
            title = parts[1].strip() if len(parts) > 1 else ""
        else:
            title = parts[0].strip()

        if not title:
            await ctx.send(
                "Usage: `!signup [cap] [title]` — include a title after the optional cap."
            )
            return

        if len(title) > 256:
            await ctx.send("Title must be 256 characters or fewer.")
            return

        message = await ctx.send(
            embed=build_empty_signup_embed(
                title, ctx.author.display_name, cap=cap
            )
        )
        view = SignupView(
            title,
            ctx.author.id,
            ctx.author.display_name,
            ctx.channel.id,
            message.id,
            cap=cap,
        )
        await message.edit(embed=view.build_embed(), view=view)
        await _publish_signup(self.bot, view)


async def setup(bot):
    await bot.add_cog(SignupCog(bot))
