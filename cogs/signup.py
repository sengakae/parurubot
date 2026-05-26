import discord
from discord.ext import commands

SIGNUP_TIMEOUT = 604800


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


class PlusOnesSelect(discord.ui.Select):
    def __init__(self):
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


class SignUpButton(discord.ui.Button):
    def __init__(self):
        super().__init__(label="Sign up", style=discord.ButtonStyle.primary, row=0)

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


class DeleteSignupButton(discord.ui.Button):
    def __init__(self):
        super().__init__(
            label="Delete sheet",
            style=discord.ButtonStyle.danger,
            row=0,
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
        view.stop()


class LeaveSignupButton(discord.ui.Button):
    def __init__(self):
        super().__init__(label="Leave", style=discord.ButtonStyle.secondary, row=0)

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


class SignupView(discord.ui.View):
    def __init__(
        self,
        title: str,
        creator_id: int,
        creator_name: str,
        *,
        cap: int | None = None,
    ):
        super().__init__(timeout=SIGNUP_TIMEOUT)
        self.title = title
        self.creator_id = creator_id
        self.creator_name = creator_name
        self.cap = cap
        self.signups: dict[int, dict] = {}
        self.guest_counts: dict[int, int] = {}
        self.add_item(SignUpButton())
        self.add_item(LeaveSignupButton())
        self.add_item(DeleteSignupButton())
        self.add_item(PlusOnesSelect())

    def build_embed(self) -> discord.Embed:
        if not self.signups:
            description = (
                "No signups yet.\n\n"
                "Choose how many +1s you are bringing from the dropdown, "
                "then click **Sign up**."
            )
            if self.cap is not None:
                description += f"\n\n_Capacity: **{self.cap}** people (including guests)._"
        else:
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
        if self.cap is not None:
            footer = (
                f"{signup_count} signed up · {headcount}/{self.cap} spots filled "
                f"(including guests) · Created by {self.creator_name}"
            )
        else:
            footer = (
                f"{signup_count} signed up · {headcount} total including guests "
                f"· Created by {self.creator_name}"
            )
        embed.set_footer(text=footer)
        return embed


class SignupCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

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

        view = SignupView(
            title, ctx.author.id, ctx.author.display_name, cap=cap
        )
        await ctx.send(embed=view.build_embed(), view=view)


async def setup(bot):
    await bot.add_cog(SignupCog(bot))
