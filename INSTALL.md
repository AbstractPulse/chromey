# Install

These instructions assume you are using Conda.

## 1. Create the Conda Environment

Use the included environment file:

```bash
conda env create -f environment.yml
conda activate chromey
```

If you prefer to create it manually:

```bash
conda create -n chromey python=3.11 -y
conda activate chromey
python -m pip install uv
```

## 2. Install the Project

From the project root:

```bash
uv sync
```

That installs the Python dependencies from `pyproject.toml` and `uv.lock`.

## 3. Optional Config

Chromey now reads `config.yaml` from the project root automatically.

If Chrome is not detected on your machine, edit:

```text
config.yaml
```

and set:

```yaml
chrome_path: "/path/to/google-chrome"
```

Examples:

```yaml
chrome_path: "/usr/bin/google-chrome"
```

```yaml
chrome_path: "/var/lib/flatpak/exports/bin/com.google.Chrome"
```

## 4. Run Chromey

Start the proxy and have it launch Chrome:

```bash
uv run python main.py proxy --launch-browser
```

Chromey will start the local server and launch its dedicated Chrome profile.

## 5. Install the Chrome Extension

In the Chrome window launched by Chromey:

1. Open `chrome://extensions`
2. Turn on `Developer mode`
3. Click `Load unpacked`
4. Select the path printed by:

```bash
uv run python main.py extension-path
```

For Flatpak Chrome this will be the staged stable path under:

```text
$HOME/.var/app/com.google.Chrome/data/chromey/extension
```

5. Click the `Chromey` extension icon
6. Open the sidepanel

## 6. Configure the Model

Open the sidepanel, click the cog, and set:

- `Proxy URL`: usually `http://127.0.0.1:8089`
- `Model`: leave blank for auto, or type the exact LM Studio model id

## 7. Start Using It

Type a message in the sidepanel chat and press `Enter`.

Useful commands:

```bash
uv run python main.py extension-path
uv run python main.py detect-chrome
uv run python main.py provider-check
```
