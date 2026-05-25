import discord
from discord.ext import commands

UNCHECKED = "☐"
CHECKED = "☑"
MAX_TASKS = 25


def parse_tasks(text: str) -> list[str]:
    text = text.strip()
    if not text:
        return []

    if "\n" in text:
        parts = text.splitlines()
    elif "|" in text:
        parts = text.split("|")
    else:
        parts = text.split(",")

    return [part.strip() for part in parts if part.strip()]


class TaskToggleButton(discord.ui.Button):
    def __init__(self, index: int, done: bool):
        super().__init__(
            label=str(index + 1),
            style=discord.ButtonStyle.success if done else discord.ButtonStyle.secondary,
            row=index // 5,
        )
        self.index = index

    async def callback(self, interaction: discord.Interaction):
        view: TaskListView = self.view
        view.tasks[self.index]["done"] = not view.tasks[self.index]["done"]
        view.clear_items()
        view._attach_buttons()
        await interaction.response.edit_message(
            embed=view.build_embed(),
            view=view,
        )


class TaskListView(discord.ui.View):
    def __init__(self, tasks: list[str], author_name: str):
        super().__init__(timeout=86400)
        self.author_name = author_name
        self.tasks = [{"text": text, "done": False} for text in tasks]
        self._attach_buttons()

    def _attach_buttons(self):
        for index in range(len(self.tasks)):
            self.add_item(TaskToggleButton(index, self.tasks[index]["done"]))

    def build_embed(self) -> discord.Embed:
        lines = [
            f"{CHECKED if task['done'] else UNCHECKED} {task['text']}"
            for task in self.tasks
        ]
        completed = sum(1 for task in self.tasks if task["done"])
        embed = discord.Embed(
            title="Task list",
            description="\n".join(lines),
            color=discord.Color.blurple(),
        )
        embed.set_footer(
            text=f"{completed}/{len(self.tasks)} completed · Created by {self.author_name}"
        )
        return embed


class TaskListCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @commands.command(
        name="tasklist",
        help="Create a to-do list with checkboxes. Separate items with |, commas, or newlines.",
    )
    async def tasklist(self, ctx, *, items: str = None):
        if not items:
            await ctx.send(
                "Usage: `!tasklist item one | item two | item three`\n"
                "You can also separate items with commas or put each item on a new line."
            )
            return

        parsed = parse_tasks(items)
        if not parsed:
            await ctx.send("Add at least one task.")
            return

        if len(parsed) > MAX_TASKS:
            await ctx.send(
                f"Only the first {MAX_TASKS} tasks were added (Discord button limit)."
            )
        tasks = parsed[:MAX_TASKS]

        view = TaskListView(tasks, ctx.author.display_name)
        await ctx.send(
            embed=view.build_embed(),
            view=view,
        )


async def setup(bot):
    await bot.add_cog(TaskListCog(bot))
