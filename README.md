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


## Install

Conda-first install instructions are in [`INSTALL.md`](INSTALL.md).

From this folder:

```bash
uv run python main.py proxy --launch-browser
```


Then in Chrome:

1. Open `chrome://extensions`
2. Turn on `Developer mode`
3. Click `Load unpacked`
4. Select the extension in the extension folder `"chromey-extension"`
5. Click the extension icon to open the sidepanel
6. Use the cog in the sidepanel to open settings if you want to change the proxy URL or model
7. you will almost certainly need to change the model

# chromey
# chromey
