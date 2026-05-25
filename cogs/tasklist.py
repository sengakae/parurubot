import discord
from discord.ext import commands

CHECKED_EMOJI_NAME = "\N{WHITE HEAVY CHECK MARK}"
MAX_TASKS = 25
BUILDER_TIMEOUT = 300


def format_task_line(task: dict) -> str:
    if task["done"]:
        return f"### {CHECKED_EMOJI_NAME} {task['text']}"
    return f"### {task['text']}"


def _author_only(interaction: discord.Interaction, author_id: int) -> bool:
    return interaction.user.id == author_id


def _select_option_label(index: int, task: str) -> str:
    label = f"{index + 1}. {task}"
    return label[:100]


async def _refresh_builder(
    interaction: discord.Interaction, builder_view: "TaskListBuilderView"
):
    builder_view.clear_items()
    builder_view._attach_buttons()
    await interaction.response.edit_message(
        embed=builder_view.build_embed(),
        view=builder_view,
    )


class AddTaskModal(discord.ui.Modal, title="Add a task"):
    def __init__(self, builder_view: "TaskListBuilderView"):
        super().__init__()
        self.builder_view = builder_view
        self.task_input = discord.ui.TextInput(
            label="Task",
            placeholder="What do you need to do?",
            max_length=200,
            required=True,
        )
        self.add_item(self.task_input)

    async def on_submit(self, interaction: discord.Interaction):
        if not _author_only(interaction, self.builder_view.author_id):
            await interaction.response.send_message(
                "Only the person who started this list can add tasks.",
                ephemeral=True,
            )
            return

        text = self.task_input.value.strip()
        if not text:
            await interaction.response.send_message(
                "Task cannot be empty.", ephemeral=True
            )
            return

        if len(self.builder_view.tasks) >= MAX_TASKS:
            await interaction.response.send_message(
                f"You can add at most {MAX_TASKS} tasks.", ephemeral=True
            )
            return

        self.builder_view.tasks.append(text)
        await _refresh_builder(interaction, self.builder_view)


class EditTaskModal(discord.ui.Modal, title="Edit task"):
    def __init__(self, builder_view: "TaskListBuilderView", index: int):
        super().__init__()
        self.builder_view = builder_view
        self.index = index
        self.task_input = discord.ui.TextInput(
            label="Task",
            default=builder_view.tasks[index],
            max_length=200,
            required=True,
        )
        self.add_item(self.task_input)

    async def on_submit(self, interaction: discord.Interaction):
        if not _author_only(interaction, self.builder_view.author_id):
            await interaction.response.send_message(
                "Only the person who started this list can edit tasks.",
                ephemeral=True,
            )
            return

        text = self.task_input.value.strip()
        if not text:
            await interaction.response.send_message(
                "Task cannot be empty.", ephemeral=True
            )
            return

        self.builder_view.tasks[self.index] = text
        await _refresh_builder(interaction, self.builder_view)


class EditTaskSelect(discord.ui.Select):
    def __init__(self, tasks: list[str]):
        options = [
            discord.SelectOption(
                label=_select_option_label(index, task),
                value=str(index),
            )
            for index, task in enumerate(tasks)
        ]
        super().__init__(
            placeholder="Edit a task...",
            options=options,
            min_values=1,
            max_values=1,
            row=1,
        )

    async def callback(self, interaction: discord.Interaction):
        view: TaskListBuilderView = self.view
        if not _author_only(interaction, view.author_id):
            await interaction.response.send_message(
                "Only the person who started this list can edit tasks.",
                ephemeral=True,
            )
            return

        index = int(self.values[0])
        await interaction.response.send_modal(EditTaskModal(view, index))


class AddTaskButton(discord.ui.Button):
    def __init__(self, *, disabled: bool = False):
        super().__init__(
            label="Add task",
            style=discord.ButtonStyle.primary,
            disabled=disabled,
        )

    async def callback(self, interaction: discord.Interaction):
        view: TaskListBuilderView = self.view
        if not _author_only(interaction, view.author_id):
            await interaction.response.send_message(
                "Only the person who started this list can add tasks.",
                ephemeral=True,
            )
            return

        await interaction.response.send_modal(AddTaskModal(view))


class FinishListButton(discord.ui.Button):
    def __init__(self):
        super().__init__(label="Finish", style=discord.ButtonStyle.success)

    async def callback(self, interaction: discord.Interaction):
        view: TaskListBuilderView = self.view
        if not _author_only(interaction, view.author_id):
            await interaction.response.send_message(
                "Only the person who started this list can finish it.",
                ephemeral=True,
            )
            return

        if not view.tasks:
            await interaction.response.send_message(
                "Add at least one task before finishing.", ephemeral=True
            )
            return

        task_view = TaskListView(view.tasks, view.author_name)
        await interaction.response.edit_message(
            embed=task_view.build_embed(),
            view=task_view,
        )


class CancelListButton(discord.ui.Button):
    def __init__(self):
        super().__init__(label="Cancel", style=discord.ButtonStyle.danger)

    async def callback(self, interaction: discord.Interaction):
        view: TaskListBuilderView = self.view
        if not _author_only(interaction, view.author_id):
            await interaction.response.send_message(
                "Only the person who started this list can cancel it.",
                ephemeral=True,
            )
            return

        for item in view.children:
            item.disabled = True

        embed = discord.Embed(
            title="Task list cancelled",
            description="This task list was not created.",
            color=discord.Color.dark_grey(),
        )
        await interaction.response.edit_message(embed=embed, view=view)


class TaskListBuilderView(discord.ui.View):
    def __init__(self, author_id: int, author_name: str):
        super().__init__(timeout=BUILDER_TIMEOUT)
        self.author_id = author_id
        self.author_name = author_name
        self.tasks: list[str] = []
        self._attach_buttons()

    def _attach_buttons(self):
        at_limit = len(self.tasks) >= MAX_TASKS
        self.add_item(AddTaskButton(disabled=at_limit))
        self.add_item(FinishListButton())
        self.add_item(CancelListButton())
        if self.tasks:
            self.add_item(EditTaskSelect(self.tasks))

    def build_embed(self) -> discord.Embed:
        if not self.tasks:
            description = (
                "No tasks yet. Click **Add task** to get started.\n\n"
                "When you're done, click **Finish** to create your checklist."
            )
        else:
            lines = [f"### {index + 1}. {task}" for index, task in enumerate(self.tasks)]
            description = (
                "\n".join(lines)
                + "\n\n_Click **Add task** for more, use **Edit a task** to change one, "
                "or **Finish** to publish._"
            )

        embed = discord.Embed(
            title="Build your task list",
            description=description,
            color=discord.Color.blurple(),
        )
        embed.set_footer(
            text=f"{len(self.tasks)}/{MAX_TASKS} tasks · {self.author_name}"
        )
        return embed

    async def on_timeout(self):
        for item in self.children:
            item.disabled = True


class TaskToggleButton(discord.ui.Button):
    def __init__(self, index: int, done: bool):
        super().__init__(
            label=str(index + 1),
            style=(
                discord.ButtonStyle.success if done else discord.ButtonStyle.secondary
            ),
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
        lines = [format_task_line(task) for task in self.tasks]
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
        help="Create an interactive to-do list with checkboxes.",
    )
    async def tasklist(self, ctx):
        view = TaskListBuilderView(ctx.author.id, ctx.author.display_name)
        await ctx.send(embed=view.build_embed(), view=view)


async def setup(bot):
    await bot.add_cog(TaskListCog(bot))
