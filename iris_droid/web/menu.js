const idleMs = 180_000;

export function mountMenu({ selectPersonality, loadPersonalities, executeCommand, web = false }) {
  const stage = document.querySelector(".iris-stage");
  stage.insertAdjacentHTML("beforeend", `
    <button type="button" class="iris-menu-trigger" aria-controls="iris-menu"
      aria-expanded="false" aria-label="Open Iris menu"><span aria-hidden="true">☰</span></button>
    <section id="iris-menu" class="iris-menu" aria-labelledby="iris-menu-title" hidden>
      <header class="iris-menu-header">
        <button type="button" class="iris-menu-back" aria-label="Back">←</button>
        <h2 id="iris-menu-title" tabindex="-1">Iris menu</h2>
        <button type="button" class="iris-menu-close" aria-label="Close menu">×</button>
      </header>
      <div class="iris-menu-content"></div>
    </section>
  `);
  const trigger = stage.querySelector(".iris-menu-trigger");
  const panel = stage.querySelector(".iris-menu");
  const back = panel.querySelector(".iris-menu-back");
  const close = panel.querySelector(".iris-menu-close");
  const title = panel.querySelector("h2");
  const content = panel.querySelector(".iris-menu-content");
  let page = "main";
  let category = null;
  let commands = null;
  let commandRequest = null;
  let commandError = "";
  let names = [];
  let active = null;
  let wakePhrase = null;
  let available = web;
  let loading = false;
  let choosing = false;
  let personalityError = "";
  let timer;
  let busy = false;
  let executing = false;
  let actionError = "";
  let confirmation = null;

  function resetTimer() {
    clearTimeout(timer);
    timer = setTimeout(dismiss, idleMs);
  }

  function dismiss() {
    clearTimeout(timer);
    panel.hidden = true;
    trigger.setAttribute("aria-expanded", "false");
    stage.classList.remove("iris-menu-open");
    if (panel.contains(document.activeElement)) trigger.focus({ preventScroll: true });
  }

  function note(text, live = false) {
    const hint = document.createElement("p");
    hint.className = "iris-menu-note";
    hint.textContent = text;
    if (live) hint.setAttribute("role", "status");
    content.append(hint);
  }

  function tiles(items) {
    const grid = document.createElement("div");
    grid.className = "iris-menu-grid";
    for (const [label, color, action, selected = null] of items) {
      const button = document.createElement("button");
      button.type = "button";
      button.className = "iris-menu-tile";
      button.dataset.color = color;
      button.textContent = label;
      if (selected !== null) {
        button.setAttribute("aria-pressed", String(selected));
        button.disabled = choosing;
      }
      button.addEventListener("click", action);
      grid.append(button);
    }
    content.append(grid);
  }

  function phrases(rows) {
    const list = document.createElement("dl");
    list.className = "iris-menu-phrases";
    for (const [phrase, meaning] of rows) {
      const row = document.createElement("div");
      const term = document.createElement("dt");
      const description = document.createElement("dd");
      const action = commands?.tap_commands?.[phrase];
      const target = web ? "web" : "client";
      if (executeCommand && action?.targets.includes(target)) {
        const button = document.createElement("button");
        button.type = "button";
        button.className = "iris-command-action";
        button.setAttribute("aria-label", phrase);
        button.textContent = phrase;
        button.disabled = busy || executing || !available;
        button.addEventListener("click", () => {
          if (action.confirm) {
            confirmation = {phrase, meaning};
            go("confirm-command", category);
          } else void runCommand(phrase);
        });
        term.append(button);
        row.classList.add("iris-command-row");
      } else term.textContent = phrase;
      description.textContent = meaning;
      row.append(term, description);
      list.append(row);
    }
    content.append(list);
  }

  function render() {
    if (panel.hidden) return;
    const focused = document.activeElement;
    const oldTiles = [...content.querySelectorAll("button")];
    const focusedIndex = oldTiles.indexOf(focused);
    const scroll = content.scrollTop;
    content.replaceChildren();
    back.hidden = page === "main";
    title.textContent = {
      main: "Iris menu",
      commands: "Command list",
      category: category?.title,
      personalities: "Personalities",
      home: "Home",
      "confirm-command": "Confirm command",
    }[page];
    if (page === "confirm-command" && confirmation) {
      note(`${confirmation.phrase}? ${confirmation.meaning}`);
      if (actionError) note(actionError, true);
      if (busy || executing) note("Wait for Iris to finish this turn.", true);
      else tiles([[`Confirm: ${confirmation.phrase}`, "peach", () => void runCommand(confirmation.phrase)]]);
      tiles([["Go back", "lilac", () => go("category", category)]]);
    } else if (page === "main") {
      tiles([
        ["Command list", "mint", () => go("commands")],
        ["Personalities", "lilac", () => go("personalities")],
        ["Home", "peach", () => go("home")],
      ]);
    } else if (page === "personalities") {
      if (loading) {
        note("Loading personalities…", true);
      } else if (personalityError && !names.length) {
        note(personalityError, true);
        if (web) tiles([["Retry", "lilac", refreshPersonalities]]);
      } else if (!available) {
        note("Waiting for the Iris client…", true);
      } else if (!names.length) {
        note("There are no personalities configured for this client.");
      } else {
        note(personalityError || (choosing ? "Switching personality…" :
          "Tap a personality to use it on the next turn."), true);
        tiles(names.map(name => [name, "lilac", () => choose(name), active === name]));
      }
    } else if (!commands) {
      note(commandError || "Loading commands…", true);
      if (commandError) tiles([["Retry", "mint", readCommands]]);
    } else if (page === "commands") {
      note("Tap a command button to run it, or say any of these phrases aloud.");
      tiles(commands.categories.map(item => [
        item.title, item.color, () => go("category", item),
      ]));
    } else if (page === "category") {
      if (actionError) note(actionError, true);
      else if (busy || executing) note("Wait for Iris to finish this turn.", true);
      const conversation = category.title === "Conversation";
      if (web && conversation) {
        note("Wake mode, voice cancel and spontaneous companion replies run on the robot.");
      } else if (category.note) {
        note(category.note);
      }
      const wake = conversation && wakePhrase
        ? [[wakePhrase, "Wake Iris and wait for the listening cue."]] : [];
      phrases([...wake, ...category.commands]);
    } else if (page === "home") {
      note("With home automation set up, ask Iris about your own devices.");
      phrases(commands.home);
    }
    content.scrollTop = scroll;
    if (focusedIndex >= 0) {
      const replacement = content.querySelectorAll("button")[focusedIndex];
      (replacement && !replacement.disabled ? replacement : title).focus({ preventScroll: true });
    }
  }

  async function runCommand(phrase) {
    if (!executeCommand || busy || executing || !available) return;
    executing = true;
    actionError = "";
    if (page === "confirm-command") page = "category";
    confirmation = null;
    render();
    try {
      await executeCommand(phrase);
    } catch (error) {
      actionError = error.message || "Command not accepted. Try again when Iris is ready.";
    } finally {
      executing = false;
      render();
    }
  }

  function readCommands() {
    if (commands || commandRequest) return;
    commandError = "";
    render();
    commandRequest = fetch("/commands.json")
      .then(response => {
        if (!response.ok) throw new Error();
        return response.json();
      })
      .then(data => { commands = data; })
      .catch(() => { commandError = "Couldn’t load the command list."; })
      .finally(() => {
        commandRequest = null;
        render();
      });
  }

  async function refreshPersonalities() {
    if (!loadPersonalities || loading) return;
    loading = true;
    personalityError = "";
    render();
    try {
      const result = await loadPersonalities();
      names = result.names;
      active = result.active;
    } catch (error) {
      names = [];
      personalityError = error.message;
    } finally {
      loading = false;
      if (page === "personalities") render();
    }
  }

  async function choose(name) {
    if (choosing || name === active) return;
    choosing = true;
    personalityError = "";
    render();
    try {
      await selectPersonality(name);
      active = name;
    } catch (error) {
      personalityError = error.message;
    } finally {
      choosing = false;
      if (page === "personalities") render();
    }
  }

  function go(next, item = null) {
    page = next;
    category = item;
    content.scrollTop = 0;
    if (page === "commands" || page === "home") readCommands();
    if (page === "personalities") {
      personalityError = "";
      void refreshPersonalities();
    }
    render();
    title.focus({ preventScroll: true });
  }

  trigger.addEventListener("click", () => {
    panel.hidden = false;
    trigger.setAttribute("aria-expanded", "true");
    stage.classList.add("iris-menu-open");
    resetTimer();
    go("main");
  });
  close.addEventListener("click", dismiss);
  back.addEventListener("click", () => go(page === "confirm-command" ? "category" : page === "category" ? "commands" : "main", category));
  for (const event of ["pointerdown", "keydown", "wheel", "scroll"]) {
    panel.addEventListener(event, resetTimer, { passive: true, capture: true });
  }
  document.addEventListener("keydown", event => {
    if (event.key === "Escape" && !panel.hidden) {
      event.preventDefault();
      dismiss();
    }
  });

  return {
    setBusy(value) {
      if (busy !== value) { busy = value; render(); }
    },
    update(state) {
      const nextBusy = state?.commands_ready !== true;
      const busyChanged = busy !== nextBusy;
      busy = nextBusy;
      const newNames = state?.personalities || [];
      const newActive = state?.personality || null;
      const newWake = state?.wake_phrase || null;
      const changed = available !== Boolean(state) || active !== newActive ||
        names.length !== newNames.length || names.some((name, i) => name !== newNames[i]);
      available = Boolean(state);
      names = newNames;
      if (!choosing) active = newActive;
      if (busyChanged || (changed && page === "personalities" && !choosing) ||
          (wakePhrase !== newWake && page === "category" && category.title === "Conversation")) {
        wakePhrase = newWake;
        render();
      } else {
        wakePhrase = newWake;
      }
    },
    setActive(name) {
      active = name;
      if (page === "personalities") render();
    },
  };
}
