# Chromey!
<p align="center">
  <img src="https://github.com/user-attachments/assets/b0f7281a-4b39-4b46-a97f-503768e144f7" alt="Chromey demo" width="100%" />
</p>

<h1 align="center">Chromey</h1>
<p align="center">Chrome-native local browser agent with LM Studio and browser-use.</p>
<p align="center">
  <a href="#quick-start">Quick Start</a> •
  <a href="#install">Install</a> 
</p>
<p align="center">
  <a href="bitcoin:YOUR_BTC_ADDRESS">
    <img
      src="https://img.shields.io/badge/Donate-Bitcoin-f7931a?style=for-the-badge&logo=bitcoin&logoColor=white"
      alt="Donate Bitcoin"
    />
  </a>
</p>
<p align="center">
  <sub>BTC: bitcoin:bc1quctuk6435rcmfpvzge05yqcdajng8g3dydl4hq?message=recieve</sub>
</p>


Chromey is a local Chrome automation companion with a built-in sidepanel chat. It connects Chrome, LM Studio, and browser-use into one simple workflow, so you can give natural-language instructions and watch the browser carry them out live. The goal is to keep local browser control simple, visible, and practical: one proxy, one extension, one chat interface.


<h2>Future Features</h2>

<ul>
  <li>Task history with saved runs, screenshots, and outcomes</li>
  <li>OpenClaw integration and broader agent interoperability</li>
  <li>Support for more local and hosted model backends beyond LM Studio</li>
  <li>Better long-running task memory and session continuity</li>
  <li>Improved workflow handling for research, shopping, and multi-step browsing</li>
  <li>Richer diagnostics, setup checks, and debugging tools</li>
</ul>


## Quick Start

The recommended path is:

1. Create the Conda environment named `chromey` (optional)
```bash
create conda -n chromey python=3.11 -y
```
2. Install dependencies with `uv`

if you dont have uv:
```bash
pip install uv
```
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

