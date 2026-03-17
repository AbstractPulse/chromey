# Chromey!
<p align="center">
  <img src="https://github.com/user-attachments/assets/b0f7281a-4b39-4b46-a97f-503768e144f7" alt="Chromey demo" width="100%" />
</p>

<h1 align="center">Chromey</h1>
<p align="center">Chrome-native local browser agent with LM Studio and browser-use.</p>
<p align="center">
  <a href="#quick-start">Quick Start</a> •
  <a href="#install">Install</a> •
  <a href="#api">API</a>
</p>

This folder is the cleaned snapshot intended for GitHub publication. It keeps the working app structure, but leaves out local-only state such as virtual environments, old prototypes, and backup folders.

## Quick Start

The recommended path is:

1. Create the Conda environment named `chromey`
2. Install dependencies with `uv`
3. Run Chromey and let it launch Chrome
4. Load the extension in that Chrome window

Use the full guide in [`INSTALL.md`](INSTALL.md).

Chromey is a small Chrome-only local server that sits between:

- a Chrome sidepanel extension
- LM Studio
- `browser-use`

The goal is simple:

1. run one local proxy
2. open a Chrome sidepanel chat
3. send instructions that drive the current Chrome session

The extension flow is intentionally minimal:

- the sidepanel is just the chat surface
- `Enter` sends and `Shift+Enter` adds a new line
- the cog opens a separate settings page
- leave the model blank to auto-prefer a loaded LM Studio model with `iq4` in its id, or type the exact model id yourself
- chat uses the OpenAI-style `POST /v1/chat/completions` contract
- `--launch-browser` starts a dedicated Chromey profile by default so it does not depend on your everyday Chrome session
- each browser task saves screenshots and agent conversation artifacts under `~/.config/chromey/artifacts` by default
- the live browser flashes the element being clicked or typed into so you can see the chosen target
- the local API only accepts browser requests from the installed Chromey extension

## What v2 keeps

- Chrome only
- LM Studio only
- one sidepanel extension
- one local proxy process

## What v2 drops

- Brave-specific code
- desktop launchers
- multiple provider abstractions
- extra compatibility layers that do not help the Chrome flow

## Install

Conda-first install instructions are in [`INSTALL.md`](INSTALL.md).

The short version is:

```bash
conda env create -f environment.yml
conda activate chromey
uv sync
```

## Repository Layout

- `main.py`: CLI entrypoint
- `src/chromey`: server, Chrome integration, session control, LM Studio client
- `extension/chromey-extension`: Chrome sidepanel and settings UI
- `config.yaml`: project-root config file loaded automatically
- `config.example.yaml`: optional config template

## Start the proxy

From this folder:

```bash
uv run python main.py proxy --launch-browser
```

By default this launches an isolated Chromey profile at `~/.config/chromey/chrome-browser`.
That is the recommended path if you want the live chat plus browser-use flow to be reliable.

Only use `--show-profile-picker` if you are intentionally launching against an explicit existing Chrome user-data dir.

If Chrome is already running with CDP enabled:

```bash
uv run python main.py proxy --cdp-url http://127.0.0.1:9222 --connect-on-start
```

## Load the extension

Print the extension directory:

```bash
uv run python main.py extension-path
```

Then in Chrome:

1. Open `chrome://extensions`
2. Turn on `Developer mode`
3. Click `Load unpacked`
4. Select the path printed by `uv run python main.py extension-path`
5. Click the extension icon to open the sidepanel
6. Use the cog in the sidepanel to open settings if you want to change the proxy URL or model

## API

- `GET /v1/models`
- `POST /v1/chat/completions`
- `GET /health`
- `GET /api/health`
- `GET /api/config`
- `GET /api/provider`
- `GET /api/browser`
- `POST /api/browser/start`
- `GET /api/session`
- `POST /api/session/stop`

## Useful commands

Check Chrome discovery:

```bash
uv run python main.py detect-chrome
```

Check LM Studio:

```bash
uv run python main.py provider-check
```

Run a one-off task against an attached Chrome session:

```bash
uv run python main.py run --cdp-url http://127.0.0.1:9222 "go to google and summarize the page"
```

## Config

Optional config files:

- `./config.yaml`
- `./config.yml`
- `./config.json`
- `~/.config/chromey/config.json`
- `~/.config/chromey/config.yaml`
- `~/.config/chromey/config.yml`

Start from [`config.yaml`](config.yaml) or [`config.example.yaml`](config.example.yaml).
If Chrome cannot be detected automatically, set `chrome_path` in the config file.

## Naming

- The product name is `Chromey`.
- The Python package is `chromey`.
- The extension folder is `extension/chromey-extension`.
- Local config, profile, and artifact paths live under `~/.config/chromey` by default.

## License

This snapshot is published under the MIT license in [`LICENSE`](LICENSE).

## Artifacts

Each run creates a folder like:

```text
~/.config/chromey/artifacts/20260312-153000-open-google/
```

Inside it you will find:

- `screenshots/step-001.png`, `step-002.png`, and a final screenshot
- `conversation/` files saved by the browser-use agent

You can override the root artifact directory with `--artifacts-dir` or in your config file.
# chromey
# chromey
