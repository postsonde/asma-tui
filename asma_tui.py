import json
import random
from pathlib import Path
import httpx
from textual import work
from textual.app import App, ComposeResult
from textual.containers import Center, Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, Footer, Header, Input, Label, OptionList, Static
from textual.widgets.option_list import Option

API_URL = "https://api.aladhan.com/v1/asmaAlHusna"
CONFIG_DIR = Path.home() / ".config" / "asma-tui"
STATE_FILE = CONFIG_DIR / "state.json"
CACHE_FILE = CONFIG_DIR / "names.json"


def load_state() -> dict:
    if STATE_FILE.exists():
        try:
            data = json.loads(STATE_FILE.read_text(encoding="utf-8"))
            data.setdefault("learned", [])
            data.setdefault("reverse", False)
            data.setdefault("best_streak", 0)
            return data
        except Exception:
            pass
    return {"learned": [], "reverse": False, "best_streak": 0}


def save_state(state: dict) -> None:
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(json.dumps(state, indent=2), encoding="utf-8")


class ListModal(ModalScreen[int | None]):
    CSS = """
    ListModal { align: center middle; }
    #dialog { width: 70; height: 26; border: thick $primary; background: $surface; padding: 1 2; }
    Input { margin-bottom: 1; }
    OptionList { height: 1fr; }
    """

    def __init__(self, names: list[dict], learned: set[int]) -> None:
        super().__init__()
        self.names = names
        self.learned = learned

    def compose(self) -> ComposeResult:
        with Vertical(id="dialog"):
            yield Label("[b]All 99 Names[/b] (Enter to practice, Esc to close)")
            yield Input(placeholder="Filter by number, name, or meaning...", id="search")
            yield OptionList(id="name_list")

    def on_mount(self) -> None:
        self.populate("")
        self.query_one("#search", Input).focus()

    def populate(self, query: str) -> None:
        ol = self.query_one("#name_list", OptionList)
        ol.clear_options()
        q = query.strip().lower()
        for item in self.names:
            num = item["number"]
            arabic = item["name"]
            trans = item["transliteration"]
            meaning = item["en"]["meaning"]
            mark = "[green]✓[/green]" if num in self.learned else " "
            line = f"{mark} {num:>2}. {trans} ({arabic}) — {meaning}"
            if not q or q in line.lower():
                ol.add_option(Option(line, id=str(num)))

    def on_input_changed(self, event: Input.Changed) -> None:
        if event.input.id == "search":
            self.populate(event.value)

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        if event.option.id:
            self.dismiss(int(event.option.id))

    def on_key(self, event) -> None:
        if event.key == "escape":
            self.dismiss(None)


class ResetModal(ModalScreen[bool]):
    CSS = """
    ResetModal { align: center middle; }
    #dialog { width: 46; height: auto; border: thick #ff5555; background: $surface; padding: 1 2; }
    #buttons { margin-top: 2; align: right middle; height: auto; }
    Button { margin-left: 1; }
    """

    def compose(self) -> ComposeResult:
        with Vertical(id="dialog"):
            yield Label("[b]Reset Progress?[/b]\nThis clears all 99 learned names and your best streak.")
            with Horizontal(id="buttons"):
                yield Button("Cancel", id="cancel")
                yield Button("Reset", variant="error", id="confirm")

    def on_key(self, event) -> None:
        if event.key == "escape":
            self.dismiss(False)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        self.dismiss(event.button.id == "confirm")


class AsmaTUI(App):
    TITLE = "☪ ✦ A S M A - U L - H U S N A ✦ ☪"
    CSS = """
    Header { color: #f1fa8c; text-style: bold; }
    #main { align: center middle; height: 1fr; padding: 1 2; }
    #stats { text-align: center; color: $accent; margin-bottom: 1; }
    #card {
        width: 66;
        height: 13;
        border: round $primary;
        background: $surface;
        content-align: center middle;
        text-align: center;
        padding: 1 3;
    }
    #card.flipped { border: round #50fa7b; }
    #card.wrong { border: round #ff5555; }
    #hint { text-align: center; color: $text-muted; margin-top: 1; }
    #actions { align: center middle; height: auto; margin-top: 1; }
    #quiz_options { width: 66; height: auto; margin-top: 1; }
    #quiz_options Button { width: 100%; margin: 0 0 1 0; }
    Button { margin: 0 1; }
    .hidden { display: none; }
    """
    BINDINGS = [
        ("m", "toggle_mode", "Quiz / Cards"),
        ("space", "flip_card", "Flip Card"),
        ("1", "key_one", "1 / Known"),
        ("2", "key_two", "2 / Again"),
        ("3", "key_three", "Option 3"),
        ("4", "key_four", "Option 4"),
        ("n", "next_card", "Next"),
        ("r", "toggle_direction", "Reverse"),
        ("l", "open_list", "All 99"),
        ("x", "reset_progress", "Reset"),
        ("q", "quit", "Quit"),
    ]

    def __init__(self) -> None:
        super().__init__()
        self.state = load_state()
        self.names: list[dict] = []
        self.current: dict | None = None
        self.flipped: bool = False
        self.quiz_mode: bool = False
        self.quiz_choices: list[dict] = []
        self.quiz_answered: bool = False
        self.streak: int = 0

    def compose(self) -> ComposeResult:
        yield Header()
        with Vertical(id="main"):
            yield Static("Loading 99 Names...", id="stats")
            with Center():
                yield Static("", id="card")
            yield Static("Press [b]Space[/b] to flip card", id="hint")
            with Horizontal(id="actions"):
                yield Button("Flip (Space)", id="btn_flip", variant="primary")
                yield Button("✓ Known (1)", id="btn_known", variant="success")
                yield Button("↻ Again (2)", id="btn_again", variant="warning")
            with Center():
                with Vertical(id="quiz_options", classes="hidden"):
                    for i in range(4):
                        yield Button("", id=f"qopt_{i}")
        yield Footer()

    def on_mount(self) -> None:
        self.theme = "dracula"
        self.fetch_names()

    @work(exclusive=True)
    async def fetch_names(self) -> None:
        if CACHE_FILE.exists():
            try:
                self.names = json.loads(CACHE_FILE.read_text(encoding="utf-8"))
            except Exception:
                pass

        if not self.names:
            try:
                async with httpx.AsyncClient(timeout=15) as client:
                    r = await client.get(API_URL)
                    r.raise_for_status()
                    self.names = r.json()["data"]
                    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
                    CACHE_FILE.write_text(json.dumps(self.names), encoding="utf-8")
            except Exception as exc:
                self.query_one("#stats", Static).update(f"[red]Error loading names: {exc}[/red]")
                return

        self.pick_next_card()

    def pick_next_card(self, specific_num: int | None = None) -> None:
        if not self.names:
            return
        if specific_num:
            self.current = next((n for n in self.names if n["number"] == specific_num), self.names[0])
        else:
            learned = set(self.state["learned"])
            unlearned = [n for n in self.names if n["number"] not in learned]
            pool = unlearned if unlearned else self.names
            candidates = [n for n in pool if not self.current or n["number"] != self.current["number"]]
            self.current = random.choice(candidates or pool)

        self.flipped = False
        self.quiz_answered = False
        if self.quiz_mode and self.current:
            others = [n for n in self.names if n["number"] != self.current["number"]]
            distractors = random.sample(others, k=min(3, len(others)))
            self.quiz_choices = [self.current, *distractors]
            random.shuffle(self.quiz_choices)
        self.render_card()

    def render_card(self) -> None:
        if not self.current:
            return
        learned_count = len(set(self.state["learned"]))
        reverse = self.state["reverse"]
        dir_label = "Meaning → Name" if reverse else "Name → Meaning"

        if self.quiz_mode:
            self.query_one("#stats", Static).update(
                f"[bold #ff79c6]QUIZ[/bold #ff79c6]  •  Streak: [bold #50fa7b]{self.streak}[/bold #50fa7b] "
                f"(Best: {self.state['best_streak']})  •  {dir_label}"
            )
            self.query_one("#actions").add_class("hidden")
            self.query_one("#quiz_options").remove_class("hidden")
        else:
            self.query_one("#stats", Static).update(
                f"Learned: [bold #50fa7b]{learned_count} / {len(self.names)}[/bold #50fa7b]  •  "
                f"Mode: [bold]{dir_label}[/bold]"
            )
            self.query_one("#actions").remove_class("hidden")
            self.query_one("#quiz_options").add_class("hidden")

        num = self.current["number"]
        arabic = self.current["name"]
        trans = self.current["transliteration"]
        meaning = self.current["en"]["meaning"]
        is_known = num in self.state["learned"]
        badge = "  [green]✓ Learned[/green]" if is_known else ""

        card = self.query_one("#card", Static)
        hint = self.query_one("#hint", Static)
        card.remove_class("flipped", "wrong")

        if self.quiz_mode:
            if reverse:
                prompt = f"[b][yellow]#{num}[/yellow][/b]{badge}\n\n[b #bd93f9]{meaning}[/b #bd93f9]"
            else:
                prompt = (
                    f"[b][yellow]#{num}[/yellow][/b]{badge}\n\n"
                    f"[b #8be9fd]{arabic}[/b #8be9fd]\n\n"
                    f"[b #f1fa8c]{trans}[/b #f1fa8c]"
                )
            card.update(prompt)
            for idx, choice in enumerate(self.quiz_choices):
                btn = self.query_one(f"#qopt_{idx}", Button)
                text = f"{choice['transliteration']} ({choice['name']})" if reverse else choice["en"]["meaning"]
                btn.label = f"[{idx + 1}]  {text}"
                btn.variant = "default"
            if not self.quiz_answered:
                hint.update("Choose the matching answer ([b]1–4[/b])")
            return

        if reverse:
            front_text = f"[b][yellow]#{num}[/yellow][/b]{badge}\n\n[b #bd93f9]{meaning}[/b #bd93f9]"
        else:
            front_text = (
                f"[b][yellow]#{num}[/yellow][/b]{badge}\n\n"
                f"[b #8be9fd]{arabic}[/b #8be9fd]\n\n"
                f"[b #f1fa8c]{trans}[/b #f1fa8c]"
            )

        back_text = (
            f"[b][yellow]#{num}[/yellow][/b]{badge}\n\n"
            f"[b #8be9fd]{arabic}[/b #8be9fd]  •  [b #f1fa8c]{trans}[/b #f1fa8c]\n\n"
            f"[bold #50fa7b]{meaning}[/bold #50fa7b]"
        )

        if self.flipped:
            card.add_class("flipped")
            card.update(back_text)
            hint.update("Press [b]1[/b] if you knew it, or [b]2[/b] to practice again")
        else:
            card.update(front_text)
            hint.update("Press [b]Space[/b] to flip card")

    def submit_quiz_answer(self, idx: int) -> None:
        if not self.quiz_mode or not self.current or idx >= len(self.quiz_choices):
            return
        if self.quiz_answered:
            self.pick_next_card()
            return

        self.quiz_answered = True
        picked = self.quiz_choices[idx]
        correct_num = self.current["number"]
        card = self.query_one("#card", Static)
        hint = self.query_one("#hint", Static)

        for i, choice in enumerate(self.quiz_choices):
            btn = self.query_one(f"#qopt_{i}", Button)
            if choice["number"] == correct_num:
                btn.variant = "success"
            elif i == idx:
                btn.variant = "error"

        if picked["number"] == correct_num:
            self.streak += 1
            if self.streak > self.state["best_streak"]:
                self.state["best_streak"] = self.streak
            if correct_num not in self.state["learned"]:
                self.state["learned"].append(correct_num)
            save_state(self.state)
            card.add_class("flipped")
            hint.update("[bold #50fa7b]Correct![/bold #50fa7b] Press [b]n[/b] or [b]Space[/b] for next")
        else:
            self.streak = 0
            card.add_class("wrong")
            hint.update(
                f"[bold #ff5555]Wrong![/bold #ff5555] Answer: [b]{self.current['en']['meaning']}[/b] — Press [b]n[/b]"
            )

        self.query_one("#stats", Static).update(
            f"[bold #ff79c6]QUIZ[/bold #ff79c6]  •  Streak: [bold #50fa7b]{self.streak}[/bold #50fa7b] "
            f"(Best: {self.state['best_streak']})  •  {'Meaning → Name' if self.state['reverse'] else 'Name → Meaning'}"
        )

    def on_button_pressed(self, event: Button.Pressed) -> None:
        btn_id = event.button.id or ""
        if btn_id == "btn_flip":
            self.action_flip_card()
        elif btn_id == "btn_known":
            self.action_mark_known()
        elif btn_id == "btn_again":
            self.action_mark_again()
        elif btn_id.startswith("qopt_"):
            self.submit_quiz_answer(int(btn_id.split("_")[1]))

    def action_toggle_mode(self) -> None:
        self.quiz_mode = not self.quiz_mode
        self.pick_next_card()

    def action_flip_card(self) -> None:
        if self.quiz_mode:
            if self.quiz_answered:
                self.pick_next_card()
        else:
            self.flipped = not self.flipped
            self.render_card()

    def action_key_one(self) -> None:
        if self.quiz_mode:
            self.submit_quiz_answer(0)
        else:
            self.action_mark_known()

    def action_key_two(self) -> None:
        if self.quiz_mode:
            self.submit_quiz_answer(1)
        else:
            self.action_mark_again()

    def action_key_three(self) -> None:
        if self.quiz_mode:
            self.submit_quiz_answer(2)

    def action_key_four(self) -> None:
        if self.quiz_mode:
            self.submit_quiz_answer(3)

    def action_mark_known(self) -> None:
        if not self.current or self.quiz_mode:
            return
        num = self.current["number"]
        if num not in self.state["learned"]:
            self.state["learned"].append(num)
            save_state(self.state)
        self.pick_next_card()

    def action_mark_again(self) -> None:
        if not self.current or self.quiz_mode:
            return
        num = self.current["number"]
        if num in self.state["learned"]:
            self.state["learned"].remove(num)
            save_state(self.state)
        self.pick_next_card()

    def action_next_card(self) -> None:
        self.pick_next_card()

    def action_toggle_direction(self) -> None:
        self.state["reverse"] = not self.state["reverse"]
        save_state(self.state)
        self.pick_next_card(specific_num=self.current["number"] if self.current else None)

    def action_open_list(self) -> None:
        def on_close(num: int | None) -> None:
            if num:
                self.pick_next_card(specific_num=num)

        self.push_screen(ListModal(self.names, set(self.state["learned"])), on_close)

    def action_reset_progress(self) -> None:
        def on_close(confirmed: bool | None) -> None:
            if confirmed:
                self.state["learned"] = []
                self.state["best_streak"] = 0
                self.streak = 0
                save_state(self.state)
                self.pick_next_card()
                self.notify("Progress reset to 0 / 99")

        self.push_screen(ResetModal(), on_close)


def main() -> None:
    AsmaTUI().run()


if __name__ == "__main__":
    main()
